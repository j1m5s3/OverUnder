import hashlib
import hmac
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.router import get_current_user
from app.config import get_settings
from app.db import get_db
from app.kyc.router import enforce_kyc_gate
from app.models import RampTx, User

router = APIRouter(prefix="/ramps", tags=["ramps"])
settings = get_settings()

# Upper bound for one widget session (USD). Anything above is a client error, not a KYC question.
MAX_SESSION_USDC = Decimal("1000000")


def parse_usdc_amount(raw: str) -> Decimal:
    """Finite, > 0 and <= MAX_SESSION_USDC, else 400. 'nan', 'inf' and negatives would
    otherwise slip past the KYC volume sum (NaN >= threshold is False)."""
    try:
        amount = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=400, detail="Invalid amount")
    if not amount.is_finite() or amount <= 0 or amount > MAX_SESSION_USDC:
        raise HTTPException(status_code=400, detail="Invalid amount")
    return amount


class MoonPaySessionRequest(BaseModel):
    usdc_amount: str = "100"


class MoonPayWebhookPayload(BaseModel):
    type: str
    externalTransactionId: str
    status: str
    walletAddress: str
    cryptoAmount: float


@router.post("/moonpay/session")
async def moonpay_session(
    req: MoonPaySessionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Sign MoonPay widget URL with secret for the authenticated user's address.
    
    Enforces KYC gate: returns 403 if notional ≥ threshold or restricted jurisdiction
    and KYC status not in {pass, not_required}.
    """
    if not settings.moonpay_api_key or not settings.moonpay_secret:
        raise HTTPException(status_code=503, detail="MoonPay not configured")

    amount = parse_usdc_amount(req.usdc_amount)

    # Enforce KYC gate (raises 403 if fails)
    await enforce_kyc_gate(float(amount), user, db)

    address = user.address

    # Build MoonPay widget URL
    params = {
        "apiKey": settings.moonpay_api_key,
        "currencyCode": "usdc",
        "walletAddress": address,
        "baseCurrencyAmount": format(amount.normalize(), "f"),
        "baseCurrencyCode": "usd",
        "network": "base",
        # Signed with the rest: the widget cannot raise the amount the KYC gate just checked.
        "lockAmount": "true",
    }

    query_string = urlencode(params)
    signature = hmac.new(
        settings.moonpay_secret.encode(), query_string.encode(), hashlib.sha256
    ).hexdigest()

    url = f"https://buy.moonpay.com?{query_string}&signature={signature}"

    return {"url": url, "provider": "moonpay", "asset": "USDC", "chain": "base"}


@router.post("/moonpay/webhook")
async def moonpay_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Verify MoonPay webhook signature and upsert RampTx."""
    if not settings.moonpay_secret:
        raise HTTPException(status_code=503, detail="MoonPay not configured")

    # Get raw body for signature verification
    body = await request.body()
    signature = request.headers.get("moonpay-signature", "")

    # Verify signature
    expected_signature = hmac.new(
        settings.moonpay_secret.encode(), body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse payload
    import json

    payload = json.loads(body)

    # Upsert RampTx
    provider_id = payload.get("externalTransactionId", "")
    address = payload.get("walletAddress", "")
    amount = str(payload.get("cryptoAmount", 0))
    status = payload.get("status", "unknown")

    # Check if exists
    result = await db.execute(select(RampTx).where(RampTx.provider_id == provider_id))
    existing = result.scalar_one_or_none()

    if existing:
        existing.status = status
        existing.amount = amount
    else:
        tx = RampTx(address=address, amount=amount, provider_id=provider_id, status=status)
        db.add(tx)

    await db.commit()

    return {"ok": True}


@router.get("/onramp-url")
async def onramp_url(address: str, usdc_amount: str = "100"):
    app_id = settings.coinbase_onramp_app_id or "demo"
    url = (
        "https://pay.coinbase.com/buy/select-asset"
        f"?appId={app_id}"
        f"&addresses={{\" {address} \":[\"base\"]}}"
        "&assets=[\"USDC\"]"
        f"&presetFiatAmount={usdc_amount}"
    )
    # Compact Coinbase Onramp session-style URL used by Polymarket-class flows.
    safe = (
        f"https://pay.coinbase.com/buy?"
        f"appId={app_id}&destinationWallets="
        f"[{{%22address%22:%22{address}%22,%22blockchains%22:[%22base%22],%22assets%22:[%22USDC%22]}}]"
    )
    return {"url": safe, "provider": "coinbase", "asset": "USDC", "chain": "base"}


@router.get("/offramp-url")
async def offramp_url(address: str):
    app_id = settings.coinbase_onramp_app_id or "demo"
    url = f"https://pay.coinbase.com/offramp?appId={app_id}&address={address}&asset=USDC"
    return {"url": url, "provider": "coinbase", "asset": "USDC", "chain": "base"}
