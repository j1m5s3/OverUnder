"""CI contract deploys and read-only checks for Base Sepolia.

Never prints private keys or the RPC URL. Writes public addresses only.

Usage (cwd contracts/):
  python script/deploy_ci.py --target verify|v2|core [--broadcast] --out DIR [--summary FILE]
      [--api-url URL] [--legacy-cids CIDS] [--min-seed-usdc N] [--listing-cooldown S] [--no-permissionless]
      [--no-close-gate]
  --in-process runs on the local boa EVM (tests only).
A broadcast (v2, core) refuses to start while the operator has pending transactions: the oracle job and the
API sign with the same key, so pause overunder-oracle-tick first (deploy-contracts.yml does).

Targets:
  verify  read-only: code at every repo-var address, factory/oracle/AMM wiring, operator and agents
  v2      MarketAMM v2 + MarketFactory v2 via deploy_v2.migrate_v2; CTF, oracle, vault, USDC, Exchange reused
  core    full stack via deploy.deploy (new addresses for everything)
Without --broadcast every target runs on boa.fork(rpc) and nothing is sent.

Env:
  OPERATOR_PRIVATE_KEY                                     required (32-byte hex)
  AGENT_ALPHA_KEY AGENT_BETA_KEY AGENT_GAMMA_KEY           verify, core
  DEPLOY_RPC_URL                                           required unless --in-process
  TREASURY_ADDRESS                                         core
  USDC_ADDRESS CTF_ADDRESS FACTORY_ADDRESS EXCHANGE_ADDRESS AMM_ADDRESS
  ORACLE_ADDRESS FEE_VAULT_ADDRESS                         existing core (verify, v2)
  OU_TOKEN_ADDRESS PAYMASTER_ADDRESS ENTRYPOINT_ADDRESS
  ACCOUNT_FACTORY_ADDRESS                                  optional, copied into the v2 deployments JSON
  GITHUB_REPOSITORY                                        repo named in the printed `gh variable set` lines
  GITHUB_RUN_ID                                            run whose artifact the printed mobile-sync commands download
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import sys
import urllib.request
import warnings
from functools import lru_cache
from pathlib import Path

import boa
from eth_account import Account

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deploy as base  # noqa: E402

CHAIN_ID = 84532
LOCAL_CHAIN_ID = 31337
DEFAULT_REPO = "j1m5s3/OverUnder"
KEY_RE = re.compile(r"(0x)?[0-9a-fA-F]{64}")
ADDR_RE = re.compile(r"0x[0-9a-fA-F]{40}")
CID_RE = re.compile(r"0x[0-9a-fA-F]{64}")
AGENT_VARS = ("AGENT_ALPHA_KEY", "AGENT_BETA_KEY", "AGENT_GAMMA_KEY")
KEY_VARS = ("OPERATOR_PRIVATE_KEY",) + AGENT_VARS
# v2 signs with the operator only; agent keys are needed to derive the expected oracle agents.
TARGET_KEYS = {"verify": KEY_VARS, "core": KEY_VARS, "v2": ("OPERATOR_PRIVATE_KEY",)}
# deployments JSON name -> GitHub repo variable
REPO_VARS = {
    "MockUSDC": "USDC_ADDRESS",
    "ConditionalTokens": "CTF_ADDRESS",
    "MarketFactory": "FACTORY_ADDRESS",
    "Exchange": "EXCHANGE_ADDRESS",
    "MarketAMM": "AMM_ADDRESS",
    "ConsensusOracle": "ORACLE_ADDRESS",
    "FeeVault": "FEE_VAULT_ADDRESS",
    "RevenueToken": "OU_TOKEN_ADDRESS",
    "OverUnderPaymaster": "PAYMASTER_ADDRESS",
    "EntryPoint": "ENTRYPOINT_ADDRESS",
    "SimpleAccountFactory": "ACCOUNT_FACTORY_ADDRESS",
    "deployBlock": "INDEXER_START_BLOCK",
}
CORE_NAMES = ["MockUSDC", "ConditionalTokens", "FeeVault", "ConsensusOracle", "Exchange", "MarketAMM", "MarketFactory"]
OPTIONAL_NAMES = ["RevenueToken", "OverUnderPaymaster", "EntryPoint", "SimpleAccountFactory"]
# Repo variables a v2 migration changes; everything else is reused.
V2_VARS = ("MarketAMM", "MarketFactory", "deployBlock")
V2_RESULT_KEYS = ("MarketAMM", "MarketFactory", "MarketAMMLegacy", "MarketFactoryLegacy", "imported", "deployBlock")
MIN_BALANCE_WEI = {"core": 7 * 10**16, "v2": 5 * 10**15}
# MarketFactory v2 setListingConfig defaults (fee recipient is the FeeVault). Same values as the contract
# constructor and deploy_v2.LISTING_DEFAULTS (a test pins the parity); minSeedUsdc and listingCooldown come
# from the CLI. The 1 h per-creator cooldown is the only per-address rate limit on user listing.
LISTING_DEFAULTS = {"listingFeeUsdc": 0, "minLeadTime": 3600, "maxHorizon": 90 * 86400, "listingCooldown": 3600}


class InputError(SystemExit):
    pass


def load_accounts(env: dict[str, str], names=KEY_VARS) -> dict[str, object]:
    out = {}
    for var in names:
        raw = (env.get(var) or "").strip()
        if not KEY_RE.fullmatch(raw):
            raise InputError(f"ERROR: {var} is missing or not a 32-byte hex key (value not shown)")
        out[var] = Account.from_key(raw)
    return out


def existing_addresses(env: dict[str, str], names: list[str], optional: list[str] = ()) -> dict[str, str]:
    out, missing = {}, []
    for name in names:
        value = (env.get(REPO_VARS[name]) or "").strip()
        if not ADDR_RE.fullmatch(value):
            missing.append(REPO_VARS[name])
        else:
            out[name] = value
    if missing:
        raise InputError(f"ERROR: missing or malformed repo variables: {', '.join(missing)}")
    for name in optional:
        value = (env.get(REPO_VARS[name]) or "").strip()
        if ADDR_RE.fullmatch(value):
            out[name] = value
    return out


redact = base.redact


def parse_cids(raw: str) -> list[str]:
    """Comma/whitespace separated condition ids -> lowercase 0x-hex, deduplicated, order kept."""
    out: list[str] = []
    for token in re.split(r"[\s,]+", raw or ""):
        if not token:
            continue
        if not CID_RE.fullmatch(token):
            raise InputError(f"ERROR: legacy cid '{token[:80]}' is not a 32-byte 0x-hex condition id")
        if token.lower() not in out:
            out.append(token.lower())
    return out


def _http_get_json(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "overunder-deploy-ci"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - http(s) checked by caller
        return json.loads(resp.read().decode("utf-8"))


def fetch_market_cids(api_url: str, http_get=None) -> list[str]:
    """Primary and child condition ids listed by GET {api}/api/v1/markets (paused rows are hidden there)."""
    if not api_url.startswith(("http://", "https://")):
        raise InputError("ERROR: --api-url must be an http(s) URL")
    url = api_url.rstrip("/") + "/api/v1/markets"
    try:
        cards = (http_get or _http_get_json)(url)
    except Exception as exc:
        raise InputError(f"ERROR: could not list markets from {url}: {type(exc).__name__}: {str(exc)[:200]}") from exc
    if not isinstance(cards, list):
        raise InputError(f"ERROR: {url} did not return a list of event cards")
    out: list[str] = []
    for card in cards:
        rows = [card.get("primary") or {}] + list(card.get("children") or []) if isinstance(card, dict) else []
        for row in rows:
            cid = str((row or {}).get("conditionId") or "").lower()
            if CID_RE.fullmatch(cid) and cid not in out:
                out.append(cid)
    return out


def connect(rpc: str, broadcast: bool, in_process: bool, operator) -> int | None:
    """Configure boa for the run; returns the operator's on-chain balance (None in-process)."""
    if in_process:
        boa.env.set_balance(operator.address, 10**20)
        boa.env.eoa = operator.address
        return None
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20}))
    chain = w3.eth.chain_id
    if chain != CHAIN_ID:
        raise InputError(f"ERROR: RPC chain id is {chain}, expected {CHAIN_ID}")
    balance = int(w3.eth.get_balance(operator.address))
    if broadcast:
        boa.set_network_env(rpc)
        boa.env.add_account(operator, force_eoa=True)
        # A failed debug_traceTransaction (flaky or load-balanced provider) must not abort a multi-tx
        # migration after a tx has already mined; receipt status is still checked.
        boa.env.suppress_debug_tt(True)
    else:
        # boa.fork defaults to the "safe" block: minutes behind head on Base Sepolia (genesis on a fresh anvil),
        # so a verify right after a broadcast would see no code at the new addresses.
        boa.fork(rpc, block_identifier="latest")
        boa.env.eoa = operator.address
        boa.env.set_balance(operator.address, 10**20)
    return balance


