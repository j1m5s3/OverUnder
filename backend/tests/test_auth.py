import pytest
from httpx import ASGITransport, AsyncClient
from eth_account import Account
from eth_account.messages import encode_defunct

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
async def test_siwe_ecrecover_and_nonce_replay(client):
    acct = Account.create()
    addr = acct.address
    
    n1 = await client.get(f"/api/v1/auth/nonce/{addr}")
    assert n1.status_code == 200
    nonce = n1.json()["nonce"]
    
    message = f"Sign in to OverUnder\n\nAddress: {addr}\nNonce: {nonce}"
    message_encoded = encode_defunct(text=message)
    signed = acct.sign_message(message_encoded)
    
    auth1 = await client.post(
        "/api/v1/auth/siwe",
        json={
            "address": addr,
            "signature": signed.signature.hex(),
            "message": message,
        },
    )
    assert auth1.status_code == 200
    token1 = auth1.json()["token"]
    assert token1
    
    auth2 = await client.post(
        "/api/v1/auth/siwe",
        json={
            "address": addr,
            "signature": signed.signature.hex(),
            "message": message,
        },
    )
    assert auth2.status_code == 401


@pytest.mark.asyncio
async def test_siwe_rejects_bad_signature(client):
    acct = Account.create()
    addr = acct.address
    
    n = await client.get(f"/api/v1/auth/nonce/{addr}")
    nonce = n.json()["nonce"]
    message = f"Sign in to OverUnder\n\nAddress: {addr}\nNonce: {nonce}"
    
    auth = await client.post(
        "/api/v1/auth/siwe",
        json={
            "address": addr,
            "signature": "0x" + "00" * 65,
            "message": message,
        },
    )
    assert auth.status_code == 400


@pytest.mark.asyncio
async def test_siwe_rejects_empty_signature(client):
    addr = "0x" + "11" * 20
    
    n = await client.get(f"/api/v1/auth/nonce/{addr}")
    nonce = n.json()["nonce"]
    message = f"Sign in to OverUnder\n\nAddress: {addr}\nNonce: {nonce}"
    
    auth = await client.post(
        "/api/v1/auth/siwe",
        json={
            "address": addr,
            "signature": "0x",
            "message": message,
        },
    )
    assert auth.status_code == 400


@pytest.mark.asyncio
async def test_privy_requires_config(client):
    auth = await client.post(
        "/api/v1/auth/privy",
        json={"token": "fake-token"},
    )
    assert auth.status_code == 503
