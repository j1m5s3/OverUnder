"""RelayerWorker state machine, nonce manager, fee policy and the emissions nonce share."""

import asyncio
import math
import secrets
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from eth_account import Account
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from web3.exceptions import ContractLogicError

import app.auth.router as auth_router
from app.db import (
    Base,
    SessionLocal,
    engine,
    ensure_live_score_facts,
    ensure_order_wide_columns,
    ensure_trade_relay_columns,
    ensure_user_cdp_user_id,
)
from app.main import create_app
from app.models import Order, RelayJob, Trade
from app.orderbook.eip712 import OrderFields, order_hash_hex
from app.relayer import worker as worker_mod
from app.relayer.chain import classify_rpc_error
from app.relayer.gas import FeeQuote, bump_fees, gas_limit, quote_fees
from app.relayer.nonce import NonceManager, get_nonce_manager
from app.relayer.worker import RelayerWorker, maybe_start_relayer
from _relayer_helpers import (
    CHAIN,
    EXCHANGE,
    FakeChain,
    make_settings,
    order_filled_log,
    rand_cid,
    relayer_env,
    sign_order,
    siwe_login,
)


@pytest.fixture(autouse=True)
async def _db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(ensure_live_score_facts)
        await conn.run_sync(ensure_user_cdp_user_id)
        await conn.run_sync(ensure_trade_relay_columns)
        await conn.run_sync(ensure_order_wide_columns)
    # The worker drains every open job in the shared test DB; park leftovers from other tests.
    async with SessionLocal() as db:
        await db.execute(
            update(RelayJob)
            .where(RelayJob.status.in_(("pending", "sending", "sent")))
            .values(status="cancelled")
        )
        await db.commit()
    yield


class Clock:
    def __init__(self, t: int):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _settings(relayer, **kw):
    return make_settings(relayer_enabled=True, relayer_private_key="0x" + bytes(relayer.key).hex(), **kw)


def _signed_order(acct, *, cid, is_buy, price, amount, expiry, nonce=0, outcome=0) -> Order:
    salt = secrets.randbits(64)
    raw = {
        "maker": acct.address, "isBuy": is_buy, "conditionId": bytes.fromhex(cid[2:]), "outcome": outcome,
        "price": price, "amount": amount, "salt": salt, "nonce": nonce, "expiry": expiry,
    }
    of = OrderFields(acct.address, is_buy, raw["conditionId"], outcome, price, amount, salt, nonce, expiry)
    return Order(
        order_hash=order_hash_hex(of, EXCHANGE, CHAIN), maker=acct.address.lower(), condition_id=cid,
        is_buy=is_buy, outcome=outcome, price=price, amount=amount, filled=amount, salt=hex(salt),
        nonce=nonce, expiry=expiry, signature=sign_order(acct.key, EXCHANGE, CHAIN, raw), cancelled=False,
    )


