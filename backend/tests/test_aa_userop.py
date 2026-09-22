import pytest
from httpx import ASGITransport, AsyncClient

from app.db import Base, engine, ensure_user_cdp_user_id
from app.main import create_app

AMM = "0x" + "ee" * 20
USDC = "0x" + "dd" * 20
SENDER = "0x" + "55" * 20
SELECTOR_EXECUTE = bytes.fromhex("b61d27f6")
SELECTOR_BUY_USDC = bytes.fromhex("a9c98025")


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


def _userop(call_data: str):
    return {
        "sender": SENDER,
        "nonce": 0,
        "initCode": "0x",
        "callData": call_data,
        "accountGasLimits": "0x" + "00" * 32,
        "preVerificationGas": 21000,
        "gasFees": "0x" + "00" * 32,
        "paymasterAndData": "0x",
        "signature": "0x",
    }


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
async def test_userop_gone(client):
    res = await client.post("/api/v1/aa/userop", json=_userop(_execute(AMM, _buy_calldata())))
    assert res.status_code == 410
