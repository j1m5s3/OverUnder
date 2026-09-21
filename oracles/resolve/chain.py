"""On-chain ConsensusOracle calls. Never calls resolveFallback."""

from __future__ import annotations

import json
import os
from pathlib import Path

from eth_account import Account
from web3 import Web3

_ABI_PATH = Path(__file__).resolve().parents[1] / "abi" / "ConsensusOracle.json"


def _cid_bytes(condition_id: str) -> bytes:
    hexed = condition_id[2:] if condition_id.startswith("0x") else condition_id
    raw = bytes.fromhex(hexed)
    if len(raw) != 32:
        raise RuntimeError("conditionId must be 32 bytes")
    return raw


def _w3() -> Web3:
    url = (os.getenv("ANVIL_RPC_URL") or os.getenv("OU_RPC_URL") or "").strip()
    if not url:
        raise RuntimeError("ANVIL_RPC_URL required to submit consensus")
    w3 = Web3(Web3.HTTPProvider(url))
    if not w3.is_connected():
        raise RuntimeError("RPC not available")
    return w3


def _contract(w3: Web3 | None = None):
    address = (os.getenv("ORACLE_ADDRESS") or "").strip()
    if not address:
        raise RuntimeError("ORACLE_ADDRESS required to submit consensus")
    provider = w3 or _w3()
    abi = json.loads(_ABI_PATH.read_text(encoding="utf-8"))
    return provider, provider.eth.contract(address=Web3.to_checksum_address(address), abi=abi)


def is_resolved(condition_id: str) -> bool:
    _w3_client, contract = _contract()
    return bool(contract.functions.resolved(_cid_bytes(condition_id)).call())


def onchain_close_time(condition_id: str) -> int:
    _w3_client, contract = _contract()
    return int(contract.functions.closeTime(_cid_bytes(condition_id)).call())


def submit_consensus(condition_id: str, outcome: int, evidence_hash: bytes, deadline: int, sigs: list[bytes]) -> str:
    key = (os.getenv("OPERATOR_PRIVATE_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPERATOR_PRIVATE_KEY required to submit consensus")
    chain_id = int(os.getenv("CHAIN_ID") or "0")
    if chain_id <= 0:
        raise RuntimeError("CHAIN_ID required to submit consensus")
    w3, contract = _contract()
    account = Account.from_key(key)
    tx = contract.functions.submitConsensus(
        _cid_bytes(condition_id),
        outcome,
        evidence_hash,
        deadline,
        sigs,
    ).build_transaction(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "chainId": chain_id,
            "gas": 500_000,
            "gasPrice": w3.eth.gas_price,
        }
    )
    signed = account.sign_transaction(tx)
    txh = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(txh)
    if receipt["status"] != 1:
        raise RuntimeError("submitConsensus transaction failed")
    return txh.hex() if hasattr(txh, "hex") else w3.to_hex(txh)