async def seed_match(chain: FakeChain, *, taker_buy=True, amount=10_000_000, price=500_000,
                     taker_expiry=None, fund=True, status="pending") -> SimpleNamespace:
    taker_acct, maker_acct = Account.create(), Account.create()
    cid = rand_cid()
    expiry = chain.now + 3600
    maker = _signed_order(maker_acct, cid=cid, is_buy=not taker_buy, price=price, amount=amount, expiry=expiry)
    taker = _signed_order(
        taker_acct, cid=cid, is_buy=taker_buy, price=price, amount=amount, expiry=taker_expiry or expiry
    )
    async with SessionLocal() as db:
        db.add_all([maker, taker])
        await db.flush()
        job = RelayJob(
            condition_id=cid, taker_hash=taker.order_hash, maker_hash=maker.order_hash, fill_amount=amount,
            status=status, attempts=0, tx_hashes=[], next_attempt_at=0,
        )
        db.add(job)
        await db.flush()
        db.add(Trade(condition_id=cid, taker=taker.maker, maker=maker.maker, fill_amount=amount,
                     volume=amount * price // 1_000_000, fee=0, tx_hash="", status="pending", relay_job_id=job.id))
        await db.commit()
        job_id = job.id
    if fund:
        buyer, seller = (taker_acct, maker_acct) if taker_buy else (maker_acct, taker_acct)
        chain.fund_buyer(buyer.address, 10**12)
        chain.fund_seller(seller.address, cid, 0, 10**12)
    return SimpleNamespace(
        job_id=job_id, cid=cid, taker=taker_acct, maker=maker_acct,
        taker_hash=taker.order_hash, maker_hash=maker.order_hash, amount=amount,
    )


async def _job(job_id: int) -> RelayJob:
    async with SessionLocal() as db:
        return await db.get(RelayJob, job_id)


async def _trade(job_id: int) -> Trade:
    async with SessionLocal() as db:
        return (await db.execute(select(Trade).where(Trade.relay_job_id == job_id))).scalar_one()


async def _filled(*hashes: str) -> list[int]:
    async with SessionLocal() as db:
        rows = {o.order_hash: o.filled for o in (await db.execute(select(Order).where(Order.order_hash.in_(hashes)))).scalars()}
    return [rows[h] for h in hashes]


def _worker(chain, relayer, clock, **kw):
    return RelayerWorker(chain, relayer, _settings(relayer, **kw), now_fn=clock)


# --------------------------------------------------------------------- happy path
async def test_happy_path_sent_then_confirmed():
    chain = FakeChain()
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock)
    m = await seed_match(chain)
    with relayer_env(w.settings, chain):
        stats = await w.tick()
        assert stats["sent"] == 1
        job = await _job(m.job_id)
        assert job.status == "sent"
        assert job.nonce == 7
        assert job.max_fee_per_gas == 2 * chain.base_fee + chain.tip
        assert job.max_priority_fee_per_gas == chain.tip
        assert job.gas_limit == math.ceil(chain.estimate * 1.2)
        assert job.sender == relayer.address.lower()
        tx = chain.sent[0]
        assert tx["hash"] == job.tx_hash and tx["nonce"] == 7 and tx["type"] == 2
        assert tx["maxFeePerGas"] == job.max_fee_per_gas and tx["gas"] == job.gas_limit
        assert "0x" + bytes(tx["to"]).hex() == EXCHANGE
        assert chain.estimate_calls[0]["from"] == relayer.address
        assert chain.pending == 8

        chain.mine(job.tx_hash, logs=[order_filled_log(m.taker_hash, m.maker_hash)], block=123)
        stats = await w.tick()
        assert stats["confirmed"] == 1
    job = await _job(m.job_id)
    assert job.status == "confirmed" and job.block_number == 123 and job.confirmed_at == clock.t
    trade = await _trade(m.job_id)
    assert trade.status == "confirmed" and trade.tx_hash == job.tx_hash and trade.block_number == 123
    assert await _filled(m.taker_hash, m.maker_hash) == [m.amount, m.amount]


async def test_taker_sell_side_preflight_and_send():
    chain = FakeChain()
    relayer = Account.create()
    w = _worker(chain, relayer, Clock(chain.now))
    m = await seed_match(chain, taker_buy=False)
    with relayer_env(w.settings, chain):
        assert (await w.tick())["sent"] == 1


async def test_confirmations_wait():
    chain = FakeChain()
    relayer = Account.create()
    w = _worker(chain, relayer, Clock(chain.now), relayer_confirmations=3)
    m = await seed_match(chain)
    with relayer_env(w.settings, chain):
        await w.tick()
        job = await _job(m.job_id)
        chain.mine(job.tx_hash, block=chain.block_number)
        await w.tick()
        assert (await _job(m.job_id)).status == "sent"
        chain.block_number += 2
        await w.tick()
    assert (await _job(m.job_id)).status == "confirmed"


async def test_success_receipt_without_fill_log_fails():
    chain = FakeChain()
    relayer = Account.create()
    w = _worker(chain, relayer, Clock(chain.now))
    m = await seed_match(chain)
    with relayer_env(w.settings, chain):
        await w.tick()
        job = await _job(m.job_id)
        chain.mine(job.tx_hash, logs=[order_filled_log("0x" + "00" * 32, m.maker_hash)])
        await w.tick()
    job = await _job(m.job_id)
    assert job.status == "failed" and "OrderFilled" in job.last_error


# ------------------------------------------------------------------ nonces
async def test_two_jobs_get_sequential_nonces_under_concurrent_ticks():
    chain = FakeChain()
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock)
    w2 = _worker(chain, relayer, clock)
    a = await seed_match(chain)
    b = await seed_match(chain)
    with relayer_env(w.settings, chain):
        await asyncio.gather(w.tick(), w.tick(), w2.tick())
    # Identical rebroadcasts are "already known" in FakeChain, so each signed tx appears once.
    nonces = [tx["nonce"] for tx in chain.sent]
    assert sorted(nonces) == [7, 8]
    ja, jb = await _job(a.job_id), await _job(b.job_id)
    assert {ja.nonce, jb.nonce} == {7, 8}
    assert ja.status == jb.status == "sent"


