"""POST /orders verification, matching, relay enqueue/cancel and the relayer endpoints."""

import secrets
import time
from unittest.mock import patch

import pytest
from eth_account import Account
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

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
from app.relayer.queue import enqueue_match
from _relayer_helpers import EXCHANGE, FakeChain, make_settings, order_body, rand_cid, relayer_env, siwe_login


@pytest.fixture
async def api():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(ensure_live_score_facts)
        await conn.run_sync(ensure_user_cdp_user_id)
        await conn.run_sync(ensure_trade_relay_columns)
        await conn.run_sync(ensure_order_wide_columns)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _relay_settings(relayer=None, **kw):
    relayer = relayer or Account.create()
    return make_settings(relayer_enabled=True, relayer_private_key="0x" + bytes(relayer.key).hex(), **kw)


async def _order_count(maker: str) -> int:
    async with SessionLocal() as db:
        return (await db.execute(select(func.count()).select_from(Order).where(Order.maker == maker.lower()))).scalar()


async def _post(api, token, body):
    return await api.post("/api/v1/orders", headers=_auth(token), json=body)


def _bad_sig(acct, cid):
    body = order_body(acct, cid=cid, is_buy=True)
    body["signature"] = "0x"
    return body


def _other_signer(acct, cid):
    return order_body(acct, cid=cid, is_buy=True, signer=Account.create())


def _hash_mismatch(acct, cid):
    body = order_body(acct, cid=cid, is_buy=True)
    body["orderHash"] = "0x" + "cd" * 32
    return body


def _expired(acct, cid):
    return order_body(acct, cid=cid, is_buy=True, expiry=int(time.time()) + 5)


def _outcome_2(acct, cid):
    return order_body(acct, cid=cid, is_buy=True, outcome=2)


def _price_0(acct, cid):
    return order_body(acct, cid=cid, is_buy=True, price=0)


@pytest.mark.parametrize(
    "make,detail",
    [
        (_bad_sig, "bad signature"),
        (_other_signer, "bad signature"),
        (_hash_mismatch, "orderHash mismatch"),
        (_expired, "order expired"),
        (_outcome_2, "outcome must be 0 or 1"),
        (_price_0, "price must be in (0, 1000000]"),
    ],
)
async def test_post_rejects_and_writes_nothing(api, make, detail):
    acct = Account.create()
    token = await siwe_login(api, acct)
    with relayer_env(make_settings(), FakeChain()):
        r = await _post(api, token, make(acct, rand_cid()))
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == detail
    assert await _order_count(acct.address) == 0


async def test_post_503_without_exchange(api):
    acct = Account.create()
    token = await siwe_login(api, acct)
    body = order_body(acct, cid=rand_cid(), is_buy=True)
    with relayer_env(make_settings(), FakeChain(), domain=None):
        r = await _post(api, token, body)
    assert r.status_code == 503
    assert r.json()["detail"] == "exchange not configured"
    assert await _order_count(acct.address) == 0


async def test_post_503_when_enabled_without_key(api):
    acct = Account.create()
    token = await siwe_login(api, acct)
    body = order_body(acct, cid=rand_cid(), is_buy=True)
    with relayer_env(make_settings(relayer_enabled=True, relayer_private_key=""), FakeChain()):
        r = await _post(api, token, body)
    assert r.status_code == 503
    assert r.json()["detail"] == "relayer misconfigured"
    assert await _order_count(acct.address) == 0


async def test_maker_must_match_session(api):
    acct = Account.create()
    token = await siwe_login(api, acct)
    other = Account.create()
    with relayer_env(make_settings(), FakeChain()):
        r = await _post(api, token, order_body(other, cid=rand_cid(), is_buy=True))
    assert r.status_code == 400
    assert r.json()["detail"] == "maker must match session"


