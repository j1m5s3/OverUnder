import pytest
from unittest.mock import patch
from httpx import ASGITransport, AsyncClient
from eth_account import Account
from eth_account.messages import encode_defunct

from app.db import Base, engine
from app.main import create_app

PAYMASTER = "0x" + "aa" * 20
FACTORY = "0x" + "bb" * 20
ENTRYPOINT = "0x" + "cc" * 20
USDC = "0x" + "dd" * 20
AMM = "0x" + "ee" * 20
CTF = "0x" + "01" * 20
EXCHANGE = "0x" + "02" * 20
ORACLE = "0x" + "03" * 20
VAULT = "0x" + "04" * 20
SENDER = "0x" + "55" * 20

SELECTOR_EXECUTE = bytes.fromhex("b61d27f6")
SELECTOR_EXECUTE_BATCH = bytes.fromhex("47e1da2a")
SELECTOR_BUY_USDC = bytes.fromhex("a9c98025")
SELECTOR_MATCH_ORDERS = bytes.fromhex("e9f2cd3e")


def _execute(target: str, data: bytes, value: int = 0) -> str:
    packed = (
        SELECTOR_EXECUTE
        + int(target, 16).to_bytes(32, "big")
        + int(value).to_bytes(32, "big")
        + (96).to_bytes(32, "big")
        + len(data).to_bytes(32, "big")
        + data
    )
    return "0x" + packed.hex()


def _buy_calldata() -> bytes:
    return (
        SELECTOR_BUY_USDC
        + b"\x11" * 32
        + (1).to_bytes(32, "big")
        + (50 * 10**6).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
    )


@pytest.fixture
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _auth(client):
    acct = Account.create()
    addr = acct.address
    n1 = await client.get(f"/api/v1/auth/nonce/{addr}")
    nonce = n1.json()["nonce"]
    message = f"Sign in to OverUnder\n\nAddress: {addr}\nNonce: {nonce}"
    signed = acct.sign_message(encode_defunct(text=message))
    auth = await client.post(
        "/api/v1/auth/siwe",
        json={"address": addr, "signature": signed.signature.hex(), "message": message},
    )
    assert auth.status_code == 200
    return acct, auth.json()["token"]


def _settings(operator_key: str):
    from app.config import Settings

    return Settings(
        paymaster_address=PAYMASTER,
        account_factory_address=FACTORY,
        entrypoint_address=ENTRYPOINT,
        operator_private_key=operator_key,
        usdc_address=USDC,
        ctf_address=CTF,
        amm_address=AMM,
        exchange_address=EXCHANGE,
        oracle_address=ORACLE,
        fee_vault_address=VAULT,
        chain_id=31337,
        anvil_rpc_url="http://127.0.0.1:1",
        bundler_url="",
    )


def _userop(call_data: str, signature: str = "0x", paymaster_and_data: str = "0x", sender: str = SENDER):
    return {
        "sender": sender,
        "nonce": 0,
        "initCode": "0x",
        "callData": call_data,
        "accountGasLimits": "0x" + "00" * 32,
        "preVerificationGas": 21000,
        "gasFees": "0x" + "00" * 32,
        "paymasterAndData": paymaster_and_data,
        "signature": signature,
    }


@pytest.mark.asyncio
async def test_userop_stamps_execute_buy(client):
    acct, token = await _auth(client)
    operator = Account.create()
    call_data = _execute(AMM, _buy_calldata())
    with patch("app.aa.router.get_settings", return_value=_settings(operator.key.hex())):
        with patch("app.aa.router._owned_by_user", return_value=True):
            res = await client.post(
                "/api/v1/aa/userop",
                headers={"Authorization": f"Bearer {token}"},
                json=_userop(call_data),
            )
    assert res.status_code == 200
    body = res.json()
    pmd = bytes.fromhex(body["paymasterAndData"].removeprefix("0x"))
    assert len(pmd) >= 129
    assert pmd[:20] == bytes.fromhex(PAYMASTER[2:])
    assert body["txHash"] is None


@pytest.mark.asyncio
async def test_signed_userop_503_when_bundler_silent(client):
    acct, token = await _auth(client)
    operator = Account.create()
    call_data = _execute(AMM, _buy_calldata())
    settings = _settings(operator.key.hex())
    with patch("app.aa.router.get_settings", return_value=settings):
        with patch("app.aa.router._owned_by_user", return_value=True):
            stamped = await client.post(
                "/api/v1/aa/userop",
                headers={"Authorization": f"Bearer {token}"},
                json=_userop(call_data),
            )
            assert stamped.status_code == 200
            paymaster_and_data = stamped.json()["paymasterAndData"]
            with patch("app.aa.router.submit_handle_ops", return_value=None):
                res = await client.post(
                    "/api/v1/aa/userop",
                    headers={"Authorization": f"Bearer {token}"},
                    json=_userop(call_data, signature="0x" + "11" * 65, paymaster_and_data=paymaster_and_data),
                )
    assert res.status_code == 503


@pytest.mark.asyncio
async def test_userop_denies_match_orders(client):
    acct, token = await _auth(client)
    operator = Account.create()
    call_data = _execute(EXCHANGE, SELECTOR_MATCH_ORDERS + b"\x00" * 200)
    with patch("app.aa.router.get_settings", return_value=_settings(operator.key.hex())):
        with patch("app.aa.router._owned_by_user", return_value=True):
            res = await client.post(
                "/api/v1/aa/userop",
                headers={"Authorization": f"Bearer {token}"},
                json=_userop(call_data),
            )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_userop_denies_execute_batch(client):
    acct, token = await _auth(client)
    operator = Account.create()
    batched = "0x" + (SELECTOR_EXECUTE_BATCH + b"\x00" * 64).hex()
    with patch("app.aa.router.get_settings", return_value=_settings(operator.key.hex())):
        with patch("app.aa.router._owned_by_user", return_value=True):
            res = await client.post(
                "/api/v1/aa/userop",
                headers={"Authorization": f"Bearer {token}"},
                json=_userop(batched),
            )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_userop_denies_unknown_selector(client):
    acct, token = await _auth(client)
    operator = Account.create()
    call_data = _execute(AMM, bytes.fromhex("deadbeef") + b"\x00" * 32)
    with patch("app.aa.router.get_settings", return_value=_settings(operator.key.hex())):
        with patch("app.aa.router._owned_by_user", return_value=True):
            res = await client.post(
                "/api/v1/aa/userop",
                headers={"Authorization": f"Bearer {token}"},
                json=_userop(call_data),
            )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_userop_denies_wrong_owner(client):
    acct, token = await _auth(client)
    operator = Account.create()
    call_data = _execute(AMM, _buy_calldata())
    with patch("app.aa.router.get_settings", return_value=_settings(operator.key.hex())):
        with patch("app.aa.router._owned_by_user", return_value=False):
            res = await client.post(
                "/api/v1/aa/userop",
                headers={"Authorization": f"Bearer {token}"},
                json=_userop(call_data),
            )
    assert res.status_code == 403
