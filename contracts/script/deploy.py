import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import boa
from dotenv import load_dotenv
from eth_account import Account

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = ROOT / "deployments"
COOLDOWN = 24 * 60 * 60

CANONICAL_ENTRYPOINT_V07 = "0x0000000071727De22E5E9d8BAf0edAc6f37da032"

ANVIL_KEYS = {
    "operator": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    "relayer": "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    "alpha": "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
    "beta": "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",
    "gamma": "0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a",
    "treasury": "0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348eac14685e0ba0",
    "generator": "0x92db14e403b83dfe3df233f83dfa3a0d7096f21ca9b0d6d6b8d88b2b4ec1564e",
}
LOCAL_CHAIN = 31337
_PUBLIC_KEYS = {k.lower().removeprefix("0x") for k in ANVIL_KEYS.values()}
CANONICAL_RECEIPT_TIMEOUT = 180.0


def _is_canonical(rpc, receipt) -> bool:
    """A receipt names a sealed block: non-zero blockHash equal to the canonical block at its height."""
    block_hash = (receipt or {}).get("blockHash") or "0x0"
    block_number = (receipt or {}).get("blockNumber")
    if not block_number or int(block_hash, 16) == 0:
        return False
    block = rpc.fetch_uncached("eth_getBlockByNumber", [block_number, False])
    return bool(block) and str(block.get("hash") or "").lower() == block_hash.lower()


def require_canonical_receipts(env=None, timeout: float = CANONICAL_RECEIPT_TIMEOUT, poll: float = 1.0) -> None:
    """Base Sepolia serves Flashblocks pre-confirmation receipts (blockHash 0x0, block not sealed yet).
    titanoboa takes the first non-null receipt, resets its fork to that block and reads contractAddress,
    which crashes part-way through a broadcast. Keep polling until the receipt is canonical."""
    rpc = getattr(env or boa.env, "_rpc", None)
    if rpc is None or not hasattr(rpc, "fetch_uncached") or getattr(rpc, "_ou_canonical_receipts", False):
        return
    first_receipt = rpc.wait_for_tx_receipt

    def wait_for_tx_receipt(tx_hash, timeout_s, poll_latency=0.25):
        deadline = time.time() + max(float(timeout_s), timeout)
        receipt = first_receipt(tx_hash, timeout_s, poll_latency)
        while not _is_canonical(rpc, receipt):
            if time.time() + poll > deadline:
                raise ValueError(f"Timed out waiting for a canonical receipt ({tx_hash})")
            time.sleep(poll)
            receipt = rpc.fetch_uncached("eth_getTransactionReceipt", [tx_hash]) or receipt
        return receipt

    rpc.wait_for_tx_receipt = wait_for_tx_receipt
    rpc._ou_canonical_receipts = True


def local_listing_config(fee_recipient: str) -> tuple:
    """MarketFactory.setListingConfig args for 31337: 10 USDC min seed, no fee, 1h lead, 90d horizon, no cooldown."""
    return (10_000_000, 0, fee_recipient, 3600, 7_776_000, 0)


def redact(text: str, rpc: str) -> str:
    """Replace the RPC URL and its host, path and query in `text` (exception messages echo the full URL)."""
    if not rpc:
        return text
    parts = {rpc}
    parsed = urlparse(rpc)
    if parsed.netloc:
        parts.add(parsed.netloc)
    if parsed.path and len(parsed.path) > 1:
        parts.add(parsed.path)
    if parsed.query:
        parts.add(parsed.query)
    for part in sorted(parts, key=len, reverse=True):
        text = text.replace(part, "<rpc>")
    return text


def answers(fn) -> bool:
    """True when a getter answers; False only for a genuine EVM revert (e.g. a v1 contract without the selector).

    Transport and node errors (HTTP 429/5xx, timeouts, rate-limit RPC codes) propagate, so a v1/v2 probe
    fails closed instead of reading "selector missing". A return value that does not decode also counts
    as "does not answer" (not the expected getter)."""
    from boa.contracts.base_evm_contract import BoaError
    from boa.rpc import RPCError
    from boa.util.abi import ABIError

    try:
        fn()
        return True
    except (BoaError, ABIError):  # local EVM revert or undecodable return (in-process, boa.fork, NetworkEnv)
        return False
    except RPCError as exc:  # eth_call revert reported by a node
        if "revert" in str(exc).lower():
            return False
        raise


