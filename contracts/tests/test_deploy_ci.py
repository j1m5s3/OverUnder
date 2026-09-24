import json
import sys
import types
from pathlib import Path

import boa
import pytest
from eth_account import Account

SCRIPT = Path(__file__).resolve().parents[1] / "script"
sys.path.insert(0, str(SCRIPT))
import deploy_ci  # noqa: E402

API = "https://api.example.test"


def _keys():
    return {var: Account.create().key.hex() for var in deploy_ci.KEY_VARS}


def _raw(key: str) -> str:
    return key[2:] if key.startswith("0x") else key


def _assert_no_keys(out, env):
    for var in deploy_ci.KEY_VARS:
        if env.get(var):
            assert _raw(env[var]) not in out.out and _raw(env[var]) not in out.err


def _core_env(tmp_path):
    """Deploy a full stack in-process and return env with its addresses as repo variables."""
    env = _keys()
    env["TREASURY_ADDRESS"] = Account.create().address
    core_dir = tmp_path / "core"
    assert deploy_ci.main(["--target", "core", "--in-process", "--out", str(core_dir)], env=env) == 0
    payload = json.loads((core_dir / "31337.json").read_text())
    for name, var in deploy_ci.REPO_VARS.items():
        if name in payload and name != "deployBlock":
            env[var] = payload[name]
    return env, payload


def _legacy_market(env, payload) -> str:
    """Create one seeded primary on the (legacy) factory; returns its cid as 0x-hex."""
    op = Account.from_key(env["OPERATOR_PRIVATE_KEY"]).address
    usdc = deploy_ci._at("MockUSDC", payload["MockUSDC"])
    factory = deploy_ci._at("MarketFactory", payload["MarketFactory"])
    seed = 200_000_000
    close = int(boa.env.evm.patch.timestamp) + 3 * 86400
    with boa.env.prank(op):
        usdc.mint(op, seed)
        usdc.approve(payload["MarketFactory"], seed)
        usdc.approve(payload["MarketAMM"], seed)
        cid = factory.createPrimaryMarket(b"\x11" * 32, close, "Legacy market?", seed)
    return "0x" + cid.hex()


def test_rejects_malformed_key_without_echoing_it(capsys, tmp_path):
    env = _keys()
    env["AGENT_BETA_KEY"] = "0x" + "ab" * 31
    rc = deploy_ci.main(["--target", "verify", "--in-process", "--out", str(tmp_path)], env=env)
    err = capsys.readouterr().err
    assert rc == 2
    assert "AGENT_BETA_KEY" in err and "ab" * 31 not in err


def test_v2_needs_only_the_operator_key(capsys, tmp_path):
    env = {"OPERATOR_PRIVATE_KEY": Account.create().key.hex()}
    assert deploy_ci.main(["--target", "v2", "--out", str(tmp_path)], env=env) == 2
    assert "DEPLOY_RPC_URL" in capsys.readouterr().err  # got past key validation
    assert deploy_ci.main(["--target", "verify", "--out", str(tmp_path)], env=env) == 2
    assert "AGENT_ALPHA_KEY" in capsys.readouterr().err


def test_requires_rpc_unless_in_process(capsys, tmp_path):
    rc = deploy_ci.main(["--target", "verify", "--out", str(tmp_path)], env=_keys())
    assert rc == 2
    assert "DEPLOY_RPC_URL" in capsys.readouterr().err


def test_rejects_non_http_rpc_without_echoing_it(capsys, tmp_path):
    env = _keys()
    env["DEPLOY_RPC_URL"] = "wss://base-sepolia.example/SECRETKEY"
    assert deploy_ci.main(["--target", "verify", "--out", str(tmp_path)], env=env) == 2
    err = capsys.readouterr().err
    assert "DEPLOY_RPC_URL" in err and "SECRETKEY" not in err


def test_redact_hides_rpc_host_path_and_query():
    rpc = "https://acct-name.base-sepolia.example/v2/SECRETKEY?token=abc"
    msg = (
        "HTTPSConnectionPool(host='acct-name.base-sepolia.example', port=443): "
        f"Max retries exceeded with url: /v2/SECRETKEY?token=abc ({rpc})"
    )
    out = deploy_ci.redact(msg, rpc)
    assert "SECRETKEY" not in out and "token=abc" not in out and "acct-name" not in out


