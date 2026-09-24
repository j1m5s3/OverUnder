"""User market listing (OU-T010): config, eligibility, prepare, confirm.

The backend never sends the listing transaction. `prepare` validates off-chain
gates and hands back two calls (USDC approve + factory
createPermissionlessMarket) that the user's CDP smart account sends as one
sponsored user operation; `confirm` then mirrors the on-chain market into the
DB once the factory reports it. Included in create_app before the markets
router so `/markets/{condition_id}` does not capture "listing".
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import eth_abi
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.auth.router import get_current_user, require_operator
from app.config import get_settings
from app.db import get_db, insert_ignore, session_dialect
from app.markets import chain as market_chain
from app.markets import listing_gates as gates
from app.markets import visibility
from app.markets.router import MarketPublic, _latest_prices, _to_public
from app.models import Market, MarketListing, User

router = APIRouter(prefix="/markets/listing", tags=["listing"])
logger = logging.getLogger(__name__)

SELECTOR_APPROVE = bytes.fromhex("095ea7b3")
SELECTOR_CREATE_PERMISSIONLESS = bytes.fromhex("4e7d1a32")
MARKET_TYPE_USER = 2


@dataclass(frozen=True)
class ListingConfig:
    permissionless: bool
    factory: str
    usdc: str
    oracle: str
    fee_recipient: str
    min_seed_usdc: int
    listing_fee_usdc: int
    min_lead_seconds: int
    max_horizon_seconds: int
    cooldown_seconds: int

    @property
    def bounds(self) -> gates.ListingBounds:
        return gates.ListingBounds(
            min_seed_usdc=self.min_seed_usdc,
            min_lead_seconds=self.min_lead_seconds,
            max_horizon_seconds=self.max_horizon_seconds,
        )


class ListingConfigPublic(BaseModel):
    enabled: bool
    permissionless: bool = False
    factory: str | None = None
    usdc: str | None = None
    oracle: str | None = None
    chainId: int
    minSeedUsdc: int = 0
    listingFeeUsdc: int = 0
    minLeadSeconds: int = 0
    maxHorizonSeconds: int = 0
    cooldownSeconds: int = 0
    reason: str | None = None


class EligibilityPublic(BaseModel):
    enabled: bool
    allowed: bool
    permissionless: bool = False
    cooldownRemaining: int = 0
    pending: int = 0
    maxPending: int = gates.MAX_PENDING_PER_DAY
    reason: str | None = None


class ListingPrepareIn(BaseModel):
    question: str = Field(max_length=1024)
    resolutionCriteria: str = Field(max_length=8192)
    closeTime: int = Field(ge=0, lt=2**64)
    # lt=2**63: market_listings.seed_usdc is BigInteger; larger values 422 before any chain I/O.
    seedUsdc: int = Field(ge=0, lt=2**63, description="USDC base units (6 decimals)")


class ListingCall(BaseModel):
    to: str
    data: str
    value: int = 0


class ListingPreparePublic(BaseModel):
    conditionId: str
    questionId: str
    salt: str
    criteriaHash: str
    seedUsdc: int
    listingFeeUsdc: int
    approveAmount: int
    calls: list[ListingCall]


class ListingConfirmIn(BaseModel):
    conditionId: str
    resolutionCriteria: str | None = Field(default=None, max_length=8192)


class ListingReviewPublic(BaseModel):
    conditionId: str
    creator: str
    status: str
    reason: str = ""
    question: str
    closeTime: int
    paused: bool
    resolved: bool
    marketType: int = MARKET_TYPE_USER


def criteria_hash(criteria: str) -> str:
    """bytes32 committed on chain: keccak256 of the stripped UTF-8 criteria text."""
    return Web3.to_hex(Web3.keccak(text=criteria.strip()))


def build_listing_calls(
    factory: str,
    usdc: str,
    salt: bytes,
    close_time: int,
    question: str,
    criteria_hash_hex: str,
    seed_usdc: int,
    listing_fee_usdc: int,
) -> list[dict[str, Any]]:
    """Pure calldata for [USDC.approve(factory, seed+fee), factory.createPermissionlessMarket(...)]."""
    factory_cs = Web3.to_checksum_address(factory)
    approve = SELECTOR_APPROVE + eth_abi.encode(["address", "uint256"], [factory_cs, seed_usdc + listing_fee_usdc])
    create = SELECTOR_CREATE_PERMISSIONLESS + eth_abi.encode(
        ["bytes32", "uint256", "string", "bytes32", "uint256"],
        [salt, close_time, question, market_chain.hex32(criteria_hash_hex, "criteriaHash"), seed_usdc],
    )
    return [
        {"to": Web3.to_checksum_address(usdc), "data": "0x" + approve.hex(), "value": 0},
        {"to": factory_cs, "data": "0x" + create.hex(), "value": 0},
    ]


def load_listing_chain(settings) -> market_chain.FactoryReader:
    return market_chain.load_factory_reader(settings)


def read_listing_config(factory: Any) -> ListingConfig | None:
    """Chain listing config, or None when the factory has no listing ABI (v1). Other errors propagate."""
    try:
        permissionless = bool(factory.functions.permissionless().call())
    except Exception:
        # Either a legacy factory (no such function) or the RPC is down; tell them apart.
        factory.functions.oracle().call()
        return None
    f = factory.functions
    return ListingConfig(
        permissionless=permissionless,
        factory=Web3.to_checksum_address(factory.address),
        usdc=Web3.to_checksum_address(f.usdc().call()),
        oracle=Web3.to_checksum_address(f.oracle().call()),
        fee_recipient=str(f.feeRecipient().call()),
        min_seed_usdc=int(f.minSeedUsdc().call()),
        listing_fee_usdc=int(f.listingFeeUsdc().call()),
        min_lead_seconds=int(f.minLeadTime().call()),
        max_horizon_seconds=int(f.maxHorizon().call()),
        cooldown_seconds=int(f.listingCooldown().call()),
    )


async def _load_config(settings) -> tuple[market_chain.FactoryReader, ListingConfig | None]:
    reader = load_listing_chain(settings)
    try:
        cfg = await asyncio.to_thread(read_listing_config, reader.factory)
    except Exception as exc:
        # Never echo transport errors: RPC URLs can embed provider keys.
        logger.warning("listing config read failed: %s", type(exc).__name__)
        raise HTTPException(503, "chain unavailable")
    return reader, cfg


def _checksum(address: str) -> str:
    try:
        return Web3.to_checksum_address(address)
    except ValueError as exc:
        raise HTTPException(400, "invalid account address") from exc


def _read_lister_state(factory: Any, account: str, cfg: ListingConfig) -> tuple[bool, int]:
    allowed = cfg.permissionless or bool(factory.functions.listerAllowed(account).call())
    last = int(factory.functions.lastListedAt(account).call())
    return allowed, last


def _cooldown_remaining(last_listed_at: int, cfg: ListingConfig, now: int) -> int:
    if last_listed_at <= 0 or cfg.cooldown_seconds <= 0:
        return 0
    return max(0, last_listed_at + cfg.cooldown_seconds - now)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _pending_count(db: AsyncSession, creator: str) -> int:
    since = _utcnow() - timedelta(seconds=gates.PENDING_WINDOW_SECONDS)
    stmt = select(func.count()).select_from(MarketListing).where(
        MarketListing.creator == creator,
        MarketListing.status == "prepared",
        MarketListing.created_at >= since,
    )
    return int((await db.execute(stmt)).scalar_one())


async def _open_questions(db: AsyncSession, now: int, exclude_cid: str | None = None) -> list[str]:
    return await visibility.open_questions(db, now, exclude_cid=exclude_cid)


@router.get("/config")
async def listing_config() -> ListingConfigPublic:
    settings = get_settings()
    try:
        _reader, cfg = await _load_config(settings)
    except HTTPException as exc:
        return ListingConfigPublic(enabled=False, chainId=settings.chain_id, reason=str(exc.detail))
    if cfg is None:
        return ListingConfigPublic(enabled=False, chainId=settings.chain_id, reason="factory has no listing support")
    return ListingConfigPublic(
        enabled=True,
        permissionless=cfg.permissionless,
        factory=cfg.factory,
        usdc=cfg.usdc,
        oracle=cfg.oracle,
        chainId=settings.chain_id,
        minSeedUsdc=cfg.min_seed_usdc,
        listingFeeUsdc=cfg.listing_fee_usdc,
        minLeadSeconds=cfg.min_lead_seconds,
        maxHorizonSeconds=cfg.max_horizon_seconds,
        cooldownSeconds=cfg.cooldown_seconds,
    )


@router.get("/eligibility")
async def listing_eligibility(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EligibilityPublic:
    settings = get_settings()
    pending = await _pending_count(db, user.address)
    reader, cfg = await _load_config(settings)
    if cfg is None:
        return EligibilityPublic(enabled=False, allowed=False, pending=pending, reason="factory has no listing support")
    try:
        allowed, last = await asyncio.to_thread(_read_lister_state, reader.factory, _checksum(user.address), cfg)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("listing eligibility read failed: %s", type(exc).__name__)
        raise HTTPException(503, "chain unavailable")
    return EligibilityPublic(
        enabled=True,
        allowed=allowed,
        permissionless=cfg.permissionless,
        cooldownRemaining=_cooldown_remaining(last, cfg, int(time.time())),
        pending=pending,
        reason=None if allowed else "listing is invite-only",
    )


@router.post("/prepare")
async def listing_prepare(
    body: ListingPrepareIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ListingPreparePublic:
    settings = get_settings()
    now = int(time.time())
    question = body.question.strip()
    criteria = body.resolutionCriteria.strip()
    creator = user.address.lower()
    account = _checksum(creator)

    reader, cfg = await _load_config(settings)
    if cfg is None:
        raise HTTPException(409, "listing not available on this factory")
    try:
        allowed, last = await asyncio.to_thread(_read_lister_state, reader.factory, account, cfg)
    except Exception as exc:
        logger.warning("listing lister read failed: %s", type(exc).__name__)
        raise HTTPException(503, "chain unavailable")
    if not allowed:
        raise HTTPException(403, "listing is invite-only")
    remaining = _cooldown_remaining(last, cfg, now)
    if remaining > 0:
        raise HTTPException(429, f"listing cooldown: {remaining}s remaining")

    try:
        gates.validate_listing(question, criteria, body.closeTime, body.seedUsdc, cfg.bounds, now)
        gates.check_pending(await _pending_count(db, creator))
    except gates.GateError as exc:
        raise HTTPException(exc.status, exc.detail)
    if gates.is_duplicate(question, await _open_questions(db, now)):
        raise HTTPException(409, "a similar market is already open")

    salt = secrets.token_bytes(32)
    question_id = market_chain.user_question_id(account, salt)
    condition_id = market_chain.condition_id_for(cfg.oracle, question_id)
    try:
        onchain_cid = await asyncio.to_thread(lambda: reader.factory.functions.userConditionId(account, salt).call())
    except Exception as exc:
        logger.warning("userConditionId read failed: %s", type(exc).__name__)
        raise HTTPException(503, "chain unavailable")
    if market_chain.to_hex32(onchain_cid) != condition_id:
        raise HTTPException(500, "condition id mismatch between backend and factory")

    crit_hash = criteria_hash(criteria)
    calls = build_listing_calls(
        cfg.factory, cfg.usdc, salt, body.closeTime, question, crit_hash, body.seedUsdc, cfg.listing_fee_usdc
    )
    db.add(
        MarketListing(
            condition_id=condition_id,
            creator=creator,
            salt=market_chain.to_hex32(salt),
            question=question,
            resolution_criteria=criteria,
            criteria_hash=crit_hash,
            close_time=body.closeTime,
            seed_usdc=body.seedUsdc,
            status="prepared",
            created_at=_utcnow(),
        )
    )
    await db.commit()
    return ListingPreparePublic(
        conditionId=condition_id,
        questionId=market_chain.to_hex32(question_id),
        salt=market_chain.to_hex32(salt),
        criteriaHash=crit_hash,
        seedUsdc=body.seedUsdc,
        listingFeeUsdc=cfg.listing_fee_usdc,
        approveAmount=body.seedUsdc + cfg.listing_fee_usdc,
        calls=[ListingCall(**c) for c in calls],
    )


def _read_confirm_state(factory: Any, cid: bytes) -> dict[str, Any] | None:
    onchain = market_chain.read_factory_market(factory, cid)
    if onchain is None or onchain["marketType"] != MARKET_TYPE_USER:
        return onchain
    f = factory.functions
    onchain["creator"] = str(f.creatorOf(cid).call()).lower()
    onchain["criteriaHash"] = market_chain.to_hex32(f.criteriaHashOf(cid).call())
    onchain["seedUsdc"] = int(f.seedOf(cid).call())
    return onchain


@router.post("/confirm")
async def listing_confirm(
    body: ListingConfirmIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MarketPublic:
    settings = get_settings()
    cid_bytes = market_chain.hex32(body.conditionId, "conditionId")
    cid = market_chain.to_hex32(cid_bytes)
    reader = load_listing_chain(settings)
    try:
        onchain = await asyncio.to_thread(_read_confirm_state, reader.factory, cid_bytes)
    except Exception as exc:
        logger.warning("listing confirm read failed: %s", type(exc).__name__)
        raise HTTPException(503, "chain unavailable")
    if onchain is None:
        raise HTTPException(409, "not on chain yet")
    if onchain["marketType"] != MARKET_TYPE_USER:
        raise HTTPException(403, "not a user-listed market")
    if onchain["creator"] != user.address.lower():
        raise HTTPException(403, "not the market creator")

    listing = await db.get(MarketListing, cid)
    candidates = [c for c in ((listing.resolution_criteria if listing else ""), (body.resolutionCriteria or "").strip()) if c]
    if not candidates:
        raise HTTPException(400, "resolutionCriteria required")
    criteria = next((c for c in candidates if criteria_hash(c) == onchain["criteriaHash"]), None)
    if criteria is None:
        raise HTTPException(409, "criteria hash mismatch")

    # The factory stores whatever question the calldata carried; re-run the gates on it.
    reason = await visibility.review_user_listing(
        db,
        cid=cid,
        listing=listing,
        question=onchain["question"],
        close_time=onchain["closeTime"],
        seed_usdc=onchain["seedUsdc"],
        criteria=criteria,
        now=int(time.time()),
    )
    market = await _apply_confirm(db, cid, onchain, criteria, reason)
    await db.commit()
    if reason:
        # Stored as rejected: hidden from lists, detail, quotes and cdp-send (see visibility.py).
        raise HTTPException(422, f"listing rejected: {reason}")

    prices = await _latest_prices(db, [cid])
    return _to_public(market, prices.get(cid), settings)


async def _apply_confirm(db: AsyncSession, cid: str, onchain: dict[str, Any], criteria: str, reason: str | None) -> Market:
    """Upsert the Market and MarketListing rows for a confirm, race-safe against the indexer.

    Both rows are inserted ON CONFLICT DO NOTHING and then re-read, so an
    indexer tick inserting the same condition id concurrently turns this into
    a plain update instead of a UniqueViolation (500) on commit.
    """
    dialect = session_dialect(db)
    await db.execute(
        insert_ignore(
            dialect,
            Market,
            {
                "condition_id": cid,
                "parent_condition_id": "",
                "question": onchain["question"],
                "resolution_criteria": "",
                "market_type": MARKET_TYPE_USER,
                "close_time": onchain["closeTime"],
                "paused": onchain["paused"],
            },
            ["condition_id"],
        )
    )
    await db.execute(
        insert_ignore(
            dialect,
            MarketListing,
            {
                "condition_id": cid,
                "creator": onchain["creator"],
                "salt": "",
                "question": onchain["question"],
                "close_time": onchain["closeTime"],
                "status": "indexed",
                "created_at": _utcnow(),
            },
            ["condition_id"],
        )
    )
    market = await db.get(Market, cid, populate_existing=True)
    listing = await db.get(MarketListing, cid, populate_existing=True)

    market.question = onchain["question"]
    market.market_type = MARKET_TYPE_USER
    market.close_time = onchain["closeTime"]
    market.paused = onchain["paused"]
    listing.creator = onchain["creator"]
    listing.criteria_hash = onchain["criteriaHash"]
    if reason is not None:
        # Keep the prepared question/closeTime/seed so a re-confirm compares against them again.
        market.resolution_criteria = ""
        listing.status = visibility.STATUS_REJECTED
        listing.reject_reason = reason[:256]
        return market
    market.resolution_criteria = criteria
    listing.question = onchain["question"]
    listing.resolution_criteria = criteria
    listing.close_time = onchain["closeTime"]
    listing.seed_usdc = onchain["seedUsdc"]
    listing.status = visibility.STATUS_CONFIRMED
    listing.reject_reason = ""
    return market


@router.get("/review")
async def listing_review(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_operator),
) -> list[ListingReviewPublic]:
    """Operator view of type-2 markets that exist but are not public (indexed, prepared or rejected).

    They are hidden from GET /markets, so the general resolver never sees them,
    yet they stay tradable directly on the AMM: the operator factory-pauses them
    (POST /markets/{id}/pause) and arbitrates.
    """
    stmt = (
        select(Market, MarketListing)
        .outerjoin(MarketListing, MarketListing.condition_id == Market.condition_id)
        .where(Market.market_type == MARKET_TYPE_USER)
        .order_by(Market.close_time, Market.condition_id)
    )
    out: list[ListingReviewPublic] = []
    for market, listing in (await db.execute(stmt)).all():
        status = listing.status if listing is not None else "unlisted"
        if status == visibility.STATUS_CONFIRMED:
            continue
        out.append(
            ListingReviewPublic(
                conditionId=market.condition_id,
                creator=listing.creator if listing is not None else "",
                status=status,
                reason=(listing.reject_reason or "") if listing is not None else "",
                question=market.question,
                closeTime=market.close_time,
                paused=market.paused,
                resolved=market.resolved,
            )
        )
    return out
