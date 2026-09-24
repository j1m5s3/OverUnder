"""OU-T010 user listing endpoints: /api/v1/markets/listing/{config,eligibility,prepare,confirm}."""

import time
from unittest.mock import MagicMock, patch

import eth_abi
import pytest
from sqlalchemy import delete, select
from web3 import Web3
from web3.exceptions import ContractLogicError

from app.auth.router import _issue
from app.db import SessionLocal
from app.markets import listing_gates as gates
from app.markets.chain import FactoryReader, condition_id_for, user_question_id
from app.markets.listing import build_listing_calls, criteria_hash
from app.models import Market, MarketListing, User

LISTER = "0x" + "4a" * 20
OTHER = "0x" + "4b" * 20
OPERATOR = "0x" + "4e" * 20
FACTORY = Web3.to_checksum_address("0x" + "fa" * 20)
USDC = Web3.to_checksum_address("0x" + "dd" * 20)
ORACLE = Web3.to_checksum_address("0x" + "0a" * 20)
VAULT = Web3.to_checksum_address("0x" + "fe" * 20)
MIN_SEED = 10_000_000
FEE = 1_000_000
DUP_CID = "0x" + "4d" * 32
USERS = (LISTER, OTHER, OPERATOR)

QUESTION = "Will the Artemis II crew launch before 2027-01-01?"
CRITERIA = "Resolves YES if NASA confirms an Artemis II crewed launch before 2027-01-01 00:00 UTC."


def _call(value=None, side_effect=None):
    fn = MagicMock()
    if side_effect is not None:
        fn.return_value.call.side_effect = side_effect
    else:
        fn.return_value.call.return_value = value
    return fn


def _factory(*, permissionless=True, legacy=False, rpc_down=False, lister_allowed=False, last_listed=0, cooldown=3600, markets=None):
    """MagicMock MarketFactory v2. `markets` maps cid hex -> dict(struct, creator, criteria_hash, seed)."""
    markets = markets if markets is not None else {}
    f = MagicMock()
    f.address = FACTORY
    fns = f.functions
    if legacy or rpc_down:
        fns.permissionless = _call(side_effect=ContractLogicError("execution reverted"))
    else:
        fns.permissionless = _call(permissionless)
    fns.oracle = _call(side_effect=ConnectionError("rpc down")) if rpc_down else _call(ORACLE)
    fns.usdc = _call(USDC)
    fns.feeRecipient = _call(VAULT)
    fns.minSeedUsdc = _call(MIN_SEED)
    fns.listingFeeUsdc = _call(FEE)
    fns.minLeadTime = _call(3600)
    fns.maxHorizon = _call(7_776_000)
    fns.listingCooldown = _call(cooldown)
    fns.listerAllowed = _call(lister_allowed)
    fns.lastListedAt = _call(last_listed)

    def user_cid(account, salt):
        return MagicMock(call=MagicMock(return_value=bytes.fromhex(condition_id_for(ORACLE, user_question_id(account, salt))[2:])))

    fns.userConditionId.side_effect = user_cid

    def _lookup(key):
        def fn(cid):
            entry = markets.get("0x" + bytes(cid).hex())
            return MagicMock(call=MagicMock(return_value=None if entry is None else entry[key]))

        return fn

    fns.marketExists.side_effect = lambda cid: MagicMock(call=MagicMock(return_value="0x" + bytes(cid).hex() in markets))
    fns.markets.side_effect = _lookup("struct")
    fns.creatorOf.side_effect = _lookup("creator")
    fns.criteriaHashOf.side_effect = _lookup("criteria_hash")
    fns.seedOf.side_effect = _lookup("seed")
    return f


# One closeTime for /prepare bodies and the fake chain, so a second boundary between them
# never makes confirm see "closeTime differs".
CLOSE_TIME = int(time.time()) + 86_400


