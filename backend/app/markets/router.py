from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.router import require_operator
from app.db import get_db
from app.markets.sports import is_sports_market
from app.models import LiveScore, Market, User

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


class MarketPublic(BaseModel):
    conditionId: str
    parentConditionId: str | None = None
    question: str
    resolutionCriteria: str = ""
    marketType: int
    closeTime: int
    paused: bool
    resolved: bool
    payoutYes: int = 0
    payoutNo: int = 0
    suggestedProbability: float = 0.5


class LiveScoreIn(BaseModel):
    homeLabel: str = Field(min_length=1, max_length=128)
    awayLabel: str = Field(min_length=1, max_length=128)
    homeScore: int | None = Field(default=None, ge=0)
    awayScore: int | None = Field(default=None, ge=0)
    status: Literal["scheduled", "in_progress", "final", "postponed", "cancelled"] = "scheduled"
    periodLabel: str | None = Field(default=None, max_length=16)
    facts: dict[str, Any] | None = None


class LiveScorePublic(LiveScoreIn):
    conditionId: str
    updatedAt: datetime


class MarketDetail(MarketPublic):
    children: list[MarketPublic]
    score: LiveScorePublic | None = None
    facts: dict[str, Any] | None = None


class EventCard(BaseModel):
    primary: MarketPublic
    children: list[MarketPublic]


class PricePointPublic(BaseModel):
    conditionId: str
    ts: int
    yesPriceMicros: int


def _visible():
    return Market.paused.is_(False)


def _child_of(parent_condition_id: str):
    return and_(
        _visible(),
        Market.market_type == 1,
        Market.parent_condition_id == parent_condition_id,
    )


@router.get("")
async def list_markets(
    parent_id: str | None = Query(default=None, alias="parentId"),
    db: AsyncSession = Depends(get_db),
) -> list[EventCard] | list[MarketPublic]:
    if parent_id:
        stmt = select(Market).where(_visible(), Market.parent_condition_id == parent_id)
        rows = (await db.execute(stmt)).scalars().all()
        return [_to_public(m) for m in rows]

    visible = list((await db.execute(select(Market).where(_visible()))).scalars().all())
    primary_ids = {m.condition_id for m in visible if m.market_type == 0}
    children_by_parent: dict[str, list[Market]] = {}
    orphans: list[Market] = []
    for m in visible:
        if m.market_type != 1:
            continue
        parent = m.parent_condition_id or ""
        if parent and parent in primary_ids:
            children_by_parent.setdefault(parent, []).append(m)
        else:
            orphans.append(m)

    cards: list[EventCard] = []
    for primary in visible:
        if primary.market_type != 0:
            continue
        kids = sorted(children_by_parent.get(primary.condition_id, []), key=lambda row: row.condition_id)
        cards.append(EventCard(primary=_to_public(primary), children=[_to_public(c) for c in kids]))
    for orphan in orphans:
        cards.append(EventCard(primary=_to_public(orphan), children=[]))
    return cards


@router.get("/{condition_id}/history")
async def get_market_history(condition_id: str, db: AsyncSession = Depends(get_db)) -> list[PricePointPublic]:
    from app.models import PricePoint

    m = await db.get(Market, condition_id)
    if m is None:
        raise HTTPException(404, "market not found")
    rows = (
        await db.execute(
            select(PricePoint)
            .where(PricePoint.condition_id == condition_id)
            .order_by(PricePoint.ts, PricePoint.block_number, PricePoint.log_index, PricePoint.id)
        )
    ).scalars().all()
    return [
        PricePointPublic(conditionId=r.condition_id, ts=r.ts, yesPriceMicros=r.yes_price_micros)
        for r in rows
    ]


@router.get("/{condition_id}")
async def get_market(condition_id: str, db: AsyncSession = Depends(get_db)) -> MarketDetail:
    m = await db.get(Market, condition_id)
    if m is None:
        raise HTTPException(404, "market not found")
    kids = (await db.execute(select(Market).where(_child_of(m.condition_id)).order_by(Market.condition_id))).scalars().all()
    public = _to_public(m)
    score = await _get_score(db, m)
    facts = await _get_facts(db, m)
    return MarketDetail(**public.model_dump(), children=[_to_public(c) for c in kids], score=score, facts=facts)