async def test_nonce_too_low_resyncs_and_retries():
    chain = FakeChain()
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock)
    m = await seed_match(chain)
    with relayer_env(w.settings, chain):
        # Win leadership first: on Postgres a newly elected leader invalidates the nonce cache
        # (correctly), which would erase the stale value this test plants. SQLite: no-op.
        assert await w._ensure_leader()
        nm = get_nonce_manager(chain, relayer.address)
        nm._next = 3  # stale cache: someone else already used 3..8
        chain.pending = 9
        chain.send_errors = [ValueError("nonce too low: next nonce 9, tx nonce 3")]
        await w.tick()
        job = await _job(m.job_id)
        # Our own tx could be what used nonce 3: stay in flight, never re-sign on one RPC answer.
        assert job.status == "sent" and job.nonce == 3 and job.nonce_consumed_at == clock.t
        assert job.last_error == "nonce consumed; awaiting receipt"
        assert nm.peek() == 9
        await w.tick()
        assert (await _job(m.job_id)).status == "sent" and chain.sent == []
        clock.t += w.settings.relayer_resubmit_after_seconds
        # Lag window over: nonce 3 is mined, no receipt/log of ours, no fill on chain -> resend at 9.
        await w.tick()
    job = await _job(m.job_id)
    assert job.status == "sent" and job.nonce == 9
    assert chain.sent[-1]["nonce"] == 9 and len(chain.sent) == 1


async def test_insufficient_funds_releases_nonce():
    chain = FakeChain()
    relayer = Account.create()
    w = _worker(chain, relayer, Clock(chain.now))
    m = await seed_match(chain)
    with relayer_env(w.settings, chain):
        chain.send_errors = [ValueError("insufficient funds for gas * price + value")]
        await w.tick()
        nm = get_nonce_manager(chain, relayer.address)
        assert nm.peek() == 7
    job = await _job(m.job_id)
    assert job.status == "pending" and job.last_error == "relayer balance low"
    assert job.next_attempt_at > w.now_fn()


async def test_transient_broadcast_error_keeps_write_ahead_and_rebroadcasts_same_bytes():
    chain = FakeChain()
    relayer = Account.create()
    w = _worker(chain, relayer, Clock(chain.now))
    m = await seed_match(chain)
    with relayer_env(w.settings, chain):
        chain.send_errors = [TimeoutError("read timed out")]
        await w.tick()
        job = await _job(m.job_id)
        assert job.status == "sending" and job.nonce == 7 and job.raw_tx
        raw = job.raw_tx
        await w.tick()  # rebroadcast backs off
        assert (await _job(m.job_id)).status == "sending"
        w.now_fn.t += w.settings.relayer_retry_backoff_seconds
        await w.tick()
    job = await _job(m.job_id)
    assert job.status == "sent" and job.nonce == 7
    assert chain.sent[-1]["raw"] == raw
    assert len(chain.sent) == 1


