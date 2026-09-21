from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.router import require_operator
from app.db import get_db
from app.models import Attestation, Market, User, Vote

router = APIRouter(prefix="/oracle", tags=["oracle"])


class AttestIn(BaseModel):
    conditionId: str
    agent: str
    outcome: int
    evidenceHash: str
    summary: str = ""
    evidenceJson: str = "[]"
    signature: str = ""


class VoteIn(BaseModel):
    conditionId: str
    voter: str
    outcome: int
    weight: int = 0


class ResolvedIn(BaseModel):
    conditionId: str
    outcome: int


@router.post("/attest")
async def attest(body: AttestIn, db: AsyncSession = Depends(get_db)):
    row = Attestation(
        condition_id=body.conditionId,
        agent=body.agent.lower(),
        outcome=body.outcome,
        evidence_hash=body.evidenceHash,
        summary=body.summary,
        evidence_json=body.evidenceJson,
        signature=body.signature,
    )
    db.add(row)
    await db.commit()
    return {"ok": True, "id": row.id}


@router.post("/resolved")
async def mark_resolved(
    body: ResolvedIn,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_operator),
):
    if body.outcome not in (0, 1):
        raise HTTPException(400, "outcome must be 0 or 1")
    market = await db.get(Market, body.conditionId)
    if market is None:
        raise HTTPException(404, "market not found")
    market.resolved = True
    market.payout_yes = 1 if body.outcome == 0 else 0
    market.payout_no = 0 if body.outcome == 0 else 1
    await db.commit()
    return {
        "ok": True,
        "conditionId": market.condition_id,
        "resolved": True,
        "payoutYes": market.payout_yes,
        "payoutNo": market.payout_no,
    }


@router.get("/{market_id}/status")
async def status(market_id: str, db: AsyncSession = Depends(get_db)):
    atts = (
        await db.execute(select(Attestation).where(Attestation.condition_id == market_id))
    ).scalars().all()
    votes = (await db.execute(select(Vote).where(Vote.condition_id == market_id))).scalars().all()
    outcomes = [a.outcome for a in atts]
    unanimous = len(outcomes) >= 3 and len(set(outcomes)) == 1
    return {
        "conditionId": market_id,
        "attestations": [
            {
                "agent": a.agent,
                "outcome": a.outcome,
                "summary": a.summary,
                "evidenceHash": a.evidence_hash,
            }
            for a in atts
        ],
        "votes": [{"voter": v.voter, "outcome": v.outcome, "weight": v.weight} for v in votes],
        "unanimous": unanimous,
    }


@router.post("/vote")
async def vote(body: VoteIn, db: AsyncSession = Depends(get_db)):
    existing = (
        await db.execute(
            select(Vote).where(Vote.condition_id == body.conditionId, Vote.voter == body.voter.lower())
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "already voted")
    db.add(
        Vote(
            condition_id=body.conditionId,
            voter=body.voter.lower(),
            outcome=body.outcome,
            weight=body.weight,
        )
    )
    await db.commit()
    return {"ok": True}