@router.post("/{condition_id}/score")
async def upsert_score(
    condition_id: str,
    body: LiveScoreIn,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_operator),
) -> LiveScorePublic:
    m = await db.get(Market, condition_id)
    if m is None:
        raise HTTPException(404, "market not found")
    if m.market_type != 0:
        raise HTTPException(400, "scores only on primaries")
    if not is_sports_market(m.question):
        raise HTTPException(400, "scores only on sports primaries")
    if body.status == "scheduled" and body.homeScore == 0 and body.awayScore == 0:
        raise HTTPException(400, "scheduled games have no score yet")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = await db.get(LiveScore, condition_id)
    if row is None:
        row = LiveScore(
            condition_id=condition_id,
            home_label=body.homeLabel,
            away_label=body.awayLabel,
            home_score=body.homeScore,
            away_score=body.awayScore,
            status=body.status,
            period_label=body.periodLabel,
            facts=body.facts,
            updated_at=now,
        )
        db.add(row)
    else:
        row.home_label = body.homeLabel
        row.away_label = body.awayLabel
        row.home_score = body.homeScore
        row.away_score = body.awayScore
        row.status = body.status
        row.period_label = body.periodLabel
        row.facts = body.facts
        row.updated_at = now
    await db.commit()
    return _to_score(row)


@router.post("")
async def create_market(
    body: CreateMarketIn,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_operator),
):
    from app.config import get_settings
    from app.contract_addresses import get_contract_addresses, load_abi
    from web3 import Web3
    from eth_account import Account

    settings = get_settings()
    
    if not settings.operator_private_key:
        raise HTTPException(500, "operator_private_key not configured")
    
    if body.seed_usdc <= 0:
        raise HTTPException(400, "seed_usdc must be > 0")
    
    try:
        addresses = get_contract_addresses()
        factory_abi = load_abi("MarketFactory")
        usdc_abi = load_abi("MockUSDC")
    except Exception as e:
        raise HTTPException(500, f"failed to load contract config: {e}")
    
    if "MarketFactory" not in addresses or "MockUSDC" not in addresses:
        raise HTTPException(500, "MarketFactory or MockUSDC address not configured")
    
    w3 = Web3(Web3.HTTPProvider(settings.anvil_rpc_url))
    if not w3.is_connected():
        raise HTTPException(500, "RPC not available")
    
    operator_acct = Account.from_key(settings.operator_private_key)
    factory = w3.eth.contract(address=Web3.to_checksum_address(addresses["MarketFactory"]), abi=factory_abi)
    usdc = w3.eth.contract(address=Web3.to_checksum_address(addresses["MockUSDC"]), abi=usdc_abi)
    
    balance = usdc.functions.balanceOf(operator_acct.address).call()
    if balance < body.seed_usdc:
        try:
            faucet_tx = usdc.functions.faucet(body.seed_usdc).build_transaction({
                "from": operator_acct.address,
                "nonce": w3.eth.get_transaction_count(operator_acct.address),
                "chainId": settings.chain_id,
                "gas": 100_000,
                "gasPrice": w3.eth.gas_price,
            })
            signed_faucet = operator_acct.sign_transaction(faucet_tx)
            faucet_txh = w3.eth.send_raw_transaction(signed_faucet.raw_transaction)
            w3.eth.wait_for_transaction_receipt(faucet_txh)
        except Exception as e:
            raise HTTPException(500, f"failed to fund operator: {e}")
    
    allowance = usdc.functions.allowance(operator_acct.address, factory.address).call()
    if allowance < body.seed_usdc:
        try:
            approve_tx = usdc.functions.approve(factory.address, body.seed_usdc).build_transaction({
                "from": operator_acct.address,
                "nonce": w3.eth.get_transaction_count(operator_acct.address),
                "chainId": settings.chain_id,
                "gas": 100_000,
                "gasPrice": w3.eth.gas_price,
            })
            signed_approve = operator_acct.sign_transaction(approve_tx)
            approve_txh = w3.eth.send_raw_transaction(signed_approve.raw_transaction)
            w3.eth.wait_for_transaction_receipt(approve_txh)
        except Exception as e:
            raise HTTPException(500, f"failed to approve USDC: {e}")
    
    question_id_bytes = bytes.fromhex(body.question_id[2:] if body.question_id.startswith("0x") else body.question_id)
    
    try:
        if body.market_type == 0:
            create_tx = factory.functions.createPrimaryMarket(
                question_id_bytes,
                body.close_time,
                body.question,
                body.seed_usdc,
            ).build_transaction({
                "from": operator_acct.address,
                "nonce": w3.eth.get_transaction_count(operator_acct.address),
                "chainId": settings.chain_id,
                "gas": 1_000_000,
                "gasPrice": w3.eth.gas_price,
            })
        else:
            parent_bytes = bytes.fromhex(body.parent_condition_id[2:] if body.parent_condition_id.startswith("0x") else body.parent_condition_id)
            create_tx = factory.functions.createWildcardMarket(
                question_id_bytes,
                parent_bytes,
                body.close_time,
                body.question,
                body.seed_usdc,
            ).build_transaction({
                "from": operator_acct.address,
                "nonce": w3.eth.get_transaction_count(operator_acct.address),
                "chainId": settings.chain_id,
                "gas": 1_000_000,
                "gasPrice": w3.eth.gas_price,
            })
        
        signed_create = operator_acct.sign_transaction(create_tx)
        create_txh = w3.eth.send_raw_transaction(signed_create.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(create_txh)
        
        if receipt["status"] != 1:
            raise HTTPException(500, "market creation transaction failed")
        
        logs = factory.events.MarketCreated().process_receipt(receipt)
        if not logs:
            raise HTTPException(500, "no MarketCreated event in receipt")
        
        event = logs[0]["args"]
        condition_id = "0x" + event["conditionId"].hex()
        parent_condition_id = "0x" + event["parentConditionId"].hex()
        
        existing = await db.get(Market, condition_id)
        if existing is None:
            market = Market(
                condition_id=condition_id,
                parent_condition_id="" if parent_condition_id == "0x" + "00" * 32 else parent_condition_id,
                question=event["question"],
                resolution_criteria=body.resolution_criteria,
                market_type=event["marketType"],
                close_time=event["closeTime"],
                suggested_probability=body.suggested_probability,
            )
            db.add(market)
        else:
            existing.question = event["question"]
            existing.market_type = event["marketType"]
            existing.close_time = event["closeTime"]
            existing.resolution_criteria = body.resolution_criteria
            existing.suggested_probability = body.suggested_probability
        
        await db.commit()
        
        final = await db.get(Market, condition_id)
        return _public(final)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"failed to create market on chain: {e}")


