from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.router import get_current_user
from app.config import get_settings
from app.db import get_db
from app.models import KycRecord, RampTx

router = APIRouter(prefix="/kyc", tags=["kyc"])
settings = get_settings()


class KycSessionRequest(BaseModel):
    jurisdiction: str = ""


class KycSessionResponse(BaseModel):
    status: str
    jurisdiction: str
    updated_at: str


class KycCheckRequest(BaseModel):
    notional_usdc: float


class KycCheckResponse(BaseModel):
    allowed: bool
    reason: str = ""
    kyc_status: str = ""


@router.post("/session", response_model=KycSessionResponse)
async def kyc_session(
    req: KycSessionRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create or update KYC session for the authenticated user.
    
    This endpoint does NOT store government IDs or documents.
    It only stores status enum and jurisdiction.
    """
    address = user["address"]

    # Check if record exists
    result = await db.execute(select(KycRecord).where(KycRecord.address == address))
    existing = result.scalar_one_or_none()

    if existing:
        if req.jurisdiction:
            existing.jurisdiction = req.jurisdiction
        existing.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(existing)
        return KycSessionResponse(
            status=existing.status,
            jurisdiction=existing.jurisdiction,
            updated_at=existing.updated_at.isoformat(),
        )
    else:
        # Create new record with pending status
        record = KycRecord(
            address=address,
            status="pending",
            jurisdiction=req.jurisdiction,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        return KycSessionResponse(
            status=record.status,
            jurisdiction=record.jurisdiction,
            updated_at=record.updated_at.isoformat(),
        )


@router.get("/status", response_model=KycSessionResponse)
async def kyc_status(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get KYC status for the authenticated user."""
    address = user["address"]

    result = await db.execute(select(KycRecord).where(KycRecord.address == address))
    record = result.scalar_one_or_none()

    if not record:
        return KycSessionResponse(
            status="not_started",
            jurisdiction="",
            updated_at=datetime.utcnow().isoformat(),
        )

    return KycSessionResponse(
        status=record.status,
        jurisdiction=record.jurisdiction,
        updated_at=record.updated_at.isoformat(),
    )


@router.post("/check", response_model=KycCheckResponse)
async def kyc_check(
    req: KycCheckRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check if KYC is required based on notional amount and jurisdiction.
    
    Gate logic:
    - notional ≥ threshold (default $500/day) OR restricted jurisdiction → require KYC
    - Operator JWT cannot bypass fiat KYC
    - Returns allowed=True if KYC status is 'pass' or 'not_required'
    """
    address = user["address"]

    # Get KYC record
    result = await db.execute(select(KycRecord).where(KycRecord.address == address))
    kyc_record = result.scalar_one_or_none()

    # Check if user is in restricted jurisdiction
    restricted_jurisdictions = settings.kyc_restricted_jurisdictions.split(",")
    restricted_jurisdictions = [j.strip().upper() for j in restricted_jurisdictions if j.strip()]

    is_restricted = False
    if kyc_record and kyc_record.jurisdiction:
        is_restricted = kyc_record.jurisdiction.upper() in restricted_jurisdictions

    # Check daily volume
    threshold = settings.kyc_threshold_usdc
    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)

    # Calculate user's 24h ramp volume
    result = await db.execute(
        select(RampTx).where(
            RampTx.address == address,
            RampTx.created_at >= day_ago,
            RampTx.status.in_(["completed", "success"]),
        )
    )
    recent_txs = result.scalars().all()
    daily_volume = sum(float(tx.amount) for tx in recent_txs if tx.amount)

    # Check if current transaction would exceed threshold
    total_volume = daily_volume + req.notional_usdc

    # Determine if KYC is required
    kyc_required = total_volume >= threshold or is_restricted

    if not kyc_required:
        return KycCheckResponse(
            allowed=True,
            reason="Below threshold and not restricted",
            kyc_status=kyc_record.status if kyc_record else "not_required",
        )

    # KYC is required - check status
    if not kyc_record:
        return KycCheckResponse(
            allowed=False,
            reason="KYC required but not started",
            kyc_status="not_started",
        )

    if kyc_record.status in ("pass", "not_required"):
        return KycCheckResponse(
            allowed=True,
            reason="KYC passed",
            kyc_status=kyc_record.status,
        )

    return KycCheckResponse(
        allowed=False,
        reason=f"KYC status: {kyc_record.status}",
        kyc_status=kyc_record.status,
    )
