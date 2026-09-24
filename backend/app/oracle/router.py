import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.router import get_current_user, require_operator
from app.config import get_settings
from app.db import get_db
from app.markets.trading import norm_cid
from app.models import Attestation, Market, User, Vote

router = APIRouter(prefix="/oracle", tags=["oracle"])
logger = logging.getLogger(__name__)

UINT8_MAX = 255


class AttestIn(BaseModel):
    conditionId: str = Field(max_length=66)
    agent: str = Field(max_length=42)
    outcome: int = Field(ge=0, le=UINT8_MAX)
    evidenceHash: str = Field(max_length=66)
    summary: str = ""
    evidenceJson: str = "[]"
    signature: str = ""


class VoteIn(BaseModel):
    conditionId: str = Field(max_length=66)
    outcome: int = Field(ge=0, le=1)
    # Ignored: the voter is the authenticated user and the weight is read from chain.
    voter: str | None = None
    weight: int | None = None


class ResolvedIn(BaseModel):
    conditionId: str
    outcome: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso_utc(value: datetime | None) -> str | None:
    """ISO 8601 UTC with a Z suffix; DB timestamps are stored naive UTC. None for legacy rows."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(timespec="seconds") + "Z"


# Attestation kinds on GET /status. The oracle job persists research attempts that did not resolve
# (oracles/resolve/publish.py persist_research) with evidenceJson [{"kind": "research", "reason": ...}];
# they drive the research cooldown via createdAt but are not resolution evidence.
KIND_RESEARCH = "research"
KIND_RESOLUTION = "resolution"


def attestation_kind(evidence_json: str | None) -> str:
    """'research' when evidenceJson carries the research marker, else 'resolution' (legacy rows too)."""
    try:
        data = json.loads(evidence_json or "[]")
    except (TypeError, ValueError):
        return KIND_RESOLUTION
    items = data if isinstance(data, list) else [data]
    if any(isinstance(item, dict) and item.get("kind") == KIND_RESEARCH for item in items):
        return KIND_RESEARCH
    return KIND_RESOLUTION


@router.post("/attest")
async def attest(
    body: AttestIn,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_operator),
):
    """Persist one agent report (the oracle job sends its operator JWT)."""
    row = Attestation(
        condition_id=body.conditionId,
        agent=body.agent.lower(),
        outcome=body.outcome,
        evidence_hash=body.evidenceHash,
        summary=body.summary,
        evidence_json=body.evidenceJson,
        signature=body.signature,
        created_at=_utcnow(),
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
        await db.execute(
            select(Attestation).where(Attestation.condition_id == market_id).order_by(Attestation.id)
        )
    ).scalars().all()
    votes = (await db.execute(select(Vote).where(Vote.condition_id == market_id))).scalars().all()
    kinds = [attestation_kind(a.evidence_json) for a in atts]
    # Only resolving reports count: research records (often outcome 2) never make or break unanimity.
    outcomes = [a.outcome for a, kind in zip(atts, kinds) if kind == KIND_RESOLUTION]
    unanimous = len(outcomes) >= 3 and len(set(outcomes)) == 1
    return {
        "conditionId": market_id,
        "attestations": [
            {
                "agent": a.agent,
                "outcome": a.outcome,
                "summary": a.summary,
                "evidenceHash": a.evidence_hash,
                "createdAt": _iso_utc(a.created_at),
                "kind": kind,
            }
            for a, kind in zip(atts, kinds)
        ],
        "votes": [{"voter": v.voter, "outcome": v.outcome, "weight": v.weight} for v in votes],
        "unanimous": unanimous,
    }


def read_position_weight(settings, condition_id: str, holder: str) -> int:
    """CTF YES + NO balance of `holder` for the condition (base units). Sync; runs in a thread."""
    from web3 import Web3

    from app.contract_addresses import get_contract_addresses, load_abi

    addresses = get_contract_addresses()
    if "ConditionalTokens" not in addresses:
        raise RuntimeError("ConditionalTokens address not configured")
    w3 = Web3(
        Web3.HTTPProvider(
            settings.anvil_rpc_url,
            request_kwargs={"timeout": settings.operator_rpc_timeout_seconds},
            exception_retry_configuration=None,
        )
    )
    ctf = w3.eth.contract(address=Web3.to_checksum_address(addresses["ConditionalTokens"]), abi=load_abi("ConditionalTokens"))
    cid = bytes.fromhex(condition_id[2:])
    account = Web3.to_checksum_address(holder)
    total = 0
    for outcome in (0, 1):
        position = ctf.functions.positionId(cid, outcome).call()
        total += int(ctf.functions.balanceOf(account, position).call())
    return total


@router.post("/vote")
async def vote(
    body: VoteIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """One vote per holder per market. voter = the caller; weight = their CTF YES+NO balance."""
    cid = norm_cid(body.conditionId)
    if len(cid) != 66:
        raise HTTPException(400, "conditionId must be 32-byte hex")
    market = await db.get(Market, cid)
    if market is None:
        raise HTTPException(404, "market not found")
    voter = user.address.lower()
    existing = (
        await db.execute(select(Vote).where(Vote.condition_id == cid, Vote.voter == voter))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "already voted")
    try:
        weight = await asyncio.to_thread(read_position_weight, get_settings(), cid, voter)
    except Exception as exc:
        # Fail closed; never echo transport errors (RPC URLs can embed provider keys).
        logger.warning("vote weight read failed: %s", type(exc).__name__)
        raise HTTPException(503, "chain unavailable")
    if weight <= 0:
        raise HTTPException(403, "only holders of this market can vote")
    weight = min(weight, 2**63 - 1)  # votes.weight is BIGINT
    db.add(Vote(condition_id=cid, voter=voter, outcome=body.outcome, weight=weight))
    await db.commit()
    return {"ok": True, "voter": voter, "weight": weight}
