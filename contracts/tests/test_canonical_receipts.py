import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "script"
sys.path.insert(0, str(SCRIPT))
import deploy  # noqa: E402

TX = "0x" + "ab" * 32
SEALED = "0x" + "cd" * 32
PRECONF = {"status": "0x1", "blockHash": "0x" + "00" * 32, "blockNumber": "0x2d0b9d3", "contractAddress": None}
CANONICAL = {"status": "0x1", "blockHash": SEALED, "blockNumber": "0x2d0b9d3", "contractAddress": "0x" + "11" * 20}


class FakeRPC:
    """First receipt is a Flashblocks pre-confirmation; later polls return the sealed receipt."""

    def __init__(self, receipts, block_hash=SEALED):
        self.receipts = list(receipts)
        self.block_hash = block_hash
        self.calls = []

    def wait_for_tx_receipt(self, tx_hash, timeout, poll_latency=0.25):
        self.calls.append("wait")
        return self.receipts.pop(0)

    def fetch_uncached(self, method, params):
        self.calls.append(method)
        if method == "eth_getTransactionReceipt":
            return self.receipts.pop(0) if self.receipts else None
        if method == "eth_getBlockByNumber":
            return {"hash": self.block_hash}
        raise AssertionError(method)


def _env(rpc):
    return SimpleNamespace(_rpc=rpc)


def test_waits_past_preconfirmation_receipt(monkeypatch):
    monkeypatch.setattr(deploy.time, "sleep", lambda _s: None)
    rpc = FakeRPC([PRECONF, PRECONF, CANONICAL])
    deploy.require_canonical_receipts(_env(rpc))
    receipt = rpc.wait_for_tx_receipt(TX, 30)
    assert receipt["blockHash"] == SEALED
    assert receipt["contractAddress"] == CANONICAL["contractAddress"]
    assert rpc.calls.count("eth_getTransactionReceipt") == 2


def test_canonical_receipt_returns_immediately():
    rpc = FakeRPC([CANONICAL])
    deploy.require_canonical_receipts(_env(rpc))
    assert rpc.wait_for_tx_receipt(TX, 30) is CANONICAL
    assert "eth_getTransactionReceipt" not in rpc.calls


def test_reorged_block_is_not_canonical(monkeypatch):
    monkeypatch.setattr(deploy.time, "sleep", lambda _s: None)
    clock = iter(range(0, 10_000, 50))
    monkeypatch.setattr(deploy.time, "time", lambda: next(clock))
    rpc = FakeRPC([CANONICAL] + [CANONICAL] * 20, block_hash="0x" + "ee" * 32)
    deploy.require_canonical_receipts(_env(rpc), timeout=120)
    with pytest.raises(ValueError, match="canonical receipt"):
        rpc.wait_for_tx_receipt(TX, 30)


def test_transient_rpc_error_is_retried(monkeypatch):
    monkeypatch.setattr(deploy.time, "sleep", lambda _s: None)

    class FlakyRPC(FakeRPC):
        def __init__(self):
            super().__init__([PRECONF, CANONICAL])
            self.failed = False

        def fetch_uncached(self, method, params):
            if method == "eth_getTransactionReceipt" and not self.failed:
                self.failed = True
                raise RuntimeError("429 Too Many Requests")
            return super().fetch_uncached(method, params)

    rpc = FlakyRPC()
    deploy.require_canonical_receipts(_env(rpc))
    assert rpc.wait_for_tx_receipt(TX, 30)["blockHash"] == SEALED
    assert rpc.failed


def test_titanoboa_still_exposes_the_hooked_rpc():
    # The guard silently no-ops if titanoboa renames these; fail loudly on an upgrade instead.
    from boa.network import NetworkEnv
    from boa.rpc import EthereumRPC

    assert hasattr(EthereumRPC, "wait_for_tx_receipt")
    assert hasattr(EthereumRPC, "fetch_uncached")
    assert "_rpc" in NetworkEnv.__init__.__code__.co_names


def test_install_is_idempotent():
    rpc = FakeRPC([CANONICAL])
    env = _env(rpc)
    deploy.require_canonical_receipts(env)
    wrapped = rpc.wait_for_tx_receipt
    deploy.require_canonical_receipts(env)
    assert rpc.wait_for_tx_receipt is wrapped


def test_broadcast_connect_installs_guard(monkeypatch):
    import deploy_ci

    installed = []

    class FakeEth:
        chain_id = deploy_ci.CHAIN_ID

        def get_balance(self, _addr):
            return 10**18

    class FakeWeb3:
        HTTPProvider = staticmethod(lambda *a, **k: None)

        def __init__(self, _provider):
            self.eth = FakeEth()

    import web3

    monkeypatch.setattr(web3, "Web3", FakeWeb3)
    monkeypatch.setattr(deploy_ci.boa, "set_network_env", lambda _rpc: None)
    monkeypatch.setattr(deploy_ci.base, "require_canonical_receipts", lambda env: installed.append(env))
    fake_env = SimpleNamespace(add_account=lambda *a, **k: None, suppress_debug_tt=lambda _on: None)
    monkeypatch.setattr(deploy_ci.boa, "env", fake_env)
    deploy_ci.connect("https://rpc.invalid", True, False, SimpleNamespace(address="0x" + "22" * 20))
    assert installed == [fake_env]
