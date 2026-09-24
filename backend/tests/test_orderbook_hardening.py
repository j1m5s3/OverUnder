"""CLOB API hardening: trading halt, per-maker fund reservation, on-chain cancel exposure, legacy salts,
culprit retirement, the manual-tick status and the relay_jobs schema upgrade."""

import secrets
import time
from unittest.mock import MagicMock, patch

import pytest
from eth_account import Account
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, inspect, select, text, update

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
from app.models import Market, Order, RelayJob
from app.relayer import worker as worker_mod
from app.relayer.schema import ensure_relayer_schema
from app.relayer.worker import RelayerWorker
from _relayer_helpers import EXCHANGE, FakeChain, make_settings, order_body, order_filled_log, rand_cid, relayer_env, siwe_login


@pytest.fixture
async def api():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(ensure_live_score_facts)
        await conn.run_sync(ensure_user_cdp_user_id)
        await conn.run_sync(ensure_trade_relay_columns)
        await conn.run_sync(ensure_order_wide_columns)
    async with SessionLocal() as db:
        await db.execute(
            update(RelayJob).where(RelayJob.status.in_(("pending", "sending", "sent"))).values(status="cancelled")
        )
        await db.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _relay_settings(relayer=None, **kw):
    relayer = relayer or Account.create()
    return make_settings(relayer_enabled=True, relayer_private_key="0x" + bytes(relayer.key).hex(), **kw)


async def _post(api, token, body):
    return await api.post("/api/v1/orders", headers=_auth(token), json=body)


async def _order_count(maker: str) -> int:
    async with SessionLocal() as db:
        return (await db.execute(select(func.count()).select_from(Order).where(Order.maker == maker.lower()))).scalar()


async def _market(cid: str, close_time: int, resolved: bool = False) -> None:
    async with SessionLocal() as db:
        db.add(Market(condition_id=cid, question="CLOB halt?", close_time=close_time, resolved=resolved))
        await db.commit()


# ------------------------------------------------------------------ 4. trading halt
async def test_post_order_on_closed_market_is_409_when_halt_on(api):
    acct = Account.create()
    token = await siwe_login(api, acct)
    cid = rand_cid()
    await _market(cid, int(time.time()) - 60)
    with relayer_env(make_settings(trading_halt_at_close=True), FakeChain()):
        r = await _post(api, token, order_body(acct, cid=cid, is_buy=True))
        book = (await api.get(f"/api/v1/orderbook/{cid}")).json()
    assert r.status_code == 409 and r.json()["detail"] == "market closed"
    assert await _order_count(acct.address) == 0
    assert book["tradingOpen"] is False and book["haltReason"] == "market closed"


async def test_post_order_on_closed_market_is_accepted_when_halt_off(api):
    acct = Account.create()
    token = await siwe_login(api, acct)
    cid = rand_cid()
    await _market(cid, int(time.time()) - 60)
    with relayer_env(make_settings(trading_halt_at_close=False), FakeChain()):
        r = await _post(api, token, order_body(acct, cid=cid, is_buy=True))
        book = (await api.get(f"/api/v1/orderbook/{cid}")).json()
    assert r.status_code == 200, r.text
    assert book["tradingOpen"] is True and book["haltReason"] is None


async def test_post_order_on_resolved_market_is_409_even_with_halt_off(api):
    acct = Account.create()
    token = await siwe_login(api, acct)
    cid = rand_cid()
    await _market(cid, int(time.time()) + 3600, resolved=True)
    with relayer_env(make_settings(trading_halt_at_close=False), FakeChain()):
        r = await _post(api, token, order_body(acct, cid=cid, is_buy=True))
    assert r.status_code == 409 and r.json()["detail"] == "market resolved"
    assert await _order_count(acct.address) == 0


async def test_bad_input_on_closed_market_is_still_400(api):
    acct = Account.create()
    token = await siwe_login(api, acct)
    cid = rand_cid()
    await _market(cid, int(time.time()) - 60)
    with relayer_env(make_settings(), FakeChain()):
        r = await _post(api, token, order_body(acct, cid=cid, is_buy=True, outcome=2))
    assert r.status_code == 400