@router.post("/{condition_id}/pause")
async def pause_market(
    condition_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_operator),
):
    from app.config import get_settings
    from app.contract_addresses import get_contract_addresses, load_abi
    from web3 import Web3
    from eth_account import Account

    settings = get_settings()
    
    m = await db.get(Market, condition_id)
    if m is None:
        raise HTTPException(404, "market not found")
    
    if not settings.operator_private_key:
        raise HTTPException(500, "operator_private_key not configured")
    
    try:
        addresses = get_contract_addresses()
        factory_abi = load_abi("MarketFactory")
    except Exception as e:
        raise HTTPException(500, f"failed to load contract config: {e}")
    
    if "MarketFactory" not in addresses:
        raise HTTPException(500, "MarketFactory address not configured")
    
    w3 = Web3(Web3.HTTPProvider(settings.anvil_rpc_url))
    if not w3.is_connected():
        raise HTTPException(500, "RPC not available")
    
    operator_acct = Account.from_key(settings.operator_private_key)
    factory = w3.eth.contract(address=Web3.to_checksum_address(addresses["MarketFactory"]), abi=factory_abi)
    
    cid_bytes = bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id)
    
    try:
        pause_tx = factory.functions.setPaused(cid_bytes, True).build_transaction({
            "from": operator_acct.address,
            "nonce": w3.eth.get_transaction_count(operator_acct.address),
            "chainId": settings.chain_id,
            "gas": 100_000,
            "gasPrice": w3.eth.gas_price,
        })
        
        signed = operator_acct.sign_transaction(pause_tx)
        txh = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(txh)
        
        if receipt["status"] != 1:
            raise HTTPException(500, "setPaused transaction failed")
        
        m.paused = True
        await db.commit()
        return _public(m)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"failed to pause market on chain: {e}")


def _to_public(m: Market) -> MarketPublic:
    return MarketPublic.model_validate(_public(m))


async def _get_facts(db: AsyncSession, m: Market) -> dict | None:
    cid = m.condition_id if m.market_type == 0 else (m.parent_condition_id or "")
    if not cid:
        return None
    row = await db.get(LiveScore, cid)
    if row is None:
        return None
    return row.facts


async def _get_score(db: AsyncSession, m: Market) -> LiveScorePublic | None:
    if m.market_type != 0:
        return None
    row = await db.get(LiveScore, m.condition_id)
    if row is None:
        return None
    return _to_score(row)


def _to_score(row: LiveScore) -> LiveScorePublic:
    return LiveScorePublic(
        conditionId=row.condition_id,
        homeLabel=row.home_label,
        awayLabel=row.away_label,
        homeScore=row.home_score,
        awayScore=row.away_score,
        status=row.status,  # type: ignore[arg-type]
        periodLabel=row.period_label,
        facts=row.facts,
        updatedAt=row.updated_at,
    )


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
