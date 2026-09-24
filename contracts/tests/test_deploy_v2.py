"""deploy_v2.migrate_v2 against a v1 stack built from the git-HEAD sources in tests/fixtures, plus deploy.py hardening."""

import sys
from pathlib import Path

import boa
import pytest
from eth_account import Account
from eth_utils import keccak

from tests.conftest import COOLDOWN, chain_id
from tests.eip712 import sign_attestation

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "script"
sys.path.insert(0, str(SCRIPT))
import deploy  # noqa: E402
import deploy_v2  # noqa: E402

FIXTURES = HERE / "fixtures"
CRIT = keccak(text="YES if the Bills score first per the official NFL play-by-play")
LISTING = {"minSeedUsdc": 5_000_000, "listingFeeUsdc": 0, "minLeadTime": 3600, "maxHorizon": 90 * 86400, "listingCooldown": 0}


def _v1_stack():
    """v1 protocol: shared contracts from src/, MarketAMM + MarketFactory from their git-HEAD copies."""
    accounts = {n: Account.create() for n in ("operator", "treasury", "generator", "alpha", "beta", "gamma", "trader", "lister")}
    for acct in accounts.values():
        boa.env.set_balance(acct.address, 10**18)
    op = accounts["operator"].address
    agents = [accounts[n].address for n in ("alpha", "beta", "gamma")]
    # Fresh deployer: the default sender's nonce (and so every other test's addresses) stays put.
    with boa.env.prank(boa.env.generate_address()):
        usdc = boa.load("src/MockUSDC.vy")
        ou = boa.load("src/RevenueToken.vy", accounts["treasury"].address)
        ctf = boa.load("src/ConditionalTokens.vy", usdc.address)
        vault = boa.load("src/FeeVault.vy", usdc.address, ou.address, COOLDOWN)
        oracle = boa.load("src/ConsensusOracle.vy", ctf.address, op, agents)
        amm = boa.load(str(FIXTURES / "MarketAMMV1.vy"), ctf.address, usdc.address, vault.address, op)
        factory = boa.load(
            str(FIXTURES / "MarketFactoryV1.vy"), ctf.address, oracle.address, amm.address, usdc.address, op, accounts["generator"].address
        )
    with boa.env.prank(op):
        oracle.setFactory(factory.address)
        amm.setFactory(factory.address)
    deployments = {
        "MockUSDC": usdc.address,
        "ConditionalTokens": ctf.address,
        "FeeVault": vault.address,
        "ConsensusOracle": oracle.address,
        "MarketAMM": amm.address,
        "MarketFactory": factory.address,
        "chainId": 31337,
        "operator": op,
    }
    return {"usdc": usdc, "ctf": ctf, "vault": vault, "oracle": oracle, "amm": amm, "factory": factory, "accounts": accounts, "deployments": deployments}


def _fund(usdc, who, amount, spender):
    with boa.env.prank(who):
        usdc.faucet(amount)
        usdc.approve(spender, amount)


def _v1_primary(s, qid, close, question="Bills vs Dolphins: Bills win?", seed=20_000_000):
    op = s["accounts"]["operator"].address
    _fund(s["usdc"], op, seed, s["factory"].address)
    with boa.env.prank(op):
        return s["factory"].createPrimaryMarket(qid, close, question, seed)


def _migrate(s, cids=(), listing=None, permissionless=True, close_gate=True, deployments=None, **kwargs):
    return deploy_v2.migrate_v2(
        deployments=dict(s["deployments"] if deployments is None else deployments),
        operator=s["accounts"]["operator"].address,
        legacy_cids=list(cids),
        listing=dict(LISTING if listing is None else listing),
        permissionless=permissionless,
        close_gate=close_gate,
        **kwargs,
    )


V2_JSON_KEYS = ("MarketAMM", "MarketFactory", "MarketAMMLegacy", "MarketFactoryLegacy", "deployBlock")


