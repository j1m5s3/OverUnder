"""Step 12: operator POST /api/v1/markets is idempotent against the factory."""

from unittest.mock import MagicMock, patch

import eth_abi
import pytest
from eth_account import Account
from sqlalchemy import select
from web3 import Web3

from app.auth.router import _issue
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.markets.chain import FactoryChain
from app.models import Market, User

OP = "0x" + "0c" * 20
NONOP = "0x" + "1c" * 20
ORACLE = "0x" + "0a" * 20
QID = "0x" + "5a" * 32
QID_OTHER = "0x" + "5b" * 32
PARENT = "0x" + "5c" * 32
USERS = (OP, NONOP)


def _cid(qid_hex: str) -> str:
    encoded = eth_abi.encode(["address", "bytes32"], [Web3.to_checksum_address(ORACLE), bytes.fromhex(qid_hex[2:])])
    return "0x" + Web3.keccak(encoded).hex().removeprefix("0x")


CID = _cid(QID)
CID_OTHER = _cid(QID_OTHER)
SEED_CIDS = (CID, CID_OTHER)


async def _reset(session) -> None:
    for cid in SEED_CIDS:
        row = await session.get(Market, cid)
        if row is not None:
            await session.delete(row)
    for user in (await session.execute(select(User).where(User.address.in_(USERS)))).scalars().all():
        await session.delete(user)


@pytest.fixture
async def op_client(client):
    async with SessionLocal() as session:
        await _reset(session)
        session.add_all([User(address=OP, is_operator=True), User(address=NONOP, is_operator=False)])
        await session.commit()
    yield client
    async with SessionLocal() as session:
        await _reset(session)
        await session.commit()


def _op_settings() -> Settings:
    return Settings(operator_private_key=Account.create().key.hex(), jwt_secret=get_settings().jwt_secret)


def _chain(*, exists: bool, struct=None, outcome_slots: int = 0) -> FactoryChain:
    factory = MagicMock()
    factory.address = Web3.to_checksum_address("0x" + "fa" * 20)
    factory.functions.oracle.return_value.call.return_value = Web3.to_checksum_address(ORACLE)
    factory.functions.marketExists.return_value.call.return_value = exists
    factory.functions.markets.return_value.call.return_value = struct
    ctf = MagicMock()
    ctf.functions.outcomeSlots.return_value.call.return_value = outcome_slots
    usdc = MagicMock()
    w3 = MagicMock()
    return FactoryChain(w3=w3, factory=factory, usdc=usdc, ctf=ctf, operator=Account.create())


def _struct(cid: str, *, close_time: int = 2_000_000_100, market_type: int = 0, paused: bool = False, question="Chiefs vs Broncos: Chiefs win?"):
    return (bytes.fromhex(cid[2:]), b"\x00" * 32, close_time, market_type, paused, question)


def _body(**overrides):
    body = {
        "question": "Chiefs vs Broncos: Chiefs win?",
        "resolution_criteria": "Official final score.",
        "close_time": 2_000_000_100,
        "question_id": QID,
        "seed_usdc": 100_000_000,
        "market_type": 0,
        "suggested_probability": 0.6,
    }
    body.update(overrides)
    return body


def _headers(address: str = OP, is_op: bool = True) -> dict[str, str]:
    return {"Authorization": f"Bearer {_issue(address, is_op)}"}


def _assert_no_tx(chain: FactoryChain) -> None:
    chain.w3.eth.send_raw_transaction.assert_not_called()
    chain.usdc.functions.faucet.assert_not_called()
    chain.usdc.functions.approve.assert_not_called()
    chain.factory.functions.createPrimaryMarket.assert_not_called()
    chain.factory.functions.createWildcardMarket.assert_not_called()


async def _post(client, chain, body):
    with patch("app.markets.router.get_settings", return_value=_op_settings()), patch(
        "app.markets.chain.load_factory_chain", return_value=chain
    ):
        return await client.post("/api/v1/markets", headers=_headers(), json=body)


