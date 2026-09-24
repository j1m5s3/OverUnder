"""Best-effort chain indexer. Polls Factory/AMM logs when RPC is available."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from statistics import NormalDist
from typing import Any

from sqlalchemy import select, text, update

from app.config import get_settings
from app.contract_addresses import get_contract_addresses, load_abi
from app.db import PRICE_POINT_KEY, SessionLocal, engine, insert_ignore, session_dialect
from app.models import Checkpoint, IndexerCheckpoint, Market, MarketListing, PricePoint

logger = logging.getLogger(__name__)

SEED_PRICE_MICROS = 500_000
ANVIL_CHAIN_ID = 31337
MARKET_TYPE_USER = 2
_STD_NORMAL = NormalDist()


@dataclass
class _IndexerState:
    """Per-process adaptive state: the current getLogs window."""

    block_range: int | None = None


_STATE = _IndexerState()


@dataclass
class _LeaderState:
    """Dedicated connection holding the indexer's Postgres session advisory lock."""

    conn: Any = None


_LEADER = _LeaderState()


async def _drop_leader_conn(invalidate: bool = False) -> None:
    """Forget the leader connection. `invalidate` ends its server session first: a pooled close()
    only rolls back, so a session advisory lock would otherwise survive in the pool and make every
    later pg_try_advisory_lock (this instance's included) return false until the connection recycles."""
    conn, _LEADER.conn = _LEADER.conn, None
    if conn is None:
        return
    if invalidate:
        try:
            await conn.invalidate()
        except Exception:
            pass
    try:
        await conn.close()
    except Exception:
        pass


async def ensure_indexer_leader(settings) -> bool:
    """True when this process may index. SQLite: always (single process).

    Postgres: pg_try_advisory_lock on one dedicated connection held across
    ticks (never a pooled SessionLocal connection, which index_once commits and
    returns to the pool with the lock still attached). Every API instance runs
    the loop; only the lock holder indexes, so two instances (autoscale, or old
    and new revisions during a rollout) never index the same range at once.
    """
    if engine.dialect.name != "postgresql":
        return True
    key = settings.indexer_leader_lock_key
    if _LEADER.conn is not None:
        try:
            await _LEADER.conn.execute(text("SELECT 1"))
            await _LEADER.conn.commit()
            return True
        except Exception as e:
            logger.warning(f"Indexer lost its leader connection ({type(e).__name__}); re-electing")
            await _drop_leader_conn(invalidate=True)
    conn = await engine.connect()
    try:
        got = bool((await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})).scalar())
        await conn.commit()
    except Exception:
        # The lock may have been granted before the failure: never return this session to the pool.
        try:
            await conn.invalidate()
        except Exception:
            pass
        await conn.close()
        raise
    if not got:
        await conn.close()
        return False
    _LEADER.conn = conn
    logger.info("Indexer acquired leadership")
    return True


async def release_indexer_leadership() -> None:
    """Unlock and close the leader connection (lifespan shutdown). Safe when not leader."""
    conn = _LEADER.conn
    if conn is None:
        return
    try:
        await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": get_settings().indexer_leader_lock_key})
        await conn.commit()
        unlocked = True
    except Exception:
        unlocked = False
    await _drop_leader_conn(invalidate=not unlocked)