def test_parse_cids_normalises_and_rejects_garbage():
    a, b = "0x" + "AB" * 32, "0x" + "cd" * 32
    assert deploy_ci.parse_cids(f"{a}, {b}\n{a.lower()}") == [a.lower(), b]
    assert deploy_ci.parse_cids("") == []
    with pytest.raises(deploy_ci.InputError):
        deploy_ci.parse_cids("0x1234")


def test_fetch_market_cids_flattens_primaries_and_children():
    p1, c1, p2 = ("0x" + h * 64 for h in "abc")
    calls = []

    def fake_get(url):
        calls.append(url)
        return [
            {"primary": {"conditionId": p1}, "children": [{"conditionId": c1}, {"conditionId": p1}]},
            {"primary": {"conditionId": p2.upper().replace("0X", "0x")}, "children": []},
            {"primary": {"conditionId": "not-a-cid"}, "children": []},
        ]

    assert deploy_ci.fetch_market_cids(API + "/", fake_get) == [p1, c1, p2]
    assert calls == [API + "/api/v1/markets"]


def test_fetch_market_cids_reports_http_errors():
    def boom(url):
        raise OSError("connection refused")

    with pytest.raises(deploy_ci.InputError, match="could not list markets"):
        deploy_ci.fetch_market_cids(API, boom)
    with pytest.raises(deploy_ci.InputError, match="http"):
        deploy_ci.fetch_market_cids("ftp://api.example", boom)


def test_summary_prints_only_v2_repo_vars():
    payload = {
        "MockUSDC": "0x" + "1" * 40,
        "MarketAMM": "0x" + "2" * 40,
        "MarketFactory": "0x" + "3" * 40,
        "MarketAMMLegacy": "0x" + "4" * 40,
        "MarketFactoryLegacy": "0x" + "5" * 40,
        "imported": ["0x" + "a" * 64],
        "deployBlock": 123,
    }
    text = deploy_ci.render_summary("v2", True, payload, [], None, skipped=["0x" + "b" * 64], repo="me/repo")
    gh = [line for line in text.splitlines() if line.startswith("gh variable set")]
    assert gh == [
        f"gh variable set AMM_ADDRESS --body {payload['MarketAMM']} -R me/repo",
        f"gh variable set FACTORY_ADDRESS --body {payload['MarketFactory']} -R me/repo",
        "gh variable set INDEXER_START_BLOCK --body 123 -R me/repo",
    ]
    assert "gh workflow run deploy-gcp.yml" in text and "Legacy markets imported: 1" in text and "b" * 64 in text
    sim = deploy_ci.render_summary("v2", False, payload, [], None)
    assert "gh variable set" not in sim and "Simulated" in sim
    assert "sync_mobile_deployments" not in sim


def test_summary_v2_broadcast_points_at_the_mobile_sync_script():
    payload = {
        "chainId": 84532,
        "MockUSDC": "0x" + "1" * 40,
        "MarketAMM": "0x" + "2" * 40,
        "MarketFactory": "0x" + "3" * 40,
        "MarketAMMLegacy": "0x" + "4" * 40,
        "MarketFactoryLegacy": "0x" + "5" * 40,
        "deployBlock": 123,
    }
    text = deploy_ci.render_summary("v2", True, payload, [], None, repo="me/repo", run_id="777")
    lines = text.splitlines()
    assert "gh run download 777 -n contracts-84532-v2-broadcast-777 -D /tmp/ou-84532-v2 -R me/repo" in lines
    assert "python scripts/sync_mobile_deployments.py 84532 --source /tmp/ou-84532-v2/84532.json" in lines
    # The hand merge (and its "do not overwrite" warning) is for the contracts file only.
    assert "into `contracts/deployments/84532.json` by hand" in text
    assert "mobile/assets/deployments/84532.json` (the upload omits" not in text
    assert "python scripts/sync_mobile_deployments.py 84532 --check" in text
    # Without GITHUB_RUN_ID (local runs) the command keeps a placeholder.
    local = deploy_ci.render_summary("v2", True, payload, [], None, repo="me/repo")
    assert "gh run download <run-id> -n contracts-84532-v2-broadcast-<run-id>" in local
    # A failed migration never suggests syncing the mobile asset.
    failed = deploy_ci.render_summary("v2", True, {**payload, "migrationFailed": True}, [], None, run_id="777")
    assert "sync_mobile_deployments" not in failed


