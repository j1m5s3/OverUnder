import asyncio
import hashlib
import logging
import threading
import time
import weakref
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.router import get_current_user, require_operator
from app.config import get_settings
from app.db import get_db, insert_ignore, session_dialect
from app.markets import chain as market_chain
from app.markets.sports import is_sports_market
from app.markets.trading import halts_at, trading_open
from app.markets.visibility import is_listed, listed_clause, visible_clause
from app.models import LiveScore, Market, MarketListing, NflScheduleGame, PricePoint, User

# Factory marketType values that render as event-card primaries (0 operator primary, 2 user-listed).
PRIMARY_TYPES = (0, 2)

router = APIRouter(prefix="/markets", tags=["markets"])
logger = logging.getLogger(__name__)

# Postgres int4 columns (markets.close_time, live_scores scores, nfl_schedule_games.*):
# out-of-range request ints 422 here instead of a DataError 500 on commit.
INT4_MAX = 2**31 - 1
# MarketAMM.MIN_LP (contracts/src/MarketAMM.vy): smallest seed, in USDC base units, the AMM accepts.
AMM_MIN_LP = 10**4

# One operator EOA signs every create/pause: serialize them per process so two
# requests running in worker threads never read the same pending nonce.
_OPERATOR_TX_LOCK = threading.Lock()

# POST /markets is check-then-create: one create at a time per process (per event loop, so a lock is
# never shared across loops), plus a Postgres advisory xact lock per questionId across instances.
_CREATE_LOCKS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _create_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _CREATE_LOCKS.get(loop)
    if lock is None:
        lock = _CREATE_LOCKS[loop] = asyncio.Lock()
    return lock


def create_lock_key(question_id: bytes) -> int:
    """Signed int64 advisory key for one questionId, namespaced so it cannot equal the fixed lock keys."""
    return int.from_bytes(hashlib.sha256(b"OU:create-market:" + question_id).digest()[:8], "big", signed=True)


@asynccontextmanager
async def _serialized_create(question_id: bytes):
    """Hold the process create lock and, on Postgres, pg_advisory_xact_lock(questionId) on a dedicated
    connection until the create and its DB mirror finish. The xact lock ends with the transaction, so
    it cannot leak into the pool even if the request fails."""
    from app import db as db_mod

    async with _create_lock():
        if db_mod.engine.dialect.name != "postgresql":
            yield
            return
        async with db_mod.engine.begin() as conn:
            await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": create_lock_key(question_id)})
            yield


class CreateMarketIn(BaseModel):
    question: str = Field(max_length=512)
    resolution_criteria: str = ""
    close_time: int = Field(ge=0, le=INT4_MAX)
    question_id: str = Field(description="bytes32 hex")
    parent_condition_id: str = ""
    seed_usdc: int = Field(default=0, ge=0, lt=2**256)
    market_type: int = 0
    suggested_probability: float = Field(default=0.5, ge=0.0, le=1.0)


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
    tradingHaltsAt: int | None = None
    tradingOpen: bool = True
    yesPriceMicros: int | None = None


class ListingPublic(BaseModel):
    creator: str
    status: str
    seedUsdc: int
    criteriaHash: str


class LiveScoreIn(BaseModel):
    homeLabel: str = Field(min_length=1, max_length=128)
    awayLabel: str = Field(min_length=1, max_length=128)
    homeScore: int | None = Field(default=None, ge=0, le=INT4_MAX)
    awayScore: int | None = Field(default=None, ge=0, le=INT4_MAX)
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
    creator: str | None = None
    listing: ListingPublic | None = None


class EventCard(BaseModel):
    primary: MarketPublic
    children: list[MarketPublic]


