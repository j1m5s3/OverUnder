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


FACTORY = "0x" + "fa" * 20
SELECTOR_CREATE_PERMISSIONLESS = bytes.fromhex("4e7d1a32")
SELECTOR_CREATE_PRIMARY = bytes.fromhex("aacf3f0f")


def _listing_settings():
    return _settings().model_copy(update={"factory_address": FACTORY})


LISTING_SALT = b"\x42" * 32
LISTING_QUESTION = "Will the listing batch be sponsored?"
LISTING_CID = "0x" + "4c" * 32


def _create_permissionless(question: str = LISTING_QUESTION, seed: int = 10_000_000) -> str:
    import eth_abi
    from web3 import Web3

    args = eth_abi.encode(
        ["bytes32", "uint256", "string", "bytes32", "uint256"],
        [LISTING_SALT, 2_000_000_000, question, Web3.keccak(text="criteria"), seed],
    )
    return "0x" + (SELECTOR_CREATE_PERMISSIONLESS + args).hex()


async def _prepared_listing(creator: str = SMART.lower()):
    """The MarketListing row /markets/listing/prepare would have stored for _create_permissionless()."""
    from web3 import Web3

    from app.db import SessionLocal
    from app.models import MarketListing

    async with SessionLocal() as session:
        row = await session.get(MarketListing, LISTING_CID)
        if row is not None:
            await session.delete(row)
            await session.flush()
        session.add(
            MarketListing(
                condition_id=LISTING_CID,
                creator=creator,
                salt="0x" + LISTING_SALT.hex(),
                question=LISTING_QUESTION,
                resolution_criteria="criteria",
                criteria_hash=Web3.to_hex(Web3.keccak(text="criteria")),
                close_time=2_000_000_000,
                seed_usdc=10_000_000,
                status="prepared",
            )
        )
        await session.commit()


async def _send(client, token, calls, settings):
    with patch("app.aa.router.get_settings", return_value=settings):
        with patch("app.aa.router.send_user_operation", new_callable=AsyncMock) as send:
            send.return_value = {"userOpHash": "0x" + "33" * 32}
            res = await client.post(
                "/api/v1/aa/cdp-send",
                headers={"Authorization": f"Bearer {token}"},
                json={"calls": calls},
            )
    return res, send


@pytest.mark.asyncio
async def test_cdp_send_allows_listing_batch(client):
    token, _ = await _cdp_auth(client)
    await _prepared_listing()
    calls = [
        {"to": USDC, "data": _approve(FACTORY), "value": 0},
        {"to": FACTORY, "data": _create_permissionless(), "value": 0},
    ]
    res, send = await _send(client, token, calls, _listing_settings())
    assert res.status_code == 200, res.text
    send.assert_awaited_once()
    assert [c["to"].lower() for c in send.await_args.kwargs["calls"]] == [USDC, FACTORY]


@pytest.mark.asyncio
async def test_cdp_send_rejects_listing_without_factory_config(client):
    token, _ = await _cdp_auth(client)
    for call in ({"to": USDC, "data": _approve(FACTORY), "value": 0}, {"to": FACTORY, "data": _create_permissionless(), "value": 0}):
        res, send = await _send(client, token, [call], _settings())
        assert res.status_code == 403
        send.assert_not_called()


@pytest.mark.asyncio
async def test_cdp_send_rejects_approve_unknown_spender(client):
    token, _ = await _cdp_auth(client)
    res, send = await _send(client, token, [{"to": USDC, "data": _approve(FORGED), "value": 0}], _listing_settings())
    assert res.status_code == 403
    send.assert_not_called()


@pytest.mark.asyncio
async def test_cdp_send_rejects_create_primary_via_cdp(client):
    token, _ = await _cdp_auth(client)
    create_primary = "0x" + (SELECTOR_CREATE_PRIMARY + b"\x00" * 128).hex()
    res, send = await _send(client, token, [{"to": FACTORY, "data": create_primary, "value": 0}], _listing_settings())
    assert res.status_code == 403
    send.assert_not_called()
    # createPermissionlessMarket sent to any other contract is not allowed either.
    res, send = await _send(client, token, [{"to": AMM, "data": _create_permissionless(), "value": 0}], _listing_settings())
    assert res.status_code == 403
    send.assert_not_called()


@pytest.mark.asyncio
async def test_cdp_send_rejects_listing_that_was_not_prepared(client):
    token, _ = await _cdp_auth(client)
    await _prepared_listing(creator=FORGED.lower())  # someone else's prepare row
    res, send = await _send(client, token, [{"to": FACTORY, "data": _create_permissionless(), "value": 0}], _listing_settings())
    assert res.status_code == 403
    assert res.json()["detail"] == "listing not prepared"
    send.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        _create_permissionless(question="Who is the best QB and should he feel underrated"),
        _create_permissionless(seed=10_000_001),
    ],
)
async def test_cdp_send_rejects_listing_edited_after_prepare(client, data):
    token, _ = await _cdp_auth(client)
    await _prepared_listing()
    res, send = await _send(client, token, [{"to": FACTORY, "data": data, "value": 0}], _listing_settings())
    assert res.status_code == 403
    assert res.json()["detail"] == "listing differs from the prepared listing"
    send.assert_not_called()