@pytest.mark.asyncio
async def test_existing_onchain_market_returns_row_without_tx(op_client):
    chain = _chain(exists=True, struct=_struct(CID))
    r = await _post(op_client, chain, _body())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["conditionId"] == CID
    assert data["closeTime"] == 2_000_000_100
    assert data["marketType"] == 0
    assert data["resolutionCriteria"] == "Official final score."
    _assert_no_tx(chain)
    chain.factory.functions.marketExists.assert_called_with(bytes.fromhex(CID[2:]))
    async with SessionLocal() as session:
        row = await session.get(Market, CID)
        assert row is not None
        assert row.question == "Chiefs vs Broncos: Chiefs win?"
        assert row.parent_condition_id == ""
        assert row.paused is False
        assert row.suggested_probability == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_existing_market_updates_stale_row(op_client):
    async with SessionLocal() as session:
        session.add(
            Market(
                condition_id=CID,
                question="stale question",
                resolution_criteria="keep me",
                market_type=0,
                close_time=1,
                paused=False,
            )
        )
        await session.commit()
    chain = _chain(exists=True, struct=_struct(CID, close_time=2_000_000_555, paused=True))
    r = await _post(op_client, chain, _body(resolution_criteria=""))
    assert r.status_code == 200, r.text
    assert r.json()["paused"] is True
    _assert_no_tx(chain)
    async with SessionLocal() as session:
        row = await session.get(Market, CID)
        assert row.close_time == 2_000_000_555
        assert row.paused is True
        assert row.question == "Chiefs vs Broncos: Chiefs win?"
        assert row.resolution_criteria == "keep me"