# ------------------------------------------------------------------ 9. one balance cannot back many orders
async def test_second_bid_beyond_usdc_balance_is_rejected(api):
    buyer = Account.create()
    token = await siwe_login(api, buyer)
    chain = FakeChain()
    chain.fund_buyer(buyer.address, 1_007_500)  # exactly one 2e6 @ 0.50 bid (+0.75% fee)
    cid = rand_cid()
    with relayer_env(_relay_settings(), chain):
        r1 = await _post(api, token, order_body(buyer, cid=cid, is_buy=True, price=500_000, amount=2_000_000))
        r2 = await _post(api, token, order_body(buyer, cid=rand_cid(), is_buy=True, price=500_000, amount=2_000_000))
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 400
    assert r2.json()["reasons"] == ["usdc balance < 2015000", "usdc allowance < 2015000"]


async def test_second_ask_beyond_ctf_balance_is_rejected(api):
    seller = Account.create()
    token = await siwe_login(api, seller)
    chain = FakeChain()
    cid = rand_cid()
    chain.fund_seller(seller.address, cid, 0, 3_000_000)
    with relayer_env(_relay_settings(), chain):
        r1 = await _post(api, token, order_body(seller, cid=cid, is_buy=False, amount=2_000_000))
        r2 = await _post(api, token, order_body(seller, cid=cid, is_buy=False, amount=2_000_000))
        r3 = await _post(api, token, order_body(seller, cid=cid, is_buy=False, amount=2_000_000, outcome=1))
    assert r1.status_code == 200 and r2.status_code == 400
    assert r2.json()["reasons"] == ["ctf balance < 4000000"]
    assert r3.status_code == 400 and r3.json()["reasons"] == ["ctf balance < 2000000"]  # other outcome: own balance


async def test_open_order_cap_per_maker(api):
    buyer = Account.create()
    token = await siwe_login(api, buyer)
    chain = FakeChain()
    chain.fund_buyer(buyer.address, 10**12)
    with relayer_env(_relay_settings(relayer_max_open_orders_per_maker=1), chain):
        r1 = await _post(api, token, order_body(buyer, cid=rand_cid(), is_buy=True))
        r2 = await _post(api, token, order_body(buyer, cid=rand_cid(), is_buy=True))
    assert r1.status_code == 200 and r2.status_code == 400 and r2.json()["detail"] == "too many open orders"


# ------------------------------------------------------------------ 10. off-chain cancel exposure
async def _crossing_pair(api, chain, cid, amount=5_000_000):
    buyer, seller = Account.create(), Account.create()
    tb, ts = await siwe_login(api, buyer), await siwe_login(api, seller)
    chain.fund_buyer(buyer.address, 10**12)
    chain.fund_seller(seller.address, cid, 0, 10**12)
    rb = await _post(api, tb, order_body(buyer, cid=cid, is_buy=True, price=600_000, amount=amount))
    assert rb.status_code == 200, rb.text
    rs = await _post(api, ts, order_body(seller, cid=cid, is_buy=False, price=600_000, amount=amount))
    assert rs.status_code == 200, rs.text
    return buyer, seller, tb, ts, rb.json()["order"], rs.json()


async def test_cancel_of_never_broadcast_order_needs_no_onchain_cancel(api):
    chain, cid = FakeChain(), rand_cid()
    acct = Account.create()
    token = await siwe_login(api, acct)
    chain.fund_buyer(acct.address, 10**12)
    with relayer_env(_relay_settings(), chain):
        resting = (await _post(api, token, order_body(acct, cid=cid, is_buy=True)))
        r = await api.delete(f"/api/v1/orders/{resting.json()['order']['orderHash']}", headers=_auth(token))
        # A pending (never signed) job's fill does not expose the signature either.
        _, _, tb, _, buy_order, _ = await _crossing_pair(api, chain, rand_cid())
        r2 = await api.delete(f"/api/v1/orders/{buy_order['orderHash']}", headers=_auth(tb))
    assert r.status_code == 200 and r.json()["offchainOnly"] is True
    assert r.json()["onchainCancelRequired"] is False
    assert r2.json()["onchainCancelRequired"] is False and len(r2.json()["cancelledJobs"]) == 1


