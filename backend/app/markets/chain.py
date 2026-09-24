"""Factory chain access shared by operator create (step 12) and user listing (OU-T010).

Everything here is sync web3; callers in async handlers wrap reads in
`asyncio.to_thread` where latency matters. Tests patch `load_factory_chain`
and `load_factory_reader` with MagicMock contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import eth_abi
from fastapi import HTTPException
from web3 import Web3

ZERO32 = b"\x00" * 32

# MarketFactory v2 views and the listing entrypoint. Merged into the checked-in
# ABI so the backend can talk to v2 before (and after) the ABI JSON is
# regenerated; entries already present in the JSON win.
FACTORY_V2_FRAGMENTS: list[dict[str, Any]] = [
    {"type": "function", "stateMutability": "view", "name": "permissionless", "inputs": [], "outputs": [{"name": "", "type": "bool"}]},
    {"type": "function", "stateMutability": "view", "name": "listerAllowed", "inputs": [{"name": "arg0", "type": "address"}], "outputs": [{"name": "", "type": "bool"}]},
    {"type": "function", "stateMutability": "view", "name": "minSeedUsdc", "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "stateMutability": "view", "name": "listingFeeUsdc", "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "stateMutability": "view", "name": "feeRecipient", "inputs": [], "outputs": [{"name": "", "type": "address"}]},
    {"type": "function", "stateMutability": "view", "name": "minLeadTime", "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "stateMutability": "view", "name": "maxHorizon", "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "stateMutability": "view", "name": "listingCooldown", "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "stateMutability": "view", "name": "lastListedAt", "inputs": [{"name": "arg0", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "stateMutability": "view", "name": "creatorOf", "inputs": [{"name": "arg0", "type": "bytes32"}], "outputs": [{"name": "", "type": "address"}]},
    {"type": "function", "stateMutability": "view", "name": "seedOf", "inputs": [{"name": "arg0", "type": "bytes32"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "stateMutability": "view", "name": "criteriaHashOf", "inputs": [{"name": "arg0", "type": "bytes32"}], "outputs": [{"name": "", "type": "bytes32"}]},
    {
        "type": "function",
        "stateMutability": "view",
        "name": "userQuestionId",
        "inputs": [{"name": "creator", "type": "address"}, {"name": "salt", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bytes32"}],
    },
    {
        "type": "function",
        "stateMutability": "view",
        "name": "userConditionId",
        "inputs": [{"name": "creator", "type": "address"}, {"name": "salt", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bytes32"}],
    },
    {
        "type": "function",
        "stateMutability": "nonpayable",
        "name": "createPermissionlessMarket",
        "inputs": [
            {"name": "salt", "type": "bytes32"},
            {"name": "closeTime", "type": "uint256"},
            {"name": "question", "type": "string"},
            {"name": "criteriaHash", "type": "bytes32"},
            {"name": "seedUsdc", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bytes32"}],
    },
]


@dataclass
class FactoryChain:
    """Operator-side handles for POST /markets (signing account included)."""

    w3: Any
    factory: Any
    usdc: Any
    ctf: Any
    operator: Any


@dataclass
class FactoryReader:
    """Read-only handles for the listing endpoints (no operator key needed)."""

    w3: Any
    factory: Any


def hex32(value: str, field: str) -> bytes:
    raw = (value or "").strip()
    if raw[:2] in ("0x", "0X"):
        raw = raw[2:]
    try:
        out = bytes.fromhex(raw)
    except ValueError:
        raise HTTPException(400, f"{field} must be 32-byte hex")
    if len(out) != 32:
        raise HTTPException(400, f"{field} must be 32-byte hex")
    return out


def to_hex32(value: bytes) -> str:
    return "0x" + bytes(value).hex()


def condition_id_for(oracle: str, question_id: bytes) -> str:
    """CTF condition id: keccak256(abi.encode(oracle, questionId)), lowercase 0x hex."""
    encoded = eth_abi.encode(["address", "bytes32"], [Web3.to_checksum_address(oracle), question_id])
    return Web3.to_hex(Web3.keccak(encoded))


def user_question_id(creator: str, salt: bytes) -> bytes:
    """MarketFactory v2 userQuestionId: keccak256(abi.encode(creator, salt))."""
    return bytes(Web3.keccak(eth_abi.encode(["address", "bytes32"], [Web3.to_checksum_address(creator), salt])))


def factory_abi() -> list[dict[str, Any]]:
    from app.contract_addresses import load_abi

    abi = list(load_abi("MarketFactory"))
    seen = {
        (entry.get("name"), tuple(i.get("type") for i in entry.get("inputs", [])))
        for entry in abi
        if entry.get("type") == "function"
    }
    for fragment in FACTORY_V2_FRAGMENTS:
        key = (fragment["name"], tuple(i["type"] for i in fragment["inputs"]))
        if key not in seen:
            abi.append(fragment)
    return abi


def load_factory_reader(settings) -> FactoryReader:
    from app.contract_addresses import get_contract_addresses

    try:
        addresses = get_contract_addresses()
        abi = factory_abi()
    except Exception as e:
        raise HTTPException(500, f"failed to load contract config: {e}")
    if "MarketFactory" not in addresses:
        raise HTTPException(503, "MarketFactory address not configured")
    w3 = Web3(Web3.HTTPProvider(settings.anvil_rpc_url, request_kwargs={"timeout": 15}))
    factory = w3.eth.contract(address=Web3.to_checksum_address(addresses["MarketFactory"]), abi=abi)
    return FactoryReader(w3=w3, factory=factory)


def load_factory_chain(settings) -> FactoryChain:
    from eth_account import Account

    from app.contract_addresses import get_contract_addresses, load_abi

    try:
        addresses = get_contract_addresses()
        abi = factory_abi()
        usdc_abi = load_abi("MockUSDC")
        ctf_abi = load_abi("ConditionalTokens")
    except Exception as e:
        raise HTTPException(500, f"failed to load contract config: {e}")

    if "MarketFactory" not in addresses or "MockUSDC" not in addresses:
        raise HTTPException(500, "MarketFactory or MockUSDC address not configured")

    w3 = Web3(Web3.HTTPProvider(settings.anvil_rpc_url))
    if not w3.is_connected():
        raise HTTPException(500, "RPC not available")

    operator = Account.from_key(settings.operator_private_key)
    factory = w3.eth.contract(address=Web3.to_checksum_address(addresses["MarketFactory"]), abi=abi)
    usdc = w3.eth.contract(address=Web3.to_checksum_address(addresses["MockUSDC"]), abi=usdc_abi)
    try:
        ctf_address = factory.functions.ctf().call()
    except Exception as e:
        raise HTTPException(500, f"failed to read factory ctf(): {type(e).__name__}")
    ctf = w3.eth.contract(address=Web3.to_checksum_address(ctf_address), abi=ctf_abi)
    return FactoryChain(w3=w3, factory=factory, usdc=usdc, ctf=ctf, operator=operator)


def market_row_from_struct(m: Any) -> dict[str, Any]:
    """Normalise a factory `markets(cid)` struct (or MarketCreated args) to a plain dict."""
    parent = bytes(m[1])
    return {
        "conditionId": to_hex32(m[0]),
        "parentConditionId": "" if parent == ZERO32 else to_hex32(parent),
        "closeTime": int(m[2]),
        "marketType": int(m[3]),
        "paused": bool(m[4]),
        "question": str(m[5]),
    }


def read_factory_market(factory: Any, cid: bytes) -> dict[str, Any] | None:
    if not factory.functions.marketExists(cid).call():
        return None
    return market_row_from_struct(factory.functions.markets(cid).call())