def _onchain(cid: str, *, creator=LISTER, criteria=CRITERIA, market_type=2, close_time=None, paused=False, question=QUESTION, seed=MIN_SEED):
    close_time = close_time or CLOSE_TIME
    return {
        "struct": (bytes.fromhex(cid[2:]), b"\x00" * 32, close_time, market_type, paused, question),
        "creator": Web3.to_checksum_address(creator),
        "criteria_hash": bytes.fromhex(criteria_hash(criteria)[2:]),
        "seed": seed,
    }


def _patch(factory):
    return patch("app.markets.listing.load_listing_chain", return_value=FactoryReader(w3=MagicMock(), factory=factory))


def _headers(address=LISTER):
    return {"Authorization": f"Bearer {_issue(address, False)}"}


async def _clean(session):
    cids = [c for (c,) in (await session.execute(select(MarketListing.condition_id).where(MarketListing.creator.in_(USERS)))).all()]
    cids.append(DUP_CID)
    await session.execute(delete(Market).where(Market.condition_id.in_(cids)))
    await session.execute(delete(MarketListing).where(MarketListing.condition_id.in_(cids)))
    await session.execute(delete(User).where(User.address.in_(USERS)))


@pytest.fixture
async def lclient(client):
    async with SessionLocal() as session:
        await _clean(session)
        session.add_all(
            [
                User(address=LISTER, is_operator=False),
                User(address=OTHER, is_operator=False),
                User(address=OPERATOR, is_operator=True),
            ]
        )
        await session.commit()
    yield client
    async with SessionLocal() as session:
        await _clean(session)
        await session.commit()


def _prepare_body(**overrides):
    body = {
        "question": QUESTION,
        "resolutionCriteria": CRITERIA,
        "closeTime": CLOSE_TIME,
        "seedUsdc": 25_000_000,
    }
    body.update(overrides)
    return body


# --- config / eligibility ---------------------------------------------------------


@pytest.mark.asyncio
async def test_config_disabled_on_legacy_factory(lclient):
    with _patch(_factory(legacy=True)):
        r = await lclient.get("/api/v1/markets/listing/config")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["reason"] == "factory has no listing support"
    assert body["chainId"] == 999999


@pytest.mark.asyncio
async def test_config_disabled_when_chain_down(lclient):
    with _patch(_factory(rpc_down=True)):
        r = await lclient.get("/api/v1/markets/listing/config")
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert r.json()["reason"] == "chain unavailable"


@pytest.mark.asyncio
async def test_config_disabled_without_factory_address(lclient):
    # Test env pins CHAIN_ID=999999 with no addresses: the real loader reports 503, config degrades.
    r = await lclient.get("/api/v1/markets/listing/config")
    assert r.status_code == 200
    assert r.json()["enabled"] is False


@pytest.mark.asyncio
async def test_config_values_from_chain(lclient):
    with _patch(_factory(permissionless=False)):
        r = await lclient.get("/api/v1/markets/listing/config")
    assert r.status_code == 200
    assert r.json() == {
        "enabled": True,
        "permissionless": False,
        "factory": FACTORY,
        "usdc": USDC,
        "oracle": ORACLE,
        "chainId": 999999,
        "minSeedUsdc": MIN_SEED,
        "listingFeeUsdc": FEE,
        "minLeadSeconds": 3600,
        "maxHorizonSeconds": 7_776_000,
        "cooldownSeconds": 3600,
        "reason": None,
    }


@pytest.mark.asyncio
async def test_listing_routes_not_captured_by_market_detail(lclient):
    r = await lclient.get("/api/v1/markets/listing/eligibility")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_eligibility_invite_only_and_cooldown(lclient):
    with _patch(_factory(permissionless=False, lister_allowed=False)):
        r = await lclient.get("/api/v1/markets/listing/eligibility", headers=_headers())
    assert r.status_code == 200
    assert r.json()["allowed"] is False
    assert r.json()["reason"] == "listing is invite-only"

    with _patch(_factory(permissionless=False, lister_allowed=True, last_listed=int(time.time()) - 100)):
        r = await lclient.get("/api/v1/markets/listing/eligibility", headers=_headers())
    body = r.json()
    assert body["allowed"] is True
    assert 3400 <= body["cooldownRemaining"] <= 3500
    assert body["pending"] == 0
    assert body["maxPending"] == gates.MAX_PENDING_PER_DAY


