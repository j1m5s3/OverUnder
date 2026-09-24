"""On-chain ConsensusOracle and CTF calls: config guard, consensus, ADR-0002 fallback.

Sends are signed by OPERATOR_PRIVATE_KEY (the job has no other relayer key).
resolveFallback / resolveArbitrated are only called by resolve/fallback.py
under OU_FALLBACK_POLICY. Each send waits for its receipt at most
min(RECEIPT_TIMEOUT, tick time left - 10s) and raises budget.SendDeferred, before
broadcasting, when the tick budget cannot cover that wait (the next tick sends).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError

import budget

_ABI_PATH = Path(__file__).resolve().parents[1] / "abi" / "ConsensusOracle.json"
_CTF_ABI_PATH = Path(__file__).resolve().parents[1] / "abi" / "ConditionalTokens.json"

AGENT_KEY_ENV = {"alpha": "AGENT_ALPHA_KEY", "beta": "AGENT_BETA_KEY", "gamma": "AGENT_GAMMA_KEY"}
RECEIPT_TIMEOUT = 180

_clients: dict[str, Web3] = {}


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
    cached = _clients.get(url)
    if cached is not None:
        return cached
    w3 = Web3(Web3.HTTPProvider(url))
    if not w3.is_connected():
        raise RuntimeError("RPC not available")
    _clients[url] = w3
    return w3


def _contract(w3: Web3 | None = None):
    address = (os.getenv("ORACLE_ADDRESS") or "").strip()
    if not address:
        raise RuntimeError("ORACLE_ADDRESS required to submit consensus")
    provider = w3 or _w3()
    abi = json.loads(_ABI_PATH.read_text(encoding="utf-8"))
    return provider, provider.eth.contract(address=Web3.to_checksum_address(address), abi=abi)


def _key_address(env_name: str) -> str | None:
    key = (os.getenv(env_name) or "").strip()
    if not key:
        return None
    try:
        return Account.from_key(key).address
    except Exception:
        return None


def expected_addresses() -> dict[str, str | None]:
    """Addresses derived from the job's keys; None when a key is missing or invalid."""
    out: dict[str, str | None] = {name: _key_address(env) for name, env in AGENT_KEY_ENV.items()}
    out["operator"] = _key_address("OPERATOR_PRIVATE_KEY")
    return out


def compare_config(expected: dict[str, str | None], onchain_agents: list[str], onchain_operator: str) -> list[str]:
    """Pure check of job keys vs oracle state. Problem text never contains key material."""
    problems: list[str] = []
    registered = {str(a).lower() for a in onchain_agents}
    agent_addrs: list[str] = []
    for name, env in AGENT_KEY_ENV.items():
        addr = expected.get(name)
        if not addr:
            problems.append(f"{env} missing or invalid")
            continue
        agent_addrs.append(addr.lower())
        if addr.lower() not in registered:
            problems.append(f"{env} address {addr} is not an oracle agent")
    if len(agent_addrs) == len(AGENT_KEY_ENV) and len(set(agent_addrs)) < len(AGENT_KEY_ENV):
        problems.append("duplicate agent keys")
    operator = expected.get("operator")
    if not operator:
        problems.append("OPERATOR_PRIVATE_KEY missing or invalid")
    elif operator.lower() != str(onchain_operator).lower():
        problems.append(f"OPERATOR_PRIVATE_KEY address {operator} != oracle.operator() {onchain_operator}")
    return problems


def config_check() -> dict:
    """Chain id, oracle code, agents and operator must match the job's env before any send."""
    w3, contract = _contract()
    oracle = contract.address
    problems: list[str] = []
    raw_chain = (os.getenv("CHAIN_ID") or "0").strip()
    try:
        env_chain = int(raw_chain)
    except ValueError:
        env_chain = 0
    rpc_chain = int(w3.eth.chain_id)
    if env_chain != rpc_chain:
        problems.append(f"CHAIN_ID {env_chain} != rpc chain {rpc_chain}")
    if not w3.eth.get_code(oracle):
        problems.append("no contract code at ORACLE_ADDRESS")
        return {"ok": False, "problems": problems, "oracle": oracle, "agents": [], "operator": ""}
    agents = [contract.functions.agents(i).call() for i in range(3)]
    operator = contract.functions.operator().call()
    problems.extend(compare_config(expected_addresses(), agents, operator))
    return {"ok": not problems, "problems": problems, "oracle": oracle, "agents": agents, "operator": operator}


def is_resolved(condition_id: str) -> bool:
    _w3_client, contract = _contract()
    return bool(contract.functions.resolved(_cid_bytes(condition_id)).call())


def onchain_close_time(condition_id: str) -> int:
    _w3_client, contract = _contract()
    return int(contract.functions.closeTime(_cid_bytes(condition_id)).call())