# -------------------------------------------------------------------- fees/gas
async def test_fee_cap_defers_without_sending():
    chain = FakeChain()
    relayer = Account.create()
    w = _worker(chain, relayer, Clock(chain.now), relayer_max_fee_per_gas_wei=chain.base_fee)
    m = await seed_match(chain)
    with relayer_env(w.settings, chain):
        stats = await w.tick()
    job = await _job(m.job_id)
    assert stats["deferred"] == 1
    assert job.status == "pending" and job.last_error == "fee above cap" and job.attempts == 0
    assert chain.sent == []


async def test_low_relayer_balance_defers():
    chain = FakeChain()
    chain.balance = 1
    relayer = Account.create()
    w = _worker(chain, relayer, Clock(chain.now))
    m = await seed_match(chain)
    with relayer_env(w.settings, chain):
        await w.tick()
    job = await _job(m.job_id)
    assert job.status == "pending" and job.last_error == "relayer balance low" and chain.sent == []


async def test_legacy_gas_price_when_no_base_fee():
    chain = FakeChain()
    chain.base_fee = None
    relayer = Account.create()
    w = _worker(chain, relayer, Clock(chain.now))
    m = await seed_match(chain)
    with relayer_env(w.settings, chain):
        await w.tick()
    job = await _job(m.job_id)
    assert job.status == "sent"
    assert job.max_fee_per_gas == chain.legacy_gas_price and job.max_priority_fee_per_gas is None


async def test_estimate_revert_fails_and_rolls_back():
    chain = FakeChain()
    chain.estimate = ContractLogicError("execution reverted: bad sig")
    relayer = Account.create()
    w = _worker(chain, relayer, Clock(chain.now))
    m = await seed_match(chain)
    with relayer_env(w.settings, chain):
        stats = await w.tick()
    job = await _job(m.job_id)
    assert stats["failed"] == 1
    assert job.status == "failed" and "bad sig" in job.last_error
    assert await _filled(m.taker_hash, m.maker_hash) == [0, 0]
    assert (await _trade(m.job_id)).status == "failed"
    assert chain.sent == []


async def test_estimate_rpc_error_retries_then_fails():
    chain = FakeChain()
    chain.estimate = ConnectionError("connection refused")
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock, relayer_max_attempts=2, relayer_retry_backoff_seconds=10)
    m = await seed_match(chain)
    with relayer_env(w.settings, chain):
        await w.tick()
        job = await _job(m.job_id)
        assert job.status == "pending" and job.attempts == 1 and job.next_attempt_at == clock.t + 10
        await w.tick()  # backoff not elapsed: untouched
        assert (await _job(m.job_id)).attempts == 1
        clock.t += 10
        await w.tick()
    job = await _job(m.job_id)
    assert job.status == "failed" and job.last_error.startswith("gave up")
    assert await _filled(m.taker_hash, m.maker_hash) == [0, 0]


async def test_gas_estimate_above_cap_fails():
    chain = FakeChain()
    chain.estimate = 700_000
    relayer = Account.create()
    w = _worker(chain, relayer, Clock(chain.now))
    m = await seed_match(chain)
    with relayer_env(w.settings, chain):
        await w.tick()
    job = await _job(m.job_id)
    assert job.status == "failed" and "above cap" in job.last_error


# ------------------------------------------------------------------ preflight
def _allowance(chain, m):
    chain.allowances[(m.taker.address.lower(), EXCHANGE.lower())] = 1


def _ctf_approval(chain, m):
    chain.approvals.discard((m.maker.address.lower(), EXCHANGE.lower()))


def _stale_nonce(chain, m):
    chain.ex_nonces[m.maker.address.lower()] = 1


def _cancelled(chain, m):
    chain.cancelled.add(m.maker_hash)


def _overfill(chain, m):
    chain.filled[m.taker_hash] = m.amount