def mid_to_micros(yes_reserve: int, no_reserve: int) -> int:
    """Pool-mid YES price in micros: no / (yes + no).

    On this CPMM a yes buy drains yesReserve and fills noReserve while
    real p(yes) rises, so the YES price is the NO reserve share.
    Empty pool seeds at 0.5.
    """
    yes_reserve = yes_reserve or 0
    no_reserve = no_reserve or 0
    total = yes_reserve + no_reserve
    if total <= 0:
        return SEED_PRICE_MICROS
    return int(no_reserve * 1_000_000 // total)


def pm_price_micros(yes_reserve: int, no_reserve: int, liquidity: int) -> int:
    """MarketAMM v2 (pm-AMM) YES price in micros: Phi((no - yes) / L).

    Same sign as the CPMM mid: a YES buy drains yesReserve, so (no - yes)
    grows and the price rises. L <= 0 (legacy pool) falls back to the mid.
    """
    if not liquidity or liquidity <= 0:
        return mid_to_micros(yes_reserve, no_reserve)
    p = _STD_NORMAL.cdf(((no_reserve or 0) - (yes_reserve or 0)) / liquidity)
    return max(0, min(1_000_000, round(p * 1_000_000)))


def pool_price_micros(pool) -> int:
    """YES micros from a `pools(cid)` tuple: pm-AMM when it carries liquidity (index 4), else CPMM mid."""
    yes_reserve, no_reserve = int(pool[0]), int(pool[1])
    if len(pool) >= 5:
        liquidity = int(pool[4])
        if liquidity > 0:
            return pm_price_micros(yes_reserve, no_reserve, liquidity)
    return mid_to_micros(yes_reserve, no_reserve)


def build_swap_point(
    condition_id: str,
    ts: int,
    block_number: int,
    log_index: int,
    yes_reserve: int,
    no_reserve: int,
    liquidity: int = 0,
) -> PricePoint:
    """Pure Swap -> PricePoint constructor (no RPC; pool price only, never trade-implied)."""
    return PricePoint(
        condition_id=condition_id,
        ts=ts,
        block_number=block_number,
        log_index=log_index,
        yes_price_micros=pm_price_micros(yes_reserve, no_reserve, liquidity),
    )


def build_seed_point(condition_id: str, ts: int, block_number: int, log_index: int) -> PricePoint:
    """Pure PoolSeeded -> first PricePoint at yesPrice 0.5."""
    return PricePoint(
        condition_id=condition_id,
        ts=ts,
        block_number=block_number,
        log_index=log_index,
        yes_price_micros=SEED_PRICE_MICROS,
    )


def resolve_start_block(last_block: int, start_block: int, head: int, chain_id: int, lookback: int) -> int:
    """First block to index this tick, from the current address set's checkpoint.

    INDEXER_START_BLOCK > 0 is a floor: a fresh address set starts exactly there,
    and a checkpoint behind it fast-forwards; it never rewinds a checkpoint.
    Unset on anvil, index from block 1. Unset elsewhere with no checkpoint yet,
    start `lookback` blocks behind head instead of scanning from genesis.
    """
    if start_block > 0:
        floor = start_block
    elif chain_id == ANVIL_CHAIN_ID or last_block > 0:
        floor = 1
    else:
        floor = max(1, head - lookback + 1)
    return max(last_block + 1, floor)


def plan_ranges(start: int, end: int, max_range: int, max_chunks: int) -> list[tuple[int, int]]:
    """Split [start, end] into at most `max_chunks` inclusive windows of at most `max_range` blocks."""
    if end < start or max_range < 1 or max_chunks < 1:
        return []
    ranges: list[tuple[int, int]] = []
    lo = start
    while lo <= end and len(ranges) < max_chunks:
        hi = min(lo + max_range - 1, end)
        ranges.append((lo, hi))
        lo = hi + 1
    return ranges


def next_delay(interval: float, failures: int, max_backoff: float) -> float:
    """Sleep before the next tick: `interval` after success, doubling per consecutive failure."""
    if failures <= 0:
        return interval
    return min(max_backoff, interval * (2 ** min(failures, 16)))


@lru_cache(maxsize=4)
def _web3(rpc_url: str, timeout: float):
    from web3 import Web3

    return Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout}))