def pending_operator_txs(rpc: str, op: str, w3=None) -> int:
    """eth_getTransactionCount(op, 'pending') - (op, 'latest'). boa's NetworkEnv takes nonces from 'latest'
    and predicts CREATE addresses locally, so a pending operator tx (oracle job, API) makes a broadcast
    replace it or fail part-way with an address mismatch."""
    if w3 is None:
        from web3 import Web3

        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20}))
    pending = int(w3.eth.get_transaction_count(op, "pending"))
    latest = int(w3.eth.get_transaction_count(op, "latest"))
    return max(0, pending - latest)


def head_block(rpc: str, in_process: bool) -> int:
    """Chain head before the first deploy tx; deployBlock = head + 1 feeds INDEXER_START_BLOCK."""
    if in_process:
        return int(boa.env.evm.patch.block_number)
    from web3 import Web3

    return int(Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20})).eth.block_number)


@lru_cache(maxsize=None)
def _deployer(name: str):
    return boa.load_partial(str(base.SRC / f"{name}.vy"))


def _at(name: str, addr: str):
    # Sources are v2 while the chain may still hold v1 (or a wrong address in a failing check):
    # the bytecode mismatch warning is expected; the ABI getters are shared.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="casted bytecode does not match")
        return _deployer(name).at(addr)