def test_summary_core_broadcast_syncs_mobile_and_artifact_name_matches_workflow():
    payload = {"chainId": 84532, "MockUSDC": "0x" + "1" * 40, "deployBlock": 5}
    text = deploy_ci.render_summary("core", True, payload, None, None, repo="me/repo", run_id="9")
    assert "python scripts/sync_mobile_deployments.py 84532 --source /tmp/ou-84532-core/84532.json" in text
    assert "-n contracts-84532-core-broadcast-9 " in text
    # The printed artifact name must match deploy-contracts.yml's upload step.
    wf = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy-contracts.yml").read_text(encoding="utf-8")
    assert "name: contracts-84532-${{ inputs.target }}-${{ inputs.broadcast && 'broadcast' || 'simulate' }}-${{ github.run_id }}" in wf
    assert "path: ${{ runner.temp }}/deployments/*.json" in wf


def test_main_passes_github_run_id_to_summary(monkeypatch, tmp_path):
    seen = {}

    def fake_summary(*args, **kwargs):
        seen.update(kwargs)
        return ""

    monkeypatch.setattr(deploy_ci, "render_summary", fake_summary)
    monkeypatch.setattr(deploy_ci, "connect", lambda *a, **k: None)
    monkeypatch.setattr(deploy_ci, "run_verify", lambda accounts, core: ([], []))
    env = {**_keys(), "DEPLOY_RPC_URL": "https://rpc.example/x", "GITHUB_RUN_ID": " 4242 "}
    for name in deploy_ci.CORE_NAMES:
        env[deploy_ci.REPO_VARS[name]] = Account.create().address
    assert deploy_ci.main(["--target", "verify", "--out", str(tmp_path)], env=env) == 0
    assert seen.get("run_id") == "4242"


def test_core_then_verify_in_process(capsys, tmp_path):
    with boa.env.anchor():
        env, payload = _core_env(tmp_path)
        for name in ("MarketFactory", "ConsensusOracle", "MarketAMM", "OverUnderPaymaster", "deployBlock"):
            assert name in payload
        assert deploy_ci.main(["--target", "verify", "--in-process", "--out", str(tmp_path)], env=env) == 0
        checks = json.loads((tmp_path / "verify-checks.json").read_text())
        assert checks and all(c["ok"] for c in checks)
        env["ORACLE_ADDRESS"] = payload["MarketAMM"]
        assert deploy_ci.main(["--target", "verify", "--in-process", "--out", str(tmp_path)], env=env) == 1
        _assert_no_keys(capsys.readouterr(), env)


def test_v2_preflight_blocks_wrong_operator_before_any_tx(capsys, tmp_path, monkeypatch):
    def never(**kwargs):
        raise AssertionError("migrate_v2 must not run when preflight fails")

    monkeypatch.setitem(sys.modules, "deploy_v2", types.SimpleNamespace(migrate_v2=never))
    with boa.env.anchor():
        env, _ = _core_env(tmp_path)
        env["OPERATOR_PRIVATE_KEY"] = Account.create().key.hex()
        out_dir = tmp_path / "v2"
        assert deploy_ci.main(["--target", "v2", "--in-process", "--out", str(out_dir)], env=env) == 1
        checks = {c["check"]: c["ok"] for c in json.loads((out_dir / "v2-checks.json").read_text())}
        assert checks["factory.operator() == operator key"] is False
        assert not (out_dir / "31337.json").exists()
        _assert_no_keys(capsys.readouterr(), env)