# --- prepare ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_returns_decodable_calls_and_stores_row(lclient):
    body = _prepare_body()
    with _patch(_factory()):
        r = await lclient.post("/api/v1/markets/listing/prepare", headers=_headers(), json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    salt = bytes.fromhex(out["salt"][2:])
    qid = Web3.keccak(eth_abi.encode(["address", "bytes32"], [Web3.to_checksum_address(LISTER), salt]))
    expected_cid = Web3.to_hex(Web3.keccak(eth_abi.encode(["address", "bytes32"], [ORACLE, bytes(qid)])))
    assert out["conditionId"] == expected_cid
    assert out["questionId"] == Web3.to_hex(qid)
    assert out["criteriaHash"] == Web3.to_hex(Web3.keccak(text=CRITERIA))
    assert out["approveAmount"] == 25_000_000 + FEE

    approve, create = out["calls"]
    assert approve["to"] == USDC and approve["value"] == 0
    assert approve["data"][:10] == "0x095ea7b3"
    spender, amount = eth_abi.decode(["address", "uint256"], bytes.fromhex(approve["data"][10:]))
    assert Web3.to_checksum_address(spender) == FACTORY
    assert amount == 25_000_000 + FEE

    assert create["to"] == FACTORY and create["value"] == 0
    assert create["data"][:10] == "0x4e7d1a32"
    d_salt, d_close, d_question, d_hash, d_seed = eth_abi.decode(
        ["bytes32", "uint256", "string", "bytes32", "uint256"], bytes.fromhex(create["data"][10:])
    )
    assert (d_salt, d_close, d_question, "0x" + d_hash.hex(), d_seed) == (
        salt,
        body["closeTime"],
        QUESTION,
        out["criteriaHash"],
        25_000_000,
    )

    async with SessionLocal() as session:
        row = await session.get(MarketListing, expected_cid)
        assert row.status == "prepared"
        assert row.creator == LISTER
        assert row.resolution_criteria == CRITERIA
        assert row.seed_usdc == 25_000_000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"question": "Too short"}, "at least 10 bytes"),
        ({"question": "Will " + "é" * 125 + " happen?"}, "at most 256 bytes"),
        ({"question": "Will it rain in Boston tomorrow"}, "ending in '?'"),
        ({"question": "Is this the best movie of 2026?"}, "subjective"),
        ({"resolutionCriteria": "too short"}, "at least 20 characters"),
        ({"closeTime": int(time.time()) + 60}, "closeTime must be at least"),
        ({"closeTime": int(time.time()) + 7_776_000 + 3600}, "closeTime must be within"),
        ({"seedUsdc": MIN_SEED - 1}, "seedUsdc must be at least"),
    ],
)
async def test_prepare_gates_400(lclient, overrides, fragment):
    with _patch(_factory()):
        r = await lclient.post("/api/v1/markets/listing/prepare", headers=_headers(), json=_prepare_body(**overrides))
    assert r.status_code == 400, r.text
    assert fragment in r.json()["detail"]


def test_multibyte_question_counts_bytes_not_chars():
    q = "Will " + "é" * 125 + " happen?"
    assert len(q) < 256 < gates.question_bytes(q)


@pytest.mark.asyncio
async def test_prepare_invite_only_403_and_cooldown_429(lclient):
    with _patch(_factory(permissionless=False, lister_allowed=False)):
        r = await lclient.post("/api/v1/markets/listing/prepare", headers=_headers(), json=_prepare_body())
    assert r.status_code == 403
    with _patch(_factory(last_listed=int(time.time()) - 10)):
        r = await lclient.post("/api/v1/markets/listing/prepare", headers=_headers(), json=_prepare_body())
    assert r.status_code == 429
    assert "cooldown" in r.json()["detail"]