def _build_contracts(w3, addresses: dict[str, str]):
    """Return (factory, amm_or_None), or None when the factory ABI is unavailable."""
    from web3 import Web3

    from app.markets.chain import factory_abi

    try:
        abi = factory_abi()
    except Exception as e:
        logger.error(f"Cannot load MarketFactory ABI: {e}")
        return None
    factory = w3.eth.contract(address=Web3.to_checksum_address(addresses["MarketFactory"]), abi=abi)
    amm = None
    if "MarketAMM" in addresses:
        try:
            amm_abi = load_abi("MarketAMM")
        except Exception as e:
            logger.error(f"Cannot load MarketAMM ABI: {e}")
        else:
            amm = w3.eth.contract(address=Web3.to_checksum_address(addresses["MarketAMM"]), abi=amm_abi)
    else:
        logger.debug("MarketAMM address not configured; skipping AMM history")
    return factory, amm


async def _rpc(fn, *args, **kwargs):
    """Run a blocking web3 call off the event loop so API requests keep flowing."""
    return await asyncio.to_thread(fn, *args, **kwargs)


async def index_once() -> bool:
    """One indexer tick. True when caught up or progressing; False on an RPC failure (caller backs off)."""
    settings = get_settings()
    try:
        addresses = get_contract_addresses()
    except Exception as e:
        logger.debug(f"Cannot load contract addresses: {e}")
        return True
    if "MarketFactory" not in addresses:
        return True
    if not await ensure_indexer_leader(settings):
        # Another instance holds the lock: skip this tick without backing off.
        return True
    try:
        w3 = _web3(settings.anvil_rpc_url, settings.indexer_rpc_timeout_seconds)
    except ImportError:
        return True
    built = _build_contracts(w3, addresses)
    if built is None:
        return True
    factory, amm = built

    try:
        head = int(await _rpc(lambda: w3.eth.block_number))
    except Exception as e:
        # Type only: transport errors can embed the RPC URL (and its key).
        logger.warning(f"Indexer cannot read block number: {type(e).__name__}")
        return False

    key = checkpoint_key(addresses)
    async with SessionLocal() as db:
        last_block = await _load_checkpoint(db, key, settings, head)
        start = resolve_start_block(
            last_block, settings.indexer_start_block, head, settings.chain_id, settings.indexer_lookback_blocks
        )
        if start > last_block + 1:
            logger.info(f"Indexer checkpoint fast-forward {last_block} -> {start - 1}")
        window = _STATE.block_range or settings.indexer_max_block_range
        for lo, hi in plan_ranges(start, head, window, settings.indexer_max_chunks_per_tick):
            ok = await index_range(w3, factory, amm, db, lo, hi)
            if not ok:
                _STATE.block_range = max(settings.indexer_min_block_range, (hi - lo + 1) // 2)
                held = await advance_checkpoint(db, key, lo - 1)
                logger.warning(
                    f"Indexer range {lo}-{hi} failed; holding last_block at {held}, next window {_STATE.block_range}"
                )
                return False
            await advance_checkpoint(db, key, hi)
        _STATE.block_range = min(settings.indexer_max_block_range, window * 2)
    return True


def checkpoint_key(addresses: dict[str, str]) -> tuple[str, str]:
    """(factory, amm) lowercase: the address set a checkpoint belongs to ('' when the AMM is unset)."""
    return (
        str(addresses.get("MarketFactory") or "").strip().lower(),
        str(addresses.get("MarketAMM") or "").strip().lower(),
    )


def _key_clause(key: tuple[str, str]):
    return (IndexerCheckpoint.factory_address == key[0]) & (IndexerCheckpoint.amm_address == key[1])


async def _read_checkpoint(db, key: tuple[str, str]) -> int | None:
    value = (await db.execute(select(IndexerCheckpoint.last_block).where(_key_clause(key)))).scalar_one_or_none()
    return None if value is None else int(value)


async def _fresh_last_block(db, settings, head: int) -> int:
    """Initial last_block for an address set with no checkpoint yet.

    INDEXER_START_BLOCK > 0: start exactly there (the v2 deploy block), whatever the
    legacy checkpoint says. Anvil: block 1. Otherwise `lookback` blocks behind head, but
    never past the legacy unkeyed checkpoint (row id=1): if it is further behind, the
    same contracts may have events it has not indexed yet. Replays are idempotent.
    """
    if settings.indexer_start_block > 0:
        return settings.indexer_start_block - 1
    if settings.chain_id == ANVIL_CHAIN_ID:
        return 0
    behind_head = max(0, head - settings.indexer_lookback_blocks)
    legacy = (await db.execute(select(Checkpoint.last_block).where(Checkpoint.id == 1))).scalar_one_or_none()
    if legacy is not None and int(legacy) > 0:
        return min(int(legacy), behind_head)
    return behind_head


async def _load_checkpoint(db, key: tuple[str, str], settings, head: int) -> int:
    """This address set's last_block, creating its row conflict-free (two instances may race here).

    The checkpoint is keyed by (factory, amm): after the v2 cutover the new contracts start
    their own row, so events between their deploy block and the old set's checkpoint (which an
    older revision may still be advancing) are never skipped."""
    current = await _read_checkpoint(db, key)
    if current is not None:
        return current
    initial = await _fresh_last_block(db, settings, head)
    await db.execute(
        insert_ignore(
            session_dialect(db),
            IndexerCheckpoint,
            {"factory_address": key[0], "amm_address": key[1], "last_block": initial},
            ["factory_address", "amm_address"],
        )
    )
    await db.commit()
    stored = await _read_checkpoint(db, key)
    logger.info(f"Indexer checkpoint for factory {key[0]} amm {key[1] or '-'} starts at {stored}")
    return int(stored or 0)


async def advance_checkpoint(db, key: tuple[str, str], block: int) -> int:
    """Commit pending writes and move this address set's last_block forward to `block`, never backward.

    A conditional UPDATE (portable; SQLite has no GREATEST) so a lagging
    instance or a stale tick can never rewind a checkpoint another one advanced.
    Returns the stored value.
    """
    await db.execute(
        update(IndexerCheckpoint)
        .where(_key_clause(key), IndexerCheckpoint.last_block < block)
        .values(last_block=block)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return int(await _read_checkpoint(db, key) or 0)


async def index_range(w3, factory, amm, db, start: int, end: int) -> bool:
    """Index factory then AMM events for [start, end]. False means hold the checkpoint before `start`."""
    try:
        created_logs = await _rpc(factory.events.MarketCreated().get_logs, from_block=start, to_block=end)
        paused_logs = await _rpc(factory.events.MarketPaused().get_logs, from_block=start, to_block=end)
    except Exception as e:
        logger.warning(f"Factory range {start}-{end} get_logs failed ({type(e).__name__}); will retry")
        return False

    all_events = []
    for ev in created_logs:
        all_events.append({"type": "created", "blockNumber": ev["blockNumber"], "logIndex": ev["logIndex"], "args": ev["args"]})
    for ev in paused_logs:
        all_events.append({"type": "paused", "blockNumber": ev["blockNumber"], "logIndex": ev["logIndex"], "args": ev["args"]})
    all_events.sort(key=lambda x: (x["blockNumber"], x["logIndex"]))

    for event in all_events:
        try:
            args = event["args"]
            cid = "0x" + args["conditionId"].hex()
            if event["type"] == "created":
                parent = "0x" + args["parentConditionId"].hex()
                # ON CONFLICT DO NOTHING: /listing/confirm (or another instance) may insert the same
                # row concurrently; a lost race must not poison the session with an IntegrityError.
                await db.execute(
                    insert_ignore(
                        session_dialect(db),
                        Market,
                        {
                            "condition_id": cid,
                            "parent_condition_id": "" if parent == "0x" + "00" * 32 else parent,
                            "question": args["question"],
                            "market_type": int(args["marketType"]),
                            "close_time": int(args["closeTime"]),
                        },
                        ["condition_id"],
                    )
                )
                existing = await db.get(Market, cid)
                if int(args["marketType"]) == MARKET_TYPE_USER:
                    await _index_user_listing(factory, db, existing, args)
            else:
                existing = await db.get(Market, cid)
                if existing is not None:
                    existing.paused = args["paused"]
        except Exception as e:
            logger.warning(f"Skipping factory event {event.get('type')} at block {event.get('blockNumber')}: {e}")
            continue

    if amm is not None:
        if not await _index_amm_history(w3, amm, db, start, end):
            # Factory writes replay idempotently; partial AMM points are deduped.
            await db.commit()
            return False
    return True


def _read_listing_commitments(factory, cid_bytes: bytes) -> tuple[str, int]:
    from app.markets.chain import to_hex32

    return to_hex32(factory.functions.criteriaHashOf(cid_bytes).call()), int(factory.functions.seedOf(cid_bytes).call())


async def _index_user_listing(factory, db, market: Market, args) -> None:
    """Record a type-2 MarketCreated in market_listings.

    The market stays hidden (see app.markets.visibility) until its listing is
    `confirmed`. A listing row the web prepared carries the criteria text; it
    is copied to the market only when its keccak matches the factory's
    criteriaHashOf AND the on-chain question (not committed in the hash) passes
    the same review /confirm runs: equal to the prepared question, closeTime and
    seed, plus the wording/duplicate gates. A failing review marks it
    `rejected`. The commitment read is best effort: on RPC failure the row stays
    as-is and POST /markets/listing/confirm (or the next replay) fills it in.
    """
    from web3 import Web3

    from app.markets import visibility

    cid = market.condition_id
    await db.execute(
        insert_ignore(
            session_dialect(db),
            MarketListing,
            {
                "condition_id": cid,
                "creator": str(args["creator"]).lower(),
                "salt": "",
                "question": str(args["question"]),
                "close_time": int(args["closeTime"]),
                "status": "indexed",
                "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
            },
            ["condition_id"],
        )
    )
    listing = await db.get(MarketListing, cid)
    if listing.status in (visibility.STATUS_CONFIRMED, visibility.STATUS_REJECTED):
        return
    try:
        onchain_hash, seed = await _rpc(_read_listing_commitments, factory, bytes.fromhex(cid[2:]))
    except Exception as e:
        logger.warning(f"User listing {cid}: commitment read failed ({type(e).__name__}); leaving criteria unset")
        return
    question, close_time = str(args["question"]), int(args["closeTime"])
    criteria = (listing.resolution_criteria or "").strip()
    listing.criteria_hash = onchain_hash
    if not (criteria and Web3.to_hex(Web3.keccak(text=criteria)) == onchain_hash):
        # Unverified: stays hidden. A prepared row keeps its prepared seed for /confirm's comparison.
        if not visibility.is_backend_prepared(listing):
            listing.seed_usdc = seed
        return
    reason = await visibility.review_user_listing(
        db,
        cid=cid,
        listing=listing,
        question=question,
        close_time=close_time,
        seed_usdc=seed,
        criteria=criteria,
        now=int(time.time()),
    )
    if reason:
        logger.warning(f"User listing {cid} rejected: {reason}")
        listing.status = visibility.STATUS_REJECTED
        listing.reject_reason = reason[:256]
        return
    market.resolution_criteria = criteria
    listing.question = question
    listing.close_time = close_time
    listing.seed_usdc = seed
    listing.status = visibility.STATUS_CONFIRMED
    listing.reject_reason = ""


async def _index_amm_history(w3, amm, db, start: int, end: int) -> bool:
    """Index PoolSeeded (first point at 0.5) and Swap (pool price) into price_points.

    Returns True only if the whole range was captured. Any fetch failure
    returns False so the caller holds the checkpoint and the range retries
    instead of committing a permanent gap that would read as empty history.
    """
    try:
        seeded_logs = await _rpc(amm.events.PoolSeeded().get_logs, from_block=start, to_block=end)
    except Exception as e:
        logger.error(f"AMM range {start}-{end} incomplete: PoolSeeded get_logs failed ({type(e).__name__}); will retry")
        return False
    try:
        swap_logs = await _rpc(amm.events.Swap().get_logs, from_block=start, to_block=end)
    except Exception as e:
        logger.error(f"AMM range {start}-{end} incomplete: Swap get_logs failed ({type(e).__name__}); will retry")
        return False

    amm_events = []
    for ev in seeded_logs:
        amm_events.append({"type": "seeded", "blockNumber": ev["blockNumber"], "logIndex": ev["logIndex"], "args": ev["args"]})
    for ev in swap_logs:
        amm_events.append({"type": "swap", "blockNumber": ev["blockNumber"], "logIndex": ev["logIndex"], "args": ev["args"]})
    amm_events.sort(key=lambda x: (x["blockNumber"], x["logIndex"]))

    for event in amm_events:
        try:
            args = event["args"]
            cid = "0x" + args["conditionId"].hex()
            block_number = event["blockNumber"]
            log_index = event["logIndex"]
        except Exception as e:
            logger.warning(f"Skipping malformed AMM event {event.get('type')}: {e}")
            continue
        # Pre-check skips the RPC calls on replays; the unique key + ON CONFLICT covers races.
        if await _point_exists(db, cid, block_number, log_index):
            continue
        try:
            block = await _rpc(w3.eth.get_block, block_number)
            ts = block["timestamp"] if isinstance(block, dict) else block.timestamp
        except Exception as e:
            logger.error(f"AMM range {start}-{end} incomplete: no timestamp for block {block_number} ({type(e).__name__}); will retry")
            return False
        if event["type"] == "seeded":
            point = build_seed_point(cid, int(ts), block_number, log_index)
        else:
            try:
                cid_bytes = bytes.fromhex(cid[2:] if cid.startswith("0x") else cid)
                pool = await _rpc(amm.functions.pools(cid_bytes).call, block_identifier=block_number)
                price = pool_price_micros(pool)
            except Exception as e:
                logger.error(f"AMM range {start}-{end} incomplete: pools() failed at block {block_number} ({type(e).__name__}); will retry")
                return False
            point = PricePoint(
                condition_id=cid,
                ts=int(ts),
                block_number=block_number,
                log_index=log_index,
                yes_price_micros=price,
            )
        await _insert_point(db, point)
    return True


async def _point_exists(db, cid: str, block_number: int, log_index: int) -> bool:
    stmt = select(PricePoint.id).where(
        PricePoint.condition_id == cid,
        PricePoint.block_number == block_number,
        PricePoint.log_index == log_index,
    )
    return (await db.execute(stmt)).first() is not None


async def _insert_point(db, point: PricePoint) -> None:
    """INSERT ... ON CONFLICT (condition_id, block_number, log_index) DO NOTHING."""
    values = {
        "condition_id": point.condition_id,
        "ts": point.ts,
        "block_number": point.block_number,
        "log_index": point.log_index,
        "yes_price_micros": point.yes_price_micros,
    }
    await db.execute(insert_ignore(session_dialect(db), PricePoint, values, list(PRICE_POINT_KEY)))


async def run_indexer_loop(interval: float = 5.0, max_backoff: float = 300.0, *, sleep=asyncio.sleep) -> None:
    failures = 0
    while True:
        try:
            ok = await index_once()
        except Exception as e:
            ok = False
            if failures == 0:
                logger.error(f"Indexer error: {e}", exc_info=True)
            else:
                logger.warning(f"Indexer error (x{failures + 1}): {type(e).__name__}")
        failures = 0 if ok else failures + 1
        await sleep(next_delay(interval, failures, max_backoff))