async def test_disabled_relayer_matches_offchain(api):
    buyer, seller = Account.create(), Account.create()
    tb, ts = await siwe_login(api, buyer), await siwe_login(api, seller)
    cid = rand_cid()
    with relayer_env(make_settings(), FakeChain()):
        r1 = await _post(api, tb, order_body(buyer, cid=cid, is_buy=True, price=600_000, amount=4_000_000))
        assert r1.status_code == 200, r1.text
        assert r1.json()["fills"] == []
        r2 = await _post(api, ts, order_body(seller, cid=cid, is_buy=False, price=550_000, amount=3_000_000))
        assert r2.status_code == 200, r2.text
        dup = await _post(api, tb, order_body(buyer, cid=cid, is_buy=True, price=600_000, amount=4_000_000, salt=1))
        dup2 = await _post(api, tb, order_body(buyer, cid=cid, is_buy=True, price=600_000, amount=4_000_000, salt=1))
    assert dup.status_code == 200 and dup2.status_code == 409
    fills = r2.json()["fills"]
    assert len(fills) == 1
    f = fills[0]
    assert f["fillAmount"] == 3_000_000
    assert f["volume"] == 3_000_000 * 600_000 // 1_000_000
    assert f["status"] == "offchain" and f["relayJobId"] is None
    async with SessionLocal() as db:
        trades = (await db.execute(select(Trade).where(Trade.condition_id == cid))).scalars().all()
        jobs = (await db.execute(select(RelayJob).where(RelayJob.condition_id == cid))).scalars().all()
        orders = {o.order_hash: o for o in (await db.execute(select(Order).where(Order.condition_id == cid))).scalars()}
    assert [t.status for t in trades] == ["offchain"]
    assert jobs == []
    assert orders[r2.json()["order"]["orderHash"]].filled == 3_000_000
    assert orders[r1.json()["order"]["orderHash"]].filled == 3_000_000
    assert orders[dup.json()["order"]["orderHash"]].filled == 0
    book = (await api.get(f"/api/v1/orderbook/{cid}")).json()
    assert len(book["bids"]) == 2 and book["asks"] == []
    trades_api = (await api.get(f"/api/v1/trades/{cid}")).json()
    assert trades_api[0]["status"] == "offchain"


async def test_self_match_is_skipped(api):
    acct = Account.create()
    token = await siwe_login(api, acct)
    cid = rand_cid()
    with relayer_env(make_settings(), FakeChain()):
        await _post(api, token, order_body(acct, cid=cid, is_buy=True, price=600_000))
        r = await _post(api, token, order_body(acct, cid=cid, is_buy=False, price=500_000))
    assert r.status_code == 200
    assert r.json()["fills"] == []


async def test_preflight_rejects_unapproved_seller(api):
    seller = Account.create()
    token = await siwe_login(api, seller)
    cid = rand_cid()
    chain = FakeChain()
    chain.ctf[(seller.address.lower(), cid, 0)] = 10_000_000
    with relayer_env(_relay_settings(), chain):
        r = await _post(api, token, order_body(seller, cid=cid, is_buy=False))
    assert r.status_code == 400
    assert r.json() == {"detail": "preflight failed", "reasons": ["ctf not approved"]}
    assert await _order_count(seller.address) == 0


async def test_preflight_rejects_short_allowance(api):
    buyer = Account.create()
    token = await siwe_login(api, buyer)
    chain = FakeChain()
    chain.usdc[buyer.address.lower()] = 10**12
    chain.allowances[(buyer.address.lower(), EXCHANGE.lower())] = 1
    with relayer_env(_relay_settings(), chain):
        r = await _post(api, token, order_body(buyer, cid=rand_cid(), is_buy=True, price=500_000, amount=2_000_000))
    assert r.status_code == 400
    # 2e6 * 0.5 = 1e6 volume + 0.75% fee = 1_007_500
    assert r.json()["reasons"] == ["usdc allowance < 1007500"]


async def test_preflight_rpc_error_is_503(api):
    buyer = Account.create()
    token = await siwe_login(api, buyer)
    chain = FakeChain()
    chain.view_error = ConnectionError("rpc down")
    with relayer_env(_relay_settings(), chain):
        r = await _post(api, token, order_body(buyer, cid=rand_cid(), is_buy=True))
    assert r.status_code == 503
    assert await _order_count(buyer.address) == 0


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