def _v2(result):
    amm = boa.load_partial("src/MarketAMM.vy").at(result["MarketAMM"])
    factory = boa.load_partial("src/MarketFactory.vy").at(result["MarketFactory"])
    return amm, factory


def _resolve(s, cid, outcome):
    oracle = s["oracle"]
    evidence = b"\xab" * 32
    deadline = boa.env.timestamp + 1000
    sigs = [
        sign_attestation(s["accounts"][n].key, oracle.address, chain_id(), cid, outcome, evidence, deadline)
        for n in ("alpha", "beta", "gamma")
    ]
    oracle.submitConsensus(cid, outcome, evidence, deadline, sigs)


def test_migrate_v2_end_to_end():
    s = _v1_stack()
    usdc, ctf, oracle, legacy, legacy_amm = s["usdc"], s["ctf"], s["oracle"], s["factory"], s["amm"]
    op = s["accounts"]["operator"].address
    gen = s["accounts"]["generator"].address
    trader = s["accounts"]["trader"].address
    lister = s["accounts"]["lister"].address
    close = boa.env.timestamp + 10_000
    live = _v1_primary(s, b"\x51" * 32, close)
    paused = _v1_primary(s, b"\x52" * 32, close, question="Jets vs Pats: Jets win?")
    with boa.env.prank(op):
        legacy.setPaused(paused, True)
    _fund(usdc, trader, 3_000_000, legacy_amm.address)
    with boa.env.prank(trader):
        yes = legacy_amm.buyWithUSDC(live, True, 3_000_000, 0)
    unknown = "0x" + "99" * 32

    result = _migrate(s, cids=["0x" + live.hex(), ("0x" + paused.hex()).upper().replace("0X", "0x"), unknown, "0x" + live.hex()])

    assert set(result) == {"MarketAMM", "MarketFactory", "MarketAMMLegacy", "MarketFactoryLegacy", "imported", "deployBlock"}
    assert result["MarketAMMLegacy"] == legacy_amm.address and result["MarketFactoryLegacy"] == legacy.address
    assert result["imported"] == ["0x" + live.hex(), "0x" + paused.hex()]
    assert isinstance(result["deployBlock"], int) and result["deployBlock"] >= 0
    amm, factory = _v2(result)

    # Wiring: the shared oracle and the new AMM both point at the new factory.
    assert oracle.factory() == factory.address
    assert amm.factory() == factory.address
    assert (factory.ctf(), factory.oracle(), factory.amm(), factory.usdc()) == (ctf.address, oracle.address, amm.address, usdc.address)
    assert factory.operator() == op and factory.wildcardGenerator() == gen
    assert (amm.ctf(), amm.usdc(), amm.feeVault(), amm.operator()) == (ctf.address, usdc.address, s["vault"].address, op)
    assert amm.closeGate() is True
    assert factory.permissionless() is True
    assert factory.minSeedUsdc() == 5_000_000
    assert factory.feeRecipient() == s["vault"].address  # defaulted to the legacy AMM's FeeVault
    assert factory.listingCooldown() == 0

    # Imported rows mirror the legacy factory, paused flag included.
    assert factory.markets(live) == (live, b"\x00" * 32, close, 0, False, "Bills vs Dolphins: Bills win?")
    assert factory.markets(paused)[4] is True
    assert not factory.marketExists(bytes.fromhex(unknown[2:]))

    # A user listing works on v2 with LP to the lister.
    _fund(usdc, lister, 5_000_000, factory.address)
    with boa.env.prank(lister):
        user_cid = factory.createPermissionlessMarket(b"\x07" * 32, boa.env.timestamp + 7200, "Will the Bills score first?", CRIT, 5_000_000)
    assert factory.markets(user_cid)[3] == 2
    assert amm.lpBalance(user_cid, lister) == 5_000_000
    assert oracle.closeTime(user_cid) == boa.env.timestamp + 7200

    # The v1 factory can no longer create anything.
    _fund(usdc, op, 10_000_000, legacy.address)
    with boa.env.prank(op):
        with boa.reverts():
            legacy.createPrimaryMarket(b"\x53" * 32, close, "Dead factory", 10_000_000)

    # The v1 market keeps trading on the legacy AMM, resolves through the shared oracle and redeems.
    _fund(usdc, trader, 1_000_000, legacy_amm.address)
    with boa.env.prank(trader):
        yes += legacy_amm.buyWithUSDC(live, True, 1_000_000, 0)
    boa.env.time_travel(seconds=close - boa.env.timestamp)
    _resolve(s, live, 0)
    assert ctf.isResolved(live)
    with boa.env.prank(trader):
        ctf.redeemPositions(live, 0, yes)
    assert usdc.balanceOf(trader) == yes


