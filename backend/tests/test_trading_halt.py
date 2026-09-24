"""TRADING_HALT_AT_CLOSE: quotes 409 before RPC, MarketPublic halt fields, cdp-send gate."""

import time
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete
from web3.exceptions import ContractLogicError

from app.amm import router as amm_router
from app.cdp import CdpUser
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.markets.trading import norm_cid, trading_halt_reason
from app.models import Market, MarketListing, PricePoint

CLOSED = "0x" + "7a" * 32
OPEN = "0x" + "7b" * 32
RESOLVED = "0x" + "7c" * 32
UNKNOWN = "0x" + "7d" * 32
TRADE_CID = "0x" + "11" * 32  # the cid baked into the cdp-send buy calldata below
USER_REJECTED = "0x" + "7e" * 32
USER_INDEXED = "0x" + "7f" * 32
USER_CONFIRMED = "0x" + "80" * 32
SEED_IDS = (CLOSED, OPEN, RESOLVED, TRADE_CID, USER_REJECTED, USER_INDEXED, USER_CONFIRMED)

USDC = "0x" + "dd" * 20
AMM = "0x" + "ee" * 20
CTF = "0x" + "01" * 20
SMART = "0x" + "ab" * 20


def _row(cid: str, close_time: int, *, resolved: bool = False, market_type: int = 0) -> Market:
    return Market(
        condition_id=cid,
        parent_condition_id="",
        question=f"Halt test {cid[:6]}?",
        resolution_criteria="",
        market_type=market_type,
        close_time=close_time,
        resolved=resolved,
        payout_yes=1 if resolved else 0,
    )


async def _clean(session) -> None:
    await session.execute(delete(Market).where(Market.condition_id.in_(SEED_IDS)))
    await session.execute(delete(MarketListing).where(MarketListing.condition_id.in_(SEED_IDS)))
    await session.execute(delete(PricePoint).where(PricePoint.condition_id.in_(SEED_IDS)))


@pytest.fixture(autouse=True)
def _fresh_amm_contract_cache():
    amm_router._amm_contract.cache_clear()
    yield
    amm_router._amm_contract.cache_clear()


@pytest.fixture
async def halt_client(client):
    now = int(time.time())
    async with SessionLocal() as session:
        await _clean(session)
        session.add_all(
            [
                _row(CLOSED, now - 60),
                _row(OPEN, now + 3600),
                _row(RESOLVED, now + 3600, resolved=True),
                _row(TRADE_CID, now - 60),
                _row(USER_REJECTED, now + 3600, market_type=2),
                _row(USER_INDEXED, now + 3600, market_type=2),
                _row(USER_CONFIRMED, now + 3600, market_type=2),
                MarketListing(condition_id=USER_REJECTED, creator="0x" + "4a" * 20, status="rejected"),
                MarketListing(condition_id=USER_INDEXED, creator="0x" + "4a" * 20, status="indexed"),
                MarketListing(condition_id=USER_CONFIRMED, creator="0x" + "4a" * 20, status="confirmed"),
            ]
        )
        await session.commit()
    yield client
    async with SessionLocal() as session:
        await _clean(session)
        await session.commit()


def _settings(flag: bool, **overrides) -> Settings:
    return Settings(trading_halt_at_close=flag, amm_address="", jwt_secret=get_settings().jwt_secret, **overrides)


def _no_rpc(*_a, **_k):
    raise AssertionError("quote must not touch contract config/RPC when halted")


def test_default_flag_is_on():
    assert Settings().trading_halt_at_close is True
    assert norm_cid("0XAB") == "0xab"


@pytest.mark.asyncio
async def test_quote_closed_market_409_before_rpc(halt_client):
    with patch("app.amm.router.get_settings", return_value=_settings(True)), patch(
        "app.contract_addresses.get_contract_addresses", side_effect=_no_rpc
    ):
        r = await halt_client.get(f"/api/v1/amm/{CLOSED}/quote", params={"buy_yes": True, "usdc_in": 1_000_000})
        assert r.status_code == 409
        assert r.json() == {"detail": "market closed"}
        r = await halt_client.get(f"/api/v1/amm/{CLOSED.upper().replace('0X', '0x')}/quote")
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_sell_quote_closed_market_409(halt_client):
    with patch("app.amm.router.get_settings", return_value=_settings(True)), patch(
        "app.contract_addresses.get_contract_addresses", side_effect=_no_rpc
    ):
        r = await halt_client.get(f"/api/v1/amm/{CLOSED}/quote", params={"sell_yes": True, "token_amount": 5})
    assert r.status_code == 409
    assert r.json()["detail"] == "market closed"


