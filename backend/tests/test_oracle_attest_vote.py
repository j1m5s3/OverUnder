"""POST /oracle/attest is operator-only; POST /oracle/vote is per-user with a server-side CTF weight;
GET /oracle/{id}/status reports createdAt per attestation."""

import json
import re
from unittest.mock import patch

import pytest
from sqlalchemy import delete, select

from app.auth.router import _issue
from app.db import SessionLocal
from app.models import Attestation, Market, User, Vote

CID = "0x" + "a7" * 32
UNKNOWN = "0x" + "a8" * 32
OP = "0x" + "0f" * 20
HOLDER = "0x" + "1f" * 20
OTHER = "0x" + "2f" * 20
USERS = (OP, HOLDER, OTHER)

ATTEST = {"conditionId": CID, "agent": "0x" + "3f" * 20, "outcome": 0, "evidenceHash": "0x" + "44" * 32, "summary": "final 31-10"}


async def _clean(session) -> None:
    await session.execute(delete(Attestation).where(Attestation.condition_id == CID))
    await session.execute(delete(Vote).where(Vote.condition_id.in_((CID, UNKNOWN))))
    await session.execute(delete(Market).where(Market.condition_id == CID))
    await session.execute(delete(User).where(User.address.in_(USERS)))


@pytest.fixture
async def oclient(client):
    async with SessionLocal() as session:
        await _clean(session)
        session.add_all(
            [
                Market(condition_id=CID, question="Oracle vote test?", market_type=0, close_time=1_700_000_000),
                User(address=OP, is_operator=True),
                User(address=HOLDER, is_operator=False),
                User(address=OTHER, is_operator=False),
            ]
        )
        await session.commit()
    yield client
    async with SessionLocal() as session:
        await _clean(session)
        await session.commit()


def _h(address: str, op: bool = False) -> dict[str, str]:
    return {"Authorization": f"Bearer {_issue(address, op)}"}


# --- attest ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attest_requires_operator(oclient):
    assert (await oclient.post("/api/v1/oracle/attest", json=ATTEST)).status_code == 401
    assert (await oclient.post("/api/v1/oracle/attest", json=ATTEST, headers=_h(HOLDER))).status_code == 403
    r = await oclient.post("/api/v1/oracle/attest", json=ATTEST, headers=_h(OP, True))
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_attest_rejects_out_of_range_outcome(oclient):
    r = await oclient.post("/api/v1/oracle/attest", json={**ATTEST, "outcome": 2**31}, headers=_h(OP, True))
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_status_reports_created_at_iso_utc_and_null_for_legacy(oclient):
    from sqlalchemy import update

    async with SessionLocal() as session:
        session.add(Attestation(condition_id=CID, agent="0xlegacy", outcome=0, evidence_hash="0x00"))
        await session.flush()
        # A row written before the column existed (the ALTER leaves it NULL).
        await session.execute(update(Attestation).where(Attestation.agent == "0xlegacy").values(created_at=None))
        await session.commit()
    assert (await oclient.post("/api/v1/oracle/attest", json=ATTEST, headers=_h(OP, True))).status_code == 200
    body = (await oclient.get(f"/api/v1/oracle/{CID}/status")).json()
    legacy, fresh = body["attestations"]
    assert set(fresh) == {"agent", "outcome", "summary", "evidenceHash", "createdAt", "kind"}
    assert legacy["createdAt"] is None
    assert legacy["kind"] == fresh["kind"] == "resolution"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", fresh["createdAt"])
    assert fresh["summary"] == "final 31-10"


# What oracles/resolve/publish.py persist_research sends as evidenceJson for a non-resolving attempt.
RESEARCH_MARKER = json.dumps([{"kind": "research", "reason": "research split"}])


async def _attest(oclient, agent: str, outcome: int, evidence_json: str | None = None) -> None:
    body = {**ATTEST, "agent": agent, "outcome": outcome}
    if evidence_json is not None:
        body["evidenceJson"] = evidence_json
    assert (await oclient.post("/api/v1/oracle/attest", json=body, headers=_h(OP, True))).status_code == 200


@pytest.mark.asyncio
async def test_status_marks_research_records_and_excludes_them_from_unanimous(oclient):
    agents = ["0x" + c * 40 for c in "abc"]
    # A research round that did not resolve (split, one undetermined), accepted with outcome 2.
    for agent, outcome in zip(agents, (0, 2, 1)):
        await _attest(oclient, agent, outcome, RESEARCH_MARKER)
    body = (await oclient.get(f"/api/v1/oracle/{CID}/status")).json()
    assert [a["kind"] for a in body["attestations"]] == ["research"] * 3
    assert all(a["createdAt"] for a in body["attestations"])  # the oracle cooldown reads these
    assert body["unanimous"] is False

    # The later resolving round agrees 3/3: unanimous despite the research rows' other outcomes.
    for agent in agents:
        await _attest(oclient, agent, 0, json.dumps([{"url": "https://example.com/box", "note": "final"}]))
    body = (await oclient.get(f"/api/v1/oracle/{CID}/status")).json()
    assert [a["kind"] for a in body["attestations"]] == ["research"] * 3 + ["resolution"] * 3
    assert body["unanimous"] is True