def test_v2_preflight_blocks_a_fee_vault_var_that_is_not_the_amm_fee_vault(capsys, tmp_path, monkeypatch):
    def never(**kwargs):
        raise AssertionError("migrate_v2 must not run when preflight fails")

    monkeypatch.setitem(sys.modules, "deploy_v2", types.SimpleNamespace(migrate_v2=never))
    with boa.env.anchor():
        env, payload = _core_env(tmp_path)
        env["FEE_VAULT_ADDRESS"] = payload["Exchange"]  # has code, so only the feeVault() row can catch it
        out_dir = tmp_path / "v2"
        argv = ["--target", "v2", "--in-process", "--broadcast", "--out", str(out_dir), "--allow-v2-source"]
        assert deploy_ci.main(argv, env=env) == 1
        checks = {c["check"]: c for c in json.loads((out_dir / "v2-checks.json").read_text())}
        row = checks["amm.feeVault() == FEE_VAULT_ADDRESS"]
        assert row["ok"] is False and row["actual"].lower() == payload["FeeVault"].lower()
        assert all(c["ok"] for name, c in checks.items() if name != "amm.feeVault() == FEE_VAULT_ADDRESS")
        assert not (out_dir / "31337.json").exists()
        _assert_no_keys(capsys.readouterr(), env)


def test_v2_permissionless_defaults_on_and_help_says_so(capsys, tmp_path, monkeypatch):
    seen = {}

    def migrate_v2(*, deployments, operator, legacy_cids, listing, permissionless, close_gate, progress=None, allow_v2_source=False):
        seen["permissionless"] = permissionless
        return {"MarketAMM": Account.create().address, "MarketFactory": Account.create().address, "imported": [], "deployBlock": 1}

    monkeypatch.setitem(sys.modules, "deploy_v2", types.SimpleNamespace(migrate_v2=migrate_v2))
    with boa.env.anchor():
        env, _ = _core_env(tmp_path)
        assert deploy_ci.main(["--target", "v2", "--in-process", "--out", str(tmp_path / "v2"), "--allow-v2-source"], env=env) == 0
    assert seen["permissionless"] is True
    with pytest.raises(SystemExit):
        deploy_ci.main(["--help"], env={})
    assert "ADR-0012" in capsys.readouterr().out


def test_v2_calls_migrate_v2_with_the_agreed_signature(capsys, tmp_path, monkeypatch):
    seen = {}
    new_amm, new_factory = Account.create().address, Account.create().address

    def migrate_v2(*, deployments, operator, legacy_cids, listing, permissionless, close_gate):
        seen.update(
            deployments=deployments, operator=operator, legacy_cids=legacy_cids,
            listing=listing, permissionless=permissionless, close_gate=close_gate,
        )
        return {
            "MarketAMM": new_amm,
            "MarketFactory": new_factory,
            "MarketAMMLegacy": deployments["MarketAMM"],
            "MarketFactoryLegacy": deployments["MarketFactory"],
            "imported": list(legacy_cids),
            "deployBlock": 77,
        }

    monkeypatch.setitem(sys.modules, "deploy_v2", types.SimpleNamespace(migrate_v2=migrate_v2))
    with boa.env.anchor():
        env, payload = _core_env(tmp_path)
        cid = _legacy_market(env, payload)
        unknown = "0x" + "ee" * 32
        out_dir, summary = tmp_path / "v2", tmp_path / "summary.md"
        argv = [
            "--target", "v2", "--in-process", "--broadcast", "--out", str(out_dir), "--summary", str(summary),
            "--api-url", API, "--legacy-cids", unknown, "--min-seed-usdc", "25000000", "--no-permissionless",
            "--allow-v2-source",  # the in-process "legacy" stack is built from the v2 sources
        ]
        env["GITHUB_REPOSITORY"] = "me/repo"
        fake_get = lambda url: [{"primary": {"conditionId": cid}, "children": []}]  # noqa: E731
        assert deploy_ci.main(argv, env=env, http_get=fake_get) == 0

    op = Account.from_key(env["OPERATOR_PRIVATE_KEY"]).address
    assert seen["operator"] == op
    assert seen["legacy_cids"] == [cid]  # the unknown cid is filtered out before migrate_v2
    assert seen["permissionless"] is False and seen["close_gate"] is True
    assert seen["listing"]["minSeedUsdc"] == 25_000_000
    # The recipient is left to migrate_v2 (legacy AMM's on-chain feeVault()); the payload records that value.
    assert "feeRecipient" not in seen["listing"]
    assert seen["listing"]["listingCooldown"] == 3600  # contract default: 1 h per creator, never 0 by default
    deployments = seen["deployments"]
    for name in deploy_ci.CORE_NAMES:
        assert deployments[name] == payload[name]
    assert deployments["operator"] == op and deployments["wildcardGenerator"] == payload["wildcardGenerator"]
    assert deployments["agents"] == payload["agents"]

    written = json.loads((out_dir / "31337.json").read_text())
    assert written["MarketAMM"] == new_amm and written["MarketFactory"] == new_factory
    assert written["MarketFactoryLegacy"] == payload["MarketFactory"]
    assert written["deployBlock"] == 77 and written["imported"] == [cid]
    assert written["listing"]["feeRecipient"] == payload["FeeVault"] and written["permissionless"] is False
    text = summary.read_text()
    assert f"gh variable set AMM_ADDRESS --body {new_amm} -R me/repo" in text
    assert f"gh variable set FACTORY_ADDRESS --body {new_factory} -R me/repo" in text
    assert "gh variable set USDC_ADDRESS" not in text
    assert unknown in text  # reported as skipped
    _assert_no_keys(capsys.readouterr(), env)


