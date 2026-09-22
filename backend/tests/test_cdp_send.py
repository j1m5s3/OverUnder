from unittest.mock import AsyncMock, patch

import jwt
import pytest
from eth_account import Account
from httpx import ASGITransport, AsyncClient

from app.cdp import CdpUser
from app.config import Settings, get_settings
from app.db import Base, engine, ensure_user_cdp_user_id
from app.main import create_app

USDC = "0x" + "dd" * 20
AMM = "0x" + "ee" * 20
CTF = "0x" + "01" * 20
EXCHANGE = "0x" + "02" * 20
SMART = "0x" + "ab" * 20
FORGED = "0x" + "cd" * 20

SELECTOR_APPROVE = bytes.fromhex("095ea7b3")
SELECTOR_SET_APPROVAL = bytes.fromhex("a22cb465")
SELECTOR_BUY_USDC = bytes.fromhex("a9c98025")
SELECTOR_SELL_USDC = bytes.fromhex("d4bd65f0")
SELECTOR_MATCH_ORDERS = bytes.fromhex("e9f2cd3e")


def _addr_word(addr: str) -> bytes:
    return int(addr, 16).to_bytes(32, "big")


def _approve(spender: str) -> str:
    return "0x" + (SELECTOR_APPROVE + _addr_word(spender) + (2**256 - 1).to_bytes(32, "big")).hex()


def _set_approval(operator: str) -> str:
    return "0x" + (SELECTOR_SET_APPROVAL + _addr_word(operator) + (1).to_bytes(32, "big")).hex()


def _buy() -> str:
    return "0x" + (
        SELECTOR_BUY_USDC
        + b"\x11" * 32
        + (1).to_bytes(32, "big")
        + (50 * 10**6).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
    ).hex()


def _sell() -> str:
    return "0x" + (
        SELECTOR_SELL_USDC
        + b"\x11" * 32
        + (1).to_bytes(32, "big")
        + (10 * 10**6).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
    ).hex()


def _settings():
    return Settings(
        usdc_address=USDC,
        ctf_address=CTF,
        amm_address=AMM,
        exchange_address=EXCHANGE,
        cdp_project_id="proj",
        cdp_api_key_id="key-id",
        cdp_api_key_secret="key-secret",
        jwt_secret=get_settings().jwt_secret,
    )


@pytest.fixture
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(ensure_user_cdp_user_id)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _cdp_auth(client, smart: str = SMART):
    cdp_user = CdpUser(user_id="end-user-1", smart_account=smart)
    with patch("app.auth.router.validate_access_token", new_callable=AsyncMock, return_value=cdp_user):
        auth = await client.post(
            "/api/v1/auth/cdp",
            json={"accessToken": "cdp-access-token", "address": FORGED},
        )
    assert auth.status_code == 200
    return auth.json()["token"], auth.json()["address"]


@pytest.mark.asyncio
async def test_cdp_send_allows_trade_batch(client):
    token, address = await _cdp_auth(client)
    assert address == SMART.lower()
    claims = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    assert claims["sub"] == SMART.lower()
    calls = [
        {"to": USDC, "data": _approve(AMM), "value": 0},
        {"to": CTF, "data": _set_approval(AMM), "value": "0"},
        {"to": AMM, "data": _buy(), "value": 0},
        {"to": AMM, "data": _sell(), "value": 0},
    ]
    with patch("app.aa.router.get_settings", return_value=_settings()):
        with patch("app.aa.router.send_user_operation", new_callable=AsyncMock) as send:
            send.return_value = {"userOpHash": "0x" + "11" * 32}
            res = await client.post(
                "/api/v1/aa/cdp-send",
                headers={"Authorization": f"Bearer {token}"},
                json={"calls": calls, "address": FORGED},
            )
    assert res.status_code == 200
    send.assert_awaited_once()
    kwargs = send.await_args.kwargs
    assert kwargs["user_id"] == "end-user-1"
    assert kwargs["address"].lower() == SMART.lower()
    assert kwargs["calls"][0]["value"] == "0"
    assert all(c["value"] == "0" for c in kwargs["calls"])


@pytest.mark.asyncio
async def test_cdp_send_rejects_match_orders(client):
    token, _ = await _cdp_auth(client)
    match = "0x" + (SELECTOR_MATCH_ORDERS + b"\x00" * 64).hex()
    with patch("app.aa.router.get_settings", return_value=_settings()):
        with patch("app.aa.router.send_user_operation", new_callable=AsyncMock) as send:
            res = await client.post(
                "/api/v1/aa/cdp-send",
                headers={"Authorization": f"Bearer {token}"},
                json={"calls": [{"to": EXCHANGE, "data": match, "value": 0}]},
            )
    assert res.status_code == 403
    send.assert_not_called()


@pytest.mark.asyncio
async def test_cdp_send_rejects_nonzero_value(client):
    token, _ = await _cdp_auth(client)
    with patch("app.aa.router.get_settings", return_value=_settings()):
        with patch("app.aa.router.send_user_operation", new_callable=AsyncMock) as send:
            res = await client.post(
                "/api/v1/aa/cdp-send",
                headers={"Authorization": f"Bearer {token}"},
                json={"calls": [{"to": AMM, "data": _buy(), "value": 1}]},
            )
    assert res.status_code == 403
    send.assert_not_called()


@pytest.mark.asyncio
async def test_cdp_send_rejects_unrelated_eoa_session(client):
    acct = Account.create()
    from eth_account.messages import encode_defunct

    n1 = await client.get(f"/api/v1/auth/nonce/{acct.address}")
    nonce = n1.json()["nonce"]
    message = f"Sign in to OverUnder\n\nAddress: {acct.address}\nNonce: {nonce}"
    signed = acct.sign_message(encode_defunct(text=message))
    auth = await client.post(
        "/api/v1/auth/siwe",
        json={"address": acct.address, "signature": signed.signature.hex(), "message": message},
    )
    assert auth.status_code == 200
    token = auth.json()["token"]
    with patch("app.aa.router.get_settings", return_value=_settings()):
        with patch("app.aa.router.send_user_operation", new_callable=AsyncMock) as send:
            res = await client.post(
                "/api/v1/aa/cdp-send",
                headers={"Authorization": f"Bearer {token}"},
                json={"calls": [{"to": AMM, "data": _buy(), "value": 0}]},
            )
    assert res.status_code == 403
    send.assert_not_called()