def _resolved(chain, m):
    chain.resolved.add(m.cid)


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (_allowance, "taker usdc allowance < 5037500"),
        (_ctf_approval, "maker ctf not approved"),
        (None, "taker expired"),
        (_stale_nonce, "maker nonce stale"),
        (_cancelled, "maker cancelled onchain"),
        (_overfill, "taker overfill"),
        (_resolved, "market resolved"),
    ],
)
async def test_preflight_failures_fail_without_sending(mutate, reason):
    chain = FakeChain()
    relayer = Account.create()
    w = _worker(chain, relayer, Clock(chain.now))
    expiry = chain.now + 30 if mutate is None else None
    m = await seed_match(chain, taker_expiry=expiry)
    if mutate is not None:
        mutate(chain, m)
    with relayer_env(w.settings, chain):
        stats = await w.tick()
    job = await _job(m.job_id)
    assert stats["failed"] == 1
    assert job.status == "failed" and reason in job.last_error
    assert chain.sent == [] and chain.estimate_calls == []
    # An overfill resyncs the order from chain (drift), every other reason just rolls the fill back.
    taker_filled = m.amount if mutate is _overfill else 0
    assert await _filled(m.taker_hash, m.maker_hash) == [taker_filled, 0]


async def test_db_cancelled_order_cancels_job():
    chain = FakeChain()
    relayer = Account.create()
    w = _worker(chain, relayer, Clock(chain.now))
    m = await seed_match(chain)
    async with SessionLocal() as db:
        await db.execute(update(Order).where(Order.order_hash == m.maker_hash).values(cancelled=True))
        await db.commit()
    with relayer_env(w.settings, chain):
        stats = await w.tick()
    assert stats["cancelled"] == 1
    assert (await _job(m.job_id)).status == "cancelled"
    assert await _filled(m.taker_hash, m.maker_hash) == [0, 0]


# ------------------------------------------------------------------ receipts
async def test_stuck_tx_is_bumped_and_first_hash_confirms():
    chain = FakeChain()
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock)
    m = await seed_match(chain)
    with relayer_env(w.settings, chain):
        await w.tick()
        first = await _job(m.job_id)
        clock.t += 30
        await w.tick()
        assert len(chain.sent) == 1  # not yet due
        clock.t += 30
        stats = await w.tick()
        assert stats["bumped"] == 1
        job = await _job(m.job_id)
        assert len(job.tx_hashes) == 2 and job.tx_hashes[0] == first.tx_hash
        assert job.nonce == first.nonce
        second = chain.sent[-1]
        assert second["nonce"] == first.nonce
        assert second["maxFeePerGas"] >= math.ceil(first.max_fee_per_gas * 1.125)
        assert second["maxPriorityFeePerGas"] >= math.ceil(first.max_priority_fee_per_gas * 1.125)
        assert job.attempts == 2

        chain.mine(first.tx_hash)
        await w.tick()
    job = await _job(m.job_id)
    assert job.status == "confirmed" and job.tx_hash == first.tx_hash
    assert (await _trade(m.job_id)).tx_hash == first.tx_hash


async def test_reverted_receipt_fails_and_rolls_back():
    chain = FakeChain()
    relayer = Account.create()
    w = _worker(chain, relayer, Clock(chain.now))
    m = await seed_match(chain)
    with relayer_env(w.settings, chain):
        await w.tick()
        job = await _job(m.job_id)
        chain.mine(job.tx_hash, status=0)
        stats = await w.tick()
    assert stats["failed"] == 1
    job = await _job(m.job_id)
    assert job.status == "failed" and job.last_error.startswith("reverted")
    assert await _filled(m.taker_hash, m.maker_hash) == [0, 0]
    assert (await _trade(m.job_id)).status == "failed"


async def test_max_attempts_stops_bumping():
    chain = FakeChain()
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock, relayer_max_attempts=2)
    m = await seed_match(chain)
    with relayer_env(w.settings, chain):
        await w.tick()
        clock.t += 60
        await w.tick()
        assert len(chain.sent) == 2
        clock.t += 60
        await w.tick()
        clock.t += 600
        await w.tick()
    job = await _job(m.job_id)
    assert len(chain.sent) == 2
    assert job.status == "sent" and job.last_error.startswith("stuck")