@pytest.mark.asyncio
async def test_flag_off_skips_gate(halt_client):
    with patch("app.amm.router.get_settings", return_value=_settings(False)):
        r = await halt_client.get(f"/api/v1/amm/{CLOSED}/quote")
    # Gate not applied; the test env has no MarketAMM address, so the real 503 surfaces (not a 200 list).
    assert r.status_code == 503
    assert r.json() == {"detail": "MarketAMM address not configured"}


@pytest.mark.asyncio
async def test_open_market_passes_gate(halt_client):
    with patch("app.amm.router.get_settings", return_value=_settings(True)):
        r = await halt_client.get(f"/api/v1/amm/{OPEN}/quote")
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_resolved_market_409_even_with_flag_off(halt_client):
    for flag in (True, False):
        with patch("app.amm.router.get_settings", return_value=_settings(flag)), patch(
            "app.contract_addresses.get_contract_addresses", side_effect=_no_rpc
        ):
            r = await halt_client.get(f"/api/v1/amm/{RESOLVED}/quote")
        assert r.status_code == 409
        assert r.json()["detail"] == "market resolved"


@pytest.mark.asyncio
async def test_unknown_market_is_not_halted(halt_client):
    with patch("app.amm.router.get_settings", return_value=_settings(True)):
        r = await halt_client.get(f"/api/v1/amm/{UNKNOWN}/quote")
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_bad_market_id_400(halt_client):
    with patch("app.amm.router.get_settings", return_value=_settings(True)):
        r = await halt_client.get("/api/v1/amm/0x1234/quote")
    assert r.status_code == 400


class _Revert:
    def __init__(self, exc):
        self._exc = exc

    def call(self):
        raise self._exc


def _fake_web3(exc):
    class _Functions:
        def quoteBuy(self, *_a):
            return _Revert(exc)

        def quoteSell(self, *_a):
            return _Revert(exc)

    class _Contract:
        functions = _Functions()

    class _FakeWeb3:
        HTTPProvider = staticmethod(lambda url, **kw: url)
        to_checksum_address = staticmethod(lambda a: a)

        def __init__(self, provider):
            self.eth = type("Eth", (), {"contract": staticmethod(lambda address, abi: _Contract())})()

    return _FakeWeb3


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["price bound", "no pool", "dust"])
async def test_quote_revert_maps_to_422(halt_client, reason):
    exc = ContractLogicError(f"execution reverted: {reason}", data="0x08c379a0")
    with patch("app.amm.router.get_settings", return_value=_settings(True)), patch(
        "app.contract_addresses.get_contract_addresses", return_value={"MarketAMM": AMM}
    ), patch("web3.Web3", _fake_web3(exc)):
        buy = await halt_client.get(f"/api/v1/amm/{OPEN}/quote")
        sell = await halt_client.get(f"/api/v1/amm/{OPEN}/quote", params={"sell_yes": False, "token_amount": 10})
    assert buy.status_code == sell.status_code == 422
    assert buy.json() == sell.json() == {"detail": reason}


@pytest.mark.asyncio
async def test_quote_transport_error_is_503_without_leaking_url(halt_client):
    exc = ConnectionError("HTTPSConnectionPool(host='rpc.example', url=/v2/SECRETKEY)")
    with patch("app.amm.router.get_settings", return_value=_settings(True)), patch(
        "app.contract_addresses.get_contract_addresses", return_value={"MarketAMM": AMM}
    ), patch("web3.Web3", _fake_web3(exc)):
        r = await halt_client.get(f"/api/v1/amm/{OPEN}/quote")
    assert r.status_code == 503
    assert "SECRETKEY" not in r.text


@pytest.mark.asyncio
async def test_market_public_halt_fields(halt_client):
    with patch("app.markets.router.get_settings", return_value=_settings(True)):
        closed = (await halt_client.get(f"/api/v1/markets/{CLOSED}")).json()
        opened = (await halt_client.get(f"/api/v1/markets/{OPEN}")).json()
        resolved = (await halt_client.get(f"/api/v1/markets/{RESOLVED}")).json()
        listed = (await halt_client.get("/api/v1/markets")).json()
    assert closed["tradingHaltsAt"] == closed["closeTime"]
    assert closed["tradingOpen"] is False
    assert opened["tradingHaltsAt"] == opened["closeTime"]
    assert opened["tradingOpen"] is True
    assert resolved["tradingOpen"] is False
    cards = {c["primary"]["conditionId"]: c["primary"] for c in listed}
    assert cards[CLOSED]["tradingOpen"] is False
    assert cards[OPEN]["tradingOpen"] is True

    with patch("app.markets.router.get_settings", return_value=_settings(False)):
        closed_off = (await halt_client.get(f"/api/v1/markets/{CLOSED}")).json()
    assert closed_off["tradingHaltsAt"] is None
    assert closed_off["tradingOpen"] is True


