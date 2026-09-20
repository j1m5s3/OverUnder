#!/usr/bin/env python3
"""Seed one sports primary + one wildcard child on Base Sepolia. Never prints keys."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from eth_account import Account
from web3 import Web3

FACTORY = Web3.to_checksum_address(os.environ.get("FACTORY_ADDRESS", "0xbcF11038c688286401fB952905085456aabe7C97"))
USDC = Web3.to_checksum_address(os.environ.get("USDC_ADDRESS", "0xa99c35B3328B4e960C424A95442B1865FAdC787D"))
RPC = os.environ.get("RPC_URL") or os.environ.get("ANVIL_RPC_URL") or "https://sepolia.base.org"
CHAIN_ID = int(os.environ.get("CHAIN_ID", "84532"))
SEED = int(os.environ.get("SEED_USDC", str(10_000_000)))  # 10 USDC (6 decimals)
PRIMARY_Q = os.environ.get("PRIMARY_QUESTION", "Chiefs vs Broncos: Chiefs win?")
CHILD_Q = os.environ.get("CHILD_QUESTION", "Chiefs vs Broncos: over 45.5 points?")
API = os.environ.get("API_URL", "https://overunder-api-wcaysyu2vq-uc.a.run.app")

ROOT = Path(__file__).resolve().parents[1]
FACTORY_ABI = json.loads((ROOT / "backend/app/abi/MarketFactory.json").read_text())
USDC_ABI = json.loads((ROOT / "backend/app/abi/MockUSDC.json").read_text())


def _qid(label: str) -> bytes:
    # unique questionId: keccak of label + timestamp salt
    return Web3.keccak(text=f"{label}:{time.time_ns()}")


def _send(w3: Web3, acct, built):
    built.setdefault("from", acct.address)
    built.setdefault("nonce", w3.eth.get_transaction_count(acct.address))
    built.setdefault("chainId", CHAIN_ID)
    built.setdefault("gasPrice", w3.eth.gas_price)
    if "gas" not in built:
        built["gas"] = int(w3.eth.estimate_gas(built) * 1.25)
    signed = acct.sign_transaction(built)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    txh = w3.eth.send_raw_transaction(raw)
    receipt = w3.eth.wait_for_transaction_receipt(txh, timeout=180)
    if receipt["status"] != 1:
        raise RuntimeError(f"tx failed: {txh.hex()}")
    return txh.hex() if hasattr(txh, "hex") else Web3.to_hex(txh), receipt


def main() -> int:
    key = os.environ.get("OPERATOR_PRIVATE_KEY") or os.environ.get("OU_OPERATOR_PRIVATE_KEY")
    if not key:
        print("ERROR: OPERATOR_PRIVATE_KEY missing", file=sys.stderr)
        return 2
    if not key.startswith("0x"):
        key = "0x" + key

    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 60}))
    if not w3.is_connected():
        print("ERROR: RPC not connected", RPC, file=sys.stderr)
        return 2

    acct = Account.from_key(key)
    print(f"operator={acct.address}")
    print(f"rpc={RPC} chainId={CHAIN_ID} factory={FACTORY} usdc={USDC}")
    if acct.address.lower() != "0x202e385019b9c4b8741ecc4aaa7fd683f02791f3":
        print("WARN: operator address != expected EOA 0x202e3850...", file=sys.stderr)

    factory = w3.eth.contract(address=FACTORY, abi=FACTORY_ABI)
    usdc = w3.eth.contract(address=USDC, abi=USDC_ABI)

    onchain_op = factory.functions.operator().call()
    print(f"factory.operator={onchain_op}")
    if onchain_op.lower() != acct.address.lower():
        print("ERROR: signer is not factory.operator", file=sys.stderr)
        return 2

    bal = usdc.functions.balanceOf(acct.address).call()
    print(f"usdc_balance={bal}")
    need = SEED * 2  # primary + child
    if bal < need:
        print(f"faucet need={need - bal}")
        _send(w3, acct, usdc.functions.faucet(need - bal).build_transaction({"gas": 120_000}))

    allowance = usdc.functions.allowance(acct.address, FACTORY).call()
    if allowance < need:
        print(f"approve need={need}")
        _send(w3, acct, usdc.functions.approve(FACTORY, need).build_transaction({"gas": 100_000}))

    close_time = int(time.time()) + 60 * 60 * 24 * 30  # 30d

    # Primary
    pqid = _qid("sports-primary")
    print(f"creating primary question={PRIMARY_Q!r}")
    ptx, prec = _send(
        w3,
        acct,
        factory.functions.createPrimaryMarket(pqid, close_time, PRIMARY_Q, SEED).build_transaction({"gas": 1_500_000}),
    )
    plogs = factory.events.MarketCreated().process_receipt(prec)
    if not plogs:
        print("ERROR: no MarketCreated for primary", file=sys.stderr)
        return 2
    primary_cid = "0x" + plogs[0]["args"]["conditionId"].hex()
    print(f"PRIMARY_CONDITION_ID={primary_cid}")
    print(f"PRIMARY_TX={ptx}")

    # Child
    cqid = _qid("sports-child")
    print(f"creating child question={CHILD_Q!r} parent={primary_cid}")
    ctx, crec = _send(
        w3,
        acct,
        factory.functions.createWildcardMarket(
            cqid, bytes.fromhex(primary_cid[2:]), close_time, CHILD_Q, SEED
        ).build_transaction({"gas": 1_500_000}),
    )
    clogs = factory.events.MarketCreated().process_receipt(crec)
    if not clogs:
        print("ERROR: no MarketCreated for child", file=sys.stderr)
        return 2
    child_cid = "0x" + clogs[0]["args"]["conditionId"].hex()
    parent_from_event = "0x" + clogs[0]["args"]["parentConditionId"].hex()
    print(f"CHILD_CONDITION_ID={child_cid}")
    print(f"CHILD_TX={ctx}")
    print(f"CHILD_PARENT={parent_from_event}")

    out = {
        "primaryConditionId": primary_cid,
        "childConditionId": child_cid,
        "primaryTx": ptx,
        "childTx": ctx,
        "primaryQuestion": PRIMARY_Q,
        "childQuestion": CHILD_Q,
        "api": API,
    }
    Path("seed_result.json").write_text(json.dumps(out, indent=2))
    print("WROTE seed_result.json")

    # Poll API for indexer
    import urllib.request

    ok = False
    for i in range(36):  # ~3 min
        try:
            with urllib.request.urlopen(f"{API}/api/v1/markets", timeout=30) as resp:
                cards = json.loads(resp.read().decode())
            match = next((c for c in cards if c.get("primary", {}).get("conditionId") == primary_cid), None)
            if match and len(match.get("children") or []) >= 1:
                print("INDEXER_OK children.length=", len(match["children"]))
                print("EVENT_CARD=", json.dumps(match)[:800])
                ok = True
                break
            print(f"poll {i}: card={bool(match)} children={len((match or {}).get('children') or [])}")
        except Exception as e:
            print(f"poll {i}: error {e}")
        time.sleep(5)

    if not ok:
        # detail endpoint too
        try:
            with urllib.request.urlopen(f"{API}/api/v1/markets/{primary_cid}", timeout=30) as resp:
                detail = json.loads(resp.read().decode())
            print("DETAIL=", json.dumps(detail)[:800])
        except Exception as e:
            print("DETAIL_ERR", e)
        print("WARN: indexer did not show children in time", file=sys.stderr)
        return 1

    with urllib.request.urlopen(f"{API}/api/v1/markets/{primary_cid}", timeout=30) as resp:
        detail = json.loads(resp.read().decode())
    print("DETAIL_CHILDREN=", len(detail.get("children") or []))
    print("GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