def _jsonable(value):
    if hasattr(value, "address"):
        return str(value.address)
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return str(value)


def _same(a, b) -> bool:
    return str(a).lower() == str(b).lower()


def _code_rows(core: dict[str, str]) -> list[tuple[str, str, str, bool]]:
    rows = []
    for name, addr in core.items():
        has_code = len(boa.env.get_code(addr)) > 0
        rows.append((f"{name} has code", "yes", "yes" if has_code else "no", has_code))
    return rows


def _info(label: str, fn) -> tuple[str, str]:
    """Optional v2 getters: report the value, or n/a on a v1 contract."""
    try:
        return label, str(fn())
    except Exception:
        return label, "n/a"


def run_core(accounts, treasury: str, chain: int) -> dict:
    op = accounts["OPERATOR_PRIVATE_KEY"].address
    agents = [accounts[v].address for v in AGENT_VARS]
    contracts = base.deploy(op, treasury, op, agents, chain=chain)
    payload = {name: _jsonable(c) for name, c in contracts.items()}
    payload.update({"operator": op, "treasury": treasury, "wildcardGenerator": op, "agents": agents})
    return payload


def run_verify(accounts, core: dict[str, str]):
    rows = _code_rows(core)
    if not all(r[3] for r in rows):
        return rows, []
    op = accounts["OPERATOR_PRIVATE_KEY"].address
    factory = _at("MarketFactory", core["MarketFactory"])
    oracle = _at("ConsensusOracle", core["ConsensusOracle"])
    amm = _at("MarketAMM", core["MarketAMM"])

    def row(label, expected, actual):
        rows.append((label, str(expected), str(actual), _same(expected, actual)))

    row("factory.oracle()", core["ConsensusOracle"], factory.oracle())
    row("factory.amm()", core["MarketAMM"], factory.amm())
    row("factory.ctf()", core["ConditionalTokens"], factory.ctf())
    row("factory.usdc()", core["MockUSDC"], factory.usdc())
    row("factory.operator()", op, factory.operator())
    row("oracle.operator()", op, oracle.operator())
    row("oracle.factory()", core["MarketFactory"], oracle.factory())
    row("oracle.ctf()", core["ConditionalTokens"], oracle.ctf())
    for i, var in enumerate(AGENT_VARS):
        row(f"oracle.agents({i})", accounts[var].address, oracle.agents(i))
    row("amm.factory()", core["MarketFactory"], amm.factory())
    row("amm.operator()", op, amm.operator())
    info = [
        _info("amm.closeGate()", lambda: amm.closeGate()),
        _info("factory.permissionless()", lambda: factory.permissionless()),
        _info("factory.minSeedUsdc()", lambda: factory.minSeedUsdc()),
        _info("factory.listingCooldown()", lambda: factory.listingCooldown()),
    ]
    return rows, info


