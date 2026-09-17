import pytest
from httpx import ASGITransport, AsyncClient

from app.db import Base, engine
from app.main import create_app


@pytest.fixture
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_siwe_and_create_market(client):
    addr = "0x" + "11" * 20
    n = await client.get(f"/api/v1/auth/nonce/{addr}")
    assert n.status_code == 200
    nonce = n.json()["nonce"]
    r = await client.post(
        "/api/v1/auth/siwe",
        json={"address": addr, "signature": "0x00", "message": f"login {addr} nonce {nonce}"},
    )
    assert r.status_code == 200
    token = r.json()["token"]

    listed = await client.get("/api/v1/markets")
    assert listed.status_code == 200

    onramp = await client.get(f"/api/v1/ramps/onramp-url?address={addr}")
    assert onramp.status_code == 200
    assert "coinbase" in onramp.json()["url"] or onramp.json()["provider"] == "coinbase"

    nav = await client.get("/api/v1/fee-vault/nav")
    assert nav.status_code == 200
    assert "nav" in nav.json()

    token = r.json()["token"]
    posted = await client.post(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "maker": addr,
            "isBuy": True,
            "conditionId": "0x" + "ab" * 32,
            "outcome": 0,
            "price": 500000,
            "amount": 1000000,
            "salt": 1,
            "nonce": 0,
            "expiry": 1999999999,
            "signature": "0x00",
            "orderHash": "0x" + "cd" * 32,
        },
    )
    assert posted.status_code == 200
    book = await client.get(f"/api/v1/orderbook/0x{'ab' * 32}")
    assert book.status_code == 200
    assert len(book.json()["bids"]) == 1