def use_memory_fork_cache(env) -> bool:
    """Local chains only: re-fork a NetworkEnv with an in-memory RPC cache.

    titanoboa caches fork reads on disk keyed by chain id and block number. Every anvil is chain 31337 and
    starts at block 0, so a second anvil would be served the previous chain's code and storage. Public
    chains keep the disk cache (their history is immutable). Returns False when `env` is not a fork env."""
    rpc = getattr(env, "_rpc", None)
    if rpc is None or not callable(getattr(env, "_reset_fork", None)):
        return False

    def _reset_fork(block_identifier="latest"):
        env.fork_rpc(rpc, reset_traces=False, block_identifier=block_identifier, cache_dir=None)

    env._reset_fork = _reset_fork
    _reset_fork()
    return True


def rpc_label(url: str) -> str:
    """scheme://host only: provider URLs often carry an API key in the path, query or credentials."""
    parsed = urlparse(url or "")
    if not parsed.scheme or not parsed.hostname:
        return "<rpc>"
    return f"{parsed.scheme}://{parsed.hostname}"


def is_public_key(key: str) -> bool:
    return (key or "").strip().lower().removeprefix("0x") in _PUBLIC_KEYS


def role_key(var: str, default_role: str, chain: int) -> str:
    """Env key for a role. Only chain 31337 may fall back to (or use) the public anvil keys."""
    raw = (os.getenv(var) or "").strip()
    if chain == LOCAL_CHAIN:
        return raw or ANVIL_KEYS[default_role]
    if not raw:
        raise SystemExit(f"ERROR: {var} is required when CHAIN_ID={chain} (anvil defaults are local-only)")
    if is_public_key(raw):
        raise SystemExit(f"ERROR: {var} is a public anvil key; refusing to deploy to CHAIN_ID={chain}")
    return raw


def deploy(
    operator: str,
    treasury: str,
    generator: str,
    agents: list[str],
    cooldown: int = COOLDOWN,
    chain: int = 31337,
    progress: dict | None = None,
):
    """Deploy and wire the full stack. `progress` (optional) receives name -> address as each contract
    lands, so a caller can report what is already on chain if a later transaction fails."""
    progress = {} if progress is None else progress

    def _load(name: str, *args):
        contract = boa.load(str(SRC / name), *args)
        progress[name.removesuffix(".vy")] = str(contract.address)
        return contract

    usdc = _load("MockUSDC.vy")
    ou = _load("RevenueToken.vy", treasury)
    ctf = _load("ConditionalTokens.vy", usdc.address)
    vault = _load("FeeVault.vy", usdc.address, ou.address, cooldown)
    oracle = _load("ConsensusOracle.vy", ctf.address, operator, agents)
    exchange = _load("Exchange.vy", ctf.address, usdc.address, vault.address, operator)
    amm = _load("MarketAMM.vy", ctf.address, usdc.address, vault.address, operator)
    factory = _load(
        "MarketFactory.vy",
        ctf.address,
        oracle.address,
        amm.address,
        usdc.address,
        operator,
        generator,
    )
    if chain == 84532:
        entrypoint_addr = CANONICAL_ENTRYPOINT_V07
        entrypoint = None
    else:
        entrypoint = _load("MockEntryPoint.vy")
        entrypoint_addr = entrypoint.address
    account_impl = _load("SimpleAccount.vy")
    account_factory = _load("SimpleAccountFactory.vy", account_impl.address, entrypoint_addr)
    paymaster = _load(
        "OverUnderPaymaster.vy",
        entrypoint_addr,
        operator,
        usdc.address,
        ctf.address,
        amm.address,
        exchange.address,
        oracle.address,
        vault.address,
    )
    default_deposit = 10**18 if chain == 31337 else 5 * 10**16
    deposit_wei = int(os.getenv("PAYMASTER_DEPOSIT_WEI") or default_deposit)
    with boa.env.prank(operator):
        oracle.setFactory(factory.address)
        amm.setFactory(factory.address)
        if chain == LOCAL_CHAIN:
            # Local dev can list user markets right away; public chains keep the constructor's closed allowlist mode.
            factory.setListingConfig(*local_listing_config(vault.address))
            factory.setPermissionless(True)
        paymaster.addFactory(account_factory.address)
        paymaster.setWeiPerUsdc(10**15)
        paymaster.deposit(value=deposit_wei)
    out = {
        "MockUSDC": usdc,
        "RevenueToken": ou,
        "ConditionalTokens": ctf,
        "FeeVault": vault,
        "ConsensusOracle": oracle,
        "Exchange": exchange,
        "MarketAMM": amm,
        "MarketFactory": factory,
        "SimpleAccount": account_impl,
        "SimpleAccountFactory": account_factory,
        "OverUnderPaymaster": paymaster,
    }
    if entrypoint is not None:
        out["MockEntryPoint"] = entrypoint
    else:
        out["EntryPoint"] = entrypoint_addr
    return out