class ScheduleGameIn(BaseModel):
    away: str = Field(min_length=1, max_length=128)
    home: str = Field(min_length=1, max_length=128)
    kickoff_unix: int = Field(ge=0, le=INT4_MAX)
    week: int = Field(ge=0, le=INT4_MAX)
    season: int = Field(ge=0, le=INT4_MAX)
    status: Literal["scheduled", "in_progress", "final", "postponed", "cancelled"] = "scheduled"
    # Omitted: keep the stored link. A condition id: link it. An explicit "": unlink (e.g. to relist a
    # game whose market was archived as an orphan).
    listedConditionId: str = Field(
        default="",
        max_length=66,
        description='Omit to keep the stored link; "" clears it (operator relist of an archived orphan).',
    )


class ScheduleGamePublic(ScheduleGameIn):
    id: int


class PricePointPublic(BaseModel):
    conditionId: str
    ts: int
    yesPriceMicros: int


def _visible():
    # Unpaused, and for user-listed (type 2) markets only a confirmed listing (visibility.py).
    return visible_clause()


def _child_of(parent_condition_id: str):
    return and_(
        _visible(),
        Market.market_type == 1,
        Market.parent_condition_id == parent_condition_id,
    )


@router.get("")
async def list_markets(
    parent_id: str | None = Query(default=None, alias="parentId"),
    include_paused: bool = Query(
        default=False,
        alias="includePaused",
        description="Operator bearer JWT required: also list paused (and archived) markets, so the oracle job "
        "can still resolve a paused registered market.",
    ),
    authorization: str | None = Header(default=None, include_in_schema=False),
    db: AsyncSession = Depends(get_db),
) -> list[EventCard] | list[MarketPublic]:
    settings = get_settings()
    now = int(time.time())
    shown = _visible()
    if include_paused:
        # Public requests are unchanged; only an operator may see paused rows (type-2 listings still
        # need a confirmed listing: unlisted ones are on GET /markets/listing/review).
        require_operator(await get_current_user(authorization, db))
        shown = listed_clause()
    if parent_id:
        stmt = select(Market).where(shown, Market.parent_condition_id == parent_id)
        rows = (await db.execute(stmt)).scalars().all()
        prices = await _latest_prices(db, [m.condition_id for m in rows])
        return [_to_public(m, prices.get(m.condition_id), settings, now) for m in rows]

    visible = list((await db.execute(select(Market).where(shown))).scalars().all())
    prices = await _latest_prices(db, [m.condition_id for m in visible])

    def pub(m: Market) -> MarketPublic:
        return _to_public(m, prices.get(m.condition_id), settings, now)

    primary_ids = {m.condition_id for m in visible if m.market_type in PRIMARY_TYPES}
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
        if primary.market_type not in PRIMARY_TYPES:
            continue
        kids = sorted(children_by_parent.get(primary.condition_id, []), key=lambda row: row.condition_id)
        cards.append(EventCard(primary=pub(primary), children=[pub(c) for c in kids]))
    for orphan in orphans:
        cards.append(EventCard(primary=pub(orphan), children=[]))
    return cards


@router.get("/schedule")
async def get_schedule(
    season: int | None = Query(default=None),
    week: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[ScheduleGamePublic]:
    stmt = select(NflScheduleGame)
    if season is not None:
        stmt = stmt.where(NflScheduleGame.season == season)
    if week is not None:
        stmt = stmt.where(NflScheduleGame.week == week)
    stmt = stmt.order_by(NflScheduleGame.season, NflScheduleGame.week, NflScheduleGame.kickoff_unix)
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_schedule(row) for row in rows]