def test_migrate_v2_gate_off_and_listing_closed():
    s = _v1_stack()
    result = _migrate(s, permissionless=False, close_gate=False, listing={"minSeedUsdc": 7_000_000})
    amm, factory = _v2(result)
    assert amm.closeGate() is False
    assert factory.permissionless() is False
    assert result["imported"] == []
    # Missing keys keep the constructor defaults.
    assert (factory.minSeedUsdc(), factory.listingFeeUsdc(), factory.minLeadTime(), factory.maxHorizon(), factory.listingCooldown()) == (
        7_000_000,
        0,
        3600,
        7_776_000,
        3600,
    )
    lister = s["accounts"]["lister"].address
    _fund(s["usdc"], lister, 7_000_000, factory.address)
    with boa.env.prank(lister):
        with boa.reverts("listing closed"):
            factory.createPermissionlessMarket(b"\x08" * 32, boa.env.timestamp + 7200, "Closed listing question", CRIT, 7_000_000)


def test_migrate_v2_imports_in_chunks_of_25():
    s = _v1_stack()
    op = s["accounts"]["operator"].address
    close = boa.env.timestamp + 10_000
    parent = _v1_primary(s, b"\x60" * 32, close)
    cids = [parent]
    with boa.env.prank(op):
        for i in range(55):
            cids.append(s["factory"].createWildcardMarket(keccak(text=f"wc-{i}"), parent, close, f"Wildcard {i}", 0))
    progress = {}
    result = _migrate(s, cids=["0x" + c.hex() for c in cids], progress=progress)
    _amm, factory = _v2(result)
    assert deploy_v2.IMPORT_CHUNK == 25
    assert [st for st in progress["steps"] if st.startswith("importLegacyMarkets")] == [
        "importLegacyMarkets[0:25]",
        "importLegacyMarkets[25:50]",
        "importLegacyMarkets[50:56]",
    ]
    assert len(result["imported"]) == 56
    assert all(factory.marketExists(c) for c in cids)
    assert factory.markets(cids[-1])[1] == parent


def test_import_chunk_worst_case_gas_has_headroom_under_the_per_tx_cap():
    """IMPORT_CHUNK rows with max-length (256-byte) questions stay under half the 2^24 per-tx gas cap (EIP-7825).

    Measured about 7.95M gas for 25 rows (50 rows is about 15.9M, 95% of the cap)."""
    s = _v1_stack()
    op = s["accounts"]["operator"].address
    close = boa.env.timestamp + 10_000
    parent = _v1_primary(s, b"a" * 32, close)
    cids = []
    with boa.env.prank(op):
        for i in range(deploy_v2.IMPORT_CHUNK):
            question = (f"Q{i:03d} " + "x" * 256)[:256]
            cids.append(s["factory"].createWildcardMarket(keccak(text=f"gas-{i}"), parent, close, question, 0))
        factory = boa.load("src/MarketFactory.vy", s["ctf"].address, s["oracle"].address, s["amm"].address, s["usdc"].address, op, op)
        factory.importLegacyMarkets(s["factory"].address, cids)
        gas = factory._computation.get_gas_used()
    assert all(factory.marketExists(c) for c in cids)
    assert len(factory.markets(cids[-1])[5]) == 256
    assert gas < 2**24 // 2, gas