def test_v2_in_process_with_real_migration(capsys, tmp_path):
    try:
        import deploy_v2
    except ImportError as exc:
        pytest.skip(f"contracts/script/deploy_v2.py not importable yet (track C2): {exc}")
    if not callable(getattr(deploy_v2, "migrate_v2", None)):
        pytest.skip("deploy_v2.migrate_v2 not defined yet (track C2)")

    with boa.env.anchor():
        env, payload = _core_env(tmp_path)
        cid = _legacy_market(env, payload)
        out_dir = tmp_path / "v2"
        argv = ["--target", "v2", "--in-process", "--out", str(out_dir), "--legacy-cids", cid, "--allow-v2-source"]
        assert deploy_ci.main(argv, env=env) == 0
        v2 = json.loads((out_dir / "31337.json").read_text())
        assert v2["MarketAMM"] != payload["MarketAMM"] and v2["MarketFactory"] != payload["MarketFactory"]
        assert v2["MarketAMMLegacy"].lower() == payload["MarketAMM"].lower()
        assert v2["MarketFactoryLegacy"].lower() == payload["MarketFactory"].lower()
        imported = v2["imported"]
        assert (cid in [str(c).lower() for c in imported]) if isinstance(imported, list) else imported == 1
        oracle = deploy_ci._at("ConsensusOracle", payload["ConsensusOracle"])
        assert oracle.factory().lower() == v2["MarketFactory"].lower()
        new_factory = deploy_ci._at("MarketFactory", v2["MarketFactory"])
        assert new_factory.marketExists(bytes.fromhex(cid[2:]))
        assert new_factory.feeRecipient().lower() == payload["FeeVault"].lower()
        assert new_factory.permissionless() is True  # deploy_ci default: listing opens (ADR-0012)
        assert v2["permissionless"] is True

        # A second run against the old repo vars is refused: the oracle already points at v2.
        assert deploy_ci.main(argv, env=env) == 1

        env["AMM_ADDRESS"], env["FACTORY_ADDRESS"] = v2["MarketAMM"], v2["MarketFactory"]
        assert deploy_ci.main(["--target", "verify", "--in-process", "--out", str(tmp_path)], env=env) == 0
        _assert_no_keys(capsys.readouterr(), env)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _swap_in_v1_pair(env, payload):
    """Replace the core stack's AMM + factory with git-HEAD v1 copies wired to the same oracle (the live Base Sepolia shape)."""
    op = Account.from_key(env["OPERATOR_PRIVATE_KEY"]).address
    # Fresh deployer: anchor() rolls back the default sender's nonce but not boa's storage traces, so deploying
    # v1 layouts from it would leave stale traces at addresses later tests reuse for other contracts.
    with boa.env.prank(boa.env.generate_address()):
        amm = boa.load(str(FIXTURES / "MarketAMMV1.vy"), payload["ConditionalTokens"], payload["MockUSDC"], payload["FeeVault"], op)
        factory = boa.load(
            str(FIXTURES / "MarketFactoryV1.vy"),
            payload["ConditionalTokens"], payload["ConsensusOracle"], amm.address, payload["MockUSDC"], op, op,
        )
    oracle = deploy_ci._at("ConsensusOracle", payload["ConsensusOracle"])
    with boa.env.prank(op):
        oracle.setFactory(factory.address)
        amm.setFactory(factory.address)
    env["AMM_ADDRESS"], env["FACTORY_ADDRESS"] = str(amm.address), str(factory.address)
    return dict(payload, MarketAMM=str(amm.address), MarketFactory=str(factory.address))