@pytest.mark.asyncio
async def test_prepare_legacy_factory_409(lclient):
    with _patch(_factory(legacy=True)):
        r = await lclient.post("/api/v1/markets/listing/prepare", headers=_headers(), json=_prepare_body())
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_prepare_rate_limit_429(lclient):
    with _patch(_factory()):
        for _ in range(gates.MAX_PENDING_PER_DAY):
            r = await lclient.post("/api/v1/markets/listing/prepare", headers=_headers(), json=_prepare_body())
            assert r.status_code == 200, r.text
        r = await lclient.post("/api/v1/markets/listing/prepare", headers=_headers(), json=_prepare_body())
    assert r.status_code == 429
    with _patch(_factory()):
        r = await lclient.get("/api/v1/markets/listing/eligibility", headers=_headers())
    assert r.json()["pending"] == gates.MAX_PENDING_PER_DAY


@pytest.mark.asyncio
async def test_prepare_rejects_duplicate_open_market(lclient):
    async with SessionLocal() as session:
        session.add(
            Market(
                condition_id=DUP_CID,
                question="Will the Artemis II crew launch before 2027-01-01?",
                market_type=0,
                close_time=int(time.time()) + 3600,
            )
        )
        await session.commit()
    with _patch(_factory()):
        r = await lclient.post(
            "/api/v1/markets/listing/prepare",
            headers=_headers(),
            json=_prepare_body(question="will the artemis ii crew launch before 2027 01 01?"),
        )
    assert r.status_code == 409
    assert "similar" in r.json()["detail"]


@pytest.mark.asyncio
async def test_prepare_cid_mismatch_500(lclient):
    factory = _factory()
    factory.functions.userConditionId.side_effect = lambda account, salt: MagicMock(call=MagicMock(return_value=b"\x01" * 32))
    with _patch(factory):
        r = await lclient.post("/api/v1/markets/listing/prepare", headers=_headers(), json=_prepare_body())
    assert r.status_code == 500


def test_build_listing_calls_is_pure():
    salt = b"\x07" * 32
    calls = build_listing_calls(FACTORY.lower(), USDC.lower(), salt, 2_000_000_000, QUESTION, criteria_hash(CRITERIA), 12, 3)
    assert [c["to"] for c in calls] == [USDC, FACTORY]
    assert calls[0]["data"] == "0x095ea7b3" + eth_abi.encode(["address", "uint256"], [FACTORY, 15]).hex()
    assert criteria_hash("  " + CRITERIA + "\n") == criteria_hash(CRITERIA)


# --- confirm ------------------------------------------------------------------------


async def _prepared_cid(lclient, factory) -> str:
    with _patch(factory):
        r = await lclient.post("/api/v1/markets/listing/prepare", headers=_headers(), json=_prepare_body())
    assert r.status_code == 200, r.text
    return r.json()["conditionId"]


@pytest.mark.asyncio
async def test_confirm_before_chain_409(lclient):
    cid = await _prepared_cid(lclient, _factory())
    with _patch(_factory()):
        r = await lclient.post("/api/v1/markets/listing/confirm", headers=_headers(), json={"conditionId": cid})
    assert r.status_code == 409
    assert r.json()["detail"] == "not on chain yet"


@pytest.mark.asyncio
async def test_confirm_wrong_creator_or_type_403(lclient):
    cid = await _prepared_cid(lclient, _factory())
    with _patch(_factory(markets={cid: _onchain(cid, creator=OTHER)})):
        r = await lclient.post("/api/v1/markets/listing/confirm", headers=_headers(), json={"conditionId": cid})
    assert r.status_code == 403
    with _patch(_factory(markets={cid: _onchain(cid, market_type=0)})):
        r = await lclient.post("/api/v1/markets/listing/confirm", headers=_headers(), json={"conditionId": cid})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_confirm_criteria_mismatch_409(lclient):
    cid = await _prepared_cid(lclient, _factory())
    with _patch(_factory(markets={cid: _onchain(cid, criteria="Some other criteria text that differs.")})):
        r = await lclient.post("/api/v1/markets/listing/confirm", headers=_headers(), json={"conditionId": cid})
    assert r.status_code == 409
    assert r.json()["detail"] == "criteria hash mismatch"