def test_migrate_v2_checks_before_any_transaction():
    s = _v1_stack()
    oracle, legacy = s["oracle"], s["factory"]
    stranger = Account.create().address
    with pytest.raises(ValueError, match="operator"):
        deploy_v2.migrate_v2(
            deployments=dict(s["deployments"]), operator=stranger, legacy_cids=[], listing={}, permissionless=False, close_gate=True
        )
    with pytest.raises(ValueError, match="unknown listing keys"):
        _migrate(s, listing={"minSeed": 1})
    with pytest.raises(ValueError, match="minLeadTime"):
        _migrate(s, listing={"minLeadTime": 599})
    with pytest.raises(ValueError, match="condition id"):
        _migrate(s, cids=["0x1234"])
    assert oracle.factory() == legacy.address

    _migrate(s)
    with pytest.raises(ValueError, match="already migrated"):
        _migrate(s)


def test_migrate_v2_rerun_with_the_json_main_writes_is_refused():
    """main() writes the v2 pair into deployments/{chain}.json; a second run must not migrate v2 -> v2."""
    s = _v1_stack()
    oracle = s["oracle"]
    r1 = _migrate(s)
    amm1, factory1 = _v2(r1)
    lister = s["accounts"]["lister"].address
    _fund(s["usdc"], lister, 5_000_000, factory1.address)
    with boa.env.prank(lister):
        user_cid = factory1.createPermissionlessMarket(b"\x09" * 32, boa.env.timestamp + 7200, "Stranded if re-run?", CRIT, 5_000_000)
    assert amm1.pools(user_cid)[3] is True

    written = dict(s["deployments"])
    written.update({k: r1[k] for k in V2_JSON_KEYS})  # exactly what main() persists
    progress: dict = {}
    with pytest.raises(ValueError, match="already record a v2 migration"):
        _migrate(s, deployments=written, progress=progress)
    assert progress["steps"] == []  # refused before any transaction
    assert oracle.factory() == factory1.address

    # Without the Legacy keys (e.g. repo vars updated by hand), the v1 probes still refuse the v2 pair.
    by_hand = dict(s["deployments"], MarketAMM=r1["MarketAMM"], MarketFactory=r1["MarketFactory"])
    with pytest.raises(ValueError, match="already v2"):
        _migrate(s, deployments=by_hand, progress=progress)
    assert progress["steps"] == []
    assert oracle.factory() == factory1.address
    # Even the test-only override never accepts a JSON that records a migration.
    with pytest.raises(ValueError, match="already record a v2 migration"):
        _migrate(s, deployments=written, allow_v2_source=True)


def test_migrate_v2_v2_source_needs_the_explicit_override():
    op, treasury, gen = (Account.create().address for _ in range(3))
    agents = [Account.create().address for _ in range(3)]
    boa.env.set_balance(op, 10**19)
    with boa.env.prank(boa.env.generate_address()):
        out = deploy.deploy(op, treasury, gen, agents, chain=31337)  # a fresh local stack is already v2
    deployments = {name: str(out[name].address) for name in ("MarketAMM", "MarketFactory", "ConsensusOracle", "FeeVault")}
    kwargs = dict(deployments=deployments, operator=op, legacy_cids=[], listing=LISTING, permissionless=True, close_gate=True)
    with pytest.raises(ValueError, match="already v2"):
        deploy_v2.migrate_v2(**kwargs)
    assert out["ConsensusOracle"].factory() == out["MarketFactory"].address
    result = deploy_v2.migrate_v2(**kwargs, allow_v2_source=True)
    assert out["ConsensusOracle"].factory() == result["MarketFactory"]