@pytest.mark.asyncio
async def test_research_rows_alone_never_make_unanimous(oclient):
    for c in "def":
        await _attest(oclient, "0x" + c * 40, 0, RESEARCH_MARKER)
    body = (await oclient.get(f"/api/v1/oracle/{CID}/status")).json()
    assert body["unanimous"] is False


@pytest.mark.parametrize(
    "evidence,kind",
    [
        ('[{"kind": "research", "reason": "x"}]', "research"),
        ('{"kind": "research"}', "research"),
        ("[]", "resolution"),
        ('[{"kind": "score"}]', "resolution"),
        ("not json", "resolution"),
        ("", "resolution"),
        (None, "resolution"),
    ],
)
def test_attestation_kind(evidence, kind):
    from app.oracle.router import attestation_kind

    assert attestation_kind(evidence) == kind


# --- vote -----------------------------------------------------------------------------


def _weight(value=None, error=None):
    def read(settings, cid, holder):
        if error is not None:
            raise error
        return value

    return patch("app.oracle.router.read_position_weight", side_effect=read)


@pytest.mark.asyncio
async def test_vote_requires_auth(oclient):
    r = await oclient.post("/api/v1/oracle/vote", json={"conditionId": CID, "outcome": 0})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_vote_uses_caller_and_chain_weight_not_body(oclient):
    big = 5_000 * 10**6  # past int4: votes.weight is BIGINT
    with _weight(big) as read:
        r = await oclient.post(
            "/api/v1/oracle/vote",
            json={"conditionId": CID.upper().replace("0X", "0x"), "outcome": 1, "voter": OTHER, "weight": 10**30},
            headers=_h(HOLDER),
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "voter": HOLDER, "weight": big}
    assert read.call_args.args[1:] == (CID, HOLDER)
    async with SessionLocal() as session:
        (vote,) = (await session.execute(select(Vote).where(Vote.condition_id == CID))).scalars().all()
        assert (vote.voter, vote.outcome, vote.weight) == (HOLDER, 1, big)
    status = (await oclient.get(f"/api/v1/oracle/{CID}/status")).json()
    assert status["votes"] == [{"voter": HOLDER, "outcome": 1, "weight": big}]


@pytest.mark.asyncio
async def test_vote_once_per_holder(oclient):
    with _weight(7):
        first = await oclient.post("/api/v1/oracle/vote", json={"conditionId": CID, "outcome": 0}, headers=_h(HOLDER))
        second = await oclient.post("/api/v1/oracle/vote", json={"conditionId": CID, "outcome": 1}, headers=_h(HOLDER))
    assert first.status_code == 200
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_vote_fails_closed_when_chain_unavailable(oclient):
    with _weight(error=ConnectionError("https://rpc.example/v2/SECRETKEY")):
        r = await oclient.post("/api/v1/oracle/vote", json={"conditionId": CID, "outcome": 0}, headers=_h(HOLDER))
    assert r.status_code == 503
    assert "SECRETKEY" not in r.text
    async with SessionLocal() as session:
        assert (await session.execute(select(Vote).where(Vote.condition_id == CID))).first() is None


@pytest.mark.asyncio
async def test_vote_without_position_is_403(oclient):
    with _weight(0):
        r = await oclient.post("/api/v1/oracle/vote", json={"conditionId": CID, "outcome": 0}, headers=_h(OTHER))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_vote_real_reader_without_ctf_address_is_503(oclient):
    # Test env pins no contract addresses: the real reader raises and the route fails closed.
    r = await oclient.post("/api/v1/oracle/vote", json={"conditionId": CID, "outcome": 0}, headers=_h(HOLDER))
    assert r.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,status",
    [
        ({"conditionId": UNKNOWN, "outcome": 0}, 404),
        ({"conditionId": "0x1234", "outcome": 0}, 400),
        ({"conditionId": CID, "outcome": 2}, 422),
    ],
)
async def test_vote_validation(oclient, body, status):
    with _weight(7):
        r = await oclient.post("/api/v1/oracle/vote", json=body, headers=_h(HOLDER))
    assert r.status_code == status


def test_read_position_weight_sums_yes_and_no():
    from types import SimpleNamespace

    from app.config import Settings
    from app.oracle.router import read_position_weight

    balances = {(HOLDER.lower(), 100): 3, (HOLDER.lower(), 101): 4}

    class _Fn:
        def __init__(self, value):
            self.value = value

        def call(self):
            return self.value

    class _Functions:
        def positionId(self, cid, outcome):
            return _Fn(100 + outcome)

        def balanceOf(self, account, position):
            return _Fn(balances[(account.lower(), position)])

    class _W3:
        HTTPProvider = staticmethod(lambda url, **kw: url)
        to_checksum_address = staticmethod(lambda a: a)

        def __init__(self, provider):
            self.eth = SimpleNamespace(contract=lambda address, abi: SimpleNamespace(functions=_Functions()))

    with patch("web3.Web3", _W3), patch(
        "app.contract_addresses.get_contract_addresses", return_value={"ConditionalTokens": "0x" + "01" * 20}
    ):
        assert read_position_weight(Settings(), CID, HOLDER) == 7