def test_v2_from_a_v1_pair_then_rerun_with_updated_vars_is_refused(capsys, tmp_path):
    with boa.env.anchor():
        env, payload = _core_env(tmp_path)
        payload = _swap_in_v1_pair(env, payload)
        cid = _legacy_market(env, payload)
        out_dir = tmp_path / "v2"
        argv = ["--target", "v2", "--in-process", "--broadcast", "--out", str(out_dir), "--legacy-cids", cid]
        assert deploy_ci.main(argv, env=env) == 0  # no --allow-v2-source needed from a real v1 pair
        v2 = json.loads((out_dir / "31337.json").read_text())
        assert v2["MarketFactoryLegacy"].lower() == payload["MarketFactory"].lower()
        assert [c.lower() for c in v2["imported"]] == [cid]

        # The operator updates the repo vars, then someone re-runs v2: every "not yet migrated" wiring row
        # passes against the v2 pair, so only the v1 rows stop a silent v2 -> v2 re-migration.
        env["AMM_ADDRESS"], env["FACTORY_ADDRESS"] = v2["MarketAMM"], v2["MarketFactory"]
        oracle = deploy_ci._at("ConsensusOracle", payload["ConsensusOracle"])
        again = tmp_path / "again"
        assert deploy_ci.main(["--target", "v2", "--in-process", "--broadcast", "--out", str(again)], env=env) == 1
        checks = {c["check"]: c["ok"] for c in json.loads((again / "v2-checks.json").read_text())}
        assert checks["FACTORY_ADDRESS is v1 (no minSeedUsdc(); v2 not yet deployed)"] is False
        assert checks["AMM_ADDRESS is v1 (no closeGate(); v2 not yet deployed)"] is False
        assert checks["oracle.factory() == FACTORY_ADDRESS (not yet migrated)"] is True
        assert not (again / "31337.json").exists()
        assert oracle.factory().lower() == v2["MarketFactory"].lower()  # untouched
        _assert_no_keys(capsys.readouterr(), env)


def test_v2_failure_part_way_reports_what_landed(capsys, tmp_path, monkeypatch):
    new_amm = Account.create().address

    def migrate_v2(*, deployments, operator, legacy_cids, listing, permissionless, close_gate, progress=None):
        progress.update(MarketAMM=new_amm, deployBlock=41, steps=["deploy MarketAMM"])
        raise RuntimeError("txn failed: nonce too low")

    monkeypatch.setitem(sys.modules, "deploy_v2", types.SimpleNamespace(migrate_v2=migrate_v2))
    with boa.env.anchor():
        env, payload = _core_env(tmp_path)
        out_dir, summary = tmp_path / "v2", tmp_path / "summary.md"
        argv = ["--target", "v2", "--in-process", "--broadcast", "--out", str(out_dir), "--summary", str(summary), "--allow-v2-source"]
        assert deploy_ci.main(argv, env=env) == 1
    written = json.loads((out_dir / "31337.json").read_text())
    assert written["migrationFailed"] is True and written["completedSteps"] == ["deploy MarketAMM"]
    assert written["MarketAMM"] == new_amm and written["deployBlock"] == 41
    assert "MarketFactory" not in written  # never report the v1 factory as the new one
    assert written["MarketFactoryLegacy"] == payload["MarketFactory"]
    text = summary.read_text()
    assert "FAILED part-way" in text and "nonce too low" in text and "gh variable set" not in text
    _assert_no_keys(capsys.readouterr(), env)