@pytest.mark.asyncio
async def test_confirm_upserts_type2_and_is_idempotent(lclient):
    cid = await _prepared_cid(lclient, _factory())
    factory = _factory(markets={cid: _onchain(cid, seed=25_000_000)})
    with _patch(factory):
        first = await lclient.post("/api/v1/markets/listing/confirm", headers=_headers(), json={"conditionId": cid})
        second = await lclient.post("/api/v1/markets/listing/confirm", headers=_headers(), json={"conditionId": cid.upper().replace("0X", "0x")})
    assert first.status_code == second.status_code == 200, first.text
    assert first.json() == second.json()
    body = first.json()
    assert body["conditionId"] == cid
    assert body["marketType"] == 2
    assert body["resolutionCriteria"] == CRITERIA
    assert body["tradingOpen"] is True

    async with SessionLocal() as session:
        listing = await session.get(MarketListing, cid)
        assert listing.status == "confirmed"
        assert listing.seed_usdc == 25_000_000
        rows = (await session.execute(select(Market).where(Market.condition_id == cid))).scalars().all()
        assert len(rows) == 1

    detail = (await lclient.get(f"/api/v1/markets/{cid}")).json()
    assert detail["creator"] == LISTER
    assert detail["listing"]["status"] == "confirmed"
    assert detail["listing"]["criteriaHash"] == criteria_hash(CRITERIA)
    cards = {c["primary"]["conditionId"]: c for c in (await lclient.get("/api/v1/markets")).json()}
    assert cards[cid]["primary"]["marketType"] == 2


@pytest.mark.asyncio
async def test_confirm_without_prepare_uses_body_criteria(lclient):
    salt = b"\x09" * 32
    cid = condition_id_for(ORACLE, user_question_id(LISTER, salt))
    factory = _factory(markets={cid: _onchain(cid)})
    with _patch(factory):
        missing = await lclient.post("/api/v1/markets/listing/confirm", headers=_headers(), json={"conditionId": cid})
        ok = await lclient.post(
            "/api/v1/markets/listing/confirm",
            headers=_headers(),
            json={"conditionId": cid, "resolutionCriteria": "  " + CRITERIA + "  "},
        )
    assert missing.status_code == 400
    assert ok.status_code == 200, ok.text
    async with SessionLocal() as session:
        listing = await session.get(MarketListing, cid)
        assert listing.salt == ""
        assert listing.creator == LISTER
        assert listing.status == "confirmed"


@pytest.mark.asyncio
async def test_listing_requires_auth(lclient):
    with _patch(_factory()):
        assert (await lclient.post("/api/v1/markets/listing/prepare", json=_prepare_body())).status_code == 401
        assert (await lclient.post("/api/v1/markets/listing/confirm", json={"conditionId": DUP_CID})).status_code == 401



# --- confirm re-runs the gates on the on-chain question (hidden until confirmed) ---------------

BAD_QUESTION = "Who is the best QB and should he feel underrated"


async def _public_ids(lclient) -> set[str]:
    cards = (await lclient.get("/api/v1/markets")).json()
    return {c["primary"]["conditionId"] for c in cards} | {k["conditionId"] for c in cards for k in c["children"]}


@pytest.mark.asyncio
async def test_confirm_rejects_gate_failing_onchain_question_without_prepare(lclient):
    salt = b"\x0b" * 32
    cid = condition_id_for(ORACLE, user_question_id(LISTER, salt))
    with _patch(_factory(markets={cid: _onchain(cid, question=BAD_QUESTION)})):
        r = await lclient.post(
            "/api/v1/markets/listing/confirm",
            headers=_headers(),
            json={"conditionId": cid, "resolutionCriteria": CRITERIA},
        )
    assert r.status_code == 422, r.text
    assert r.json()["detail"].startswith("listing rejected:")
    async with SessionLocal() as session:
        listing = await session.get(MarketListing, cid)
        assert listing.status == "rejected"
        assert listing.reject_reason
        assert (await session.get(Market, cid)).resolution_criteria == ""
    assert cid not in await _public_ids(lclient)
    assert (await lclient.get(f"/api/v1/markets/{cid}")).status_code == 404


