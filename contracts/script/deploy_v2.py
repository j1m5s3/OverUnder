"""Migrate an OverUnder stack to MarketAMM v2 + MarketFactory v2 (pm-AMM, close gate, OU-T010 user listing).

CTF, ConsensusOracle, FeeVault, USDC and Exchange are reused. Legacy markets keep trading on the legacy AMM
and resolve through the shared oracle; their factory rows are copied into the new factory for pause and
idempotent-create lookups. After `oracle.setFactory(new)` the legacy factory can no longer create markets.

`migrate_v2(...)` runs inside an already-configured boa env (network, fork or in-process) that can sign as
`operator`. The CLI below is for local use; CI goes through script/deploy_ci.py --target v2.

Usage (cwd contracts/):
  python script/deploy_v2.py [--legacy-cids CIDS] [--api URL] [--permissionless|--no-permissionless]
      [--no-close-gate] [--min-seed-usdc N] [--listing-fee-usdc N] [--fee-recipient ADDR] [--min-lead-time S]
      [--max-horizon S] [--listing-cooldown S] [--allow-v2-source] [--dry-run]
User listing: the new factory is constructed closed; the migration then calls setPermissionless, which
defaults to on for 31337 and Base Sepolia 84532 (ADR-0012, same as deploy_ci.py) and off on other chains.
Env: CHAIN_ID (default 31337), OPERATOR_PRIVATE_KEY (anvil default on 31337 only),
     DEPLOY_RPC_URL or ANVIL_RPC_URL (default http://127.0.0.1:8545).
Never prints private keys or the full RPC URL. A migration runs once per stack: it refuses a deployments
JSON that already records one (MarketFactoryLegacy) and a v2 source pair (a fresh local deploy.py stack is
already v2; pass --allow-v2-source on 31337 to migrate it anyway). If a transaction fails part-way the
completed steps go to deployments/{chain}.v2-partial.json and deployments/{chain}.json is left unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import boa

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deploy as base  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = ROOT / "deployments"

# MarketFactory.importLegacyMarkets takes DynArray[bytes32, 50], but 50 rows with 256-byte questions cost about
# 15.9M gas, 95% of the 2^24 (16,777,216) per-transaction cap (EIP-7825). 25 per tx is about 8.0M, under half.
IMPORT_CHUNK = 25
CID_RE = re.compile(r"0x[0-9a-fA-F]{64}")
ADDR_RE = re.compile(r"0x[0-9a-fA-F]{40}")
ZERO_ADDRESS = "0x" + "00" * 20

# MarketFactory v2 constructor defaults and setListingConfig bounds (MIN_LEAD_FLOOR, MAX_HORIZON_CAP).
LISTING_DEFAULTS = {
    "minSeedUsdc": 10_000_000,
    "listingFeeUsdc": 0,
    "minLeadTime": 3600,
    "maxHorizon": 7_776_000,
    "listingCooldown": 3600,
}
LISTING_KEYS = ("minSeedUsdc", "listingFeeUsdc", "feeRecipient", "minLeadTime", "maxHorizon", "listingCooldown")
MIN_LEAD_FLOOR = 600
MAX_HORIZON_CAP = 31_622_400
AMM_CLOSE_GATE_DEFAULT = True
# The MarketFactory v2 constructor leaves user listing closed; migrate_v2 then calls setPermissionless(flag).
# ADR-0012: the Base Sepolia v2 migration opens it (min seed plus the 1 h per-creator cooldown are the gates),
# as does a local anvil. Any other chain stays allowlist-only unless --permissionless is passed.
PERMISSIONLESS_CHAINS = (31337, 84532)


def default_permissionless(chain: int) -> bool:
    return int(chain) in PERMISSIONLESS_CHAINS


def _fn(name: str, inputs=(), outputs=(), mutability: str = "view") -> dict:
    return {
        "type": "function",
        "name": name,
        "stateMutability": mutability,
        "inputs": [{"name": f"a{i}", "type": t} for i, t in enumerate(inputs)],
        "outputs": [{"name": "", "type": t} for t in outputs],
    }


# Only the getters/setters shared by v1 and v2, so the legacy contracts never need their source.
LEGACY_FACTORY_ABI = [
    _fn("ctf", outputs=["address"]),
    _fn("oracle", outputs=["address"]),
    _fn("amm", outputs=["address"]),
    _fn("usdc", outputs=["address"]),
    _fn("operator", outputs=["address"]),
    _fn("wildcardGenerator", outputs=["address"]),
    _fn("marketExists", ["bytes32"], ["bool"]),
    _fn("minSeedUsdc", outputs=["uint256"]),  # v2 only: probe, never relied on
]
LEGACY_AMM_ABI = [
    _fn("feeVault", outputs=["address"]),
    _fn("operator", outputs=["address"]),
    _fn("closeGate", outputs=["bool"]),  # v2 only: probe, never relied on
]
MISMATCH_RE = re.compile(r"uh oh! (0x[0-9a-fA-F]{40}) != (0x[0-9a-fA-F]{40})")
ORACLE_ABI = [
    _fn("factory", outputs=["address"]),
    _fn("operator", outputs=["address"]),
    _fn("setFactory", ["address"], mutability="nonpayable"),
]


def _at(abi: list, address: str, name: str):
    return boa.loads_abi(json.dumps(abi), name=name).at(address)


def _same(a, b) -> bool:
    return str(a).lower() == str(b).lower()


def normalize_cids(cids) -> list[str]:
    """0x-hex strings or 32-byte values -> lowercase 0x-hex, deduplicated, order kept."""
    out: list[str] = []
    for raw in cids or []:
        if isinstance(raw, (bytes, bytearray)) and len(raw) == 32:
            cid = "0x" + bytes(raw).hex()
        elif isinstance(raw, str) and CID_RE.fullmatch(raw.strip()):
            cid = raw.strip().lower()
        else:
            raise ValueError(f"legacy cid {str(raw)[:80]!r} is not a 32-byte 0x-hex condition id")
        if cid not in out:
            out.append(cid)
    return out


def listing_args(listing: dict, fee_vault: str) -> tuple:
    """setListingConfig args in ABI order. Missing keys keep the constructor defaults; the fee recipient
    defaults to the FeeVault. Validated here so a bad config fails before any transaction."""
    listing = dict(listing or {})
    unknown = sorted(set(listing) - set(LISTING_KEYS))
    if unknown:
        raise ValueError(f"unknown listing keys: {', '.join(unknown)} (expected {', '.join(LISTING_KEYS)})")
    cfg = {k: listing.get(k) for k in LISTING_KEYS}
    for key, default in LISTING_DEFAULTS.items():
        value = default if cfg[key] is None else cfg[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"listing {key} must be a non-negative integer")
        cfg[key] = value
    recipient = cfg["feeRecipient"] or fee_vault
    if not ADDR_RE.fullmatch(str(recipient)):
        raise ValueError("listing feeRecipient must be a 0x address")
    cfg["feeRecipient"] = str(recipient)
    if cfg["minSeedUsdc"] == 0:
        raise ValueError("listing minSeedUsdc must be > 0")
    if cfg["listingFeeUsdc"] > 0 and _same(cfg["feeRecipient"], ZERO_ADDRESS):
        raise ValueError("listing feeRecipient is required when listingFeeUsdc > 0")
    if cfg["minLeadTime"] < MIN_LEAD_FLOOR:
        raise ValueError(f"listing minLeadTime must be >= {MIN_LEAD_FLOOR}")
    if not cfg["minLeadTime"] < cfg["maxHorizon"] <= MAX_HORIZON_CAP:
        raise ValueError(f"listing maxHorizon must be > minLeadTime and <= {MAX_HORIZON_CAP}")
    return tuple(cfg[k] for k in LISTING_KEYS)


def _load_recorded(progress: dict, steps: list, name: str, *args):
    """boa.load that records a contract left on chain by an address mismatch.

    NetworkEnv predicts the CREATE address on its local fork; if another tx from the same key lands first
    (shared operator nonce) the deploy mines at a different address and boa raises "uh oh! local != create"
    after the contract exists. The mined address goes to progress["orphans"] before re-raising."""
    try:
        return boa.load(str(SRC / f"{name}.vy"), *args)
    except RuntimeError as exc:
        m = MISMATCH_RE.search(str(exc))
        if m:
            progress.setdefault("orphans", {})[name] = m.group(2)
            steps.append(f"deploy {name} (mined at {m.group(2)}; address mismatch, unwired)")
        raise


def _block_number() -> int:
    # Network env re-forks at each receipt's block, so right after a deploy this is that tx's block.
    return int(boa.env.evm.patch.block_number)


def migrate_v2(
    *,
    deployments: dict,
    operator: str,
    legacy_cids: list[str],
    listing: dict,
    permissionless: bool,
    close_gate: bool,
    progress: dict | None = None,
    allow_v2_source: bool = False,
) -> dict:
    """Deploy MarketAMM v2 + MarketFactory v2 next to the legacy pair and cut the shared oracle over.

    Order: all reads and checks first (no tx on failure), then deploy, configure the fresh contracts,
    switch `oracle.setFactory` (legacy creates stop here), then import the legacy rows in chunks of IMPORT_CHUNK (25).
    `progress` (optional) is filled as transactions land ("MarketAMM", "MarketFactory", "deployBlock",
    "steps", and "orphans" for a contract mined at an unexpected address), so a caller can report what is
    already on chain if a later transaction fails.

    Refuses to run twice: a deployments dict that records a migration (MarketFactoryLegacy/MarketAMMLegacy,
    as main() writes it) and a legacy pair that is already v2 (answers minSeedUsdc()/closeGate()) are both
    rejected before any transaction. Otherwise a re-run would deploy a third pair, repoint the oracle and
    strand user-listed pools on the previous v2 AMM. `allow_v2_source` lifts only the v2-pair probe (tests,
    and a fresh local deploy.py stack, which is already v2).
    """
    progress = {} if progress is None else progress
    steps = progress.setdefault("steps", [])
    if deployments.get("MarketFactoryLegacy") or deployments.get("MarketAMMLegacy"):
        raise ValueError("deployments already record a v2 migration (MarketFactoryLegacy present); refusing to re-run")
    legacy_factory_addr = str(deployments["MarketFactory"])
    legacy_amm_addr = str(deployments["MarketAMM"])
    cids = normalize_cids(legacy_cids)

    legacy = _at(LEGACY_FACTORY_ABI, legacy_factory_addr, "MarketFactoryLegacy")
    legacy_amm = _at(LEGACY_AMM_ABI, legacy_amm_addr, "MarketAMMLegacy")
    ctf = str(legacy.ctf())
    oracle_addr = str(legacy.oracle())
    usdc = str(legacy.usdc())
    generator = str(legacy.wildcardGenerator())
    fee_vault = str(legacy_amm.feeVault())
    oracle = _at(ORACLE_ABI, oracle_addr, "ConsensusOracle")

    # Transport errors propagate from base.answers (fail closed); only a revert reads as "v1".
    if not allow_v2_source and (base.answers(legacy.minSeedUsdc) or base.answers(legacy_amm.closeGate)):
        raise ValueError(
            "deployments MarketFactory/MarketAMM are already v2 (answers minSeedUsdc()/closeGate()); "
            "refusing a v2 -> v2 migration"
        )
    if not _same(legacy.operator(), operator):
        raise ValueError(f"legacy MarketFactory operator {legacy.operator()} is not {operator}")
    if not _same(oracle.operator(), operator):
        raise ValueError(f"ConsensusOracle operator {oracle.operator()} is not {operator}")
    if not _same(legacy.amm(), legacy_amm_addr):
        raise ValueError(f"legacy MarketFactory.amm() {legacy.amm()} does not match deployments MarketAMM")
    if not _same(oracle.factory(), legacy_factory_addr):
        raise ValueError(f"ConsensusOracle.factory() is {oracle.factory()}, not the legacy factory (already migrated?)")
    config = listing_args(listing, fee_vault)
    importable = [c for c in cids if legacy.marketExists(bytes.fromhex(c[2:]))]

    with boa.env.prank(operator):
        amm = _load_recorded(progress, steps, "MarketAMM", ctf, usdc, fee_vault, operator)
        deploy_block = _block_number()
        progress.update(MarketAMM=str(amm.address), deployBlock=deploy_block)
        steps.append("deploy MarketAMM")
        factory = _load_recorded(progress, steps, "MarketFactory", ctf, oracle_addr, amm.address, usdc, operator, generator)
        progress["MarketFactory"] = str(factory.address)
        steps.append("deploy MarketFactory")
        amm.setFactory(factory.address)
        steps.append("amm.setFactory")
        if bool(close_gate) != AMM_CLOSE_GATE_DEFAULT:
            amm.setCloseGate(bool(close_gate))
            steps.append("amm.setCloseGate")
        factory.setListingConfig(*config)
        steps.append("factory.setListingConfig")
        factory.setPermissionless(bool(permissionless))
        steps.append("factory.setPermissionless")
        oracle.setFactory(factory.address)
        steps.append("oracle.setFactory")
        for i in range(0, len(importable), IMPORT_CHUNK):
            chunk = [bytes.fromhex(c[2:]) for c in importable[i : i + IMPORT_CHUNK]]
            factory.importLegacyMarkets(legacy_factory_addr, chunk)
            steps.append(f"importLegacyMarkets[{i}:{i + len(chunk)}]")

    return {
        "MarketAMM": str(amm.address),
        "MarketFactory": str(factory.address),
        "MarketAMMLegacy": legacy_amm_addr,
        "MarketFactoryLegacy": legacy_factory_addr,
        "imported": importable,
        "deployBlock": deploy_block,
    }


def _http_get_json(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "overunder-deploy-v2"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - http(s) checked by caller
        return json.loads(resp.read().decode("utf-8"))


def fetch_market_cids(api_url: str, http_get=None) -> list[str]:
    """Primary and child cids from GET {api}/api/v1/markets. Paused rows are hidden there: add them via --legacy-cids."""
    if not api_url.startswith(("http://", "https://")):
        raise SystemExit("ERROR: --api must be an http(s) URL")
    url = api_url.rstrip("/") + "/api/v1/markets"
    rows = (http_get or _http_get_json)(url)
    if not isinstance(rows, list):
        raise SystemExit(f"ERROR: {url} did not return a list")
    out: list[str] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        group = [item.get("primary") or {}] + list(item.get("children") or []) if "primary" in item else [item]
        for row in group:
            cid = str((row or {}).get("conditionId") or "").lower()
            if CID_RE.fullmatch(cid) and cid not in out:
                out.append(cid)
    return out


def _parse_cid_arg(raw: str) -> list[str]:
    return normalize_cids([t for t in re.split(r"[\s,]+", raw or "") if t])


def _report_failure(exc: BaseException, rpc: str, progress: dict, deployments: dict, chain: int, dry_run: bool) -> int:
    """Print a redacted error and what already landed; write a sidecar partial record. Returns the exit code."""
    print(f"ERROR: migrate_v2 failed: {type(exc).__name__}: {base.redact(str(exc), rpc)[:300]}", file=sys.stderr)
    steps = list(progress.get("steps") or [])
    for name in ("MarketAMM", "MarketFactory", "deployBlock"):
        if progress.get(name) is not None:
            print(f"  {name}: {progress[name]}", file=sys.stderr)
    for name, addr in (progress.get("orphans") or {}).items():
        print(f"  {name} (address mismatch, unwired): {addr}", file=sys.stderr)
    print(f"  completed steps: {', '.join(steps) if steps else 'none'}", file=sys.stderr)
    if dry_run or not steps:
        return 1
    import deploy_ci

    # deployments/{chain}.json stays as it was: the backend and mobile read MarketAMM/MarketFactory from it,
    # and partial_v2_payload drops the ones that were never deployed.
    sidecar = OUT / f"{chain}.v2-partial.json"
    sidecar.write_text(json.dumps(deploy_ci.partial_v2_payload(deployments, progress), indent=2))
    print(f"wrote {sidecar} ({chain}.json unchanged)", file=sys.stderr)
    if "oracle.setFactory" in steps:
        print(
            "The oracle already points at the new factory (legacy creates stop). Do not re-run blindly: "
            "finish the remaining steps by hand as the operator using the addresses above.",
            file=sys.stderr,
        )
    return 1


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv
    from eth_account import Account

    p = argparse.ArgumentParser(description="Migrate to MarketAMM v2 + MarketFactory v2")
    p.add_argument("--legacy-cids", default="", help="comma/space separated legacy condition ids to import")
    p.add_argument("--api", default="", help="backend base URL; imports the cids listed by GET /api/v1/markets")
    p.add_argument("--permissionless", action=argparse.BooleanOptionalAction, default=None,
                   help="open user listing to everyone (default: on for 31337 and Base Sepolia 84532, matching "
                        "ADR-0012 and deploy_ci.py; off on any other chain, where only setLister addresses may list)")
    p.add_argument("--close-gate", action=argparse.BooleanOptionalAction, default=True,
                   help="halt AMM trading at closeTime (default: on)")
    for flag, key in (
        ("--min-seed-usdc", "minSeedUsdc"),
        ("--listing-fee-usdc", "listingFeeUsdc"),
        ("--min-lead-time", "minLeadTime"),
        ("--max-horizon", "maxHorizon"),
        ("--listing-cooldown", "listingCooldown"),
    ):
        p.add_argument(flag, dest=key, type=int, default=None)
    p.add_argument("--fee-recipient", dest="feeRecipient", default=None, help="default: the FeeVault")
    p.add_argument("--allow-v2-source", action="store_true",
                   help="31337 only: migrate a pair that is already v2 (a fresh local deploy.py stack)")
    p.add_argument("--dry-run", action="store_true", help="simulate on boa.fork(rpc); nothing is sent or written")
    args = p.parse_args(argv)

    load_dotenv(ROOT.parent / ".env")
    chain = int(os.getenv("CHAIN_ID", str(base.LOCAL_CHAIN)))
    rpc = os.getenv("DEPLOY_RPC_URL") or os.getenv("ANVIL_RPC_URL") or "http://127.0.0.1:8545"
    label = base.rpc_label(rpc)
    local = chain == base.LOCAL_CHAIN
    if args.allow_v2_source and not local:
        raise SystemExit(f"ERROR: --allow-v2-source is local-only (CHAIN_ID={chain})")
    path = OUT / f"{chain}.json"
    if not path.exists():
        raise SystemExit(f"ERROR: {path} not found; deploy the v1 stack first")
    deployments = json.loads(path.read_text())
    operator = Account.from_key(base.role_key("OPERATOR_PRIVATE_KEY", "operator", chain))

    cids = _parse_cid_arg(args.legacy_cids)
    if args.api:
        cids = normalize_cids(cids + fetch_market_cids(args.api))
    permissionless = default_permissionless(chain) if args.permissionless is None else args.permissionless
    listing = {k: getattr(args, k) for k in LISTING_KEYS if getattr(args, k) is not None}

    try:
        if args.dry_run:
            # "latest": the default "safe" block lags head. Local anvils skip the on-disk fork cache (all 31337).
            boa.fork(rpc, block_identifier="latest", **({"cache_dir": None} if local else {}))
            boa.env.eoa = operator.address
            boa.env.set_balance(operator.address, 10**20)
        else:
            boa.set_network_env(rpc)
            if local:
                base.use_memory_fork_cache(boa.env)
            boa.env.add_account(operator, force_eoa=True)
            boa.env.suppress_debug_tt(True)  # a trace failure must not abort after a tx has mined
        rpc_chain = boa.env.get_chain_id() if not args.dry_run else int(boa.env.evm.patch.chain_id)
    except Exception as exc:
        raise SystemExit(f"ERROR: cannot use RPC at {label} ({type(exc).__name__})") from None
    if rpc_chain != chain:
        raise SystemExit(f"ERROR: RPC at {label} reports chain id {rpc_chain}, expected CHAIN_ID={chain}")
    mode = "dry run (fork)" if args.dry_run else "broadcast"
    print(f"{mode} via {label}; operator {operator.address}; {len(cids)} legacy cid(s) requested")

    progress: dict = {}
    try:
        result = migrate_v2(
            deployments=dict(deployments),
            operator=operator.address,
            legacy_cids=cids,
            listing=listing,
            permissionless=permissionless,
            close_gate=args.close_gate,
            progress=progress,
            allow_v2_source=args.allow_v2_source,
        )
    except Exception as exc:  # never let a traceback echo the RPC URL (requests: "... for url: <full url>")
        return _report_failure(exc, rpc, progress, deployments, chain, args.dry_run)
    try:
        print(f"  MarketAMM: {result['MarketAMM']} (legacy {result['MarketAMMLegacy']})")
        print(f"  MarketFactory: {result['MarketFactory']} (legacy {result['MarketFactoryLegacy']})")
        print(f"  deployBlock: {result['deployBlock']}; imported {len(result['imported'])} of {len(cids)} legacy cid(s)")
        skipped = [c for c in cids if c not in result["imported"]]
        if skipped:
            print(f"  not in legacy factory (skipped): {', '.join(skipped)}")
        if args.dry_run:
            print("dry run: addresses are indicative only; deployments JSON not written")
            return 0
        deployments.update({k: result[k] for k in ("MarketAMM", "MarketFactory", "MarketAMMLegacy", "MarketFactoryLegacy", "deployBlock")})
        path.write_text(json.dumps(deployments, indent=2))
        print(f"wrote {path}")
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {base.redact(str(exc), rpc)[:300]}", file=sys.stderr)
        done = {k: str(result.get(k)) for k in ("MarketAMM", "MarketFactory", "MarketAMMLegacy", "MarketFactoryLegacy", "deployBlock")}
        print(f"  the migration completed on chain; record these by hand: {json.dumps(done)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