def test_migrate_v2_records_a_contract_mined_at_an_unexpected_address(monkeypatch):
    s = _v1_stack()
    real_load = boa.load
    mined = "0x" + "ab" * 20

    def load(path, *args, **kwargs):
        if str(path).endswith("MarketFactory.vy"):
            raise RuntimeError(f"uh oh! 0x{'cd' * 20} != {mined}")
        return real_load(path, *args, **kwargs)

    monkeypatch.setattr(deploy_v2.boa, "load", load)
    progress: dict = {}
    with pytest.raises(RuntimeError, match="uh oh"):
        _migrate(s, progress=progress)
    assert progress["orphans"] == {"MarketFactory": mined}
    assert progress["steps"][0] == "deploy MarketAMM"
    assert progress["steps"][1] == f"deploy MarketFactory (mined at {mined}; address mismatch, unwired)"
    assert "MarketFactory" not in progress  # never reported as the wired factory
    assert s["oracle"].factory() == s["factory"].address


def test_answers_counts_only_reverts_as_missing():
    import requests
    from boa.rpc import RPCError

    s = _v1_stack()
    v1 = deploy_v2._at(deploy_v2.LEGACY_FACTORY_ABI, s["factory"].address, "MarketFactoryLegacy")
    assert deploy.answers(v1.minSeedUsdc) is False  # real v1: missing selector reverts
    assert deploy.answers(lambda: 1) is True

    def raiser(exc):
        def fn():
            raise exc

        return fn

    assert deploy.answers(raiser(RPCError("execution reverted", 3))) is False
    with pytest.raises(RPCError):
        deploy.answers(raiser(RPCError("rate limit exceeded", -32005)))
    with pytest.raises(requests.HTTPError):
        deploy.answers(raiser(requests.HTTPError("429 Client Error: Too Many Requests for url: http://x")))


def test_use_memory_fork_cache_reforks_without_the_disk_cache():
    from boa.network import NetworkEnv

    assert callable(getattr(NetworkEnv, "_reset_fork", None))  # the private hook this relies on still exists

    class FakeEnv:
        _rpc = object()

        def __init__(self):
            self.calls = []

        def _reset_fork(self, block_identifier="latest"):
            raise AssertionError("replaced")

        def fork_rpc(self, rpc, **kwargs):
            self.calls.append((rpc, kwargs))

    env = FakeEnv()
    assert deploy.use_memory_fork_cache(env) is True
    env._reset_fork(block_identifier=7)
    assert [c[1] for c in env.calls] == [
        {"reset_traces": False, "block_identifier": "latest", "cache_dir": None},
        {"reset_traces": False, "block_identifier": 7, "cache_dir": None},
    ]
    assert all(c[0] is FakeEnv._rpc for c in env.calls)
    assert deploy.use_memory_fork_cache(object()) is False


def test_listing_args_defaults_and_bounds():
    vault = "0x" + "11" * 20
    assert deploy_v2.listing_args({}, vault) == (10_000_000, 0, vault, 3600, 7_776_000, 3600)
    other = "0x" + "22" * 20
    assert deploy_v2.listing_args({"feeRecipient": other, "listingFeeUsdc": 5, "listingCooldown": 0}, vault) == (
        10_000_000,
        5,
        other,
        3600,
        7_776_000,
        0,
    )
    for bad in (
        {"minSeedUsdc": 0},
        {"maxHorizon": 3600},
        {"maxHorizon": 31_622_401},
        {"minLeadTime": -1},
        {"minSeedUsdc": True},
        {"feeRecipient": "nope"},
    ):
        with pytest.raises(ValueError):
            deploy_v2.listing_args(bad, vault)


def test_fetch_market_cids_flattens_cards_and_rows():
    a, b, c = ("0x" + h * 64 for h in "abc")
    payload = [{"primary": {"conditionId": a.upper().replace("0X", "0x")}, "children": [{"conditionId": b}, {"conditionId": "junk"}]}, {"conditionId": c}, {"conditionId": a}]
    seen = []

    def http_get(url):
        seen.append(url)
        return payload

    assert deploy_v2.fetch_market_cids("https://api.example.test/", http_get=http_get) == [a, b, c]
    assert seen == ["https://api.example.test/api/v1/markets"]
    with pytest.raises(SystemExit):
        deploy_v2.fetch_market_cids("ftp://x", http_get=http_get)