@pytest.mark.asyncio
async def test_confirm_rejects_onchain_question_that_differs_from_prepared(lclient):
    cid = await _prepared_cid(lclient, _factory())
    edited = "Will the Artemis II crew launch before 2030-01-01?"  # passes every wording gate
    with _patch(_factory(markets={cid: _onchain(cid, question=edited, seed=25_000_000)})):
        r = await lclient.post("/api/v1/markets/listing/confirm", headers=_headers(), json={"conditionId": cid})
        again = await lclient.post("/api/v1/markets/listing/confirm", headers=_headers(), json={"conditionId": cid})
    assert r.status_code == again.status_code == 422
    assert "differs from the prepared question" in r.json()["detail"]
    async with SessionLocal() as session:
        listing = await session.get(MarketListing, cid)
        assert listing.status == "rejected"
        assert listing.question == QUESTION  # the prepared text is kept for the next comparison
    assert cid not in await _public_ids(lclient)


@pytest.mark.asyncio
async def test_confirm_rejects_seed_or_close_time_that_differs_from_prepared(lclient):
    cid = await _prepared_cid(lclient, _factory())
    with _patch(_factory(markets={cid: _onchain(cid, seed=MIN_SEED)})):
        r = await lclient.post("/api/v1/markets/listing/confirm", headers=_headers(), json={"conditionId": cid})
    assert r.status_code == 422
    assert "seed" in r.json()["detail"]


@pytest.mark.asyncio
async def test_confirm_rejects_duplicate_of_open_market(lclient):
    async with SessionLocal() as session:
        session.add(Market(condition_id=DUP_CID, question=QUESTION, market_type=0, close_time=int(time.time()) + 3600))
        await session.commit()
    salt = b"\x0c" * 32
    cid = condition_id_for(ORACLE, user_question_id(LISTER, salt))
    with _patch(_factory(markets={cid: _onchain(cid)})):
        r = await lclient.post(
            "/api/v1/markets/listing/confirm",
            headers=_headers(),
            json={"conditionId": cid, "resolutionCriteria": CRITERIA},
        )
    assert r.status_code == 422
    assert "similar market" in r.json()["detail"]


@pytest.mark.asyncio
async def test_reconfirm_is_not_a_duplicate_of_itself(lclient):
    cid = await _prepared_cid(lclient, _factory())
    factory = _factory(markets={cid: _onchain(cid, seed=25_000_000)})
    with _patch(factory):
        for _ in range(3):
            r = await lclient.post("/api/v1/markets/listing/confirm", headers=_headers(), json={"conditionId": cid})
            assert r.status_code == 200, r.text
    async with SessionLocal() as session:
        assert (await session.get(MarketListing, cid)).status == "confirmed"
    assert cid in await _public_ids(lclient)


@pytest.mark.asyncio
async def test_confirm_survives_indexer_inserting_the_rows_concurrently(lclient, monkeypatch):
    """The indexer commits Market + MarketListing rows mid-confirm: confirm updates them (no 500)."""
    from app.markets import listing as listing_mod
    from app.markets import visibility

    cid = await _prepared_cid(lclient, _factory())
    real_review = visibility.review_user_listing

    async def review_then_indexer_commits(db, **kwargs):
        result = await real_review(db, **kwargs)
        async with SessionLocal() as other:
            other.add(Market(condition_id=cid, question=QUESTION, market_type=2, close_time=1))
            await other.commit()
        return result

    monkeypatch.setattr(listing_mod.visibility, "review_user_listing", review_then_indexer_commits)
    with _patch(_factory(markets={cid: _onchain(cid, seed=25_000_000)})):
        r = await lclient.post("/api/v1/markets/listing/confirm", headers=_headers(), json={"conditionId": cid})
    assert r.status_code == 200, r.text
    assert r.json()["resolutionCriteria"] == CRITERIA
    async with SessionLocal() as session:
        assert (await session.get(MarketListing, cid)).status == "confirmed"
        market = await session.get(Market, cid)
        assert market.resolution_criteria == CRITERIA
        assert market.close_time != 1  # chain values win over the indexer's stale insert


