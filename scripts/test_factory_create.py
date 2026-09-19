#!/usr/bin/env python3
"""Test factory-backed market creation via backend API."""

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

from httpx import ASGITransport, AsyncClient
from app.main import create_app
from app.db import engine, Base


async def test_create_market():
    proto = deploy_protocol()
    
    out = CONTRACTS / "deployments" / "31337.json"
    out.parent.mkdir(exist_ok=True)
    
    operator_key = proto["accounts"]["operator"].key.hex()
    if operator_key.startswith("0x"):
        operator_key = operator_key[2:]
    operator_key = "0x" + operator_key
    
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
    
    os.environ["OPERATOR_PRIVATE_KEY"] = operator_key
    os.environ["ANVIL_RPC_URL"] = "memory"
    os.environ["CHAIN_ID"] = "31337"
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_factory.db"
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    app = create_app()
    transport = ASGITransport(app=app)
    
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        addr = proto["accounts"]["operator"].address
        
        n = await client.get(f"/api/v1/auth/nonce/{addr}")
        assert n.status_code == 200
        nonce = n.json()["nonce"]
        
        r = await client.post(
            "/api/v1/auth/siwe",
            json={"address": addr, "signature": "0x00", "message": f"login {addr} nonce {nonce}"},
        )
        assert r.status_code == 200
        token = r.json()["token"]
        
        close_time = boa.env.timestamp + 3600
        question_id = "0x" + "aa" * 32
        
        create_resp = await client.post(
            "/api/v1/markets",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "question": "Test market from factory",
                "question_id": question_id,
                "close_time": close_time,
                "seed_usdc": 10_000_000,
                "market_type": 0,
            },
        )
        
        if create_resp.status_code != 200:
            print(f"Create failed: {create_resp.status_code} {create_resp.text}")
            return False
        
        market = create_resp.json()
        print(f"Created market: {market['conditionId']}")
        
        assert market["conditionId"] != question_id, "conditionId should not equal questionId"
        assert market["question"] == "Test market from factory"
        assert market["closeTime"] == close_time
        
        list_resp = await client.get("/api/v1/markets")
        assert list_resp.status_code == 200
        markets = list_resp.json()
        assert len(markets) == 1
        assert markets[0]["conditionId"] == market["conditionId"]
        
        print("✓ Factory-backed market creation works")
        return True


def main():
    result = asyncio.run(test_create_market())
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