def _answers(fn) -> bool:
    """True when a v2-only getter answers (so the contract is already v2); v1 has no such selector and reverts.
    Only a genuine revert reads as v1: RPC/transport errors propagate and main() fails closed (nothing sent)."""
    return base.answers(fn)


def preflight_v2(op: str, core: dict[str, str], allow_v2_source: bool = False) -> list[tuple[str, str, str, bool]]:
    """Read-only guards before any v2 transaction: the repo vars must describe the live, unmigrated stack.

    After a migration the operator updates AMM_ADDRESS / FACTORY_ADDRESS to the v2 pair; the oracle then
    points at that factory, so the "not yet migrated" row alone would pass and a re-run would silently
    migrate v2 -> v2 (stranding user-listed pools on the old v2 AMM). The v1 rows refuse that unless
    allow_v2_source (tests only)."""
    rows = _code_rows(core)
    if not all(r[3] for r in rows):
        return rows
    factory = _at("MarketFactory", core["MarketFactory"])
    oracle = _at("ConsensusOracle", core["ConsensusOracle"])
    amm = _at("MarketAMM", core["MarketAMM"])

    def row(label, expected, actual):
        rows.append((label, str(expected), str(actual), _same(expected, actual)))

    if not allow_v2_source:
        row("FACTORY_ADDRESS is v1 (no minSeedUsdc(); v2 not yet deployed)", "v1", "v2" if _answers(factory.minSeedUsdc) else "v1")
        row("AMM_ADDRESS is v1 (no closeGate(); v2 not yet deployed)", "v1", "v2" if _answers(amm.closeGate) else "v1")

    row("factory.operator() == operator key", op, factory.operator())
    row("oracle.operator() == operator key", op, oracle.operator())
    row("oracle.factory() == FACTORY_ADDRESS (not yet migrated)", core["MarketFactory"], oracle.factory())
    row("factory.oracle() == ORACLE_ADDRESS", core["ConsensusOracle"], factory.oracle())
    row("factory.amm() == AMM_ADDRESS", core["MarketAMM"], factory.amm())
    row("amm.factory() == FACTORY_ADDRESS", core["MarketFactory"], amm.factory())
    return rows


def filter_legacy(factory_addr: str, cids: list[str]) -> tuple[list[str], list[str]]:
    """Split cids into ones the legacy factory knows (importable) and ones it does not (skipped)."""
    factory = _at("MarketFactory", factory_addr)
    known, skipped = [], []
    for cid in cids:
        (known if factory.marketExists(bytes.fromhex(cid[2:])) else skipped).append(cid)
    return known, skipped