async def test_enabled_relayer_enqueues_one_job(api):
    chain, cid = FakeChain(), rand_cid()
    with relayer_env(_relay_settings(), chain):
        buyer, seller, _, _, buy_order, res = await _crossing_pair(api, chain, cid)
    fill = res["fills"][0]
    assert fill["status"] == "pending" and fill["relayJobId"]
    async with SessionLocal() as db:
        jobs = (await db.execute(select(RelayJob).where(RelayJob.condition_id == cid))).scalars().all()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.id == fill["relayJobId"]
        assert job.status == "pending"
        assert job.taker_hash == res["order"]["orderHash"]
        assert job.maker_hash == buy_order["orderHash"]
        assert job.fill_amount == 5_000_000
        trade = (await db.execute(select(Trade).where(Trade.relay_job_id == job.id))).scalar_one()
        assert trade.status == "pending"
        taker = (await db.execute(select(Order).where(Order.order_hash == job.taker_hash))).scalar_one()
        maker = (await db.execute(select(Order).where(Order.order_hash == job.maker_hash))).scalar_one()
        # Every match call is its own job: a re-match of the pair never reuses (or merges into) this one.
        again = await enqueue_match(db, taker, maker, 1_000_000)
        await db.commit()
        assert again.id != job.id and again.status == "pending" and again.fill_amount == 1_000_000
        assert again.matched_at > 0
        n = (await db.execute(select(func.count()).select_from(RelayJob).where(RelayJob.condition_id == cid))).scalar()
        assert n == 2
        await db.execute(update(RelayJob).where(RelayJob.condition_id == cid).values(status="cancelled"))
        await db.commit()


async def test_cancel_rolls_back_pending_job(api):
    chain, cid = FakeChain(), rand_cid()
    with relayer_env(_relay_settings(), chain):
        buyer, seller, tb, ts, buy_order, res = await _crossing_pair(api, chain, cid)
        job_id = res["fills"][0]["relayJobId"]
        r = await api.delete(f"/api/v1/orders/{buy_order['orderHash']}", headers=_auth(tb))
    assert r.status_code == 200
    assert r.json()["cancelledJobs"] == [job_id] and r.json()["inFlightJobs"] == []
    async with SessionLocal() as db:
        job = await db.get(RelayJob, job_id)
        assert job.status == "cancelled"
        orders = (await db.execute(select(Order).where(Order.condition_id == cid))).scalars().all()
        assert all(o.filled == 0 for o in orders)
        trade = (await db.execute(select(Trade).where(Trade.relay_job_id == job_id))).scalar_one()
        assert trade.status == "failed"


async def test_cancel_reports_in_flight_job(api):
    chain, cid = FakeChain(), rand_cid()
    with relayer_env(_relay_settings(), chain):
        buyer, seller, tb, ts, buy_order, res = await _crossing_pair(api, chain, cid)
        job_id = res["fills"][0]["relayJobId"]
        async with SessionLocal() as db:
            await db.execute(update(RelayJob).where(RelayJob.id == job_id).values(status="sent"))
            await db.commit()
        r = await api.delete(f"/api/v1/orders/{res['order']['orderHash']}", headers=_auth(ts))
    assert r.json()["inFlightJobs"] == [job_id]
    async with SessionLocal() as db:
        assert (await db.get(RelayJob, job_id)).status == "sent"
        await db.execute(update(RelayJob).where(RelayJob.id == job_id).values(status="cancelled"))
        await db.commit()


async def test_expired_resting_order_not_matched_or_listed(api):
    cid = rand_cid()
    stale = Account.create()
    async with SessionLocal() as db:
        db.add(
            Order(
                order_hash="0x" + secrets.token_hex(32),
                maker=stale.address.lower(), condition_id=cid, is_buy=False, outcome=0,
                price=100_000, amount=5_000_000, filled=0, salt="0x1", nonce=0,
                expiry=int(time.time()) - 10, signature="0x" + "00" * 65, cancelled=False,
            )
        )
        await db.commit()
    buyer = Account.create()
    token = await siwe_login(api, buyer)
    with relayer_env(make_settings(), FakeChain()):
        r = await _post(api, token, order_body(buyer, cid=cid, is_buy=True, price=900_000))
    assert r.status_code == 200
    assert r.json()["fills"] == []
    book = (await api.get(f"/api/v1/orderbook/{cid}")).json()
    assert book["asks"] == [] and len(book["bids"]) == 1