def test_deploy_sets_local_listing_config():
    op, treasury, gen = (Account.create().address for _ in range(3))
    agents = [Account.create().address for _ in range(3)]
    boa.env.set_balance(op, 10**19)
    with boa.env.prank(boa.env.generate_address()):
        out = deploy.deploy(op, treasury, gen, agents, chain=31337)
    factory, vault = out["MarketFactory"], out["FeeVault"]
    assert factory.permissionless() is True
    assert (factory.minSeedUsdc(), factory.listingFeeUsdc(), factory.feeRecipient()) == (10_000_000, 0, vault.address)
    assert (factory.minLeadTime(), factory.maxHorizon(), factory.listingCooldown()) == (3600, 7_776_000, 0)
    assert out["MarketAMM"].closeGate() is True


def test_rpc_label_hides_path_query_and_credentials():
    assert deploy.rpc_label("https://user:pw@base-sepolia.g.alchemy.com/v2/SECRET?k=1") == "https://base-sepolia.g.alchemy.com"
    assert deploy.rpc_label("http://127.0.0.1:8545") == "http://127.0.0.1"
    assert deploy.rpc_label("not a url") == "<rpc>"


def test_role_key_refuses_anvil_defaults_off_local(monkeypatch):
    monkeypatch.delenv("OPERATOR_PRIVATE_KEY", raising=False)
    assert deploy.role_key("OPERATOR_PRIVATE_KEY", "operator", 31337) == deploy.ANVIL_KEYS["operator"]
    with pytest.raises(SystemExit, match="required"):
        deploy.role_key("OPERATOR_PRIVATE_KEY", "operator", 84532)
    monkeypatch.setenv("OPERATOR_PRIVATE_KEY", deploy.ANVIL_KEYS["beta"].upper().replace("0X", ""))
    with pytest.raises(SystemExit, match="public anvil key"):
        deploy.role_key("OPERATOR_PRIVATE_KEY", "operator", 84532)
    real = Account.create().key.hex()
    monkeypatch.setenv("OPERATOR_PRIVATE_KEY", real)
    assert deploy.role_key("OPERATOR_PRIVATE_KEY", "operator", 84532) == real