async def test_bump_nonce_too_low_without_receipt_resends_fresh():
    chain = FakeChain()
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock)
    m = await seed_match(chain)
    with relayer_env(w.settings, chain):
        await w.tick()
        clock.t += 60
        chain.pending = 20
        chain.send_errors = [ValueError("nonce too low")]
        await w.tick()
        job = await _job(m.job_id)
        assert job.status == "sent" and job.nonce == 7 and job.nonce_consumed_at == clock.t
        clock.t += 60
        # Lag window over, nonce 7 mined by someone else: reset and the same tick's send phase resends fresh.
        await w.tick()
    job = await _job(m.job_id)
    assert job.status == "sent" and job.nonce == 20
    assert job.last_error == ""
    assert [tx["nonce"] for tx in chain.sent] == [7, 20]


async def test_crash_recovery_rebroadcasts_sending_job():
    chain = FakeChain()
    relayer = Account.create()
    w = _worker(chain, relayer, Clock(chain.now))
    m = await seed_match(chain)
    signed = relayer.sign_transaction({
        "type": 2, "chainId": CHAIN, "nonce": 7, "to": EXCHANGE, "value": 0, "data": "0x",
        "gas": 100_000, "maxFeePerGas": 10**9, "maxPriorityFeePerGas": 10**6,
    })
    h = "0x" + bytes(signed.hash).hex()
    async with SessionLocal() as db:
        job = await db.get(RelayJob, m.job_id)
        job.status = "sending"
        job.sender = relayer.address.lower()
        job.nonce = 7
        job.raw_tx = "0x" + bytes(signed.raw_transaction).hex()
        job.tx_hash = h
        job.tx_hashes = [h]
        job.gas_limit = 100_000
        job.max_fee_per_gas = 10**9
        job.max_priority_fee_per_gas = 10**6
        await db.commit()
    with relayer_env(w.settings, chain):
        chain.send_errors = [ValueError("already known")]
        stats = await w.tick()
    assert stats["reconciled"] == 1
    job = await _job(m.job_id)
    assert job.status == "sent" and job.tx_hash == h and job.sent_at is not None


async def test_sending_job_sets_nonce_floor_for_new_sends():
    chain = FakeChain()
    relayer = Account.create()
    w = _worker(chain, relayer, Clock(chain.now))
    held = await seed_match(chain)
    fresh = await seed_match(chain)
    async with SessionLocal() as db:
        job = await db.get(RelayJob, held.job_id)
        job.status = "sending"
        job.sender = relayer.address.lower()
        job.nonce = 7
        job.raw_tx = "0x00"  # not yet in any mempool
        job.tx_hashes = ["0x" + "11" * 32]
        await db.commit()
    with relayer_env(w.settings, chain):
        chain.send_errors = [TimeoutError("down")]  # the held job's rebroadcast fails
        await w.tick()
    assert (await _job(fresh.job_id)).nonce == 8


# --------------------------------------------------------------- lifecycle
async def test_maybe_start_relayer_gates():
    relayer = Account.create()
    key = "0x" + bytes(relayer.key).hex()
    chain = FakeChain()
    with relayer_env(make_settings(), chain):
        assert maybe_start_relayer(make_settings()) is None
        assert maybe_start_relayer(make_settings(relayer_enabled=True, relayer_private_key=key)) is None
        assert maybe_start_relayer(make_settings(relayer_worker_enabled=True, relayer_private_key=key)) is None
        assert maybe_start_relayer(make_settings(relayer_enabled=True, relayer_worker_enabled=True)) is None
    with relayer_env(make_settings(), chain, domain=None):
        s = make_settings(relayer_enabled=True, relayer_worker_enabled=True, relayer_private_key=key)
        assert maybe_start_relayer(s) is None
    with relayer_env(make_settings(), chain), patch.object(RelayerWorker, "run_forever", new=AsyncMock()) as run:
        s = make_settings(relayer_enabled=True, relayer_worker_enabled=True, relayer_private_key=key)
        task = maybe_start_relayer(s)
        assert task is not None
        await task
        run.assert_awaited_once_with(s.relayer_poll_seconds)
        assert worker_mod.get_active_worker().account.address == relayer.address
    worker_mod.stop_relayer_state()