async def test_round_trip_huge_salt_and_amount(api):
    acct = Account.create()
    token = await siwe_login(api, acct)
    cid = rand_cid()
    big_salt = 2**255 + 7
    body = order_body(acct, cid=cid, is_buy=True, salt=big_salt, amount=5_000_000_000, price=999_999)
    body_hex = order_body(acct, cid=cid, is_buy=False, salt=2**200 + 1, amount=5_000_000_000, price=999_999)
    body_hex["salt"] = hex(2**200 + 1)
    with relayer_env(make_settings(), FakeChain()):
        r = await _post(api, token, body)
        r2 = await _post(api, token, body_hex)
    assert r.status_code == 200, r.text
    assert r2.status_code == 200, r2.text
    order = r.json()["order"]
    assert order["salt"] == str(big_salt)
    assert order["amount"] == 5_000_000_000
    assert r2.json()["order"]["salt"] == str(2**200 + 1)
    async with SessionLocal() as db:
        row = (await db.execute(select(Order).where(Order.order_hash == order["orderHash"]))).scalar_one()
    assert row.salt == hex(big_salt)
    assert row.amount == 5_000_000_000


async def test_job_endpoint_access(api):
    chain, cid = FakeChain(), rand_cid()
    with relayer_env(_relay_settings(), chain):
        buyer, seller, tb, ts, buy_order, res = await _crossing_pair(api, chain, cid)
    job_id = res["fills"][0]["relayJobId"]
    async with SessionLocal() as db:
        await db.execute(update(RelayJob).where(RelayJob.id == job_id).values(raw_tx="0xdeadbeef"))
        await db.commit()
    stranger = await siwe_login(api, Account.create())
    assert (await api.get(f"/api/v1/relayer/jobs/{job_id}")).status_code == 401
    assert (await api.get(f"/api/v1/relayer/jobs/{job_id}", headers=_auth(stranger))).status_code == 404
    for tok in (tb, ts):
        r = await api.get(f"/api/v1/relayer/jobs/{job_id}", headers=_auth(tok))
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == job_id and body["status"] == "pending"
        assert "raw_tx" not in body and "rawTx" not in body and "deadbeef" not in r.text
    async with SessionLocal() as db:
        await db.execute(update(RelayJob).where(RelayJob.id == job_id).values(status="cancelled"))
        await db.commit()


async def test_operator_endpoints(api):
    user_token = await siwe_login(api, Account.create())
    op = Account.create()
    with patch.object(auth_router.settings, "operator_private_key", "0x" + bytes(op.key).hex()):
        op_token = await siwe_login(api, op)
    relayer = Account.create()
    settings = _relay_settings(relayer)
    chain = FakeChain()
    with relayer_env(settings, chain):
        for path in ("/api/v1/relayer/jobs", "/api/v1/relayer/status"):
            assert (await api.get(path, headers=_auth(user_token))).status_code == 403
        assert (await api.post("/api/v1/relayer/tick", headers=_auth(user_token))).status_code == 403

        cid = rand_cid()
        buyer, seller, tb, ts, buy_order, res = await _crossing_pair(api, chain, cid)
        job_id = res["fills"][0]["relayJobId"]

        status = await api.get("/api/v1/relayer/status", headers=_auth(op_token))
        assert status.status_code == 200
        st = status.json()
        assert st["enabled"] is True and st["ready"] is True
        assert st["relayerAddress"] == relayer.address
        assert st["counts"]["pending"] >= 1
        assert st["ethBalanceWei"] == chain.balance

        listed = await api.get("/api/v1/relayer/jobs?status=pending", headers=_auth(op_token))
        assert listed.status_code == 200
        assert job_id in [j["id"] for j in listed.json()["jobs"]]
        assert all("rawTx" not in j for j in listed.json()["jobs"])
        assert (await api.get("/api/v1/relayer/jobs?status=bogus", headers=_auth(op_token))).status_code == 400

        tick = await api.post("/api/v1/relayer/tick", headers=_auth(op_token))
        assert tick.status_code == 200, tick.text
        assert tick.json()["sent"] >= 1
        mine = (await api.get(f"/api/v1/relayer/jobs/{job_id}", headers=_auth(op_token))).json()
        assert mine["status"] == "sent" and mine["txHash"].startswith("0x")
        assert any(tx["hash"] == mine["txHash"] for tx in chain.sent)

    with relayer_env(make_settings(), chain):
        assert (await api.post("/api/v1/relayer/tick", headers=_auth(op_token))).status_code == 409