async def test_cancel_after_confirmed_fill_requires_onchain_cancel(api):
    chain, cid = FakeChain(), rand_cid()
    relayer = Account.create()
    settings = _relay_settings(relayer)
    with relayer_env(settings, chain):
        buyer, seller, tb, ts, buy_order, res = await _crossing_pair(api, chain, cid, amount=5_000_000)
        # A second, larger bid only partly filled by a confirmed relay job.
        big = await _post(api, tb, order_body(buyer, cid=cid, is_buy=True, price=600_000, amount=9_000_000, salt=77))
        big_hash = big.json()["order"]["orderHash"]
        r3 = await _post(api, ts, order_body(seller, cid=cid, is_buy=False, price=600_000, amount=3_000_000))
        job_id = r3.json()["fills"][0]["relayJobId"]
        w = RelayerWorker(chain, relayer, settings)
        await w.tick()
        async with SessionLocal() as db:
            job = await db.get(RelayJob, job_id)
            chain.mine(job.tx_hash, logs=[order_filled_log(job.taker_hash, job.maker_hash)])
        await w.tick()
        r = await api.delete(f"/api/v1/orders/{big_hash}", headers=_auth(tb))
    assert r.status_code == 200
    body = r.json()
    assert body["onchainCancelRequired"] is True and body["offchainOnly"] is True
    args = body["cancelOrderArgs"]
    assert args["maker"] == buyer.address.lower() and args["isBuy"] is True
    assert args["conditionId"] == cid and args["outcome"] == 0
    assert args["price"] == "600000" and args["amount"] == "9000000" and args["salt"] == "77"
    assert args["nonce"] == "0" and int(args["expiry"]) > int(time.time())


# ------------------------------------------------------------------ seed: legacy salt
async def test_legacy_negative_salt_does_not_500(api):
    cid = rand_cid()
    async with SessionLocal() as db:
        db.add(Order(
            order_hash="0x" + secrets.token_hex(32), maker="0x" + "12" * 20, condition_id=cid, is_buy=True,
            outcome=0, price=500_000, amount=1_000_000, filled=0, salt="-5", nonce=0,
            expiry=int(time.time()) + 3600, signature="0x" + "00" * 65, cancelled=False,
        ))
        await db.commit()
    r = await api.get(f"/api/v1/orderbook/{cid}")
    assert r.status_code == 200
    assert r.json()["bids"][0]["salt"] == "-5"


# ------------------------------------------------------------------ seed: culprit retirement + re-match
async def test_preflight_failure_cancels_culprit_only_and_rematches_innocent_taker(api):
    chain, cid = FakeChain(), rand_cid()
    relayer = Account.create()
    settings = _relay_settings(relayer)
    bad, good, taker = Account.create(), Account.create(), Account.create()
    tbad, tgood, tt = await siwe_login(api, bad), await siwe_login(api, good), await siwe_login(api, taker)
    chain.fund_seller(bad.address, cid, 0, 10**12)
    chain.fund_seller(good.address, cid, 0, 10**12)
    chain.fund_buyer(taker.address, 10**12)
    with relayer_env(settings, chain):
        rb = await _post(api, tbad, order_body(bad, cid=cid, is_buy=False, price=400_000, amount=4_000_000))
        rg = await _post(api, tgood, order_body(good, cid=cid, is_buy=False, price=500_000, amount=4_000_000))
        rt = await _post(api, tt, order_body(taker, cid=cid, is_buy=True, price=500_000, amount=4_000_000))
        assert rt.json()["fills"][0]["makerHash"] == rb.json()["order"]["orderHash"]
        chain.approvals.discard((bad.address.lower(), EXCHANGE.lower()))  # the cheap maker revoked approval
        w = RelayerWorker(chain, relayer, settings)
        await w.tick()
    async with SessionLocal() as db:
        orders = {o.order_hash: o for o in (await db.execute(select(Order).where(Order.condition_id == cid))).scalars()}
        jobs = (await db.execute(select(RelayJob).where(RelayJob.condition_id == cid).order_by(RelayJob.id))).scalars().all()
    bad_o, good_o, t_o = (orders[x.json()["order"]["orderHash"]] for x in (rb, rg, rt))
    assert bad_o.cancelled and not t_o.cancelled and not good_o.cancelled
    assert jobs[0].status == "failed" and "maker ctf not approved" in jobs[0].last_error
    assert len(jobs) == 2 and jobs[1].maker_hash == good_o.order_hash and jobs[1].taker_hash == t_o.order_hash
    assert jobs[1].status in ("pending", "sent") and jobs[1].fill_amount == 4_000_000
    assert t_o.filled == 4_000_000 and good_o.filled == 4_000_000 and bad_o.filled == 0


