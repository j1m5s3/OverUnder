"""Regenerate the mobile app's bundled contract addresses.

mobile/assets/deployments/<chainId>.json is what AmmSwapWidget falls back to
when the API does not serve GET /api/v1/chain/addresses. The backend's
/aa/cdp-send only accepts calls to its configured USDC / CTF / AMM, so a stale
asset makes every mobile trade fail with 403. Run this after every contract
deploy (the v2 AMM + Factory redeploy moves MarketAMM and MarketFactory):

  python scripts/sync_mobile_deployments.py 84532            # write the asset
  python scripts/sync_mobile_deployments.py 84532 --check    # exit 1 on drift
  python scripts/sync_mobile_deployments.py 84532 --source path/to/84532.json

The default source is contracts/deployments/<chainId>.json (gitignored). Pass
--source for the deploy-contracts.yml broadcast upload. Every contract address
comes from the source, so stale keys (MockEntryPoint, an old USDC) are dropped;
operator, treasury, wildcardGenerator and agents fall back to the current asset
when the source omits them. Only public contract addresses are read and printed.

The script refuses (exit 1, nothing written) a source that is not a real
deployment: a simulation (`simulated`, `dryRun`, `fork` markers), a failed or
partial v2 migration (`migrationFailed`, `orphans`, `completedSteps`), or a v2
payload (it names MarketAMMLegacy / MarketFactoryLegacy) without every v2 key
or whose new MarketAMM / MarketFactory still equals the legacy address.
--force skips that check for a file you have verified by hand.

Exit codes: 0 ok / in sync, 1 drift (--check) or unsafe source refused,
2 usage or input error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The keys the app sends trades to; /aa/cdp-send allowlists the same set.
TRADE_KEYS = ("MockUSDC", "ConditionalTokens", "MarketAMM", "MarketFactory")
# Every contract address the asset carries (the deploy-gcp repo-var set plus
# EmissionsDistributor on local stacks), in output order.
ADDRESS_KEYS = (
    "MockUSDC",
    "RevenueToken",
    "ConditionalTokens",
    "FeeVault",
    "ConsensusOracle",
    "Exchange",
    "MarketAMM",
    "MarketFactory",
    "OverUnderPaymaster",
    "EntryPoint",
    "SimpleAccountFactory",
    "EmissionsDistributor",
)
META_KEYS = ("operator", "treasury", "wildcardGenerator", "agents")

_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")

# deploy_ci.py writes `simulated` into every upload; the others cover hand-made or older payloads.
SIMULATION_MARKERS = ("simulated", "dryRun", "dry_run", "fork", "forked")
# deploy_ci.partial_v2_payload marks a v2 migration that stopped part-way.
FAILURE_MARKERS = ("migrationFailed", "orphans", "completedSteps")
LEGACY_KEYS = ("MarketAMMLegacy", "MarketFactoryLegacy")
V2_KEYS = ("MarketAMM", "MarketFactory", "MarketAMMLegacy", "MarketFactoryLegacy", "deployBlock")


class SyncError(ValueError):
    """Input that cannot produce a correct asset (exit 2)."""


class UnsafeSource(ValueError):
    """A source that parses but is not a finished real deployment (exit 1 unless --force)."""


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def unsafe_reasons(source: dict) -> list[str]:
    """Why source must not become the mobile asset (empty when it looks like a finished deployment)."""
    reasons = []
    for key in SIMULATION_MARKERS:
        if _truthy(source.get(key)):
            reasons.append(f"{key}={source[key]!r} (a simulation on a fork, nothing was deployed)")
    for key in FAILURE_MARKERS:
        if key in source and (_truthy(source[key]) or key != "migrationFailed"):
            reasons.append(f"has {key} (a failed or partial v2 migration)")
    if any(key in source for key in LEGACY_KEYS):
        missing = [key for key in V2_KEYS if source.get(key) in (None, "")]
        if missing:
            reasons.append(f"v2 payload lacks {', '.join(missing)}")
        for new, old in (("MarketAMM", "MarketAMMLegacy"), ("MarketFactory", "MarketFactoryLegacy")):
            a, b = source.get(new), source.get(old)
            if isinstance(a, str) and isinstance(b, str) and a.lower() == b.lower():
                reasons.append(f"{new} equals {old} (the new contract was never deployed)")
    return reasons


def contracts_path(chain_id: int, root: Path = ROOT) -> Path:
    return root / "contracts" / "deployments" / f"{chain_id}.json"


def mobile_path(chain_id: int, root: Path = ROOT) -> Path:
    return root / "mobile" / "assets" / "deployments" / f"{chain_id}.json"


def _address(data: dict, key: str) -> str | None:
    value = data.get(key)
    if value is None and key == "EntryPoint":
        value = data.get("MockEntryPoint")
    if value is None:
        return None
    if not isinstance(value, str) or not _ADDR.match(value):
        raise SyncError(f"{key} is not an address: {value!r}")
    return value


def _check_chain(data: dict, chain_id: int, label: str) -> None:
    declared = data.get("chainId")
    if declared is not None and str(declared) != str(chain_id):
        raise SyncError(f"{label} declares chainId {declared}, expected {chain_id}")


def build_asset(source: dict, chain_id: int, existing: dict | None = None) -> dict:
    """The mobile asset for chain_id from a contracts deployments dict."""
    if not isinstance(source, dict):
        raise SyncError("source is not a JSON object")
    _check_chain(source, chain_id, "source")
    missing = [key for key in TRADE_KEYS if not source.get(key)]
    if missing:
        raise SyncError(f"source is missing trade targets: {', '.join(missing)}")
    out: dict = {"chainId": chain_id}
    for key in ADDRESS_KEYS:
        value = _address(source, key)
        if value:
            out[key] = value
    previous = existing if isinstance(existing, dict) else {}
    for key in META_KEYS:
        if key in source:
            out[key] = source[key]
        elif key in previous:
            out[key] = previous[key]
    return out


def diff(mobile: dict, source: dict, keys: tuple[str, ...] = ADDRESS_KEYS) -> list[tuple[str, str | None, str]]:
    """(key, mobile, source) for each address in source that mobile lacks or differs on."""
    out = []
    for key in keys:
        want = _address(source, key)
        if not want:
            continue
        have = mobile.get(key)
        if not isinstance(have, str) or have.lower() != want.lower():
            out.append((key, have if isinstance(have, str) else None, want))
    return out


def _read(path: Path, label: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SyncError(f"missing {label} {path}") from None
    except (OSError, ValueError) as exc:
        raise SyncError(f"cannot read {label} {path}: {exc}") from None
    if not isinstance(data, dict):
        raise SyncError(f"{label} {path} is not a JSON object")
    return data


def main(argv: list[str] | None = None, root: Path = ROOT, out=sys.stdout, err=sys.stderr) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("chain_id", type=int, nargs="?", default=84532)
    parser.add_argument("--source", type=Path, help="deployments JSON (default contracts/deployments/<chainId>.json)")
    parser.add_argument("--check", action="store_true", help="report drift and exit 1 instead of writing")
    parser.add_argument(
        "--force",
        action="store_true",
        help="accept a source flagged as simulated, failed or incomplete (only after checking it by hand)",
    )
    args = parser.parse_args(argv)

    source_path = args.source or contracts_path(args.chain_id, root)
    target = mobile_path(args.chain_id, root)
    try:
        source = _read(source_path, "source")
        _check_chain(source, args.chain_id, "source")
        reasons = unsafe_reasons(source)
        if reasons and not args.force:
            raise UnsafeSource("; ".join(reasons))
        if reasons:
            print(f"warning: --force: using {source_path.name} despite: {'; '.join(reasons)}", file=err)
        if args.check:
            mobile = _read(target, "mobile asset")
            _check_chain(mobile, args.chain_id, "mobile asset")
            drift = diff(mobile, source)
            if not drift:
                print(f"{target.relative_to(root)} matches {source_path.name}", file=out)
                return 0
            print(f"{target.relative_to(root)} is stale:", file=out)
            for key, have, want in drift:
                print(f"  {key}: {have or '(missing)'} -> {want}", file=out)
            print(f"regenerate: python scripts/sync_mobile_deployments.py {args.chain_id}", file=out)
            return 1
        existing = _read(target, "mobile asset") if target.exists() else None
        asset = build_asset(source, args.chain_id, existing)
    except UnsafeSource as exc:
        print(
            f"error: refusing {source_path}: {exc}. Use the contracts-<chain>-<target>-broadcast-<run> artifact of a "
            "successful deploy-contracts run, or pass --force after checking every address on chain. Nothing written.",
            file=err,
        )
        return 1
    except SyncError as exc:
        print(f"error: {exc}", file=err)
        return 2
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asset, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target.relative_to(root)} from {source_path.name}", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