@router.post("/schedule")
async def upsert_schedule(
    games: list[ScheduleGameIn],
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_operator),
) -> list[ScheduleGamePublic]:
    seasons_weeks: set[tuple[int, int]] = set()
    for game in games:
        seasons_weeks.add((game.season, game.week))
        existing = (
            await db.execute(
                select(NflScheduleGame).where(
                    NflScheduleGame.season == game.season,
                    NflScheduleGame.week == game.week,
                    NflScheduleGame.home == game.home,
                    NflScheduleGame.away == game.away,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                NflScheduleGame(
                    season=game.season,
                    week=game.week,
                    home=game.home,
                    away=game.away,
                    kickoff_unix=game.kickoff_unix,
                    status=game.status,
                    listed_condition_id=game.listedConditionId,
                )
            )
        else:
            existing.kickoff_unix = game.kickoff_unix
            existing.status = game.status
            if game.listedConditionId or "listedConditionId" in game.model_fields_set:
                # An explicit "" unlinks the row; an omitted field never touches the link.
                existing.listed_condition_id = game.listedConditionId
    await db.commit()
    out: list[ScheduleGamePublic] = []
    for season, week in sorted(seasons_weeks):
        rows = (
            await db.execute(
                select(NflScheduleGame)
                .where(NflScheduleGame.season == season, NflScheduleGame.week == week)
                .order_by(NflScheduleGame.kickoff_unix)
            )
        ).scalars().all()
        out.extend(_to_schedule(row) for row in rows)
    return out


@router.get("/{condition_id}/history")
async def get_market_history(condition_id: str, db: AsyncSession = Depends(get_db)) -> list[PricePointPublic]:
    m = await db.get(Market, condition_id)
    if m is None or not await is_listed(db, m):
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
    # Unconfirmed or rejected user listings are not public (operators: GET /markets/listing/review).
    if m is None or not await is_listed(db, m):
        raise HTTPException(404, "market not found")
    kids = (await db.execute(select(Market).where(_child_of(m.condition_id)).order_by(Market.condition_id))).scalars().all()
    settings = get_settings()
    now = int(time.time())
    prices = await _latest_prices(db, [m.condition_id, *(c.condition_id for c in kids)])
    public = _to_public(m, prices.get(m.condition_id), settings, now)
    score = await _get_score(db, m)
    facts = await _get_facts(db, m)
    listing = await _get_listing(db, m)
    return MarketDetail(
        **public.model_dump(),
        children=[_to_public(c, prices.get(c.condition_id), settings, now) for c in kids],
        score=score,
        facts=facts,
        creator=listing.creator if listing else None,
        listing=listing,
    )


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
    settings = get_settings()

    if not settings.operator_private_key:
        raise HTTPException(500, "operator_private_key not configured")

    if body.seed_usdc < AMM_MIN_LP:
        # MarketAMM._seed asserts usdcAmount >= MIN_LP ("seed below min lp"): fail before any tx.
        raise HTTPException(422, f"seed_usdc must be at least {AMM_MIN_LP} (MarketAMM MIN_LP, base units)")

    if body.market_type not in (0, 1):
        raise HTTPException(400, "user markets use /markets/listing")

    question_id_bytes = market_chain.hex32(body.question_id, "question_id")
    parent_bytes = market_chain.hex32(body.parent_condition_id, "parent_condition_id") if body.market_type == 1 else None

    # Chain reads, the faucet/approve/create txs and their receipts run in a worker
    # thread so a slow RPC or a stuck tx never blocks the event loop; the DB session
    # stays on the loop. Serialized per questionId: a replay racing a create re-reads
    # marketExists after it and mirrors the market instead of sending a second create.
    async with _serialized_create(question_id_bytes):
        _kind, chain_row = await asyncio.to_thread(_create_on_chain, settings, body, question_id_bytes, parent_bytes)
        row = await _upsert_market_row(db, chain_row, body)
    return _public(row)


def _operator_tx(w3, operator_acct, fn, settings, gas: int):
    """Build, sign, send and wait for one operator tx (bounded wait). Caller holds _OPERATOR_TX_LOCK."""
    tx = fn.build_transaction({
        "from": operator_acct.address,
        "nonce": w3.eth.get_transaction_count(operator_acct.address, "pending"),
        "chainId": settings.chain_id,
        "gas": gas,
        "gasPrice": w3.eth.gas_price,
    })
    signed = operator_acct.sign_transaction(tx)
    txh = w3.eth.send_raw_transaction(signed.raw_transaction)
    return w3.eth.wait_for_transaction_receipt(txh, timeout=settings.operator_tx_timeout_seconds)


def _create_on_chain(settings, body: CreateMarketIn, question_id_bytes: bytes, parent_bytes: bytes | None) -> tuple[str, dict[str, Any]]:
    """Sync chain half of POST /markets: ("existing", row) on an idempotent replay, else ("created", row).

    Runs in a worker thread; never touches the DB session. HTTPExceptions raised
    here propagate to the handler through asyncio.to_thread.
    """
    chain = market_chain.load_factory_chain(settings)
    w3, factory, usdc, operator_acct = chain.w3, chain.factory, chain.usdc, chain.operator

    with _OPERATOR_TX_LOCK:
        # Idempotency (step 12): the condition id is a pure function of (oracle, questionId),
        # so a replay finds the market on chain and mirrors it without sending a tx. Read under
        # the operator lock (and the caller's per-questionId lock) so a racing replay sees the
        # first create's receipt instead of a stale "not created".
        try:
            condition_id = market_chain.condition_id_for(factory.functions.oracle().call(), question_id_bytes)
            cid_bytes = bytes.fromhex(condition_id[2:])
            onchain = market_chain.read_factory_market(factory, cid_bytes)
            prepared_elsewhere = onchain is None and int(chain.ctf.functions.outcomeSlots(cid_bytes).call()) != 0
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"failed to read factory state: {type(e).__name__}")
        if onchain is not None:
            return "existing", onchain
        if prepared_elsewhere:
            # Squatted questionId (anyone can prepareCondition) or a legacy market not imported into this factory.
            raise HTTPException(409, "condition prepared outside this factory")

        balance = usdc.functions.balanceOf(operator_acct.address).call()
        if balance < body.seed_usdc:
            try:
                _operator_tx(w3, operator_acct, usdc.functions.faucet(body.seed_usdc), settings, 100_000)
            except Exception as e:
                raise HTTPException(500, f"failed to fund operator: {type(e).__name__}")

        allowance = usdc.functions.allowance(operator_acct.address, factory.address).call()
        if allowance < body.seed_usdc:
            try:
                _operator_tx(w3, operator_acct, usdc.functions.approve(factory.address, body.seed_usdc), settings, 100_000)
            except Exception as e:
                raise HTTPException(500, f"failed to approve USDC: {type(e).__name__}")

        try:
            if body.market_type == 0:
                create_fn = factory.functions.createPrimaryMarket(
                    question_id_bytes,
                    body.close_time,
                    body.question,
                    body.seed_usdc,
                )
            else:
                create_fn = factory.functions.createWildcardMarket(
                    question_id_bytes,
                    parent_bytes,
                    body.close_time,
                    body.question,
                    body.seed_usdc,
                )
            receipt = _operator_tx(w3, operator_acct, create_fn, settings, 1_000_000)
        except HTTPException:
            raise
        except Exception as e:
            # Type only: transport errors can embed the RPC URL. A timed-out tx is recovered
            # by the idempotent replay on the oracle job's next run.
            raise HTTPException(500, f"failed to create market on chain: {type(e).__name__}")

    if receipt["status"] != 1:
        raise HTTPException(500, "market creation transaction failed")

    logs = factory.events.MarketCreated().process_receipt(receipt)
    if not logs:
        raise HTTPException(500, "no MarketCreated event in receipt")

    event = logs[0]["args"]
    parent = bytes(event["parentConditionId"])
    return "created", {
        "conditionId": market_chain.to_hex32(event["conditionId"]),
        "parentConditionId": "" if parent == market_chain.ZERO32 else market_chain.to_hex32(parent),
        "closeTime": int(event["closeTime"]),
        "marketType": int(event["marketType"]),
        "question": str(event["question"]),
    }


async def _upsert_market_row(db: AsyncSession, chain_row: dict[str, Any], body: CreateMarketIn) -> Market:
    """Mirror a factory market into the DB. Chain fields win; the body only supplies off-chain metadata.

    INSERT ... ON CONFLICT DO NOTHING first: the indexer may insert the same row from MarketCreated
    at any moment, and a lost race must not turn a successful create into a 500."""
    cid = chain_row["conditionId"]
    result = await db.execute(
        insert_ignore(
            session_dialect(db),
            Market,
            {
                "condition_id": cid,
                "parent_condition_id": chain_row["parentConditionId"],
                "question": chain_row["question"],
                "resolution_criteria": body.resolution_criteria,
                "market_type": chain_row["marketType"],
                "close_time": chain_row["closeTime"],
                "paused": bool(chain_row.get("paused", False)),
                "suggested_probability": body.suggested_probability,
            },
            ["condition_id"],
        )
    )
    inserted = bool(result.rowcount)
    existing = (
        await db.execute(select(Market).where(Market.condition_id == cid).execution_options(populate_existing=True))
    ).scalar_one()
    if not inserted:
        existing.question = chain_row["question"]
        existing.market_type = chain_row["marketType"]
        existing.close_time = chain_row["closeTime"]
        existing.parent_condition_id = chain_row["parentConditionId"]
        # MarketCreated events carry no paused flag; only a markets() read may flip it.
        if "paused" in chain_row:
            existing.paused = bool(chain_row["paused"])
        if body.resolution_criteria:
            existing.resolution_criteria = body.resolution_criteria
        existing.suggested_probability = body.suggested_probability
    await db.commit()
    return existing


def _pause_on_chain(settings, cid_bytes: bytes) -> None:
    """Sync factory.setPaused(cid, True) for POST /markets/{id}/pause; runs in a worker thread."""
    from eth_account import Account
    from web3 import Web3

    from app.contract_addresses import get_contract_addresses

    try:
        addresses = get_contract_addresses()
        factory_abi = market_chain.factory_abi()
    except Exception as e:
        raise HTTPException(500, f"failed to load contract config: {type(e).__name__}")
    if "MarketFactory" not in addresses:
        raise HTTPException(500, "MarketFactory address not configured")

    w3 = Web3(Web3.HTTPProvider(settings.anvil_rpc_url, request_kwargs={"timeout": settings.operator_rpc_timeout_seconds}))
    if not w3.is_connected():
        raise HTTPException(500, "RPC not available")
    operator_acct = Account.from_key(settings.operator_private_key)
    factory = w3.eth.contract(address=Web3.to_checksum_address(addresses["MarketFactory"]), abi=factory_abi)
    try:
        with _OPERATOR_TX_LOCK:
            receipt = _operator_tx(w3, operator_acct, factory.functions.setPaused(cid_bytes, True), settings, 100_000)
    except Exception as e:
        raise HTTPException(500, f"failed to pause market on chain: {type(e).__name__}")
    if receipt["status"] != 1:
        raise HTTPException(500, "setPaused transaction failed")


@router.post("/{condition_id}/pause")
async def pause_market(
    condition_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_operator),
):
    settings = get_settings()
    m = await db.get(Market, condition_id)
    if m is None:
        raise HTTPException(404, "market not found")
    if not settings.operator_private_key:
        raise HTTPException(500, "operator_private_key not configured")
    cid_bytes = market_chain.hex32(condition_id, "condition_id")
    await asyncio.to_thread(_pause_on_chain, settings, cid_bytes)
    m.paused = True
    await db.commit()
    return _public(m)