@pytest.mark.asyncio
async def test_yes_price_micros_is_latest_point(halt_client):
    async with SessionLocal() as session:
        session.add_all(
            [
                PricePoint(condition_id=OPEN, ts=100, block_number=1, log_index=0, yes_price_micros=500_000),
                PricePoint(condition_id=OPEN, ts=300, block_number=3, log_index=0, yes_price_micros=610_000),
                PricePoint(condition_id=OPEN, ts=300, block_number=3, log_index=2, yes_price_micros=640_000),
                PricePoint(condition_id=OPEN, ts=200, block_number=2, log_index=5, yes_price_micros=580_000),
            ]
        )
        await session.commit()
    detail = (await halt_client.get(f"/api/v1/markets/{OPEN}")).json()
    assert detail["yesPriceMicros"] == 640_000
    listed = (await halt_client.get("/api/v1/markets")).json()
    cards = {c["primary"]["conditionId"]: c["primary"] for c in listed}
    assert cards[OPEN]["yesPriceMicros"] == 640_000
    assert cards[CLOSED]["yesPriceMicros"] is None


@pytest.mark.asyncio
async def test_trading_halt_reason_helper(halt_client):
    async with SessionLocal() as session:
        on = _settings(True)
        assert await trading_halt_reason(session, CLOSED, on) == "market closed"
        assert await trading_halt_reason(session, OPEN, on) is None
        assert await trading_halt_reason(session, OPEN, on, now=int(time.time()) + 7200) == "market closed"
        assert await trading_halt_reason(session, RESOLVED, _settings(False)) == "market resolved"
        assert await trading_halt_reason(session, UNKNOWN, on) is None


def _buy_calldata() -> str:
    return "0x" + (
        bytes.fromhex("a9c98025")
        + bytes.fromhex(TRADE_CID[2:])
        + (1).to_bytes(32, "big")
        + (50 * 10**6).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
    ).hex()


def _cdp_settings(flag: bool) -> Settings:
    return Settings(
        trading_halt_at_close=flag,
        usdc_address=USDC,
        ctf_address=CTF,
        amm_address=AMM,
        cdp_project_id="proj",
        cdp_api_key_id="key-id",
        cdp_api_key_secret="key-secret",
        jwt_secret=get_settings().jwt_secret,
    )


async def _cdp_token(client) -> str:
    cdp_user = CdpUser(user_id="end-user-halt", smart_account=SMART)
    with patch("app.auth.router.validate_access_token", new_callable=AsyncMock, return_value=cdp_user):
        auth = await client.post("/api/v1/auth/cdp", json={"accessToken": "cdp-access-token"})
    assert auth.status_code == 200
    return auth.json()["token"]


@pytest.mark.asyncio
async def test_cdp_send_blocks_trade_on_closed_market(halt_client):
    token = await _cdp_token(halt_client)
    for flag, expected in ((True, 409), (False, 200)):
        with patch("app.aa.router.get_settings", return_value=_cdp_settings(flag)):
            with patch("app.aa.router.send_user_operation", new_callable=AsyncMock) as send:
                send.return_value = {"userOpHash": "0x" + "22" * 32}
                res = await halt_client.post(
                    "/api/v1/aa/cdp-send",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"calls": [{"to": AMM, "data": _buy_calldata(), "value": 0}]},
                )
        assert res.status_code == expected, res.text
        if expected == 409:
            assert res.json()["detail"] == "market closed"
            send.assert_not_called()
        else:
            send.assert_awaited_once()


# --- user listings: only confirmed type-2 markets quote / trade ----------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("cid", [USER_REJECTED, USER_INDEXED])
async def test_quote_refuses_unconfirmed_user_listing(halt_client, cid):
    with patch("app.amm.router.get_settings", return_value=_settings(True)), patch(
        "app.contract_addresses.get_contract_addresses", side_effect=_no_rpc
    ):
        r = await halt_client.get(f"/api/v1/amm/{cid}/quote")
    assert r.status_code == 409
    assert r.json()["detail"] == "listing not confirmed"


