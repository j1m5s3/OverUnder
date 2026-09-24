import pytest

from app.auth.router import _issue
from app.db import SessionLocal
from app.models import KycRecord, User

ADDR = "0x" + "77" * 20


async def _session_token() -> str:
    async with SessionLocal() as s:
        if (await s.get(KycRecord, ADDR)) is not None:
            await s.delete(await s.get(KycRecord, ADDR))
        from sqlalchemy import select
        user = (await s.execute(select(User).where(User.address == ADDR))).scalar_one_or_none()
        if user is None:
            s.add(User(address=ADDR, is_operator=False))
        await s.commit()
    return _issue(ADDR, False)


@pytest.mark.asyncio
async def test_kyc_routes_accept_session_user(client):
    headers = {"Authorization": f"Bearer {await _session_token()}"}
    status = await client.get("/api/v1/kyc/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["status"] == "not_started"
    check = await client.post("/api/v1/kyc/check", json={"notional_usdc": 10}, headers=headers)
    assert check.status_code == 200
    assert check.json()["allowed"] is True
    session = await client.post("/api/v1/kyc/session", json={"jurisdiction": "US"}, headers=headers)
    assert session.status_code == 200
    assert session.json()["status"] == "pending"
    over = await client.post("/api/v1/kyc/check", json={"notional_usdc": 900}, headers=headers)
    assert over.status_code == 200
    assert over.json()["allowed"] is False


@pytest.mark.asyncio
async def test_moonpay_session_route_uses_session_address(client, monkeypatch):
    import app.ramps.router as ramps

    monkeypatch.setattr(ramps.settings, "moonpay_api_key", "pk_test")
    monkeypatch.setattr(ramps.settings, "moonpay_secret", "sk_test")
    headers = {"Authorization": f"Bearer {await _session_token()}"}
    r = await client.post("/api/v1/ramps/moonpay/session", json={"usdc_amount": "50"}, headers=headers)
    assert r.status_code == 200
    assert f"walletAddress={ADDR}" in r.json()["url"]


# --- amount validation: NaN / inf / negative must never skip the KYC gate ------------------


async def _with_completed_volume(amount: str) -> None:
    from sqlalchemy import delete

    from app.models import RampTx

    async with SessionLocal() as s:
        await s.execute(delete(RampTx).where(RampTx.address == ADDR))
        s.add(RampTx(address=ADDR, amount=amount, provider_id=f"kyc-test-{ADDR}", status="completed"))
        await s.commit()


async def _clear_volume() -> None:
    from sqlalchemy import delete

    from app.models import RampTx

    async with SessionLocal() as s:
        await s.execute(delete(RampTx).where(RampTx.address == ADDR))
        await s.commit()


@pytest.fixture
def moonpay_on(monkeypatch):
    import app.ramps.router as ramps

    monkeypatch.setattr(ramps.settings, "moonpay_api_key", "pk_test")
    monkeypatch.setattr(ramps.settings, "moonpay_secret", "sk_test")


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", ["nan", "NaN", "-inf", "inf", "sNaN", "-100000", "0", "abc", "", "1e999999"])
async def test_moonpay_session_rejects_invalid_amounts(client, moonpay_on, amount):
    headers = {"Authorization": f"Bearer {await _session_token()}"}
    await _with_completed_volume("100000")
    try:
        r = await client.post("/api/v1/ramps/moonpay/session", json={"usdc_amount": amount}, headers=headers)
    finally:
        await _clear_volume()
    assert r.status_code == 400, r.text
    assert "url" not in r.json()


@pytest.mark.asyncio
async def test_moonpay_session_over_volume_without_kyc_is_403(client, moonpay_on):
    headers = {"Authorization": f"Bearer {await _session_token()}"}
    await _with_completed_volume("100000")
    try:
        r = await client.post("/api/v1/ramps/moonpay/session", json={"usdc_amount": "1"}, headers=headers)
    finally:
        await _clear_volume()
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_moonpay_session_url_locks_the_gated_amount(client, moonpay_on):
    from urllib.parse import parse_qs, urlsplit

    headers = {"Authorization": f"Bearer {await _session_token()}"}
    r = await client.post("/api/v1/ramps/moonpay/session", json={"usdc_amount": " 50.50 "}, headers=headers)
    assert r.status_code == 200
    query = parse_qs(urlsplit(r.json()["url"]).query)
    assert query["lockAmount"] == ["true"]
    assert query["baseCurrencyAmount"] == ["50.5"]
    assert "signature" in query


@pytest.mark.asyncio
@pytest.mark.parametrize("notional", ["nan", "inf", -5, 0])
async def test_kyc_check_rejects_invalid_notional(client, notional):
    headers = {"Authorization": f"Bearer {await _session_token()}"}
    r = await client.post("/api/v1/kyc/check", json={"notional_usdc": notional}, headers=headers)
    assert r.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("notional", [float("nan"), float("inf"), -1.0, 0.0])
async def test_enforce_kyc_gate_refuses_invalid_amount(notional):
    from fastapi import HTTPException

    from app.kyc.router import enforce_kyc_gate

    with pytest.raises(HTTPException) as exc:
        await enforce_kyc_gate(notional, User(address=ADDR), None)
    assert exc.value.status_code == 400


# --- jurisdiction: ISO 3166-1 alpha-2 only, write-once -------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["USA", "U", "1A", "U$", "x" * 300, "U S", "gb", "Gb"])
async def test_kyc_session_rejects_non_alpha2_jurisdiction(client, bad):
    headers = {"Authorization": f"Bearer {await _session_token()}"}
    r = await client.post("/api/v1/kyc/session", json={"jurisdiction": bad}, headers=headers)
    assert r.status_code == 422
    async with SessionLocal() as s:
        assert await s.get(KycRecord, ADDR) is None


@pytest.mark.asyncio
async def test_kyc_session_jurisdiction_is_write_once(client):
    headers = {"Authorization": f"Bearer {await _session_token()}"}
    first = await client.post("/api/v1/kyc/session", json={"jurisdiction": "GB"}, headers=headers)
    assert first.status_code == 200 and first.json()["jurisdiction"] == "GB"
    same = await client.post("/api/v1/kyc/session", json={"jurisdiction": "GB"}, headers=headers)
    assert same.status_code == 200
    blank = await client.post("/api/v1/kyc/session", json={}, headers=headers)
    assert blank.status_code == 200 and blank.json()["jurisdiction"] == "GB"
    changed = await client.post("/api/v1/kyc/session", json={"jurisdiction": "XX"}, headers=headers)
    assert changed.status_code == 409
    async with SessionLocal() as s:
        assert (await s.get(KycRecord, ADDR)).jurisdiction == "GB"


@pytest.mark.asyncio
async def test_kyc_session_sets_jurisdiction_once_when_first_blank(client):
    headers = {"Authorization": f"Bearer {await _session_token()}"}
    assert (await client.post("/api/v1/kyc/session", json={}, headers=headers)).json()["jurisdiction"] == ""
    later = await client.post("/api/v1/kyc/session", json={"jurisdiction": "CA"}, headers=headers)
    assert later.status_code == 200 and later.json()["jurisdiction"] == "CA"


@pytest.mark.asyncio
async def test_moonpay_webhook_rejects_fields_past_column_sizes(client, monkeypatch):
    import hashlib
    import hmac
    import json

    import app.ramps.router as ramps

    monkeypatch.setattr(ramps.settings, "moonpay_secret", "sk_test")

    def signed(payload: dict):
        body = json.dumps(payload).encode()
        sig = hmac.new(b"sk_test", body, hashlib.sha256).hexdigest()
        return {"content": body, "headers": {"moonpay-signature": sig, "content-type": "application/json"}}

    base = {"type": "transaction_updated", "externalTransactionId": "kyc-bounds-1", "status": "completed",
            "walletAddress": ADDR, "cryptoAmount": 5.0}
    for field, value in (("externalTransactionId", "x" * 256), ("walletAddress", "0x" + "a" * 41),
                         ("status", "s" * 33), ("status", 7)):
        r = await client.post("/api/v1/ramps/moonpay/webhook", **signed({**base, field: value}))
        assert r.status_code == 400, (field, r.text)
    ok = await client.post("/api/v1/ramps/moonpay/webhook", **signed(base))
    assert ok.status_code == 200
    from sqlalchemy import delete

    from app.models import RampTx

    async with SessionLocal() as s:
        await s.execute(delete(RampTx).where(RampTx.provider_id == "kyc-bounds-1"))
        await s.commit()