def _oracle_close_time(settings, cid_bytes: bytes) -> int:
    """ConsensusOracle.closeTime(cid) on the configured oracle; 0 means never registered there."""
    from web3 import Web3

    from app.contract_addresses import get_contract_addresses, load_abi

    try:
        addresses = get_contract_addresses()
        abi = load_abi("ConsensusOracle")
    except Exception as e:
        raise HTTPException(503, f"failed to load contract config: {type(e).__name__}")
    if "ConsensusOracle" not in addresses:
        raise HTTPException(503, "ConsensusOracle address not configured")
    w3 = Web3(Web3.HTTPProvider(settings.anvil_rpc_url, request_kwargs={"timeout": settings.operator_rpc_timeout_seconds}))
    oracle = w3.eth.contract(address=Web3.to_checksum_address(addresses["ConsensusOracle"]), abi=abi)
    return int(oracle.functions.closeTime(cid_bytes).call())


@router.post("/{condition_id}/archive")
async def archive_market(
    condition_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_operator),
):
    """Hide an orphaned row created on an older deployment (DB only, no tx).

    Allowed only when the configured ConsensusOracle has no closeTime for the
    condition, i.e. it can never resolve here. Registered markets must be
    paused on the factory instead (POST /markets/{id}/pause). Idempotent.
    """
    settings = get_settings()
    cid = "0x" + condition_id.strip().lower().removeprefix("0x")
    m = await db.get(Market, cid)
    if m is None:
        raise HTTPException(404, "market not found")
    cid_bytes = market_chain.hex32(cid, "condition_id")
    try:
        close_time = await asyncio.to_thread(_oracle_close_time, settings, cid_bytes)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("archive: oracle closeTime read failed: %s", type(e).__name__)
        raise HTTPException(503, "chain unavailable")
    if close_time != 0:
        raise HTTPException(409, "market is registered on the configured oracle; pause it on the factory instead")
    if not m.paused:
        m.paused = True
    # Unlink schedule rows that point at the orphan so the listing job can relist the game.
    await db.execute(
        update(NflScheduleGame)
        .where(NflScheduleGame.listed_condition_id == cid)
        .values(listed_condition_id="")
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return _public(m)


def _to_public(m: Market, yes_price: int | None = None, settings=None, now: int | None = None) -> MarketPublic:
    return MarketPublic.model_validate(_public(m, yes_price=yes_price, settings=settings, now=now))


async def _latest_prices(db: AsyncSession, condition_ids: list[str]) -> dict[str, int]:
    """Latest indexed YES price per market (same ordering as /history), one query."""
    if not condition_ids:
        return {}
    rank = (
        func.row_number()
        .over(
            partition_by=PricePoint.condition_id,
            order_by=(
                PricePoint.ts.desc(),
                PricePoint.block_number.desc(),
                PricePoint.log_index.desc(),
                PricePoint.id.desc(),
            ),
        )
        .label("rank")
    )
    ranked = (
        select(PricePoint.condition_id, PricePoint.yes_price_micros, rank)
        .where(PricePoint.condition_id.in_(set(condition_ids)))
        .subquery()
    )
    rows = await db.execute(select(ranked.c.condition_id, ranked.c.yes_price_micros).where(ranked.c.rank == 1))
    return {cid: int(price) for cid, price in rows.all()}


async def _get_listing(db: AsyncSession, m: Market) -> ListingPublic | None:
    if m.market_type != 2:
        return None
    row = await db.get(MarketListing, m.condition_id)
    if row is None:
        return None
    return ListingPublic(
        creator=row.creator,
        status=row.status,
        seedUsdc=int(row.seed_usdc or 0),
        criteriaHash=row.criteria_hash,
    )


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


def _to_schedule(row: NflScheduleGame) -> ScheduleGamePublic:
    return ScheduleGamePublic(
        id=row.id,
        away=row.away,
        home=row.home,
        kickoff_unix=row.kickoff_unix,
        week=row.week,
        season=row.season,
        status=row.status,  # type: ignore[arg-type]
        listedConditionId=row.listed_condition_id,
    )


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


def _public(m: Market, *, yes_price: int | None = None, settings=None, now: int | None = None) -> dict:
    settings = settings or get_settings()
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
        "tradingHaltsAt": halts_at(m, settings),
        "tradingOpen": trading_open(m, settings, now),
        "yesPriceMicros": yes_price,
    }