def test_listing_defaults_match_deploy_v2_and_the_contract():
    import deploy_v2

    for key, value in deploy_ci.LISTING_DEFAULTS.items():
        assert deploy_v2.LISTING_DEFAULTS[key] == value, key


def _fake_migrate(seen):
    def migrate_v2(*, deployments, operator, legacy_cids, listing, permissionless, close_gate, progress=None, allow_v2_source=False):
        seen.update(listing=listing, allow_v2_source=allow_v2_source)
        return {
            "MarketAMM": Account.create().address,
            "MarketFactory": Account.create().address,
            "MarketAMMLegacy": deployments["MarketAMM"],
            "MarketFactoryLegacy": deployments["MarketFactory"],
            "imported": [],
            "deployBlock": 5,
        }

    return migrate_v2


@pytest.mark.parametrize("cooldown", ["0", "120"])
def test_v2_listing_cooldown_passes_through(tmp_path, monkeypatch, cooldown):
    seen = {}
    monkeypatch.setitem(sys.modules, "deploy_v2", types.SimpleNamespace(migrate_v2=_fake_migrate(seen)))
    with boa.env.anchor():
        env, _ = _core_env(tmp_path)
        argv = ["--target", "v2", "--in-process", "--out", str(tmp_path / "v2"), "--allow-v2-source", "--listing-cooldown", cooldown]
        assert deploy_ci.main(argv, env=env) == 0
    assert seen["listing"]["listingCooldown"] == int(cooldown)
    assert seen["allow_v2_source"] is True  # forwarded to migrate_v2 when it accepts the keyword
    written = json.loads((tmp_path / "v2" / "31337.json").read_text())
    assert written["listing"]["listingCooldown"] == int(cooldown)


def test_v2_negative_listing_cooldown_is_an_input_error(capsys, tmp_path):
    env = {"OPERATOR_PRIVATE_KEY": Account.create().key.hex()}
    argv = ["--target", "v2", "--in-process", "--out", str(tmp_path), "--listing-cooldown", "-1"]
    assert deploy_ci.main(argv, env=env) == 2
    assert "--listing-cooldown" in capsys.readouterr().err


def test_v2_probe_rpc_error_fails_closed(capsys, tmp_path, monkeypatch):
    """A 429 behind the v1/v2 probes must not read as "v1": nothing is sent and no JSON is written."""
    import requests

    with boa.env.anchor():
        env, payload = _core_env(tmp_path)
        payload = _swap_in_v1_pair(env, payload)
        out_dir = tmp_path / "v2"
        argv = ["--target", "v2", "--in-process", "--broadcast", "--out", str(out_dir)]
        assert deploy_ci.main(argv, env=env) == 0
        v2 = json.loads((out_dir / "31337.json").read_text())
        env["AMM_ADDRESS"], env["FACTORY_ADDRESS"] = v2["MarketAMM"], v2["MarketFactory"]
        oracle = deploy_ci._at("ConsensusOracle", payload["ConsensusOracle"])

        real_at = deploy_ci._at

        def rate_limited(*a, **k):
            raise requests.exceptions.HTTPError("429 Client Error: Too Many Requests for url: https://rpc.example/v2/SECRET")

        def flaky_at(name, addr):
            contract = real_at(name, addr)
            if name == "MarketFactory":
                contract.minSeedUsdc = rate_limited
            if name == "MarketAMM":
                contract.closeGate = rate_limited
            return contract

        monkeypatch.setattr(deploy_ci, "_at", flaky_at)
        again = tmp_path / "again"
        assert deploy_ci.main(["--target", "v2", "--in-process", "--broadcast", "--out", str(again)], env=env) != 0
        assert not (again / "31337.json").exists()
        assert oracle.factory().lower() == v2["MarketFactory"].lower()
        err = capsys.readouterr().err
        assert "HTTPError" in err