def v2_deployments(core: dict[str, str], op: str, chain: int) -> dict:
    """The deployments dict migrate_v2 starts from: repo-var addresses plus on-chain roles."""
    factory = _at("MarketFactory", core["MarketFactory"])
    oracle = _at("ConsensusOracle", core["ConsensusOracle"])
    out = dict(core)
    out.update(
        {
            "chainId": chain,
            "operator": op,
            "wildcardGenerator": str(factory.wildcardGenerator()),
            "agents": [str(oracle.agents(i)) for i in range(3)],
        }
    )
    return out


class MigrationError(Exception):
    """migrate_v2 raised; `progress` holds what already landed on chain (addresses, completed steps)."""

    def __init__(self, progress: dict, exc: BaseException):
        super().__init__(str(exc))
        self.progress = progress
        self.exc = exc


def partial_v2_payload(deployments: dict, progress: dict) -> dict:
    """Deployments JSON for a migration that failed part-way: new addresses only if they were deployed."""
    payload = dict(deployments)
    payload["MarketAMMLegacy"] = deployments["MarketAMM"]
    payload["MarketFactoryLegacy"] = deployments["MarketFactory"]
    for name in ("MarketAMM", "MarketFactory", "deployBlock"):
        payload.pop(name, None)
        if progress.get(name) is not None:
            payload[name] = _jsonable(progress[name])
    if progress.get("orphans"):
        # Mined at an unexpected address (shared operator nonce): on chain but never wired.
        payload["orphans"] = {str(k): _jsonable(v) for k, v in progress["orphans"].items()}
    payload["completedSteps"] = list(progress.get("steps") or [])
    payload["migrationFailed"] = True
    return payload


def run_v2(
    deployments: dict,
    op: str,
    cids: list[str],
    listing: dict,
    permissionless: bool,
    close_gate: bool,
    allow_v2_source: bool = False,
):
    """Returns (payload, missing result keys). Never raises after migrate_v2 returns, so the
    addresses of a broadcast migration always reach the JSON and the step summary. If migrate_v2
    raises, MigrationError carries the partial progress (addresses already deployed, steps done)."""
    try:
        import deploy_v2
    except ImportError as exc:
        raise InputError(f"ERROR: contracts/script/deploy_v2.py is not importable ({exc})") from exc
    kwargs = dict(
        deployments=dict(deployments),
        operator=op,
        legacy_cids=list(cids),
        listing=dict(listing),
        permissionless=permissionless,
        close_gate=close_gate,
    )
    progress: dict = {}
    try:
        params = inspect.signature(deploy_v2.migrate_v2).parameters
    except (TypeError, ValueError):
        params = {}
    if "progress" in params:
        kwargs["progress"] = progress
    if "allow_v2_source" in params:
        kwargs["allow_v2_source"] = bool(allow_v2_source)
    try:
        result = deploy_v2.migrate_v2(**kwargs)
    except Exception as exc:
        raise MigrationError(progress, exc) from exc
    result = dict(result or {})
    payload = dict(deployments)
    payload["MarketAMMLegacy"] = deployments["MarketAMM"]
    payload["MarketFactoryLegacy"] = deployments["MarketFactory"]
    for name in ("MarketAMM", "MarketFactory"):
        if name not in result:
            payload.pop(name)  # never report a v1 address as the new one
    payload.update({str(k): _jsonable(v) for k, v in result.items()})
    return payload, [k for k in V2_RESULT_KEYS if k not in result]


def _gh_lines(payload: dict, names, repo: str) -> list[str]:
    return [f"gh variable set {REPO_VARS[k]} --body {payload[k]} -R {repo}" for k in names if k in payload]


