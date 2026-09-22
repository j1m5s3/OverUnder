import pytest
from httpx import ASGITransport, AsyncClient
from eth_account import Account
from eth_account.messages import encode_defunct

from app.db import Base, engine, ensure_user_cdp_user_id
from app.main import create_app


@pytest.fixture
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(ensure_user_cdp_user_id)
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
async def test_cdp_session_uses_smart_account_and_ignores_client_address(client):
    from unittest.mock import AsyncMock, patch

    from app.cdp import CdpUser

    smart = "0x" + "ab" * 20
    forged = "0x" + "cd" * 20
    cdp_user = CdpUser(user_id="end-user-1", smart_account=smart)
    with patch("app.auth.router.validate_access_token", new_callable=AsyncMock, return_value=cdp_user):
        auth = await client.post(
            "/api/v1/auth/cdp",
            json={"accessToken": "cdp-access-token", "address": forged},
        )
    assert auth.status_code == 200
    body = auth.json()
    assert body["address"] == smart.lower()
    assert body["token"]
    import jwt
    from app.config import get_settings

    claims = jwt.decode(body["token"], get_settings().jwt_secret, algorithms=["HS256"])
    assert claims["sub"] == smart.lower()
    assert claims["op"] is False


@pytest.mark.asyncio
async def test_cdp_fails_closed_without_config(client):
    from unittest.mock import patch

    from app.config import Settings

    with patch("app.cdp.get_settings", return_value=Settings(cdp_project_id="", cdp_api_key_secret="")):
        auth = await client.post(
            "/api/v1/auth/cdp",
            json={"accessToken": "cdp-access-token", "address": "0x" + "11" * 20},
        )
    assert auth.status_code == 503