@pytest.mark.asyncio
async def test_listing_review_lists_hidden_type2_for_operators(lclient):
    salt = b"\x0d" * 32
    cid = condition_id_for(ORACLE, user_question_id(LISTER, salt))
    with _patch(_factory(markets={cid: _onchain(cid, question=BAD_QUESTION)})):
        await lclient.post(
            "/api/v1/markets/listing/confirm",
            headers=_headers(),
            json={"conditionId": cid, "resolutionCriteria": CRITERIA},
        )
    assert (await lclient.get("/api/v1/markets/listing/review", headers=_headers())).status_code == 403
    r = await lclient.get("/api/v1/markets/listing/review", headers=_headers(OPERATOR))
    assert r.status_code == 200
    rows = {row["conditionId"]: row for row in r.json()}
    assert rows[cid]["status"] == "rejected"
    assert rows[cid]["reason"]


# --- seed bound (market_listings.seed_usdc is BIGINT) -------------------------------------------


@pytest.mark.asyncio
async def test_prepare_rejects_seed_above_int64_without_a_row(lclient):
    with _patch(_factory()):
        r = await lclient.post("/api/v1/markets/listing/prepare", headers=_headers(), json=_prepare_body(seedUsdc=2**63))
    assert r.status_code == 422
    async with SessionLocal() as session:
        rows = (await session.execute(select(MarketListing).where(MarketListing.creator == LISTER))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_prepare_accepts_int64_max_seed(lclient):
    with _patch(_factory()):
        r = await lclient.post("/api/v1/markets/listing/prepare", headers=_headers(), json=_prepare_body(seedUsdc=2**63 - 1))
    assert r.status_code == 200, r.text
    async with SessionLocal() as session:
        assert (await session.get(MarketListing, r.json()["conditionId"])).seed_usdc == 2**63 - 1


def test_check_seed_caps_at_int64():
    bounds = gates.ListingBounds(min_seed_usdc=1, min_lead_seconds=0, max_horizon_seconds=10)
    gates.check_seed(gates.MAX_SEED_USDC, bounds)
    with pytest.raises(gates.GateError) as exc:
        gates.check_seed(gates.MAX_SEED_USDC + 1, bounds)
    assert exc.value.status == 400


# --- duplicate gate: meaningful tokens + identical numbers ----------------------------------------


@pytest.mark.parametrize(
    "new,existing,dup",
    [
        ("Will the Bills win on Sunday?", "Will the Chiefs win on Sunday?", False),
        ("Will it rain in Boston on October 5 2026?", "Will it rain in Seattle on October 5 2026?", False),
        ("Will it rain in Boston on October 5 2026?", "Will it rain in Boston on December 5 2026?", False),
        ("Will Bitcoin close above 100000 on 2026-12-31?", "Will Bitcoin close above 120000 on 2026-12-31?", False),
        ("Will the Chiefs beat the Bills?", "Will the Bills beat the Chiefs?", True),
        ("Will the Artemis II crew launch before 2027-01-01?", "will the artemis ii crew launch before 2027 01 01?", True),
        ("Will the Chiefs win on Sunday?", "Will Chiefs win Sunday?", True),
    ],
)
def test_is_duplicate_rule(new, existing, dup):
    assert gates.is_duplicate(new, [existing]) is dup


def test_check_listed_question_uses_exact_onchain_string():
    gates.check_listed_question(QUESTION, [])
    for bad in (" " + QUESTION, QUESTION + " ", BAD_QUESTION):
        with pytest.raises(gates.GateError):
            gates.check_listed_question(bad, [])
    with pytest.raises(gates.GateError) as exc:
        gates.check_listed_question(QUESTION, [QUESTION])
    assert exc.value.status == 409