# ------------------------------------------------------------------ seed: status after a manual tick
async def test_status_last_tick_at_after_manual_tick_with_worker_disabled(api):
    op = Account.create()
    with patch.object(auth_router.settings, "operator_private_key", "0x" + bytes(op.key).hex()):
        op_token = await siwe_login(api, op)
    settings = _relay_settings(relayer_worker_enabled=False)
    worker_mod.stop_relayer_state()
    worker_mod._MANUAL_WORKER = None
    try:
        with relayer_env(settings, FakeChain()):
            before = (await api.get("/api/v1/relayer/status", headers=_auth(op_token))).json()
            tick = await api.post("/api/v1/relayer/tick", headers=_auth(op_token))
            after = (await api.get("/api/v1/relayer/status", headers=_auth(op_token))).json()
    finally:
        worker_mod._MANUAL_WORKER = None
    assert before["lastTickAt"] is None
    assert tick.status_code == 200 and "skipped" not in tick.json()
    assert after["workerRunning"] is False and isinstance(after["lastTickAt"], int)


# ------------------------------------------------------------------ schema upgrade
async def test_relayer_schema_drops_legacy_pair_key_on_sqlite(tmp_path):
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    try:
        async with eng.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE relay_jobs (id INTEGER PRIMARY KEY, kind VARCHAR(32), condition_id VARCHAR(66), "
                "taker_hash VARCHAR(66), maker_hash VARCHAR(66), fill_amount BIGINT, sender VARCHAR(42), "
                "status VARCHAR(16), attempts INTEGER, nonce BIGINT, tx_hash VARCHAR(66), tx_hashes JSON, raw_tx TEXT, "
                "gas_limit BIGINT, max_fee_per_gas BIGINT, max_priority_fee_per_gas BIGINT, block_number BIGINT, "
                "last_error TEXT, next_attempt_at BIGINT, created_at DATETIME, updated_at DATETIME, sent_at BIGINT, "
                "confirmed_at BIGINT, CONSTRAINT uq_relay_job_pair UNIQUE (taker_hash, maker_hash))"
            ))
            await conn.execute(text("CREATE INDEX ix_relay_jobs_status ON relay_jobs (status)"))
            await conn.execute(text(
                "INSERT INTO relay_jobs (kind, condition_id, taker_hash, maker_hash, fill_amount, sender, status, "
                "attempts, tx_hash, tx_hashes, raw_tx, last_error, next_attempt_at) VALUES "
                "('match_orders', '0x01', '0xt', '0xm', 5, '', 'failed', 1, '', '[]', '', 'x', 0)"
            ))
            await conn.run_sync(ensure_relayer_schema)
            await conn.run_sync(ensure_relayer_schema)  # idempotent
            uniques = await conn.run_sync(lambda c: inspect(c).get_unique_constraints("relay_jobs"))
            cols = await conn.run_sync(lambda c: {x["name"] for x in inspect(c).get_columns("relay_jobs")})
            await conn.execute(text(
                "INSERT INTO relay_jobs (kind, condition_id, taker_hash, maker_hash, fill_amount, sender, status, "
                "attempts, tx_hash, tx_hashes, raw_tx, last_error, next_attempt_at, matched_at) VALUES "
                "('match_orders', '0x01', '0xt', '0xm', 5, '', 'pending', 0, '', '[]', '', '', 0, 1)"
            ))
            rows = (await conn.execute(text("SELECT status, fill_amount FROM relay_jobs ORDER BY id"))).all()
        assert uniques == []
        assert {"matched_at", "first_sent_block", "nonce_consumed_at"} <= cols
        assert [tuple(r) for r in rows] == [("failed", 5), ("pending", 5)]
    finally:
        await eng.dispose()


def test_relayer_schema_drops_legacy_pair_key_on_postgres():
    conn = MagicMock()
    conn.dialect.name = "postgresql"
    insp = MagicMock()
    insp.has_table.return_value = True
    insp.get_columns.return_value = [{"name": n} for n in ("id", "taker_hash", "maker_hash", "matched_at")]
    with patch("app.relayer.schema.inspect", return_value=insp):
        ensure_relayer_schema(conn)
    sql = [str(c.args[0]) for c in conn.execute.call_args_list]
    assert "ALTER TABLE relay_jobs DROP CONSTRAINT IF EXISTS uq_relay_job_pair" in sql
    assert any("ADD COLUMN first_sent_block" in q for q in sql)
    assert not any("ADD COLUMN matched_at" in q for q in sql)
