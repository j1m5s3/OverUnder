"""Relayer hardening regressions: in-flight nonce ambiguity, never-terminal signed jobs, per-match jobs,
atomic fills, conditional rollback, CLOB halt, redaction, in-flight accounting, emissions idempotency
and leader-lock release."""

import asyncio
import secrets
import time
from types import SimpleNamespace
from unittest.mock import patch

import requests
from eth_account import Account
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from web3.exceptions import ContractLogicError

import app.auth.router as auth_router
from app.db import SessionLocal
from app.main import create_app
from app.models import Market, Order, RelayJob, Trade
from app.orderbook.eip712 import OrderFields, order_hash_hex
from app.orderbook.matcher import try_match
from app.relayer import router as relayer_router
from app.relayer import worker as worker_mod
from app.relayer.queue import IN_FLIGHT, job_public, rollback_job
from app.relayer.redact import redact
from app.relayer.worker import RelayerWorker
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
from test_relayer_worker import _db, _filled, _job, _settings, _trade, _worker, Clock, seed_match  # noqa: F401

SECRET_URL = "https://rpc.example/v2/SECRETKEY1234567890"


class LagChain(FakeChain):
    """FakeChain whose receipt index / log index can lag behind the chain (load-balanced RPC)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.receipt_lag = 0
        self.hide_logs = False

    async def get_receipt(self, tx_hash):
        if self.receipt_lag > 0:
            self.receipt_lag -= 1
            return None
        return await super().get_receipt(tx_hash)

    async def get_logs(self, address, topics, from_block):
        if self.hide_logs:
            return []
        return await super().get_logs(address, topics, from_block)


def accept_then_raise(chain, exc, mine_logs=None):
    """The node takes the first broadcast but the call raises (timeout / a transport retry)."""
    orig = chain.send_raw
    state = {}

    async def send(raw):
        if "h" not in state:
            h = await orig(raw)
            state["h"] = h
            if mine_logs is not None:
                chain.mine(h, logs=mine_logs)
            raise exc
        return await orig(raw)

    chain.send_raw = send
    return state


def _order(acct, *, cid, is_buy, price, amount, expiry, filled=0, outcome=0) -> Order:
    salt = secrets.randbits(64)
    raw = {"maker": acct.address, "isBuy": is_buy, "conditionId": bytes.fromhex(cid[2:]), "outcome": outcome,
           "price": price, "amount": amount, "salt": salt, "nonce": 0, "expiry": expiry}
    of = OrderFields(acct.address, is_buy, raw["conditionId"], outcome, price, amount, salt, 0, expiry)
    return Order(order_hash=order_hash_hex(of, EXCHANGE, CHAIN), maker=acct.address.lower(), condition_id=cid,
                 is_buy=is_buy, outcome=outcome, price=price, amount=amount, filled=filled, salt=hex(salt), nonce=0,
                 expiry=expiry, signature=sign_order(acct.key, EXCHANGE, CHAIN, raw), cancelled=False)


async def _orders(cid):
    async with SessionLocal() as db:
        return {o.order_hash: o for o in (await db.execute(select(Order).where(Order.condition_id == cid))).scalars()}


async def _jobs(cid):
    async with SessionLocal() as db:
        return (await db.execute(select(RelayJob).where(RelayJob.condition_id == cid).order_by(RelayJob.id))).scalars().all()


async def _trades(cid):
    async with SessionLocal() as db:
        return (await db.execute(select(Trade).where(Trade.condition_id == cid).order_by(Trade.id))).scalars().all()


def _fill_log(m):
    return [order_filled_log(m.taker_hash, m.maker_hash)]


# ================================================= 1. 'nonce too low' for our own signed tx
async def test_sending_rebroadcast_nonce_low_receipt_appears_right_after():
    chain = LagChain()
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock)
    m = await seed_match(chain)
    st = accept_then_raise(chain, TimeoutError("read timed out"))
    with relayer_env(w.settings, chain):
        await w.tick()
        assert (await _job(m.job_id)).status == "sending"
        chain.mine(st["h"], logs=_fill_log(m))
        chain.filled[m.taker_hash] = chain.filled[m.maker_hash] = m.amount
        chain.receipt_lag = 1  # the top-of-reconcile lookup misses it
        chain.send_errors = [ValueError("nonce too low")]
        clock.t += w.settings.relayer_retry_backoff_seconds
        await w.tick()
    job = await _job(m.job_id)
    assert job.status == "confirmed" and job.tx_hash == st["h"]
    assert [tx["nonce"] for tx in chain.sent] == [7]
    assert (await _trade(m.job_id)).status == "confirmed"
    assert await _filled(m.taker_hash, m.maker_hash) == [m.amount, m.amount]


async def test_sending_rebroadcast_nonce_low_receipt_appears_after_delay():
    chain = LagChain()
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock)
    m = await seed_match(chain, amount=10_000_000)
    st = accept_then_raise(chain, TimeoutError("read timed out"))
    with relayer_env(w.settings, chain):
        await w.tick()
        chain.mine(st["h"], logs=_fill_log(m))
        chain.receipt_lag = 3
        chain.send_errors = [ValueError("nonce too low")]
        clock.t += w.settings.relayer_retry_backoff_seconds
        await w.tick()  # top lookup + re-check both miss
        job = await _job(m.job_id)
        assert job.status == "sent" and job.nonce == 7 and job.nonce_consumed_at == clock.t
        clock.t += 5
        await w.tick()  # one more miss; still inside the lag window
        assert (await _job(m.job_id)).status == "sent"
        clock.t += 5
        await w.tick()
    job = await _job(m.job_id)
    assert job.status == "confirmed" and job.tx_hash == st["h"]
    assert len(chain.sent) == 1


async def test_consumed_nonce_confirms_from_our_log_when_receipts_never_show():
    chain = LagChain()
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock)
    m = await seed_match(chain)
    st = accept_then_raise(chain, TimeoutError("read timed out"))
    with relayer_env(w.settings, chain):
        await w.tick()
        chain.mine(st["h"], logs=_fill_log(m), block=150)
        chain.receipt_lag = 10**6
        chain.send_errors = [ValueError("nonce too low")]
        clock.t += w.settings.relayer_retry_backoff_seconds
        await w.tick()
        clock.t += w.settings.relayer_resubmit_after_seconds
        await w.tick()
    job = await _job(m.job_id)
    assert job.status == "confirmed" and job.tx_hash == st["h"] and job.block_number == 150
    assert len(chain.sent) == 1
    # The log search starts at the block the job first signed at.
    assert chain.log_calls[-1]["from_block"] == 100


async def test_send_first_broadcast_nonce_low_while_own_tx_mined():
    chain = LagChain()
    relayer = Account.create()
    w = _worker(chain, relayer, Clock(chain.now))
    m = await seed_match(chain)
    st = accept_then_raise(chain, ValueError("nonce too low"), mine_logs=_fill_log(m))
    with relayer_env(w.settings, chain):
        stats = await w.tick()
    job = await _job(m.job_id)
    assert stats["confirmed"] == 1
    assert job.status == "confirmed" and job.tx_hash == st["h"]
    assert len(chain.sent) == 1


async def test_send_first_broadcast_nonce_low_lagging_receipt_never_resends():
    chain = LagChain()
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock)
    m = await seed_match(chain)
    st = accept_then_raise(chain, ValueError("nonce too low"), mine_logs=_fill_log(m))
    chain.receipt_lag = 10**6
    with relayer_env(w.settings, chain):
        await w.tick()
        assert (await _job(m.job_id)).status == "sent"
        for _ in range(3):
            clock.t += 10
            await w.tick()
        assert len(chain.sent) == 1
        clock.t += w.settings.relayer_resubmit_after_seconds
        await w.tick()
    job = await _job(m.job_id)
    assert job.status == "confirmed" and job.tx_hash == st["h"]
    assert len(chain.sent) == 1


async def test_no_room_fill_landed_is_never_rolled_back_or_cancelled():
    """Receipts and logs lag, but chain shows the fill: stay in flight, never resend, roll back or cancel."""
    chain = LagChain()
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock)
    m = await seed_match(chain)  # job fill == order amount: no room for a second fill
    st = accept_then_raise(chain, TimeoutError("read timed out"))
    with relayer_env(w.settings, chain):
        await w.tick()
        chain.mine(st["h"], logs=_fill_log(m))
        chain.filled[m.taker_hash] = chain.filled[m.maker_hash] = m.amount
        chain.receipt_lag = 10**6
        chain.hide_logs = True
        chain.send_errors = [ValueError("nonce too low")]
        clock.t += w.settings.relayer_retry_backoff_seconds
        await w.tick()
        for _ in range(3):
            clock.t += w.settings.relayer_resubmit_after_seconds
            await w.tick()
    job = await _job(m.job_id)
    assert job.status in IN_FLIGHT and job.last_error.startswith("stuck: fill may have landed")
    assert len(chain.sent) == 1
    assert await _filled(m.taker_hash, m.maker_hash) == [m.amount, m.amount]
    assert (await _trade(m.job_id)).status == "pending"
    orders = await _orders(m.cid)
    assert not any(o.cancelled for o in orders.values())


async def test_send_guard_for_job_with_prior_hashes():
    """A pending job that already signed txs checks the chain before a fresh nonce: landed -> confirm,
    unexplained fill -> defer (no preflight overfill, nobody cancelled), absent -> send."""
    chain = LagChain()
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock)
    ms = [await seed_match(chain) for _ in range(3)]
    olds = ["0x" + secrets.token_hex(32) for _ in ms]
    async with SessionLocal() as db:
        for m, old in zip(ms, olds):
            await db.execute(update(RelayJob).where(RelayJob.id == m.job_id).values(tx_hashes=[old], first_sent_block=90))
        await db.commit()
    landed, maybe, absent = ms
    old = olds[0]
    chain.logs.append({**order_filled_log(landed.taker_hash, landed.maker_hash), "transactionHash": old, "blockNumber": 101})
    chain.filled[maybe.taker_hash] = chain.filled[maybe.maker_hash] = maybe.amount
    with relayer_env(w.settings, chain):
        await w.tick()
    jl, jm, ja = await _job(landed.job_id), await _job(maybe.job_id), await _job(absent.job_id)
    assert jl.status == "confirmed" and jl.tx_hash == old and (await _trade(landed.job_id)).status == "confirmed"
    assert jm.status == "pending" and jm.last_error.startswith("stuck: fill may have landed")
    assert not any(o.cancelled for o in (await _orders(maybe.cid)).values())
    assert await _filled(maybe.taker_hash, maybe.maker_hash) == [maybe.amount, maybe.amount]
    assert ja.status == "sent" and len(chain.sent) == 1


async def test_foreign_nonce_use_resends_only_after_deadline():
    chain = LagChain()
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock)
    m = await seed_match(chain)
    with relayer_env(w.settings, chain):
        chain.send_errors = [TimeoutError("read timed out")]  # node never got it
        await w.tick()
        assert (await _job(m.job_id)).status == "sending"
        chain.pending = 8  # someone else mined nonce 7
        chain.send_errors = [ValueError("nonce too low")]
        clock.t += w.settings.relayer_retry_backoff_seconds
        await w.tick()
        assert (await _job(m.job_id)).status == "sent" and chain.sent == []
        clock.t += w.settings.relayer_resubmit_after_seconds - 1
        await w.tick()
        assert chain.sent == []
        clock.t += 1
        await w.tick()
    job = await _job(m.job_id)
    assert job.status == "sent" and job.nonce == 8
    assert [tx["nonce"] for tx in chain.sent] == [8]
    assert len(job.tx_hashes) == 2


async def test_consumed_nonce_not_yet_mined_returns_to_normal_flow():
    chain = LagChain()
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock)
    m = await seed_match(chain)
    with relayer_env(w.settings, chain):
        chain.send_errors = [ValueError("nonce too low")]
        await w.tick()
        chain.latest = 7  # latest (mined) count has not passed our nonce 7
        clock.t += w.settings.relayer_resubmit_after_seconds
        await w.tick()
    job = await _job(m.job_id)
    assert job.status == "sent" and job.nonce_consumed_at is None and job.nonce == 7


# ================================================= 7. never terminal while a signed tx can mine
async def test_rebroadcast_failures_never_give_up_while_tx_pooled():
    chain = FakeChain()
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock)
    m = await seed_match(chain)
    orig = chain.send_raw
    accepted = {}

    async def send(raw):
        if not accepted:
            accepted["h"] = await orig(raw)
            raise TimeoutError("read timed out")
        raise ValueError("429 Too Many Requests")

    chain.send_raw = send
    with relayer_env(w.settings, chain):
        await w.tick()
        for _ in range(w.settings.relayer_max_attempts + 3):
            clock.t += 1000
            await w.tick()
        job = await _job(m.job_id)
        assert job.status in IN_FLIGHT and job.last_error.startswith("stuck")
        assert await _filled(m.taker_hash, m.maker_hash) == [m.amount, m.amount]
        assert (await _trade(m.job_id)).status == "pending"
        chain.mine(accepted["h"], logs=_fill_log(m), block=200)
        await w.tick()
    job = await _job(m.job_id)
    assert job.status == "confirmed" and job.tx_hash == accepted["h"]
    assert (await _trade(m.job_id)).status == "confirmed"


async def test_rebroadcast_backs_off_between_attempts():
    chain = FakeChain()
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock, relayer_retry_backoff_seconds=10)
    m = await seed_match(chain)
    calls = []
    orig = chain.send_raw

    async def send(raw):
        calls.append(clock.t)
        raise TimeoutError("down")

    chain.send_raw = send
    with relayer_env(w.settings, chain):
        await w.tick()  # first broadcast, next attempt at +10
        await w.tick()
        clock.t += 10
        await w.tick()  # attempt 2 -> backoff 20
        clock.t += 10
        await w.tick()
    assert len(calls) == 2
    assert (await _job(m.job_id)).status == "sending"
    chain.send_raw = orig


def test_web3_client_never_retries_send_raw():
    from app.relayer.chain import Web3ChainClient

    c = Web3ChainClient("http://127.0.0.1:1", EXCHANGE, timeout=1)
    cfg = c.w3.provider.exception_retry_configuration
    assert "eth_sendRawTransaction" not in cfg.method_allowlist
    assert "eth_getTransactionReceipt" in cfg.method_allowlist and "eth_call" in cfg.method_allowlist


# ================================================= 2. one job per match call
async def test_retire_rematch_after_failed_pair_job_creates_new_job():
    chain = FakeChain()
    relayer = Account.create()
    s = _settings(relayer)
    w = RelayerWorker(chain, relayer, s, now_fn=Clock(chain.now))
    ta, m1a, m2a = Account.create(), Account.create(), Account.create()
    cid = rand_cid()
    exp = chain.now + 3600
    m1 = _order(m1a, cid=cid, is_buy=False, price=400_000, amount=10, expiry=exp)
    m2 = _order(m2a, cid=cid, is_buy=False, price=500_000, amount=10, expiry=exp)
    t = _order(ta, cid=cid, is_buy=True, price=500_000, amount=20, expiry=exp)
    chain.fund_buyer(ta.address, 10**12)
    chain.fund_seller(m1a.address, cid, 0, 10**12)  # m2 unfunded -> preflight culprit
    with relayer_env(s, chain):
        async with SessionLocal() as db:
            db.add_all([m1, m2])
            await db.flush()
            db.add(t)
            await db.flush()
            fills = await try_match(db, t)
            await db.commit()
        assert [f["fillAmount"] for f in fills] == [10, 10]
        j1, j2 = fills[0]["relayJobId"], fills[1]["relayJobId"]
        chain.estimate = ContractLogicError("execution reverted")
        await w.tick()
        jobs = await _jobs(cid)
        assert [(j.id, j.status) for j in jobs[:2]] == [(j1, "failed"), (j2, "failed")]
        j3 = jobs[2]
        assert j3.status == "pending" and j3.fill_amount == 10
        assert (j3.taker_hash, j3.maker_hash) == (t.order_hash, m1.order_hash)
        orders = await _orders(cid)
        assert orders[t.order_hash].filled == 10 and orders[m1.order_hash].filled == 10
        assert orders[m2.order_hash].cancelled and not orders[t.order_hash].cancelled
        trades = {tr.relay_job_id: tr.status for tr in await _trades(cid)}
        assert trades == {j1: "failed", j2: "failed", j3.id: "pending"}
        chain.estimate = 200_000
        stats = await w.tick()
    assert stats["sent"] == 1
    assert (await _job(j3.id)).status == "sent"
    assert (await _job(j1)).status == "failed"


async def test_rematch_while_pair_job_in_flight_is_a_separate_job():
    chain = FakeChain()
    relayer = Account.create()
    s = _settings(relayer)
    w = RelayerWorker(chain, relayer, s, now_fn=Clock(chain.now))
    ta, ma = Account.create(), Account.create()
    cid = rand_cid()
    exp = chain.now + 3600
    t = _order(ta, cid=cid, is_buy=True, price=500_000, amount=20, expiry=exp, filled=10)
    m = _order(ma, cid=cid, is_buy=False, price=500_000, amount=20, expiry=exp, filled=10)
    chain.fund_buyer(ta.address, 10**12)
    chain.fund_seller(ma.address, cid, 0, 10**12)
    ha = "0x" + "a1" * 32
    with relayer_env(s, chain):
        async with SessionLocal() as db:
            db.add_all([m, t])
            await db.flush()
            a = RelayJob(condition_id=cid, taker_hash=t.order_hash, maker_hash=m.order_hash, fill_amount=10,
                         status="sent", attempts=1, tx_hashes=[ha], tx_hash=ha, nonce=7, sender=relayer.address.lower(),
                         next_attempt_at=0, sent_at=chain.now)
            db.add(a)
            await db.flush()
            db.add(Trade(condition_id=cid, taker=t.maker, maker=m.maker, fill_amount=10, volume=5, fee=0,
                         tx_hash="", status="pending", relay_job_id=a.id))
            await db.commit()
            job_a = a.id
            fills = await try_match(db, t)
            await db.commit()
        assert len(fills) == 1 and fills[0]["relayJobId"] != job_a
        job_b = fills[0]["relayJobId"]
        assert (await _orders(cid))[t.order_hash].filled == 20
        chain.mine(ha, logs=[order_filled_log(t.order_hash, m.order_hash)])
        chain.estimate = ContractLogicError("execution reverted: nope")
        await w.tick()
    assert (await _job(job_a)).status == "confirmed" and (await _job(job_b)).status == "failed"
    trades = {tr.relay_job_id: tr.status for tr in await _trades(cid)}
    assert trades == {job_a: "confirmed", job_b: "failed"}
    orders = await _orders(cid)
    assert orders[t.order_hash].filled == 10 and orders[m.order_hash].filled == 10


# ================================================= 3. atomic fills
async def test_concurrent_takers_never_overcommit_a_maker():
    chain = FakeChain()
    relayer = Account.create()
    s = _settings(relayer)
    ma, t1a, t2a = Account.create(), Account.create(), Account.create()
    cid = rand_cid()
    exp = int(time.time()) + 3600
    m = _order(ma, cid=cid, is_buy=False, price=500_000, amount=100, expiry=exp)
    async with SessionLocal() as db:
        db.add(m)
        await db.commit()
    t1 = _order(t1a, cid=cid, is_buy=True, price=500_000, amount=60, expiry=exp)
    t2 = _order(t2a, cid=cid, is_buy=True, price=500_000, amount=60, expiry=exp)
    with relayer_env(s, chain):
        a, b = SessionLocal(), SessionLocal()
        try:
            # POST #2 has read the resting ask (filled=0) before POST #1 commits.
            await b.execute(select(Order).where(Order.condition_id == cid))
            a.add(t1)
            await a.flush()
            await try_match(a, t1)
            await a.commit()
            b.add(t2)
            await b.flush()
            await try_match(b, t2)
            await b.commit()
        finally:
            await a.close()
            await b.close()
    orders = await _orders(cid)
    jobs = await _jobs(cid)
    total = sum(j.fill_amount for j in jobs)
    assert total <= 100
    assert orders[m.order_hash].filled == total == 100
    assert orders[t1.order_hash].filled == 60 and orders[t2.order_hash].filled == 40
    assert sorted(j.fill_amount for j in jobs) == [40, 60]


async def test_rollback_keeps_a_concurrent_fill():
    chain = FakeChain()
    relayer = Account.create()
    s = _settings(relayer)
    ma, t1a, t2a = Account.create(), Account.create(), Account.create()
    cid = rand_cid()
    exp = int(time.time()) + 3600
    m = _order(ma, cid=cid, is_buy=False, price=500_000, amount=100, expiry=exp, filled=60)
    t1 = _order(t1a, cid=cid, is_buy=True, price=500_000, amount=60, expiry=exp, filled=60)
    async with SessionLocal() as db:
        db.add_all([m, t1])
        await db.flush()
        x = RelayJob(condition_id=cid, taker_hash=t1.order_hash, maker_hash=m.order_hash, fill_amount=60,
                     status="pending", attempts=0, tx_hashes=[], next_attempt_at=0)
        db.add(x)
        await db.commit()
        xid = x.id
    t2 = _order(t2a, cid=cid, is_buy=True, price=500_000, amount=40, expiry=exp)
    with relayer_env(s, chain):
        a, b = SessionLocal(), SessionLocal()
        try:
            job = await a.get(RelayJob, xid)
            await a.execute(select(Order).where(Order.condition_id == cid))  # worker holds filled=60
            b.add(t2)
            await b.flush()
            await try_match(b, t2)
            await b.commit()
            assert await rollback_job(a, job, "failed", "estimate reverted")
            await a.commit()
        finally:
            await a.close()
            await b.close()
    orders = await _orders(cid)
    assert orders[m.order_hash].filled == 40 and orders[t1.order_hash].filled == 0
    assert orders[t2.order_hash].filled == 40


# ================================================= 6. conditional rollback
async def test_rollback_is_a_noop_once_the_worker_claimed_the_job():
    chain = FakeChain()
    m = await seed_match(chain)
    async with SessionLocal() as a, SessionLocal() as b:
        job = await a.get(RelayJob, m.job_id)
        assert job.status == "pending"
        await b.execute(update(RelayJob).where(RelayJob.id == m.job_id).values(status="sending"))
        await b.commit()
        assert await rollback_job(a, job, "cancelled", "order cancelled") is False
        await a.commit()
        assert job.status == "sending"
    assert await _filled(m.taker_hash, m.maker_hash) == [m.amount, m.amount]
    assert (await _trade(m.job_id)).status == "pending"
    async with SessionLocal() as db:
        await db.execute(update(RelayJob).where(RelayJob.id == m.job_id).values(status="cancelled"))
        await db.commit()


async def test_stale_pending_job_is_never_rolled_back_twice():
    chain = FakeChain()
    m = await seed_match(chain)
    async with SessionLocal() as a, SessionLocal() as b:
        stale = await a.get(RelayJob, m.job_id)
        fresh = await b.get(RelayJob, m.job_id)
        assert await rollback_job(b, fresh, "cancelled", "order cancelled")
        await b.commit()
        assert await rollback_job(a, stale, "failed", "preflight") is False
        await a.commit()
    assert await _filled(m.taker_hash, m.maker_hash) == [0, 0]
    assert (await _job(m.job_id)).status == "cancelled"


async def test_cancel_route_reports_job_claimed_mid_cancel_as_in_flight():
    import app.orderbook.router as ob_router

    chain = FakeChain()
    relayer = Account.create()
    m = await seed_match(chain)
    real = ob_router.rollback_job

    async def claim_first(db, job, status, reason):
        # The worker's conditional claim lands between the route's read and its rollback.
        claim = update(RelayJob).where(RelayJob.id == job.id).values(status="sending")
        if db.get_bind().dialect.name == "postgresql":
            # The route holds FOR UPDATE on the job here, so a real worker's claim just waits for its
            # commit; a second session awaited from inside the route would wait on itself forever.
            # Write the claim in the route's transaction, leaving the ORM copy stale at 'pending',
            # so rollback_job's conditional UPDATE still has to notice the move.
            await db.execute(claim.execution_options(synchronize_session=False))
        else:
            async with SessionLocal() as other:
                await other.execute(claim)
                await other.commit()
        return await real(db, job, status, reason)

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        token = await siwe_login(api, m.maker)
        with relayer_env(_settings(relayer), chain), patch.object(ob_router, "rollback_job", new=claim_first):
            r = await api.delete(f"/api/v1/orders/{m.maker_hash}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cancelledJobs"] == [] and body["inFlightJobs"] == [m.job_id]
    assert body["onchainCancelRequired"] is True
    assert (await _job(m.job_id)).status == "sending"
    assert await _filled(m.taker_hash, m.maker_hash) == [m.amount, m.amount]
    assert (await _trade(m.job_id)).status == "pending"
    async with SessionLocal() as db:
        await db.execute(update(RelayJob).where(RelayJob.id == m.job_id).values(status="cancelled"))
        await db.commit()


# ================================================= 4. CLOB trading halt in the worker
async def test_worker_rolls_back_fill_matched_after_close_and_sends_earlier_one():
    chain = FakeChain()
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock)
    late = await seed_match(chain)
    early = await seed_match(chain)
    close = chain.now - 10
    async with SessionLocal() as db:
        for mm in (late, early):
            db.add(Market(condition_id=mm.cid, question="halt?", close_time=close, resolved=False))
        await db.execute(update(RelayJob).where(RelayJob.id == late.job_id).values(matched_at=close + 5))
        await db.execute(update(RelayJob).where(RelayJob.id == early.job_id).values(matched_at=close - 100))
        await db.commit()
    with relayer_env(w.settings, chain):
        stats = await w.tick()
    jl, je = await _job(late.job_id), await _job(early.job_id)
    assert jl.status == "failed" and jl.last_error == "preflight: market closed"
    assert await _filled(late.taker_hash, late.maker_hash) == [0, 0]
    assert not any(o.cancelled for o in (await _orders(late.cid)).values())
    assert je.status == "sent" and stats["sent"] == 1 and len(chain.sent) == 1


async def test_try_match_skips_halted_market():
    chain = FakeChain()
    relayer = Account.create()
    s = _settings(relayer)
    cid = rand_cid()
    exp = int(time.time()) + 3600
    m = _order(Account.create(), cid=cid, is_buy=False, price=400_000, amount=10, expiry=exp)
    t = _order(Account.create(), cid=cid, is_buy=True, price=500_000, amount=10, expiry=exp)
    with relayer_env(s, chain):
        async with SessionLocal() as db:
            db.add(Market(condition_id=cid, question="halt?", close_time=int(time.time()) - 1, resolved=False))
            db.add(m)
            await db.flush()
            db.add(t)
            await db.flush()
            assert await try_match(db, t) == []
            await db.commit()
    assert await _jobs(cid) == []


# ================================================= 9. in-flight accounting and revert retirement
async def test_jobs_sharing_one_buyer_balance_send_one_at_a_time():
    chain = FakeChain()
    relayer = Account.create()
    clock = Clock(chain.now)
    w = _worker(chain, relayer, clock)
    a = Account.create()
    cid = rand_cid()
    exp = chain.now + 3600
    t1 = _order(a, cid=cid, is_buy=True, price=500_000, amount=10_000_000, expiry=exp, filled=10_000_000)
    t2 = _order(a, cid=cid, is_buy=True, price=500_000, amount=10_000_000, expiry=exp, filled=10_000_000)
    b1, b2 = Account.create(), Account.create()
    m1 = _order(b1, cid=cid, is_buy=False, price=500_000, amount=10_000_000, expiry=exp, filled=10_000_000)
    m2 = _order(b2, cid=cid, is_buy=False, price=500_000, amount=10_000_000, expiry=exp, filled=10_000_000)
    need = 5_037_500
    chain.fund_buyer(a.address, 6_000_000)  # funds one job, not two
    chain.fund_seller(b1.address, cid, 0, 10**12)
    chain.fund_seller(b2.address, cid, 0, 10**12)
    async with SessionLocal() as db:
        db.add_all([t1, t2, m1, m2])
        await db.flush()
        ids = []
        for t, m in ((t1, m1), (t2, m2)):
            j = RelayJob(condition_id=cid, taker_hash=t.order_hash, maker_hash=m.order_hash, fill_amount=10_000_000,
                         status="pending", attempts=0, tx_hashes=[], next_attempt_at=0)
            db.add(j)
            await db.flush()
            ids.append(j.id)
        await db.commit()
    with relayer_env(w.settings, chain):
        await w.tick()
        j1, j2 = await _job(ids[0]), await _job(ids[1])
        assert j1.status == "sent" and j2.status == "pending" and j2.last_error.startswith("busy:")
        clock.t += 5
        await w.tick()
        assert len(chain.sent) == 1 and (await _job(ids[1])).status == "pending"
        chain.mine(j1.tx_hash, logs=[order_filled_log(t1.order_hash, m1.order_hash)])
        chain.usdc[a.address.lower()] -= need
        chain.allowances[(a.address.lower(), EXCHANGE.lower())] -= need
        clock.t += 5
        await w.tick()
    assert (await _job(ids[0])).status == "confirmed"
    j2 = await _job(ids[1])
    assert j2.status == "failed" and "taker usdc balance" in j2.last_error
    assert len(chain.sent) == 1
    orders = await _orders(cid)
    assert orders[t2.order_hash].cancelled and not orders[m2.order_hash].cancelled


async def test_reverted_receipt_retires_unfunded_maker_and_rematches_taker():
    chain = FakeChain()
    relayer = Account.create()
    s = _settings(relayer)
    w = RelayerWorker(chain, relayer, s, now_fn=Clock(chain.now))
    ta, m1a, m2a = Account.create(), Account.create(), Account.create()
    cid = rand_cid()
    exp = chain.now + 3600
    m1 = _order(m1a, cid=cid, is_buy=False, price=400_000, amount=10, expiry=exp)
    m2 = _order(m2a, cid=cid, is_buy=False, price=500_000, amount=10, expiry=exp)
    t = _order(ta, cid=cid, is_buy=True, price=500_000, amount=10, expiry=exp)
    chain.fund_buyer(ta.address, 10**12)
    chain.fund_seller(m1a.address, cid, 0, 10**12)
    chain.fund_seller(m2a.address, cid, 0, 10**12)
    with relayer_env(s, chain):
        async with SessionLocal() as db:
            db.add_all([m1, m2])
            await db.flush()
            db.add(t)
            await db.flush()
            fills = await try_match(db, t)
            await db.commit()
        j1 = fills[0]["relayJobId"]
        await w.tick()
        chain.mine((await _job(j1)).tx_hash, status=0)
        chain.ctf[(m1a.address.lower(), cid, 0)] = 0  # the maker moved its tokens
        await w.tick()
    jobs = await _jobs(cid)
    assert jobs[0].status == "failed" and jobs[0].last_error.startswith("reverted")
    # The re-match is a new job; the same tick's send phase already picks it up.
    assert len(jobs) == 2 and jobs[1].status == "sent" and jobs[1].maker_hash == m2.order_hash
    orders = await _orders(cid)
    assert orders[m1.order_hash].cancelled and not orders[t.order_hash].cancelled
    assert orders[t.order_hash].filled == 10 and orders[m2.order_hash].filled == 10


# ================================================= 5. redaction
class LeakChain(FakeChain):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.leak: set[str] = set()

    def _err(self):
        return requests.HTTPError(f"429 Client Error: Too Many Requests for url: {SECRET_URL}")

    async def latest_block(self):
        if "block" in self.leak:
            raise self._err()
        return await super().latest_block()

    async def get_receipt(self, tx_hash):
        if "receipt" in self.leak:
            raise self._err()
        return await super().get_receipt(tx_hash)

    async def send_raw(self, raw):
        if "send" in self.leak:
            raise self._err()
        return await super().send_raw(raw)

    def encode_match(self, *a):
        if "encode" in self.leak:
            raise RuntimeError(f"boom calling {SECRET_URL} key in path")
        return super().encode_match(*a)


def test_redact_masks_rpc_secrets_and_keeps_hashes():
    key = "0x" + "4f" * 32
    s = make_settings(anvil_rpc_url=SECRET_URL, relayer_private_key=key)
    with patch("app.relayer.redact.get_settings", return_value=s):
        assert "SECRETKEY" not in redact(f"429 Client Error: Too Many Requests for url: {SECRET_URL}")
        assert "SECRETKEY" not in redact("Max retries exceeded with url: /v2/SECRETKEY1234567890 (Caused by x)")
        assert redact(f"calling {SECRET_URL} now") == "calling https://rpc.example now"
        assert "4f4f4f" not in redact(f"bad key {key} / {key[2:]}")
        h = "0x" + "ab" * 32
        assert redact(f"reverted in {h}") == f"reverted in {h}"
    assert "FAKEKEY" not in redact("HTTPConnectionPool: Max retries exceeded with url: /v2/FAKEKEY123")
    assert redact("https://user:pw@host.example:8545/path/abcdef?k=v") == "https://host.example:8545"


async def test_rpc_errors_never_reach_last_error_or_api():
    chain = LeakChain()
    relayer = Account.create()
    op = Account.create()
    clock = Clock(chain.now)
    settings = _settings(relayer, anvil_rpc_url=SECRET_URL)
    w = RelayerWorker(chain, relayer, settings, now_fn=clock)
    pre = await seed_match(chain)
    with relayer_env(settings, chain):
        chain.leak = {"block"}
        await w.tick()  # preflight rpc
        j = await _job(pre.job_id)
        assert j.status == "pending" and j.last_error == "preflight rpc: transient (HTTPError)"
        chain.leak = {"send"}
        clock.t += 1000
        await w.tick()  # broadcast fails -> sending
        j = await _job(pre.job_id)
        assert j.status == "sending" and "SECRETKEY" not in j.last_error and "rpc.example" not in j.last_error
        chain.leak = {"receipt"}
        clock.t += 1000
        await w.tick()
        j = await _job(pre.job_id)
        assert j.last_error == "receipt lookup: transient (HTTPError)"
        enc = await seed_match(chain)
        chain.leak = {"encode"}
        await w.tick()
        assert w.last_error and "SECRETKEY" not in w.last_error
        # A row written before the fix is cleaned on read.
        async with SessionLocal() as db:
            await db.execute(update(RelayJob).where(RelayJob.id == enc.job_id).values(last_error=f"rpc: {SECRET_URL}"))
            await db.commit()
        assert "SECRETKEY" not in job_public(await _job(enc.job_id))["lastError"]
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
            token = await siwe_login(api, enc.taker)
            r = await api.get(f"/api/v1/relayer/jobs/{enc.job_id}", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200 and "SECRETKEY" not in r.text
            with patch.object(auth_router.settings, "operator_private_key", "0x" + bytes(op.key).hex()):
                op_token = await siwe_login(api, op)
            st = await api.get("/api/v1/relayer/status", headers={"Authorization": f"Bearer {op_token}"})
            assert st.status_code == 200 and "SECRETKEY" not in st.text
    async with SessionLocal() as db:
        await db.execute(update(RelayJob).where(RelayJob.id.in_([pre.job_id, enc.job_id])).values(status="cancelled"))
        await db.commit()


# ================================================= 8. emissions idempotency
def _emission_settings(relayer, **kw):
    return _settings(
        relayer, emissions_distributor_address="0x" + "0e" * 20, ou_token_address="0x" + "0f" * 20,
        chain_id=CHAIN, **kw,
    )


async def _op_token(api):
    op = Account.create()
    with patch.object(auth_router.settings, "operator_private_key", "0x" + bytes(op.key).hex()):
        return {"Authorization": f"Bearer {await siwe_login(api, op)}"}


def _dist_body():
    return {"program": 2, "recipients": [{"address": "0x" + secrets.token_hex(20), "amount": "1000"}]}


async def test_emissions_ambiguous_broadcast_is_202_and_never_pays_twice():
    relayer = Account.create()
    chain = FakeChain()
    chain.pending = 40
    settings = _emission_settings(relayer)
    orig = chain.send_raw
    accepted = {}

    async def send(raw):
        if not accepted:
            accepted["h"] = await orig(raw)
            raise TimeoutError("read timed out")
        return await orig(raw)

    chain.send_raw = send
    body = _dist_body()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        hdr = await _op_token(api)
        with relayer_env(settings, chain):
            r1 = await api.post("/api/v1/emissions/distribute", headers=hdr, json=body)
            assert r1.status_code == 202, r1.text
            assert r1.json()["status"] == "unknown" and r1.json()["txHash"] == accepted["h"]
            assert r1.json()["nonce"] == 40
            r2 = await api.post("/api/v1/emissions/distribute", headers=hdr, json=body)
            assert r2.status_code == 202 and r2.json()["duplicate"] is True
            assert r2.json()["txHash"] == accepted["h"]
            chain.mine(accepted["h"])
            r3 = await api.post("/api/v1/emissions/distribute", headers=hdr, json=body)
            assert r3.status_code == 409 and r3.json()["txHash"] == accepted["h"]
            assert r3.json()["status"] == "confirmed"
            got = await api.get(f"/api/v1/emissions/distributions/{r1.json()['distributionId']}", headers=hdr)
            assert got.status_code == 200 and got.json()["status"] == "confirmed"
    assert len(chain.sent) == 1 and chain.sent[0]["nonce"] == 40


async def test_emissions_definite_rejection_releases_nonce_and_allows_retry():
    relayer = Account.create()
    chain = FakeChain()
    chain.pending = 40
    settings = _emission_settings(relayer)
    body = _dist_body()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        hdr = await _op_token(api)
        with relayer_env(settings, chain):
            chain.send_errors = [ValueError("insufficient funds for gas * price + value")]
            r1 = await api.post("/api/v1/emissions/distribute", headers=hdr, json=body)
            assert r1.status_code == 502 and r1.json()["status"] == "failed" and r1.json()["txHash"]
            r2 = await api.post("/api/v1/emissions/distribute", headers=hdr, json=body)
            assert r2.status_code == 200 and r2.json()["nonce"] == 40 and r2.json()["status"] == "sent"


async def test_emissions_and_worker_respect_each_others_in_flight_nonces():
    relayer = Account.create()
    chain = FakeChain()
    chain.pending = 40
    settings = _emission_settings(relayer)
    held = await seed_match(chain)
    async with SessionLocal() as db:
        await db.execute(
            update(RelayJob).where(RelayJob.id == held.job_id)
            .values(status="sending", nonce=50, sender=relayer.address.lower(), raw_tx="0x00", tx_hashes=["0x" + "12" * 32])
        )
        await db.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        hdr = await _op_token(api)
        with relayer_env(settings, chain):
            r = await api.post("/api/v1/emissions/distribute", headers=hdr, json=_dist_body())
            assert r.status_code == 200 and r.json()["nonce"] == 51
            async with SessionLocal() as db:
                await db.execute(update(RelayJob).where(RelayJob.id == held.job_id).values(status="cancelled"))
                await db.commit()
            fresh = await seed_match(chain)
            w = RelayerWorker(chain, relayer, settings)
            await w.tick()
    assert (await _job(fresh.job_id)).nonce == 52


# ================================================= 11. leader lock release under concurrent manual ticks
class _Res:
    def __init__(self, v):
        self.v = v

    def scalar(self):
        return self.v


class _LockDB:
    holder = None


class _FakeConn:
    def __init__(self, db):
        self.db = db
        self.closed = False
        self.invalidated = False

    async def execute(self, stmt, params=None):
        if self.closed:
            raise RuntimeError("connection closed")
        await asyncio.sleep(0)
        sql = str(stmt)
        if "pg_try_advisory_lock" in sql:
            if self.db.holder in (None, self):
                self.db.holder = self
                return _Res(True)
            return _Res(False)
        if "pg_advisory_unlock" in sql:
            if self.db.holder is self:
                self.db.holder = None
                return _Res(True)
            return _Res(False)
        return _Res(1)

    async def commit(self):
        await asyncio.sleep(0)

    async def invalidate(self):
        self.invalidated = True
        if self.db.holder is self:
            self.db.holder = None

    async def close(self):
        # A pooled close only rolls back: a session advisory lock would survive it.
        self.closed = True


class _FakePgEngine:
    def __init__(self):
        self.dialect = SimpleNamespace(name="postgresql")
        self.db = _LockDB()
        self.conns: list[_FakeConn] = []

    async def connect(self):
        c = _FakeConn(self.db)
        self.conns.append(c)
        return c


async def test_concurrent_manual_ticks_never_share_a_closing_leader_connection():
    relayer = Account.create()
    chain = FakeChain()
    settings = _settings(relayer)
    eng = _FakePgEngine()
    violations = []
    w = RelayerWorker(chain, relayer, settings, engine=eng)

    class _Checked:
        async def __aenter__(self):
            for _ in range(6):
                await asyncio.sleep(0)
                conn = w._leader_conn
                if conn is None or conn.closed or eng.db.holder is not conn:
                    violations.append(conn)
            self.db = SessionLocal()
            return await self.db.__aenter__()

        async def __aexit__(self, *exc):
            return await self.db.__aexit__(*exc)

    w.session_factory = lambda: _Checked()
    worker_mod.stop_relayer_state()
    with relayer_env(settings, chain):
        worker_mod._MANUAL_WORKER = w
        try:
            results = await asyncio.gather(relayer_router.tick(None), relayer_router.tick(None))
        finally:
            worker_mod._MANUAL_WORKER = None
    assert all("skipped" not in r for r in results)
    assert violations == []
    assert eng.db.holder is None and w._leader_conn is None
    assert len(eng.conns) == 2 and all(c.closed for c in eng.conns)


async def test_lost_leader_connection_is_invalidated():
    relayer = Account.create()
    eng = _FakePgEngine()
    w = RelayerWorker(FakeChain(), relayer, _settings(relayer), engine=eng)
    assert await w._ensure_leader()
    first = w._leader_conn
    first.closed = True  # the server dropped it
    assert await w._ensure_leader()
    assert w._leader_conn is not first and first.invalidated
    assert eng.db.holder is w._leader_conn
    await w.release_leadership()
    assert eng.db.holder is None and w._leader_conn is None