def payout_outcome(yes: int, no: int) -> int | None:
    if yes > 0 and no == 0:
        return 0
    if no > 0 and yes == 0:
        return 1
    return None


def onchain_outcome(condition_id: str) -> int | None:
    """Outcome from CTF payouts; None when unreported or not a clean [1,0]/[0,1]."""
    w3, contract = _contract()
    ctf_abi = json.loads(_CTF_ABI_PATH.read_text(encoding="utf-8"))
    ctf = w3.eth.contract(address=Web3.to_checksum_address(contract.functions.ctf().call()), abi=ctf_abi)
    cid = _cid_bytes(condition_id)
    if int(ctf.functions.payoutDenominator(cid).call()) == 0:
        return None
    return payout_outcome(int(ctf.functions.payoutNumerators(cid, 0).call()), int(ctf.functions.payoutNumerators(cid, 1).call()))


def chain_now() -> int:
    w3 = _w3()
    return int(w3.eth.get_block("latest")["timestamp"])


def fallback_state(condition_id: str) -> dict:
    w3, contract = _contract()
    cid = _cid_bytes(condition_id)
    fns = contract.functions
    agents = {}
    for i in range(3):
        addr = fns.agents(i).call()
        agents[str(addr).lower()] = {
            "submitted": bool(fns.agentSubmitted(cid, addr).call()),
            "outcome": int(fns.agentOutcome(cid, addr).call()),
        }
    return {
        "resolved": bool(fns.resolved(cid).call()),
        "closeTime": int(fns.closeTime(cid).call()),
        "now": int(w3.eth.get_block("latest")["timestamp"]),
        "window": int(fns.WINDOW().call()),
        "agents": agents,
        "votes": (int(fns.voteWeight(cid, 0).call()), int(fns.voteWeight(cid, 1).call())),
    }


def _reason(exc: Exception) -> str:
    text = str(getattr(exc, "message", None) or exc or "")
    for prefix in ("execution reverted: ", "execution reverted"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text.strip() or "reverted"


def _operator_account():
    key = (os.getenv("OPERATOR_PRIVATE_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPERATOR_PRIVATE_KEY required to send oracle transactions")
    return Account.from_key(key)


def _chain_id() -> int:
    chain_id = int(os.getenv("CHAIN_ID") or "0")
    if chain_id <= 0:
        raise RuntimeError("CHAIN_ID required to send oracle transactions")
    return chain_id


def _send(w3: Web3, account, fn, gas: int, label: str) -> str:
    # Raises SendDeferred before anything is signed or broadcast.
    wait = budget.receipt_timeout(RECEIPT_TIMEOUT)
    try:
        fn.call({"from": account.address})
    except ContractLogicError as exc:
        raise RuntimeError(f"{label} preflight reverted: {_reason(exc)}") from None
    tx = fn.build_transaction(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address, "pending"),
            "chainId": _chain_id(),
            "gas": gas,
            "gasPrice": w3.eth.gas_price,
        }
    )
    signed = account.sign_transaction(tx)
    txh = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(txh, timeout=wait)
    if receipt["status"] != 1:
        raise RuntimeError(f"{label} transaction failed")
    return Web3.to_hex(txh)


def submit_consensus(condition_id: str, outcome: int, evidence_hash: bytes, deadline: int, sigs: list[bytes]) -> str:
    account = _operator_account()
    _chain_id()
    w3, contract = _contract()
    fn = contract.functions.submitConsensus(_cid_bytes(condition_id), outcome, evidence_hash, deadline, sigs)
    return _send(w3, account, fn, 500_000, "submitConsensus")


def submit_attestation(condition_id: str, outcome: int, evidence_hash: bytes, deadline: int, sig: bytes) -> str:
    account = _operator_account()
    w3, contract = _contract()
    fn = contract.functions.submitAttestation(_cid_bytes(condition_id), outcome, evidence_hash, deadline, sig)
    return _send(w3, account, fn, 200_000, "submitAttestation")


def preflight_fallback(condition_id: str) -> tuple[bool, str]:
    account = _operator_account()
    _w3_client, contract = _contract()
    try:
        contract.functions.resolveFallback(_cid_bytes(condition_id)).call({"from": account.address})
    except ContractLogicError as exc:
        return False, _reason(exc)
    return True, ""


def resolve_fallback(condition_id: str) -> str:
    account = _operator_account()
    w3, contract = _contract()
    return _send(w3, account, contract.functions.resolveFallback(_cid_bytes(condition_id)), 300_000, "resolveFallback")


def resolve_arbitrated(condition_id: str, outcome: int) -> str:
    account = _operator_account()
    w3, contract = _contract()
    fn = contract.functions.resolveArbitrated(_cid_bytes(condition_id), outcome)
    return _send(w3, account, fn, 300_000, "resolveArbitrated")
