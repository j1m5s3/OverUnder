"""Best-effort chain indexer. Polls Factory/Oracle logs when RPC is available."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.config import get_settings
from app.contract_addresses import get_contract_addresses, load_abi
from app.db import SessionLocal
from app.models import Checkpoint, Market

logger = logging.getLogger(__name__)


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
        
        cp.last_block = end
        await db.commit()


async def run_indexer_loop(interval: float = 5.0) -> None:
    while True:
        try:
            await index_once()
        except Exception as e:
            logger.error(f"Indexer error: {e}", exc_info=True)
        await asyncio.sleep(interval)