def dump_addresses(contracts: dict, chain: int, extra=None) -> Path:
    OUT.mkdir(exist_ok=True)
    payload = {name: (c if isinstance(c, str) else c.address) for name, c in contracts.items()}
    payload["chainId"] = chain
    if extra:
        payload.update(extra)
    path = OUT / f"{chain}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def main():
    load_dotenv(ROOT.parent / ".env")
    rpc = os.getenv("ANVIL_RPC_URL", "http://127.0.0.1:8545")
    chain = int(os.getenv("CHAIN_ID", str(LOCAL_CHAIN)))
    usdc_override = os.getenv("USDC_ADDRESS", "").strip()

    # Off 31337 every role needs an explicit, non-public key (role_key raises otherwise).
    operator = Account.from_key(role_key("OPERATOR_PRIVATE_KEY", "operator", chain))
    treasury = Account.from_key(role_key("TREASURY_PRIVATE_KEY", "treasury", chain))
    generator = Account.from_key(role_key("OPERATOR_PRIVATE_KEY", "generator", chain))
    alpha = Account.from_key(role_key("AGENT_ALPHA_KEY", "alpha", chain))
    beta = Account.from_key(role_key("AGENT_BETA_KEY", "beta", chain))
    gamma = Account.from_key(role_key("AGENT_GAMMA_KEY", "gamma", chain))

    label = rpc_label(rpc)
    try:
        boa.set_network_env(rpc)
        rpc_chain = boa.env.get_chain_id()
        # Also guards CHAIN_ID left at 31337 (anvil keys allowed) while the RPC points at a public chain.
        if rpc_chain != chain:
            raise SystemExit(f"ERROR: RPC at {label} reports chain id {rpc_chain}, expected CHAIN_ID={chain}")
        if chain == LOCAL_CHAIN:
            use_memory_fork_cache(boa.env)  # a restarted anvil must not see the last one's cached state
        require_canonical_receipts(boa.env)
        boa.env.add_account(operator)
        print(f"deploying via {label}")
    except SystemExit:
        raise
    except Exception as exc:
        # Exception text can echo the full URL (and its API key), so only the type is printed.
        if chain != LOCAL_CHAIN:
            raise SystemExit(
                f"ERROR: cannot use RPC at {label} ({type(exc).__name__}); "
                f"the in-process fallback is local-only (CHAIN_ID={chain})"
            ) from None
        print(f"no rpc at {label} ({type(exc).__name__}); deploying in-process boa EVM")
        boa.reset_env()
        boa.env.set_balance(operator.address, 10**18)

    agents = [alpha.address, beta.address, gamma.address]
    progress: dict = {}
    try:
        contracts = deploy(operator.address, treasury.address, generator.address, agents, chain=chain, progress=progress)

        extra = {
            "operator": operator.address,
            "treasury": treasury.address,
            "wildcardGenerator": generator.address,
            "agents": agents,
        }
        if usdc_override and chain != 31337:
            extra["MockUSDC"] = usdc_override
            extra["canonicalUSDC"] = usdc_override
        path = dump_addresses(contracts, chain, extra)
    except Exception as exc:
        # A traceback (e.g. requests' "429 ... for url: <full url>") would print the provider key.
        print(f"ERROR: deploy failed: {type(exc).__name__}: {redact(str(exc), rpc)[:300]}", file=sys.stderr)
        if progress:
            print("Already deployed on chain (not wired unless listed in order):", file=sys.stderr)
            for name, addr in progress.items():
                print(f"  {name}: {addr}", file=sys.stderr)
        raise SystemExit(1) from None
    print(f"wrote {path}")
    for k, v in json.loads(path.read_text()).items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