async def test_tick_skips_when_not_ready():
    chain = FakeChain()
    relayer = Account.create()
    m = await seed_match(chain)
    w = RelayerWorker(chain, relayer, make_settings(relayer_private_key="0x" + bytes(relayer.key).hex()))
    with relayer_env(w.settings, chain):
        assert (await w.tick())["skipped"] == "not ready"
    assert (await _job(m.job_id)).status == "pending"


async def test_run_forever_survives_tick_errors():
    relayer = Account.create()
    w = RelayerWorker(FakeChain(), relayer, _settings(relayer))
    calls = []

    async def boom():
        calls.append(1)
        if len(calls) >= 3:
            raise asyncio.CancelledError
        raise RuntimeError("rpc exploded")

    with patch.object(w, "tick", new=boom):
        with pytest.raises(asyncio.CancelledError):
            await w.run_forever(0)
    assert len(calls) == 3 and "exploded" in w.last_error


# ------------------------------------------------------------ pure helpers
async def test_nonce_manager_reserve_release_resync():
    chain = FakeChain()
    chain.pending = 4
    nm = NonceManager(chain, "0x" + "11" * 20)
    assert await nm.reserve() == 4
    assert await nm.reserve() == 5
    nm.release(5)
    assert await nm.reserve() == 5
    nm.release(4)  # not the last one: ignored
    assert nm.peek() == 6
    assert await nm.reserve(floor=10) == 10
    chain.pending = 2
    assert await nm.resync() == 2


def test_fee_policy():
    s = make_settings(relayer_max_fee_per_gas_wei=1_000, relayer_priority_fee_wei=10, relayer_fee_bump_bps=1250)
    q = quote_fees(100, 5, None, s)
    assert q == FeeQuote(max_fee=210, tip=10)
    assert quote_fees(995, 5, None, s) is None
    assert quote_fees(600, 5, None, s) == FeeQuote(max_fee=1_000, tip=10)
    assert quote_fees(None, 0, 300, s) == FeeQuote(300, 300, 300)
    assert quote_fees(None, 0, 2_000, s) is None
    b = bump_fees(q, s)
    assert b.max_fee >= math.ceil(210 * 1.125) and b.tip >= math.ceil(10 * 1.125)
    assert bump_fees(FeeQuote(max_fee=950, tip=10), s) is None
    assert bump_fees(FeeQuote(300, 300, 300), s) == FeeQuote(338, 338, 338)
    g = make_settings(relayer_gas_buffer_bps=2000, relayer_gas_limit_cap=600_000)
    assert gas_limit(100_000, g) == 120_000
    assert gas_limit(550_000, g) == 600_000
    assert gas_limit(600_001, g) is None


def test_classify_rpc_error():
    assert classify_rpc_error(ValueError({"message": "nonce too low"})) == "nonce_low"
    assert classify_rpc_error(ValueError("already known")) == "known"
    assert classify_rpc_error(ValueError("replacement transaction underpriced")) == "underpriced"
    assert classify_rpc_error(ValueError("insufficient funds for gas")) == "insufficient_funds"
    assert classify_rpc_error(ContractLogicError("execution reverted")) == "revert"
    assert classify_rpc_error(TimeoutError("timed out")) == "transient"