@pytest.mark.asyncio
async def test_confirmed_user_listing_passes_gate(halt_client):
    async with SessionLocal() as session:
        assert await trading_halt_reason(session, USER_CONFIRMED, _settings(True)) is None
        assert await trading_halt_reason(session, USER_REJECTED, _settings(False)) == "listing not confirmed"


def _buy_calldata_for(cid: str) -> str:
    return "0x" + (
        bytes.fromhex("a9c98025")
        + bytes.fromhex(cid[2:])
        + (1).to_bytes(32, "big")
        + (50 * 10**6).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
    ).hex()


@pytest.mark.asyncio
async def test_cdp_send_refuses_trade_on_rejected_user_listing(halt_client):
    token = await _cdp_token(halt_client)
    with patch("app.aa.router.get_settings", return_value=_cdp_settings(True)):
        with patch("app.aa.router.send_user_operation", new_callable=AsyncMock) as send:
            res = await halt_client.post(
                "/api/v1/aa/cdp-send",
                headers={"Authorization": f"Bearer {token}"},
                json={"calls": [{"to": AMM, "data": _buy_calldata_for(USER_REJECTED), "value": 0}]},
            )
    assert res.status_code == 409
    assert res.json()["detail"] == "listing not confirmed"
    send.assert_not_called()


# --- quote input bounds: client errors never read as an RPC outage -------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"usdc_in": -5},
        {"usdc_in": 2**256},
        {"sell_yes": True, "token_amount": -1},
        {"sell_yes": False, "token_amount": 2**256},
    ],
)
async def test_quote_rejects_out_of_range_amounts(halt_client, params, caplog):
    with patch("app.amm.router.get_settings", return_value=_settings(True)), patch(
        "app.contract_addresses.get_contract_addresses", side_effect=_no_rpc
    ):
        r = await halt_client.get(f"/api/v1/amm/{OPEN}/quote", params=params)
    assert r.status_code == 422
    assert "AMM quote RPC failed" not in caplog.text


@pytest.mark.asyncio
async def test_quote_encoding_error_maps_to_400(halt_client, caplog):
    from web3.exceptions import Web3ValidationError

    with patch("app.amm.router.get_settings", return_value=_settings(True)), patch(
        "app.contract_addresses.get_contract_addresses", return_value={"MarketAMM": AMM}
    ), patch("web3.Web3", _fake_web3(Web3ValidationError("bad uint256"))):
        r = await halt_client.get(f"/api/v1/amm/{OPEN}/quote")
    assert r.status_code == 400
    assert r.json() == {"detail": "invalid quote arguments"}
    assert "AMM quote RPC failed" not in caplog.text


def test_amm_contract_uses_short_timeout_and_no_retries():
    seen = {}

    class _Provider:
        def __init__(self, url, **kw):
            seen.update(url=url, **kw)

    class _W3:
        HTTPProvider = _Provider
        to_checksum_address = staticmethod(lambda a: a)

        def __init__(self, provider):
            self.eth = type("Eth", (), {"contract": staticmethod(lambda address, abi: ("contract", address))})()

    with patch("web3.Web3", _W3):
        first = amm_router._amm_contract("http://rpc", AMM, 5.0)
        again = amm_router._amm_contract("http://rpc", AMM, 5.0)
    assert first is again  # cached: no Web3 per request
    assert seen["request_kwargs"] == {"timeout": 5.0}
    assert seen["exception_retry_configuration"] is None
    assert Settings().amm_quote_rpc_timeout_seconds == 5.0


@pytest.mark.asyncio
async def test_slow_quote_rpc_does_not_block_the_event_loop(halt_client):
    """The eth_call runs in a worker thread: /health answers while a quote is stuck on RPC."""
    import asyncio
    import time as _time

    class _Slow:
        def call(self):
            _time.sleep(0.6)
            return 123

    class _Functions:
        def quoteBuy(self, *_a):
            return _Slow()

    class _Contract:
        functions = _Functions()

    with patch("app.amm.router.get_settings", return_value=_settings(True)), patch(
        "app.contract_addresses.get_contract_addresses", return_value={"MarketAMM": AMM}
    ), patch.object(amm_router, "_amm_contract", lambda *a: _Contract()):
        started = _time.monotonic()
        quote_task = asyncio.create_task(halt_client.get(f"/api/v1/amm/{OPEN}/quote"))
        await asyncio.sleep(0.1)
        health = await halt_client.get("/health")
        health_done = _time.monotonic() - started
        quote = await quote_task
    assert health.status_code == 200
    assert quote.status_code == 200 and quote.json()["tokensOut"] == 123
    assert health_done < 0.5