@pytest.mark.asyncio
async def test_replay_twice_is_stable(op_client):
    chain = _chain(exists=True, struct=_struct(CID))
    first = await _post(op_client, chain, _body())
    second = await _post(op_client, chain, _body())
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    _assert_no_tx(chain)
    async with SessionLocal() as session:
        rows = (await session.execute(select(Market).where(Market.condition_id == CID))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_condition_prepared_outside_factory_409(op_client):
    chain = _chain(exists=False, outcome_slots=2)
    r = await _post(op_client, chain, _body())
    assert r.status_code == 409
    assert r.json()["detail"] == "condition prepared outside this factory"
    _assert_no_tx(chain)
    async with SessionLocal() as session:
        assert await session.get(Market, CID) is None


@pytest.mark.asyncio
async def test_new_market_sends_create_tx(op_client):
    chain = _chain(exists=False, outcome_slots=0)
    chain.usdc.functions.balanceOf.return_value.call.return_value = 10**12
    chain.usdc.functions.allowance.return_value.call.return_value = 10**12
    chain.w3.eth.get_transaction_count.return_value = 7
    chain.w3.eth.gas_price = 1_000_000_000
    chain.w3.eth.wait_for_transaction_receipt.return_value = {"status": 1}
    chain.factory.functions.createPrimaryMarket.return_value.build_transaction.return_value = {
        "to": chain.factory.address,
        "data": "0x",
        "nonce": 7,
        "gas": 1_000_000,
        "gasPrice": 1_000_000_000,
        "chainId": 31337,
        "value": 0,
    }
    chain.factory.events.MarketCreated.return_value.process_receipt.return_value = [
        {
            "args": {
                "conditionId": bytes.fromhex(CID_OTHER[2:]),
                "parentConditionId": b"\x00" * 32,
                "closeTime": 2_000_000_100,
                "marketType": 0,
                "question": "Chiefs vs Broncos: Chiefs win?",
            }
        }
    ]
    r = await _post(op_client, chain, _body(question_id=QID_OTHER))
    assert r.status_code == 200, r.text
    assert r.json()["conditionId"] == CID_OTHER
    chain.factory.functions.createPrimaryMarket.assert_called_once()
    args = chain.factory.functions.createPrimaryMarket.call_args.args
    assert args == (bytes.fromhex(QID_OTHER[2:]), 2_000_000_100, "Chiefs vs Broncos: Chiefs win?", 100_000_000)
    chain.w3.eth.send_raw_transaction.assert_called_once()
    chain.usdc.functions.faucet.assert_not_called()
    async with SessionLocal() as session:
        row = await session.get(Market, CID_OTHER)
        assert row is not None
        assert row.resolution_criteria == "Official final score."


@pytest.mark.asyncio
async def test_rejects_market_type_2(op_client):
    chain = _chain(exists=True, struct=_struct(CID))
    r = await _post(op_client, chain, _body(market_type=2))
    assert r.status_code == 400
    assert "listing" in r.json()["detail"]
    chain.factory.functions.marketExists.assert_not_called()


@pytest.mark.asyncio
async def test_bad_question_id_400(op_client):
    chain = _chain(exists=True, struct=_struct(CID))
    for bad in ("0x1234", "zz" * 32, ""):
        r = await _post(op_client, chain, _body(question_id=bad))
        assert r.status_code == 400, bad
        assert r.json()["detail"] == "question_id must be 32-byte hex"
    r = await _post(op_client, chain, _body(market_type=1, parent_condition_id=""))
    assert r.status_code == 400
    assert r.json()["detail"] == "parent_condition_id must be 32-byte hex"


@pytest.mark.asyncio
async def test_wildcard_replay_mirrors_parent(op_client):
    struct = (bytes.fromhex(CID[2:]), bytes.fromhex(PARENT[2:]), 2_000_000_100, 1, False, "Over 45.5 points?")
    chain = _chain(exists=True, struct=struct)
    r = await _post(op_client, chain, _body(market_type=1, parent_condition_id=PARENT, question="Over 45.5 points?"))
    assert r.status_code == 200, r.text
    assert r.json()["parentConditionId"] == PARENT
    assert r.json()["marketType"] == 1
    _assert_no_tx(chain)


@pytest.mark.asyncio
async def test_create_requires_operator(op_client):
    r = await op_client.post("/api/v1/markets", json=_body())
    assert r.status_code == 401
    r = await op_client.post("/api/v1/markets", headers=_headers(NONOP, False), json=_body())
    assert r.status_code == 403


# --- chain work off the event loop, bounded waits, serialized operator txs -------------------


def _new_market_chain():
    chain = _chain(exists=False, outcome_slots=0)
    chain.usdc.functions.balanceOf.return_value.call.return_value = 10**12
    chain.usdc.functions.allowance.return_value.call.return_value = 10**12
    chain.w3.eth.get_transaction_count.return_value = 7
    chain.w3.eth.gas_price = 1_000_000_000
    chain.w3.eth.wait_for_transaction_receipt.return_value = {"status": 1}
    chain.factory.functions.createPrimaryMarket.return_value.build_transaction.return_value = {
        "to": chain.factory.address,
        "data": "0x",
        "nonce": 7,
        "gas": 1_000_000,
        "gasPrice": 1_000_000_000,
        "chainId": 31337,
        "value": 0,
    }
    chain.factory.events.MarketCreated.return_value.process_receipt.return_value = [
        {
            "args": {
                "conditionId": bytes.fromhex(CID_OTHER[2:]),
                "parentConditionId": b"\x00" * 32,
                "closeTime": 2_000_000_100,
                "marketType": 0,
                "question": "Chiefs vs Broncos: Chiefs win?",
            }
        }
    ]
    return chain


@pytest.mark.asyncio
async def test_create_waits_with_bounded_timeout_and_pending_nonce(op_client):
    chain = _new_market_chain()
    r = await _post(op_client, chain, _body(question_id=QID_OTHER))
    assert r.status_code == 200, r.text
    chain.w3.eth.get_transaction_count.assert_called_with(chain.operator.address, "pending")
    _, kwargs = chain.w3.eth.wait_for_transaction_receipt.call_args
    assert kwargs["timeout"] == Settings().operator_tx_timeout_seconds == 60.0


@pytest.mark.asyncio
async def test_create_tx_failure_does_not_echo_transport_error(op_client):
    chain = _new_market_chain()
    chain.w3.eth.send_raw_transaction.side_effect = ConnectionError("https://rpc.example/v2/SECRETKEY")
    r = await _post(op_client, chain, _body(question_id=QID_OTHER))
    assert r.status_code == 500
    assert "SECRETKEY" not in r.text


@pytest.mark.asyncio
async def test_slow_create_does_not_block_the_event_loop(op_client):
    """Receipt waits run in a worker thread: /health answers while a create is stuck on chain."""
    import asyncio
    import time as _time

    chain = _new_market_chain()

    def slow_receipt(*_a, **_k):
        _time.sleep(0.6)
        return {"status": 1}

    chain.w3.eth.wait_for_transaction_receipt.side_effect = slow_receipt
    with patch("app.markets.router.get_settings", return_value=_op_settings()), patch(
        "app.markets.chain.load_factory_chain", return_value=chain
    ):
        started = _time.monotonic()
        create = asyncio.create_task(op_client.post("/api/v1/markets", headers=_headers(), json=_body(question_id=QID_OTHER)))
        await asyncio.sleep(0.1)
        health = await op_client.get("/health")
        health_done = _time.monotonic() - started
        created = await create
    assert health.status_code == 200
    assert created.status_code == 200, created.text
    assert health_done < 0.5


def test_operator_txs_share_one_lock():
    from app.markets import router as markets_router

    assert markets_router._OPERATOR_TX_LOCK.acquire(blocking=False)
    markets_router._OPERATOR_TX_LOCK.release()


# --- request ints that map to Postgres int4 columns -------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("close_time", 2**31), ("close_time", -1), ("suggested_probability", 1.5)])
async def test_create_rejects_out_of_range_fields(op_client, field, value):
    chain = _chain(exists=True, struct=_struct(CID))
    r = await _post(op_client, chain, _body(**{field: value}))
    assert r.status_code == 422
    chain.factory.functions.marketExists.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "game",
    [
        {"away": "A", "home": "B", "kickoff_unix": 2**31, "week": 1, "season": 2026},
        {"away": "A", "home": "B", "kickoff_unix": 1, "week": 2**31, "season": 2026},
        {"away": "A", "home": "B", "kickoff_unix": 1, "week": 1, "season": -1},
    ],
)
async def test_schedule_rejects_int4_overflow(op_client, game):
    r = await op_client.post("/api/v1/markets/schedule", headers=_headers(), json=[game])
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_score_rejects_int4_overflow(op_client):
    body = {"homeLabel": "KC", "awayLabel": "DEN", "homeScore": 2**31, "awayScore": 0, "status": "in_progress"}
    r = await op_client.post(f"/api/v1/markets/{CID}/score", headers=_headers(), json=body)
    assert r.status_code == 422


