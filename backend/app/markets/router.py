from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.router import require_operator
from app.db import get_db
from app.models import Market, User

router = APIRouter(prefix="/markets", tags=["markets"])


class CreateMarketIn(BaseModel):
    question: str
    resolution_criteria: str = ""
    close_time: int
    question_id: str = Field(description="bytes32 hex")
    parent_condition_id: str = ""
    seed_usdc: int = 0
    market_type: int = 0
    suggested_probability: float = 0.5


@router.get("")
async def list_markets(
    parent_id: str | None = Query(default=None, alias="parentId"),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Market).where(Market.paused.is_(False))
    if parent_id:
        stmt = stmt.where(Market.parent_condition_id == parent_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [_public(m) for m in rows]


@router.get("/{condition_id}")
async def get_market(condition_id: str, db: AsyncSession = Depends(get_db)):
    m = await db.get(Market, condition_id)
    if m is None:
        raise HTTPException(404, "market not found")
    return _public(m)


@router.post("")
async def create_market(
    body: CreateMarketIn,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_operator),
):
    cid = body.question_id if body.question_id.startswith("0x") else "0x" + body.question_id
    market = Market(
        condition_id=cid,
        parent_condition_id=body.parent_condition_id,
        question=body.question,
        resolution_criteria=body.resolution_criteria,
        market_type=body.market_type,
        close_time=body.close_time,
        suggested_probability=body.suggested_probability,
    )
    db.add(market)
    await db.commit()
    return _public(market)


@router.post("/{condition_id}/pause")
async def pause_market(
    condition_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_operator),
):
    m = await db.get(Market, condition_id)
    if m is None:
        raise HTTPException(404, "market not found")
    m.paused = True
    await db.commit()
    return _public(m)


def _public(m: Market) -> dict:
    return {
        "conditionId": m.condition_id,
        "parentConditionId": m.parent_condition_id or None,
        "question": m.question,
        "resolutionCriteria": m.resolution_criteria,
        "marketType": m.market_type,
        "closeTime": m.close_time,
        "paused": m.paused,
        "resolved": m.resolved,
        "payoutYes": m.payout_yes,
        "payoutNo": m.payout_no,
        "suggestedProbability": m.suggested_probability,
    }