def test_main_refuses_in_process_fallback_off_local(monkeypatch, capsys):
    monkeypatch.setattr(deploy, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("CHAIN_ID", "84532")
    monkeypatch.setenv("ANVIL_RPC_URL", "http://127.0.0.1:9/v2/SECRETPATH?apikey=SECRETQ")
    for var in ("OPERATOR_PRIVATE_KEY", "TREASURY_PRIVATE_KEY", "AGENT_ALPHA_KEY", "AGENT_BETA_KEY", "AGENT_GAMMA_KEY"):
        monkeypatch.setenv(var, Account.create().key.hex())
    env_before = boa.env
    with pytest.raises(SystemExit) as exc:
        deploy.main()
    assert "in-process fallback is local-only" in str(exc.value)
    assert boa.env is env_before
    out = capsys.readouterr()
    text = str(exc.value) + out.out + out.err
    assert "SECRET" not in text
    assert "http://127.0.0.1" in str(exc.value)


def test_deploy_v2_main_refuses_unreachable_rpc_without_leaking(monkeypatch, capsys, tmp_path):
    import json

    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(deploy_v2, "OUT", tmp_path)
    (tmp_path / "84532.json").write_text(json.dumps({"MarketAMM": "0x" + "11" * 20, "MarketFactory": "0x" + "22" * 20}))
    monkeypatch.setenv("CHAIN_ID", "84532")
    monkeypatch.setenv("DEPLOY_RPC_URL", "http://127.0.0.1:9/v2/SECRETPATH?apikey=SECRETQ")
    monkeypatch.setenv("OPERATOR_PRIVATE_KEY", deploy.ANVIL_KEYS["operator"])
    with pytest.raises(SystemExit, match="public anvil key"):
        deploy_v2.main([])
    key = Account.create().key.hex()
    monkeypatch.setenv("OPERATOR_PRIVATE_KEY", key)
    env_before = boa.env
    with pytest.raises(SystemExit) as exc:
        deploy_v2.main(["--legacy-cids", "0x" + "ab" * 32])
    assert "cannot use RPC at http://127.0.0.1" in str(exc.value)
    assert boa.env is env_before
    out = capsys.readouterr()
    text = str(exc.value) + out.out + out.err
    assert "SECRET" not in text and key.removeprefix("0x") not in text
    assert json.loads((tmp_path / "84532.json").read_text())["MarketFactory"] == "0x" + "22" * 20


SECRET_RPC = "http://127.0.0.1:9/v2/SECRETPATH?apikey=SECRETQ"


def _fake_network(monkeypatch, chain=84532):
    """Stub the NetworkEnv setup deploy_v2.main / deploy.main do, on the in-process env."""
    monkeypatch.setattr(boa, "set_network_env", lambda rpc: None)
    monkeypatch.setattr(boa.env, "get_chain_id", lambda: chain, raising=False)
    monkeypatch.setattr(boa.env, "add_account", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(boa.env, "suppress_debug_tt", lambda *a, **k: None, raising=False)


def _cli_env(monkeypatch, tmp_path, chain=84532):
    import json

    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(deploy_v2, "OUT", tmp_path)
    original = {"MarketAMM": "0x" + "11" * 20, "MarketFactory": "0x" + "22" * 20, "chainId": chain}
    (tmp_path / f"{chain}.json").write_text(json.dumps(original))
    monkeypatch.setenv("CHAIN_ID", str(chain))
    monkeypatch.setenv("DEPLOY_RPC_URL", SECRET_RPC)
    key = Account.create().key.hex()
    monkeypatch.setenv("OPERATOR_PRIVATE_KEY", key)
    return original, key


def test_deploy_v2_main_failure_after_setfactory_is_redacted_and_recorded(monkeypatch, capsys, tmp_path):
    import json

    import requests

    original, key = _cli_env(monkeypatch, tmp_path)
    _fake_network(monkeypatch)
    new_amm, new_factory = "0x" + "33" * 20, "0x" + "44" * 20
    seen = {}

    def migrate_v2(*, progress, allow_v2_source, **kwargs):
        seen.update(kwargs, allow_v2_source=allow_v2_source)
        progress.update(
            MarketAMM=new_amm,
            MarketFactory=new_factory,
            deployBlock=99,
            steps=["deploy MarketAMM", "deploy MarketFactory", "amm.setFactory", "oracle.setFactory"],
        )
        raise requests.HTTPError(f"429 Client Error: Too Many Requests for url: {SECRET_RPC}")

    monkeypatch.setattr(deploy_v2, "migrate_v2", migrate_v2)
    assert deploy_v2.main([]) == 1
    out = capsys.readouterr()
    text = out.out + out.err
    assert "SECRET" not in text and key.removeprefix("0x") not in text
    assert "HTTPError" in text and "429" in text and "<rpc>" in text
    assert new_amm in out.err and new_factory in out.err and "Do not re-run blindly" in out.err
    # Base Sepolia defaults: listing opens (ADR-0012, same as deploy_ci.py); the v2-source override stays off.
    assert seen["permissionless"] is True and seen["allow_v2_source"] is False
    assert json.loads((tmp_path / "84532.json").read_text()) == original  # readers keep working
    sidecar = json.loads((tmp_path / "84532.v2-partial.json").read_text())
    assert sidecar["migrationFailed"] is True
    assert "oracle.setFactory" in sidecar["completedSteps"]
    assert (sidecar["MarketAMM"], sidecar["MarketFactory"], sidecar["deployBlock"]) == (new_amm, new_factory, 99)
    assert sidecar["MarketFactoryLegacy"] == original["MarketFactory"]


def test_deploy_v2_main_refusal_before_any_tx_exits_cleanly(monkeypatch, capsys, tmp_path):
    original, _ = _cli_env(monkeypatch, tmp_path)
    _fake_network(monkeypatch)

    def migrate_v2(**kwargs):
        raise ValueError("deployments already record a v2 migration (MarketFactoryLegacy present); refusing to re-run")

    monkeypatch.setattr(deploy_v2, "migrate_v2", migrate_v2)
    assert deploy_v2.main([]) == 1
    out = capsys.readouterr()
    assert "refusing to re-run" in out.err and "Traceback" not in out.err
    assert not (tmp_path / "84532.v2-partial.json").exists()  # nothing landed, nothing to record


def test_default_permissionless_matches_adr_0012():
    import deploy_ci

    assert deploy_v2.default_permissionless(31337) is True
    assert deploy_v2.default_permissionless(deploy_ci.CHAIN_ID) is True  # the Sepolia migration opens listing
    assert deploy_v2.default_permissionless(8453) is False  # any other chain: allowlist-only by default
    assert deploy_v2.default_permissionless(1) is False


@pytest.mark.parametrize(("chain", "argv", "expected"), [(8453, [], False), (8453, ["--permissionless"], True), (84532, ["--no-permissionless"], False)])
def test_deploy_v2_main_permissionless_flag_and_chain_default(monkeypatch, tmp_path, chain, argv, expected):
    _cli_env(monkeypatch, tmp_path, chain=chain)
    _fake_network(monkeypatch, chain=chain)
    seen = {}

    def migrate_v2(**kwargs):
        seen.update(kwargs)
        raise ValueError("stop before any tx")

    monkeypatch.setattr(deploy_v2, "migrate_v2", migrate_v2)
    assert deploy_v2.main(argv) == 1
    assert seen["permissionless"] is expected


def test_deploy_v2_main_allow_v2_source_is_local_only(monkeypatch, tmp_path):
    _cli_env(monkeypatch, tmp_path)
    with pytest.raises(SystemExit, match="local-only"):
        deploy_v2.main(["--allow-v2-source"])


def test_deploy_main_failure_is_redacted_and_lists_deployed(monkeypatch, capsys, tmp_path):
    import requests

    monkeypatch.setattr(deploy, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "OUT", tmp_path)
    monkeypatch.setenv("CHAIN_ID", "84532")
    monkeypatch.setenv("ANVIL_RPC_URL", SECRET_RPC)
    keys = {var: Account.create().key.hex() for var in ("OPERATOR_PRIVATE_KEY", "TREASURY_PRIVATE_KEY", "AGENT_ALPHA_KEY", "AGENT_BETA_KEY", "AGENT_GAMMA_KEY")}
    for var, key in keys.items():
        monkeypatch.setenv(var, key)
    _fake_network(monkeypatch)
    usdc = "0x" + "55" * 20

    def fake_deploy(*args, progress=None, **kwargs):
        progress["MockUSDC"] = usdc
        raise requests.HTTPError(f"503 Server Error: Service Unavailable for url: {SECRET_RPC}")

    monkeypatch.setattr(deploy, "deploy", fake_deploy)
    with pytest.raises(SystemExit) as exc:
        deploy.main()
    assert exc.value.code == 1
    out = capsys.readouterr()
    text = out.out + out.err
    assert "SECRET" not in text and all(k.removeprefix("0x") not in text for k in keys.values())
    assert "HTTPError" in out.err and f"MockUSDC: {usdc}" in out.err
    assert not (tmp_path / "84532.json").exists()