# --------------------------------------------------------------- emissions
async def test_emissions_shares_nonce_manager_and_returns_0x_hash():
    relayer, op = Account.create(), Account.create()
    chain = FakeChain()
    chain.pending = 40
    settings = _settings(
        relayer,
        emissions_distributor_address="0x" + "0e" * 20,
        ou_token_address="0x" + "0f" * 20,
        chain_id=CHAIN,
    )
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        with patch.object(auth_router.settings, "operator_private_key", "0x" + bytes(op.key).hex()):
            token = await siwe_login(api, op)
        # Fresh recipient: the payload hash is the default idempotency key, so a fixed body would
        # dedupe against a distribution left in a persistent test DB by an earlier run.
        body = {"program": 1, "recipients": [{"address": "0x" + secrets.token_hex(20), "amount": "1000"}]}
        with relayer_env(settings, chain):
            r1 = await api.post("/api/v1/emissions/distribute", headers={"Authorization": f"Bearer {token}"}, json=body)
            r2 = await api.post(
                "/api/v1/emissions/distribute", headers={"Authorization": f"Bearer {token}"},
                json={**body, "idempotencyKey": "second-run"},
            )
            assert r1.status_code == 200, r1.text
            assert r1.json()["txHash"].startswith("0x") and len(r1.json()["txHash"]) == 66
            assert [r1.json()["nonce"], r2.json()["nonce"]] == [40, 41]
            assert get_nonce_manager(chain, relayer.address).peek() == 42
            assert "0x" + bytes(chain.sent[0]["to"]).hex() == "0x" + "0e" * 20

            m = await seed_match(chain)
            w = RelayerWorker(chain, relayer, settings)
            await w.tick()
            assert (await _job(m.job_id)).nonce == 42

            chain.estimate = ContractLogicError("execution reverted: treasury empty")
            r3 = await api.post(
                "/api/v1/emissions/distribute", headers={"Authorization": f"Bearer {token}"},
                json={**body, "idempotencyKey": "third-run"},
            )
            assert r3.status_code == 400


# ------------------------------------------------------------- migrations/web3
async def test_ensure_helpers_upgrade_legacy_sqlite_trades(tmp_path):
    from sqlalchemy import inspect, text
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    try:
        async with eng.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE trades (id INTEGER PRIMARY KEY, condition_id VARCHAR(66), taker VARCHAR(42), "
                "maker VARCHAR(42), fill_amount INTEGER, volume INTEGER, fee INTEGER, tx_hash VARCHAR(66), "
                "created_at DATETIME)"
            ))
            await conn.execute(text("INSERT INTO trades (condition_id, taker, maker, fill_amount, volume, fee, tx_hash) "
                                    "VALUES ('0x01', '0xa', '0xb', 1, 1, 0, '')"))
            await conn.run_sync(ensure_trade_relay_columns)
            await conn.run_sync(ensure_trade_relay_columns)  # idempotent
            await conn.run_sync(ensure_order_wide_columns)  # sqlite: no-op
            cols = await conn.run_sync(lambda c: {x["name"] for x in inspect(c).get_columns("trades")})
            status = (await conn.execute(text("SELECT status FROM trades"))).scalar()
        assert {"status", "relay_job_id", "block_number"} <= cols
        assert status == "offchain"
    finally:
        await eng.dispose()


def test_order_wide_columns_is_gated_on_postgres():
    from unittest.mock import MagicMock

    conn = MagicMock()
    conn.dialect.name = "sqlite"
    ensure_order_wide_columns(conn)
    conn.execute.assert_not_called()


def test_web3_chain_client_encodes_offline():
    from app.relayer.chain import Web3ChainClient

    c = Web3ChainClient("http://127.0.0.1:1", EXCHANGE, timeout=1)
    o = (Account.create().address, True, bytes([0xAB]) * 32, 0, 500_000, 10, 1, 0, 2_000_000_000)
    data = c.encode_match(o, o, 5, bytes([1]) * 65, bytes([2]) * 65)
    assert data[:4] == bytes.fromhex("e9f2cd3e")
    assert data == FakeChain().encode_match(o, o, 5, bytes([1]) * 65, bytes([2]) * 65)
    assert Web3ChainClient("http://127.0.0.1:1", None)._exchange is None