# --- operator archive of orphaned rows (older deployments) ------------------------------------


async def _seed_row(cid: str = CID, paused: bool = False) -> None:
    async with SessionLocal() as session:
        session.add(Market(condition_id=cid, question="Orphan?", market_type=0, close_time=1, paused=paused))
        await session.commit()


def _oracle_close(value=None, error=None):
    def read(settings, cid_bytes):
        if error is not None:
            raise error
        return value

    return patch("app.markets.router._oracle_close_time", side_effect=read)


@pytest.mark.asyncio
async def test_archive_hides_unregistered_row_idempotently(op_client):
    await _seed_row()
    with _oracle_close(0):
        first = await op_client.post(f"/api/v1/markets/{CID}/archive", headers=_headers())
        second = await op_client.post(f"/api/v1/markets/{CID.upper().replace('0X', '0x')}/archive", headers=_headers())
    assert first.status_code == second.status_code == 200, first.text
    assert first.json()["paused"] is True and second.json() == first.json()
    cards = (await op_client.get("/api/v1/markets")).json()
    assert CID not in {c["primary"]["conditionId"] for c in cards}


@pytest.mark.asyncio
async def test_archive_refuses_registered_market(op_client):
    await _seed_row()
    with _oracle_close(2_000_000_000):
        r = await op_client.post(f"/api/v1/markets/{CID}/archive", headers=_headers())
    assert r.status_code == 409
    async with SessionLocal() as session:
        assert (await session.get(Market, CID)).paused is False


@pytest.mark.asyncio
async def test_archive_unknown_404_chain_down_503_and_operator_only(op_client):
    with _oracle_close(0):
        assert (await op_client.post(f"/api/v1/markets/{CID}/archive", headers=_headers())).status_code == 404
    await _seed_row()
    with _oracle_close(error=ConnectionError("https://rpc.example/v2/SECRETKEY")):
        r = await op_client.post(f"/api/v1/markets/{CID}/archive", headers=_headers())
    assert r.status_code == 503 and "SECRETKEY" not in r.text
    # Real reader with no ConsensusOracle address configured also fails closed.
    assert (await op_client.post(f"/api/v1/markets/{CID}/archive", headers=_headers())).status_code == 503
    assert (await op_client.post(f"/api/v1/markets/{CID}/archive")).status_code == 401
    assert (await op_client.post(f"/api/v1/markets/{CID}/archive", headers=_headers(NONOP, False))).status_code == 403


@pytest.mark.asyncio
async def test_pause_runs_chain_work_off_loop(op_client):
    await _seed_row()
    calls = []
    with patch("app.markets.router.get_settings", return_value=_op_settings()), patch(
        "app.markets.router._pause_on_chain", side_effect=lambda settings, cid: calls.append(cid)
    ):
        r = await op_client.post(f"/api/v1/markets/{CID}/pause", headers=_headers())
    assert r.status_code == 200, r.text
    assert r.json()["paused"] is True
    assert calls == [bytes.fromhex(CID[2:])]

@pytest.mark.asyncio
@pytest.mark.parametrize("seed", [0, 1, 9_999])
async def test_seed_below_amm_min_lp_422_without_chain_work(op_client, seed):
    chain = _chain(exists=False)
    with patch("app.markets.router.get_settings", return_value=_op_settings()), patch(
        "app.markets.chain.load_factory_chain", return_value=chain
    ) as load:
        r = await op_client.post("/api/v1/markets", headers=_headers(), json=_body(seed_usdc=seed))
    assert r.status_code == 422, r.text
    assert "MIN_LP" in r.json()["detail"]
    load.assert_not_called()
    _assert_no_tx(chain)


@pytest.mark.asyncio
async def test_seed_at_amm_min_lp_is_accepted(op_client):
    chain = _chain(exists=True, struct=_struct(CID))
    r = await _post(op_client, chain, _body(seed_usdc=10_000))
    assert r.status_code == 200, r.text