def test_v2_failure_reports_contracts_mined_at_an_unexpected_address(capsys, tmp_path, monkeypatch):
    new_amm, orphan = Account.create().address, Account.create().address

    def migrate_v2(*, deployments, operator, legacy_cids, listing, permissionless, close_gate, progress=None):
        progress.update(
            MarketAMM=new_amm,
            deployBlock=41,
            steps=["deploy MarketAMM", f"deploy MarketFactory (mined at {orphan}; address mismatch, unwired)"],
            orphans={"MarketFactory": orphan},
        )
        raise RuntimeError(f"uh oh! {Account.create().address} != {orphan}")

    monkeypatch.setitem(sys.modules, "deploy_v2", types.SimpleNamespace(migrate_v2=migrate_v2))
    with boa.env.anchor():
        env, payload = _core_env(tmp_path)
        out_dir, summary = tmp_path / "v2", tmp_path / "summary.md"
        argv = ["--target", "v2", "--in-process", "--broadcast", "--out", str(out_dir), "--summary", str(summary), "--allow-v2-source"]
        assert deploy_ci.main(argv, env=env) == 1
    written = json.loads((out_dir / "31337.json").read_text())
    assert written["orphans"] == {"MarketFactory": orphan}
    assert "MarketFactory" not in written
    text = summary.read_text()
    assert "never wired" in text and orphan in text and "Completed on chain: nothing" not in text


def test_pending_operator_txs_counts_the_gap():
    class Eth:
        def __init__(self, pending, latest):
            self.counts = {"pending": pending, "latest": latest}

        def get_transaction_count(self, addr, block):
            return self.counts[block]

    w3 = types.SimpleNamespace(eth=Eth(12, 10))
    assert deploy_ci.pending_operator_txs("http://unused", "0x" + "11" * 20, w3=w3) == 2
    assert deploy_ci.pending_operator_txs("http://unused", "0x" + "11" * 20, w3=types.SimpleNamespace(eth=Eth(10, 10))) == 0


def test_broadcast_refuses_while_operator_txs_are_pending(capsys, tmp_path, monkeypatch):
    def never(**kwargs):
        raise AssertionError("migrate_v2 must not run while operator txs are pending")

    monkeypatch.setitem(sys.modules, "deploy_v2", types.SimpleNamespace(migrate_v2=never))
    monkeypatch.setattr(deploy_ci, "connect", lambda *a, **k: 10**18)
    monkeypatch.setattr(deploy_ci, "pending_operator_txs", lambda rpc, op: 1)
    env = {"OPERATOR_PRIVATE_KEY": Account.create().key.hex(), "DEPLOY_RPC_URL": "https://rpc.example/v2/SECRETKEY"}
    for name in deploy_ci.CORE_NAMES:
        env[deploy_ci.REPO_VARS[name]] = Account.create().address
    rc = deploy_ci.main(["--target", "v2", "--broadcast", "--out", str(tmp_path)], env=env)
    err = capsys.readouterr().err
    assert rc == 2
    assert "pending" in err and "overunder-oracle-tick" in err and "SECRETKEY" not in err
    assert not (tmp_path / "84532.json").exists()


def test_deploy_contracts_workflow_wires_cooldown_and_scheduler_pause():
    wf = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy-contracts.yml").read_text(encoding="utf-8")
    assert "listing_cooldown:" in wf and 'default: "3600"' in wf
    assert "LISTING_COOLDOWN: ${{ inputs.listing_cooldown }}" in wf
    assert '--listing-cooldown "$LISTING_COOLDOWN"' in wf
    # A broadcast pauses the oracle scheduler (same operator key) and resumes it if the run fails.
    assert "gcloud scheduler jobs pause" in wf and "id: pause" in wf
    assert "if: (failure() || cancelled()) && steps.pause.outputs.paused == 'true'" in wf
