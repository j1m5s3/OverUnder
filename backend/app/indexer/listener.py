"""Best-effort chain indexer. Polls Factory/AMM logs when RPC is available."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.config import get_settings
from app.contract_addresses import get_contract_addresses, load_abi
from app.db import SessionLocal
from app.models import Checkpoint, Market, PricePoint

logger = logging.getLogger(__name__)

SEED_PRICE_MICROS = 500_000


def mid_to_micros(yes_reserve: int, no_reserve: int) -> int:
    """Pool-mid YES price in micros: yes / (yes + no). Empty pool seeds at 0.5."""
    total = (yes_reserve or 0) + (no_reserve or 0)
    if total <= 0:
        return SEED_PRICE_MICROS
    return int((yes_reserve or 0) * 1_000_000 // total)


def build_swap_point(
    condition_id: str,
    ts: int,
    block_number: int,
    log_index: int,
    yes_reserve: int,
    no_reserve: int,
) -> PricePoint:
    """Pure Swap -> PricePoint constructor (no RPC; pool-mid only, never trade-implied)."""
    return PricePoint(
        condition_id=condition_id,
        ts=ts,
        block_number=block_number,
        log_index=log_index,
        yes_price_micros=mid_to_micros(yes_reserve, no_reserve),
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


async def index_once() -> None:
    settings = get_settings()
    try:
        addresses = get_contract_addresses()
    except Exception as e:
        logger.debug(f"Cannot load contract addresses: {e}")
        return

    if "MarketFactory" not in addresses:
        return

    try:
        from web3 import Web3
    except ImportError:
        return

    w3 = Web3(Web3.HTTPProvider(settings.anvil_rpc_url))
    if not w3.is_connected():
        return

    try:
        factory_abi = load_abi("MarketFactory")
    except Exception as e:
        logger.error(f"Cannot load MarketFactory ABI: {e}")
        return

    factory = w3.eth.contract(address=Web3.to_checksum_address(addresses["MarketFactory"]), abi=factory_abi)
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

    async with SessionLocal() as db:
        cp = await db.get(Checkpoint, 1)
        if cp is None:
            cp = Checkpoint(id=1, last_block=0)
            db.add(cp)
            await db.commit()
        start = cp.last_block + 1
        end = w3.eth.block_number
        if end < start:
            return

        created_logs = factory.events.MarketCreated().get_logs(from_block=start, to_block=end)
        paused_logs = factory.events.MarketPaused().get_logs(from_block=start, to_block=end)

        all_events = []
        for ev in created_logs:
            all_events.append({
                "type": "created",
                "blockNumber": ev["blockNumber"],
                "logIndex": ev["logIndex"],
                "args": ev["args"],
            })
        for ev in paused_logs:
            all_events.append({
                "type": "paused",
                "blockNumber": ev["blockNumber"],
                "logIndex": ev["logIndex"],
                "args": ev["args"],
            })

        all_events.sort(key=lambda x: (x["blockNumber"], x["logIndex"]))

        for event in all_events:
            try:
                if event["type"] == "created":
                    args = event["args"]
                    cid = "0x" + args["conditionId"].hex()
                    parent = "0x" + args["parentConditionId"].hex()
                    existing = await db.get(Market, cid)
                    if existing is None:
                        db.add(
                            Market(
                                condition_id=cid,
                                parent_condition_id="" if parent == "0x" + "00" * 32 else parent,
                                question=args["question"],
                                market_type=args["marketType"],
                                close_time=args["closeTime"],
                            )
                        )
                elif event["type"] == "paused":
                    args = event["args"]
                    cid = "0x" + args["conditionId"].hex()
                    existing = await db.get(Market, cid)
                    if existing is not None:
                        existing.paused = args["paused"]
            except Exception as e:
                logger.warning(f"Skipping factory event {event.get('type')} at block {event.get('blockNumber')}: {e}")
                continue

        if amm is not None:
            await _index_amm_history(w3, amm, db, start, end)

        cp.last_block = end
        await db.commit()


async def _index_amm_history(w3, amm, db, start: int, end: int) -> None:
    """Index PoolSeeded (first point at 0.5) and Swap (pool-mid) into price_points."""
    try:
        seeded_logs = amm.events.PoolSeeded().get_logs(from_block=start, to_block=end)
    except Exception as e:
        logger.warning(f"Skipping PoolSeeded range {start}-{end}: {e}")
        seeded_logs = []
    try:
        swap_logs = amm.events.Swap().get_logs(from_block=start, to_block=end)
    except Exception as e:
        logger.warning(f"Skipping Swap range {start}-{end}: {e}")
        swap_logs = []

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
            existing = (
                await db.execute(
                    select(PricePoint).where(
                        PricePoint.condition_id == cid,
                        PricePoint.block_number == block_number,
                        PricePoint.log_index == log_index,
                    )
                )
            ).scalars().first()
            if existing is not None:
                continue
            try:
                block = w3.eth.get_block(block_number)
                ts = block["timestamp"] if isinstance(block, dict) else block.timestamp
            except Exception as e:
                logger.warning(f"Skipping AMM event at block {block_number}: no timestamp ({e})")
                continue
            if event["type"] == "seeded":
                db.add(build_seed_point(cid, int(ts), block_number, log_index))
            else:
                cid_bytes = bytes.fromhex(cid[2:] if cid.startswith("0x") else cid)
                pool = amm.functions.pools(cid_bytes).call(block_identifier=block_number)
                yes_reserve, no_reserve = int(pool[0]), int(pool[1])
                db.add(build_swap_point(cid, int(ts), block_number, log_index, yes_reserve, no_reserve))
        except Exception as e:
            logger.warning(f"Skipping AMM event {event.get('type')} at block {event.get('blockNumber')}: {e}")
            continue


async def run_indexer_loop(interval: float = 5.0) -> None:
    while True:
        try:
            await index_once()
        except Exception as e:
            logger.error(f"Indexer error: {e}", exc_info=True)
        await asyncio.sleep(interval)