def _mobile_sync_lines(target: str, chain: int, repo: str, run_id: str) -> list[str]:
    """Download this run's deployments artifact and regenerate the mobile asset from it.

    The artifact name mirrors deploy-contracts.yml's upload step (a test pins the two together)."""
    run = run_id or "<run-id>"
    folder = f"/tmp/ou-{chain}-{target}"
    return [
        f"gh run download {run} -n contracts-{chain}-{target}-broadcast-{run} -D {folder} -R {repo}",
        f"python scripts/sync_mobile_deployments.py {chain} --source {folder}/{chain}.json",
    ]


def render_summary(
    target: str,
    broadcast: bool,
    payload: dict | None,
    rows=None,
    balance_wei: int | None = None,
    *,
    info=None,
    skipped=None,
    repo: str = DEFAULT_REPO,
    warnings=None,
    run_id: str = "",
) -> str:
    mode = "broadcast" if broadcast else "simulation (nothing sent)"
    lines = [f"### Contracts: {target} ({mode})", ""]
    if balance_wei is not None:
        lines += [f"Operator balance: {balance_wei / 10**18:.6f} ETH", ""]
    for w in warnings or []:
        lines += [f"> **Warning:** {w}", ""]
    if rows is not None:
        lines += ["| check | expected | actual | ok |", "|---|---|---|---|"]
        lines += [f"| {label} | `{exp}` | `{act}` | {'yes' if ok else '**NO**'} |" for label, exp, act, ok in rows]
        lines += [""]
    if info:
        lines += [f"- {label}: `{value}`" for label, value in info] + [""]
    if payload:
        lines += ["| contract | address |", "|---|---|"]
        lines += [f"| {k} | `{v}` |" for k, v in payload.items() if isinstance(v, (str, int)) and not isinstance(v, bool)]
        lines += [""]
        imported = payload.get("imported")
        if imported is not None:
            count = len(imported) if isinstance(imported, list) else imported
            lines += [f"Legacy markets imported: {count}", ""]
        if skipped:
            lines += [f"Skipped {len(skipped)} cid(s) the legacy factory does not know:", ""]
            lines += [f"- `{cid}`" for cid in skipped] + [""]
        if payload.get("migrationFailed"):
            done = payload.get("completedSteps") or []
            orphans = payload.get("orphans") or {}
            lines += [
                f"**Migration FAILED part-way.** Completed on chain: {', '.join(done) if done else 'nothing'}.",
                "",
            ]
            if orphans:
                lines += ["Deployed but never wired (mined at an unexpected address, usually a concurrent operator tx):", ""]
                lines += [f"- {name}: `{addr}`" for name, addr in orphans.items()] + [""]
            lines += [
                "Do not update repo variables and do not re-run v2 blindly: if `oracle.setFactory` is listed, "
                "the oracle already points at the new factory (legacy creates stop) and a re-run fails preflight; "
                "finish the remaining steps by hand as the operator using the addresses above.",
                "",
            ]
        elif broadcast:
            names = V2_VARS if target == "v2" else list(REPO_VARS)
            lines += ["Update repo variables (GITHUB_TOKEN cannot write them), then redeploy the app:", "```"]
            lines += _gh_lines(payload, names, repo)
            lines += [f"gh workflow run deploy-gcp.yml --ref main -R {repo}", "```", ""]
            chain = payload.get("chainId") or CHAIN_ID
            if target == "v2":
                lines += [
                    "Also allowlist the new MarketAMM / MarketFactory (and `createPermissionlessMarket`) in the CDP Portal "
                    f"paymaster policy. Then regenerate the mobile asset from this run's uploaded `{chain}.json` "
                    "(from the repo root):",
                    "```",
                    *_mobile_sync_lines(target, chain, repo, run_id),
                    "```",
                    "",
                    f"The sync script rewrites `mobile/assets/deployments/{chain}.json` with every contract address from "
                    "the upload and carries `treasury` over from the current asset. Merge `MarketAMM`, `MarketFactory`, "
                    "`MarketAMMLegacy`, `MarketFactoryLegacy` and `deployBlock` from the same file into "
                    f"`contracts/deployments/{chain}.json` by hand: the upload omits `SimpleAccount`, `treasury` and "
                    "`canonicalUSDC`, so do not overwrite that file wholesale. "
                    f"`python scripts/sync_mobile_deployments.py {chain} --check` then confirms the two files agree.",
                    "",
                ]
            elif target == "core":
                lines += [
                    f"Then regenerate the mobile asset from this run's uploaded `{chain}.json` (from the repo root) and "
                    f"merge the same file into `contracts/deployments/{chain}.json`:",
                    "```",
                    *_mobile_sync_lines(target, chain, repo, run_id),
                    "```",
                    "",
                ]
        else:
            lines += ["Simulated on a fork: addresses above are indicative only; re-run with broadcast to deploy.", ""]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, env: dict[str, str] | None = None, http_get=None) -> int:
    env = dict(os.environ if env is None else env)
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=("verify", "v2", "core"), required=True)
    ap.add_argument("--broadcast", action="store_true")
    ap.add_argument("--in-process", action="store_true", help="tests only: local boa EVM, chain 31337 contracts")
    ap.add_argument("--out", required=True)
    ap.add_argument("--summary", default="")
    ap.add_argument("--api-url", default="", help="v2: list legacy cids from GET {api}/api/v1/markets")
    ap.add_argument("--legacy-cids", default="", help="v2: extra legacy cids (comma/space separated), e.g. paused markets")
    ap.add_argument("--min-seed-usdc", type=int, default=10_000_000, help="v2: listing minimum seed (6-decimal USDC units)")
    ap.add_argument(
        "--listing-cooldown",
        type=int,
        default=LISTING_DEFAULTS["listingCooldown"],
        help="v2: seconds between user listings per creator (0 disables the rate limit)",
    )
    ap.add_argument("--permissionless", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--close-gate", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument(
        "--allow-v2-source",
        action="store_true",
        help="v2: allow migrating a stack whose factory/AMM are already v2 (tests only; never on Base Sepolia)",
    )
    args = ap.parse_args(argv)
    rpc = (env.get("DEPLOY_RPC_URL") or "").strip()
    repo = (env.get("GITHUB_REPOSITORY") or "").strip() or DEFAULT_REPO
    prev_eoa = boa.env.eoa if args.in_process else None
    try:
        accounts = load_accounts(env, TARGET_KEYS[args.target])
        if not args.in_process and not rpc.startswith(("http://", "https://")):
            raise InputError("ERROR: DEPLOY_RPC_URL is missing or not http(s) (value not shown)")
        if args.min_seed_usdc <= 0:
            raise InputError("ERROR: --min-seed-usdc must be positive")
        if args.listing_cooldown < 0:
            raise InputError("ERROR: --listing-cooldown must be >= 0")
        operator = accounts["OPERATOR_PRIVATE_KEY"]
        op = operator.address
        chain = LOCAL_CHAIN_ID if args.in_process else CHAIN_ID
        print(f"operator {op}; target {args.target}; {'broadcast' if args.broadcast else 'simulate'}")

        # Inputs that need no chain access are resolved before connecting.
        cids: list[str] = []
        if args.target == "v2":
            if args.api_url:
                cids = fetch_market_cids(args.api_url, http_get)
            cids += [c for c in parse_cids(args.legacy_cids) if c not in cids]
            print(f"legacy cids requested: {len(cids)}")
        core = existing_addresses(env, CORE_NAMES, OPTIONAL_NAMES) if args.target in ("verify", "v2") else {}
        treasury = ""
        if args.target == "core":
            treasury = (env.get("TREASURY_ADDRESS") or "").strip()
            if not ADDR_RE.fullmatch(treasury):
                raise InputError("ERROR: TREASURY_ADDRESS repo variable is required for core")

        balance = connect(rpc, args.broadcast, args.in_process, operator)
        warnings: list[str] = []
        need = MIN_BALANCE_WEI.get(args.target)
        if need is not None and balance is not None and balance < need:
            msg = f"operator balance {balance / 10**18:.6f} ETH is below {need / 10**18} ETH"
            if args.broadcast:
                raise InputError(f"ERROR: {msg}. Fund {op} on Base Sepolia first.")
            warnings.append(msg + "; fund it before broadcasting.")
        if args.broadcast and not args.in_process and args.target in ("v2", "core"):
            gap = pending_operator_txs(rpc, op)
            if gap:
                raise InputError(
                    f"ERROR: operator {op} has {gap} pending transaction(s). The oracle job and the API sign with "
                    "the same key; pause overunder-oracle-tick, wait for them to mine, then re-run (nothing sent)."
                )
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)

        rows, info, payload, skipped = None, None, None, None
        failed = False
        if args.target == "verify":
            rows, info = run_verify(accounts, {k: core[k] for k in CORE_NAMES})
        elif args.target == "core":
            start = head_block(rpc, args.in_process) + 1
            payload = run_core(accounts, treasury, chain)
            payload["deployBlock"] = start
        else:
            rows = preflight_v2(op, {k: core[k] for k in CORE_NAMES}, allow_v2_source=args.allow_v2_source)
            if all(r[3] for r in rows):
                known, skipped = filter_legacy(core["MarketFactory"], cids)
                listing = dict(
                    LISTING_DEFAULTS,
                    minSeedUsdc=args.min_seed_usdc,
                    listingCooldown=args.listing_cooldown,
                    feeRecipient=core["FeeVault"],
                )
                start = head_block(rpc, args.in_process) + 1
                deployments = v2_deployments(core, op, chain)
                try:
                    payload, missing = run_v2(
                        deployments, op, known, listing, args.permissionless, args.close_gate, args.allow_v2_source
                    )
                except MigrationError as err:
                    failed = True
                    payload, missing = partial_v2_payload(deployments, err.progress), []
                    warnings.append(
                        f"migrate_v2 failed: {type(err.exc).__name__}: {redact(str(err.exc), rpc)[:300]}"
                    )
                if missing:
                    warnings.append(f"migrate_v2 did not return {', '.join(missing)}; check the addresses below by hand.")
                if not failed:
                    payload.setdefault("deployBlock", start)
                payload.update({"listing": listing, "permissionless": args.permissionless, "closeGate": args.close_gate})
        if payload is not None:
            payload["chainId"] = chain
            payload["simulated"] = not args.broadcast
            (out_dir / f"{chain}.json").write_text(json.dumps(payload, indent=2))
        if rows is not None:
            (out_dir / f"{args.target}-checks.json").write_text(
                json.dumps([dict(zip(("check", "expected", "actual", "ok"), r)) for r in rows], indent=2)
            )
        summary = render_summary(
            args.target,
            args.broadcast,
            payload,
            rows,
            balance,
            info=info,
            skipped=skipped,
            repo=repo,
            warnings=warnings,
            run_id=(env.get("GITHUB_RUN_ID") or "").strip(),
        )
        print(summary)
        if args.summary:
            with open(args.summary, "a", encoding="utf-8") as fh:
                fh.write(summary)
        if failed:
            return 1
        return 1 if rows is not None and not all(r[3] for r in rows) else 0
    except InputError as exc:
        print(redact(str(exc), rpc), file=sys.stderr)
        return 2
    except Exception as exc:  # never let a traceback echo the RPC URL
        print(f"ERROR: {type(exc).__name__}: {redact(str(exc), rpc)[:500]}", file=sys.stderr)
        return 1
    finally:
        if args.in_process:
            boa.env.eoa = prev_eoa


if __name__ == "__main__":
    raise SystemExit(main())
