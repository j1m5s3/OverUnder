"""Tests for scripts/sync_mobile_deployments.py plus the real-tree parity check."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import sync_mobile_deployments as sync  # noqa: E402

CHAIN = 84532


def addr(n: int) -> str:
    return "0x" + f"{n:040x}"


def contracts_file(**overrides) -> dict:
    data = {
        "chainId": CHAIN,
        "MockUSDC": addr(1),
        "RevenueToken": addr(2),
        "ConditionalTokens": addr(3),
        "FeeVault": addr(4),
        "ConsensusOracle": addr(5),
        "Exchange": addr(6),
        "MarketAMM": addr(7),
        "MarketFactory": addr(8),
        "OverUnderPaymaster": addr(9),
        "EntryPoint": "0x0000000071727De22E5E9d8BAf0edAc6f37da032",
        "SimpleAccountFactory": addr(10),
        "SimpleAccount": addr(11),
        "canonicalUSDC": addr(12),
        "operator": addr(13),
        "treasury": addr(14),
        "wildcardGenerator": addr(15),
        "agents": [addr(16), addr(17), addr(18)],
    }
    data.update(overrides)
    return {k: v for k, v in data.items() if v is not None}


def stale_mobile() -> dict:
    """The shape of the pre-CDP asset: other stack, MockEntryPoint, no SimpleAccountFactory."""
    return {
        "chainId": CHAIN,
        "MockUSDC": addr(101),
        "ConditionalTokens": addr(103),
        "ConsensusOracle": addr(105),
        "MarketAMM": addr(107),
        "MarketFactory": addr(108),
        "MockEntryPoint": addr(119),
        "operator": addr(13),
        "treasury": addr(14),
    }


class BuildAssetTests(unittest.TestCase):
    def test_copies_every_address_and_drops_stale_keys(self):
        asset = sync.build_asset(contracts_file(), CHAIN, stale_mobile())
        self.assertEqual(asset["chainId"], CHAIN)
        for key in ("MockUSDC", "ConditionalTokens", "ConsensusOracle", "MarketAMM", "MarketFactory",
                    "FeeVault", "Exchange", "RevenueToken", "OverUnderPaymaster", "SimpleAccountFactory"):
            self.assertEqual(asset[key], contracts_file()[key], key)
        self.assertEqual(asset["EntryPoint"], "0x0000000071727De22E5E9d8BAf0edAc6f37da032")
        self.assertNotIn("MockEntryPoint", asset)
        self.assertNotIn("SimpleAccount", asset)
        self.assertNotIn("canonicalUSDC", asset)
        self.assertEqual(asset["agents"], contracts_file()["agents"])
        self.assertEqual(sync.diff(asset, contracts_file()), [])

    def test_meta_keys_fall_back_to_existing_asset(self):
        # The deploy-contracts.yml upload omits treasury (and SimpleAccount / canonicalUSDC).
        upload = contracts_file(treasury=None, agents=None)
        asset = sync.build_asset(upload, CHAIN, {**stale_mobile(), "agents": ["0xkeep"]})
        self.assertEqual(asset["treasury"], addr(14))
        self.assertEqual(asset["agents"], ["0xkeep"])
        self.assertEqual(asset["MarketAMM"], addr(7))

    def test_mock_entrypoint_source_maps_to_entrypoint(self):
        local = contracts_file(EntryPoint=None, MockEntryPoint=addr(20))
        asset = sync.build_asset(local, CHAIN)
        self.assertEqual(asset["EntryPoint"], addr(20))
        self.assertNotIn("MockEntryPoint", asset)

    def test_rejects_missing_trade_targets(self):
        with self.assertRaisesRegex(sync.SyncError, "MarketAMM"):
            sync.build_asset(contracts_file(MarketAMM=None), CHAIN)

    def test_rejects_other_chain_and_bad_address(self):
        with self.assertRaisesRegex(sync.SyncError, "chainId"):
            sync.build_asset(contracts_file(chainId=31337), CHAIN)
        with self.assertRaisesRegex(sync.SyncError, "MockUSDC"):
            sync.build_asset(contracts_file(MockUSDC="0x1234"), CHAIN)


class DiffTests(unittest.TestCase):
    def test_reports_each_stale_or_missing_address(self):
        drift = {key for key, _, _ in sync.diff(stale_mobile(), contracts_file())}
        self.assertTrue(set(sync.TRADE_KEYS) <= drift)
        self.assertIn("ConsensusOracle", drift)
        self.assertIn("SimpleAccountFactory", drift)  # missing in the stale asset
        self.assertIn("EntryPoint", drift)  # MockEntryPoint differs from the canonical one

    def test_case_insensitive_and_trade_key_subset(self):
        mobile = {k: v.upper().replace("0X", "0x") if isinstance(v, str) else v for k, v in contracts_file().items()}
        self.assertEqual(sync.diff(mobile, contracts_file()), [])
        mobile["FeeVault"] = addr(99)
        self.assertEqual(sync.diff(mobile, contracts_file(), sync.TRADE_KEYS), [])
        self.assertEqual([k for k, _, _ in sync.diff(mobile, contracts_file())], ["FeeVault"])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = sync.main(list(argv), root=self.root, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def test_check_fails_on_drift_then_write_fixes_it(self):
        self.write(sync.contracts_path(CHAIN, self.root), contracts_file())
        self.write(sync.mobile_path(CHAIN, self.root), stale_mobile())

        code, out, _ = self.run_main(str(CHAIN), "--check")
        self.assertEqual(code, 1)
        self.assertIn("MarketAMM: " + addr(107) + " -> " + addr(7), out)
        self.assertIn("sync_mobile_deployments.py 84532", out)

        code, out, _ = self.run_main(str(CHAIN))
        self.assertEqual(code, 0, out)
        written = json.loads(sync.mobile_path(CHAIN, self.root).read_text(encoding="utf-8"))
        self.assertEqual(written["MarketAMM"], addr(7))
        self.assertNotIn("MockEntryPoint", written)

        code, out, _ = self.run_main(str(CHAIN), "--check")
        self.assertEqual(code, 0, out)

    def test_source_flag_reads_an_upload(self):
        upload = self.root / "artifact" / "84532.json"
        self.write(upload, contracts_file(MarketAMM=addr(70), treasury=None))
        self.write(sync.mobile_path(CHAIN, self.root), stale_mobile())
        code, _, err = self.run_main(str(CHAIN), "--source", str(upload))
        self.assertEqual(code, 0, err)
        written = json.loads(sync.mobile_path(CHAIN, self.root).read_text(encoding="utf-8"))
        self.assertEqual(written["MarketAMM"], addr(70))
        self.assertEqual(written["treasury"], addr(14))

    def test_missing_source_is_usage_error_and_writes_nothing(self):
        code, _, err = self.run_main(str(CHAIN))
        self.assertEqual(code, 2)
        self.assertIn("missing source", err)
        self.assertFalse(sync.mobile_path(CHAIN, self.root).exists())

    def test_incomplete_source_leaves_asset_untouched(self):
        self.write(sync.contracts_path(CHAIN, self.root), contracts_file(MockUSDC=None))
        self.write(sync.mobile_path(CHAIN, self.root), stale_mobile())
        code, _, err = self.run_main(str(CHAIN))
        self.assertEqual(code, 2)
        self.assertIn("MockUSDC", err)
        self.assertEqual(json.loads(sync.mobile_path(CHAIN, self.root).read_text(encoding="utf-8")), stale_mobile())


    # deploy_ci.py uploads for a successful v2 broadcast, a simulation and a failed migration.
    def v2_upload(self, **overrides) -> dict:
        data = {
            "chainId": CHAIN,
            "MockUSDC": addr(1),
            "ConditionalTokens": addr(3),
            "FeeVault": addr(4),
            "ConsensusOracle": addr(5),
            "Exchange": addr(6),
            "MarketAMM": addr(70),
            "MarketFactory": addr(80),
            "MarketAMMLegacy": addr(7),
            "MarketFactoryLegacy": addr(8),
            "deployBlock": 123,
            "imported": ["0x" + "a" * 64],
            "permissionless": True,
            "closeGate": True,
            "simulated": False,
        }
        data.update(overrides)
        return {k: v for k, v in data.items() if v is not None}

    def assert_refused(self, source: dict, *needles: str) -> None:
        upload = self.root / "artifact" / "84532.json"
        self.write(upload, source)
        self.write(sync.mobile_path(CHAIN, self.root), stale_mobile())
        for extra in ((), ("--check",)):
            code, out, err = self.run_main(str(CHAIN), "--source", str(upload), *extra)
            self.assertEqual(code, 1, (extra, out, err))
            self.assertIn("refusing", err)
            self.assertIn("--force", err)
            self.assertIn("Nothing written", err)
            for needle in needles:
                self.assertIn(needle, err)
        self.assertEqual(json.loads(sync.mobile_path(CHAIN, self.root).read_text(encoding="utf-8")), stale_mobile())

    def test_successful_v2_broadcast_upload_is_written(self):
        upload = self.root / "artifact" / "84532.json"
        self.write(upload, self.v2_upload())
        self.write(sync.mobile_path(CHAIN, self.root), stale_mobile())
        code, _, err = self.run_main(str(CHAIN), "--source", str(upload))
        self.assertEqual(code, 0, err)
        written = json.loads(sync.mobile_path(CHAIN, self.root).read_text(encoding="utf-8"))
        self.assertEqual((written["MarketAMM"], written["MarketFactory"]), (addr(70), addr(80)))
        self.assertNotIn("MarketAMMLegacy", written)

    def test_refuses_simulation_markers(self):
        self.assert_refused(self.v2_upload(simulated=True), "simulated=True")
        self.assert_refused(self.v2_upload(simulated="true"), "simulated")
        self.assert_refused(self.v2_upload(dryRun=1), "dryRun")
        self.assert_refused(contracts_file(fork="https://fork.example"), "fork")
        # Core uploads carry `simulated` too.
        self.assert_refused(contracts_file(simulated=True, deployBlock=5), "simulated")

    def test_refuses_failed_or_partial_migration(self):
        failed = self.v2_upload(migrationFailed=True, completedSteps=["deploy MarketAMM"], MarketFactory=None, deployBlock=None)
        self.assert_refused(failed, "migrationFailed", "completedSteps", "lacks MarketFactory, deployBlock")
        self.assert_refused(self.v2_upload(orphans={"MarketFactory": addr(99)}), "orphans")

    def test_refuses_v2_payload_without_every_v2_key_or_with_legacy_as_new(self):
        self.assert_refused(self.v2_upload(deployBlock=None), "lacks deployBlock")
        self.assert_refused(self.v2_upload(MarketFactoryLegacy=None), "lacks MarketFactoryLegacy")
        self.assert_refused(self.v2_upload(MarketAMM=addr(7).upper().replace("0X", "0x")), "MarketAMM equals MarketAMMLegacy")

    def test_false_markers_and_pre_v2_files_are_accepted(self):
        # contracts/deployments files from deploy.py have no v2 or simulation keys at all.
        self.assertEqual(sync.unsafe_reasons(contracts_file()), [])
        self.assertEqual(sync.unsafe_reasons(self.v2_upload(simulated="false", migrationFailed=False, dryRun=0)), [])

    def test_force_writes_a_flagged_source_with_a_warning(self):
        upload = self.root / "artifact" / "84532.json"
        self.write(upload, self.v2_upload(simulated=True))
        self.write(sync.mobile_path(CHAIN, self.root), stale_mobile())
        code, _, err = self.run_main(str(CHAIN), "--source", str(upload), "--force")
        self.assertEqual(code, 0, err)
        self.assertIn("warning: --force", err)
        written = json.loads(sync.mobile_path(CHAIN, self.root).read_text(encoding="utf-8"))
        self.assertEqual(written["MarketAMM"], addr(70))
        # --force does not bypass the structural checks.
        self.write(upload, self.v2_upload(simulated=True, MockUSDC=None))
        code, _, err = self.run_main(str(CHAIN), "--source", str(upload), "--force")
        self.assertEqual(code, 2)
        self.assertIn("MockUSDC", err)


def _tracked(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path.relative_to(REPO))],
            cwd=REPO,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


class RealTreeParityTests(unittest.TestCase):
    """Bundled mobile asset vs the local contracts/deployments file.

    contracts/deployments/*.json is gitignored, so this skips on clean clones
    and CI. It enforces once the mobile asset is tracked or staged (so a stale
    asset cannot ship) or when OU_CHECK_DEPLOY_PARITY=1; an untracked local
    copy is only reported in the skip reason.
    """

    def test_mobile_asset_matches_contracts_deployments(self):
        # Base Sepolia only: local anvil addresses depend on the deployer's
        # nonce, and the app prefers the API's addresses there anyway.
        source_path = sync.contracts_path(CHAIN)
        mobile = sync.mobile_path(CHAIN)
        if not source_path.exists():
            self.skipTest(f"{source_path.relative_to(REPO)} absent (gitignored)")
        if not mobile.exists():
            self.skipTest(f"{mobile.relative_to(REPO)} absent (the app fails closed)")
        source = json.loads(source_path.read_text(encoding="utf-8"))
        asset = json.loads(mobile.read_text(encoding="utf-8"))
        drift = sync.diff(asset, source, sync.TRADE_KEYS)
        enforce = os.environ.get("OU_CHECK_DEPLOY_PARITY") == "1" or _tracked(mobile)
        if drift and not enforce:
            self.skipTest(
                f"untracked {mobile.relative_to(REPO)} is stale ({', '.join(k for k, _, _ in drift)}); "
                f"run scripts/sync_mobile_deployments.py {CHAIN} before committing it"
            )
        self.assertEqual(drift, [], f"run: python scripts/sync_mobile_deployments.py {CHAIN}")

if __name__ == "__main__":
    unittest.main()
