#!/usr/bin/env python3
"""Test indexer picks up MarketPaused events."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
sys.path.insert(0, str(CONTRACTS))
sys.path.insert(0, str(ROOT / "backend"))

os.chdir(CONTRACTS)

import boa
from tests.conftest import deploy_protocol

from app.db import engine, Base, SessionLocal
from app.models import Market
from app.indexer.listener import index_once


async def test_indexer_pause():
    proto = deploy_protocol()
    
    out = CONTRACTS / "deployments" / "31337.json"
    out.parent.mkdir(exist_ok=True)
    
    payload = {
        "chainId": 31337,
        "MockUSDC": proto["usdc"].address,
        "RevenueToken": proto["ou"].address,
        "ConditionalTokens": proto["ctf"].address,
        "FeeVault": proto["vault"].address,
        "ConsensusOracle": proto["oracle"].address,
        "Exchange": proto["exchange"].address,
        "MarketAMM": proto["amm"].address,
        "MarketFactory": proto["factory"].address,
    }
    out.write_text(json.dumps(payload, indent=2))
    
    os.environ["ANVIL_RPC_URL"] = "memory"
    os.environ["CHAIN_ID"] = "31337"
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_indexer_pause.db"
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    factory = proto["factory"]
    usdc = proto["usdc"]
    operator = proto["accounts"]["operator"]
    close = boa.env.timestamp + 3600
    seed = 10_000_000
    
    with boa.env.prank(operator.address):
        usdc.faucet(seed)
        usdc.approve(factory.address, seed)
        cid = factory.createPrimaryMarket(b"\xaa" * 32, close, "Test market", seed)
    
    await index_once()
    
    async with SessionLocal() as db:
        market = await db.get(Market, "0x" + cid.hex())
        assert market is not None, "Market should be indexed"
        assert market.paused is False, "Market should not be paused initially"
        assert market.question == "Test market"
        print(f"✓ Market indexed: {market.condition_id}, paused={market.paused}")
    
    with boa.env.prank(operator.address):
        factory.setPaused(cid, True)
    
    await index_once()
    
    async with SessionLocal() as db:
        market = await db.get(Market, "0x" + cid.hex())
        assert market is not None, "Market should still exist"
        assert market.paused is True, "Market should be paused after setPaused"
        print(f"✓ Market paused via indexer: {market.condition_id}, paused={market.paused}")
    
    print("✓ Indexer pause events work correctly")
    return True


def main():
    result = asyncio.run(test_indexer_pause())
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
