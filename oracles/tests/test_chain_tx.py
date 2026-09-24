"""chain_tx.py and resolve/chain.py sends on Flashblocks chains (Base pre-confirmation receipts)."""

import copy
import time
from types import SimpleNamespace

import pytest
from eth_account import Account
from web3.exceptions import BlockNotFound, ContractLogicError, TransactionNotFound

import budget
import chain_tx
from resolve import chain as chain_mod
from resolve import fallback as fallback_mod

CID = "0x" + "ab" * 32
ORACLE = "0x" + "99" * 20
WINDOW = 86400
CLOSE = 1
KEYS = {"alpha": "0x" + "01" * 32, "beta": "0x" + "02" * 32, "gamma": "0x" + "03" * 32}
OPERATOR_KEY = "0x" + "04" * 32
ADDRS = {name: Account.from_key(key).address for name, key in KEYS.items()}
EVIDENCE = {"alpha": "0x" + "a1" * 32, "beta": "0x" + "b2" * 32, "gamma": "0x" + "c3" * 32}
ZERO = b"\x00" * 32
SEALED = bytes.fromhex("b1" * 32)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ORACLE_ADDRESS", ORACLE)
    monkeypatch.setenv("CHAIN_ID", "84532")
    monkeypatch.setenv("OPERATOR_PRIVATE_KEY", OPERATOR_KEY)
    budget.clear()
    yield
    budget.clear()


class _Clock:
    """Stands in for chain_tx's time module; `on_sleep` runs once per poll interval."""

    def __init__(self, on_sleep=None):
        self.t = 0.0
        self.sleeps = 0
        self.on_sleep = on_sleep

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps += 1
        self.t += seconds
        if self.on_sleep:
            self.on_sleep()


def _use_clock(monkeypatch, clock: _Clock) -> _Clock:
    monkeypatch.setattr(chain_tx, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    return clock


# --- the helper ------------------------------------------------------------------------------


def _receipt(block_hash, number=100, status=1):
    return {"status": status, "blockNumber": number, "blockHash": block_hash}


def test_is_canonical():
    blocks = {100: SEALED}

    def get_block(n):
        if n not in blocks:
            raise BlockNotFound("not found")
        return {"hash": blocks[n]}

    w3 = SimpleNamespace(eth=SimpleNamespace(get_block=get_block))
    assert chain_tx.is_canonical(w3, _receipt(SEALED))
    assert chain_tx.is_canonical(w3, _receipt("0x" + "b1" * 32))
    assert not chain_tx.is_canonical(w3, _receipt(ZERO))
    assert not chain_tx.is_canonical(w3, _receipt(bytes.fromhex("c2" * 32)))
    assert not chain_tx.is_canonical(w3, _receipt(SEALED, number=101))
    assert not chain_tx.is_canonical(w3, None)


def test_wait_times_out_when_the_block_never_seals(monkeypatch):
    clock = _use_clock(monkeypatch, _Clock())
    eth = SimpleNamespace(
        wait_for_transaction_receipt=lambda h, timeout: _receipt(ZERO),
        get_transaction_receipt=lambda h: _receipt(ZERO),
        get_block=lambda n: (_ for _ in ()).throw(BlockNotFound("not found")),
    )
    with pytest.raises(TimeoutError):
        chain_tx.wait_canonical_receipt(SimpleNamespace(eth=eth), b"\x11" * 32, timeout=3)
    assert clock.t == pytest.approx(3)


# --- resolve/chain.py _send ------------------------------------------------------------------


class _SendW3:
    """One send whose receipt is a pre-confirmation until `seal_after` polls have passed."""

    def __init__(self, seal_after=2):
        self.eth = self
        self.seal_after = seal_after
        self.polls = 0
        self.sealed = False
        self.sent = []
        self.timeouts = []

    gas_price = 1

    def get_transaction_count(self, *_args):
        return 7

    def send_raw_transaction(self, raw):
        self.sent.append(raw)
        return b"\x11" * 32

    def _receipt(self):
        return _receipt(SEALED if self.sealed else ZERO)

    def wait_for_transaction_receipt(self, txh, timeout):
        self.timeouts.append(timeout)
        return self._receipt()  # web3 returns the pre-confirmation at once

    def get_transaction_receipt(self, txh):
        return self._receipt()

    def get_block(self, number):
        if not self.sealed:
            raise BlockNotFound("not found")
        return {"number": number, "hash": SEALED}

    def tick(self):
        self.polls += 1
        self.sealed = self.sealed or self.polls >= self.seal_after


class _SendFn:
    def __init__(self):
        self.calls = []

    def call(self, *args, **kwargs):
        self.calls.append(kwargs)

    def build_transaction(self, tx):
        return {**tx, "to": "0x" + "22" * 20, "data": "0x", "value": 0}


def test_send_waits_for_a_canonical_receipt_and_preflights_at_pending(monkeypatch):
    w3 = _SendW3(seal_after=3)
    clock = _use_clock(monkeypatch, _Clock(on_sleep=w3.tick))
    fn = _SendFn()
    txh = chain_mod._send(w3, Account.from_key(OPERATOR_KEY), fn, 100_000, "submitAttestation")
    assert txh == "0x" + "11" * 32
    assert w3.sealed and clock.sleeps == 3  # returned only once the receipt's block was sealed
    assert w3.timeouts == [chain_mod.RECEIPT_TIMEOUT]
    assert fn.calls == [{"block_identifier": "pending"}]


def test_send_canonical_wait_stays_inside_the_tick_budget(monkeypatch):
    w3 = _SendW3(seal_after=10**9)  # never seals
    budget.start(60)
    left = budget.total_remaining()
    clock = _use_clock(monkeypatch, _Clock(on_sleep=w3.tick))
    with pytest.raises(TimeoutError):
        chain_mod._send(w3, Account.from_key(OPERATOR_KEY), _SendFn(), 100_000, "submitConsensus")
    wait = w3.timeouts[0]
    assert 45 < wait <= left - budget.SEND_MARGIN_SECONDS
    assert clock.t == pytest.approx(wait)  # receipt + canonical polling never past min(180, left - 10)


def test_send_reverted_canonical_receipt_raises(monkeypatch):
    w3 = _SendW3(seal_after=1)
    w3._receipt = lambda: _receipt(SEALED if w3.sealed else ZERO, status=0)
    _use_clock(monkeypatch, _Clock(on_sleep=w3.tick))
    with pytest.raises(RuntimeError, match="resolveFallback transaction failed"):
        chain_mod._send(w3, Account.from_key(OPERATOR_KEY), _SendFn(), 300_000, "resolveFallback")


# --- ADR-0002 fallback in one tick on a Flashblocks chain ------------------------------------


class _Fn:
    def __init__(self, fake, name, args):
        self.fake, self.name, self.args = fake, name, args

    def call(self, *_a, block_identifier="latest"):
        return self.fake.eval(self.name, self.args, block_identifier)

    def build_transaction(self, tx):
        self.fake.built = (self.name, self.args)
        return {**tx, "to": "0x" + "99" * 20, "data": "0x", "value": 0}


class FlashOracle:
    """ConsensusOracle on Base Sepolia (also the fake w3, with eth = self).

    Every tx executes at once against 'pending'; 'latest' (the eth_call default) reads sealed blocks
    only. A receipt is a Flashblocks pre-confirmation (blockHash zero, block not found) until its
    block seals; the previous tx's block is sealed when the next tx arrives, and one more block per
    poll interval. Reverts use ConsensusOracle's reason strings.
    """

    BASE_BLOCK = 5_000
    gas_price = 1
    agents_list = [ADDRS["alpha"], ADDRS["beta"], ADDRS["gamma"]]

    def __init__(self):
        self.eth = self
        self.functions = self
        self.address = ORACLE
        self.states = [{"submitted": {}, "resolved": False}]
        self.sealed = 0
        self.txs = []
        self.built = None
        self.reads = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args: _Fn(self, name, args)

    def _state(self, block):
        return self.states[-1] if block == "pending" else self.states[self.sealed]

    def eval(self, name, args, block):
        self.reads.append((name, block))
        st = self._state(block)
        if name == "agents":
            return self.agents_list[args[0]]
        if name == "agentSubmitted":
            return args[1] in st["submitted"]
        if name == "agentOutcome":
            return st["submitted"].get(args[1], 0)
        if name == "resolved":
            return st["resolved"]
        if name == "closeTime":
            return CLOSE
        if name == "WINDOW":
            return WINDOW
        if name == "voteWeight":
            return 0
        if name == "submitAttestation":
            if ADDRS[args[4].rstrip(b"\x00").decode()] in st["submitted"]:
                raise ContractLogicError("execution reverted: dup attestation")
            return None
        if name == "resolveFallback":
            if st["resolved"]:
                raise ContractLogicError("execution reverted")
            outcomes = list(st["submitted"].values())
            if max(outcomes.count(0), outcomes.count(1)) < 2:
                raise ContractLogicError("execution reverted: no agent majority")
            return None
        raise AssertionError(f"unexpected call {name}")

    # w3.eth
    def get_transaction_count(self, *_args):
        return len(self.txs)

    def send_raw_transaction(self, _raw):
        (name, args), self.built = self.built, None
        st = copy.deepcopy(self.states[-1])
        if name == "submitAttestation":
            st["submitted"][ADDRS[args[4].rstrip(b"\x00").decode()]] = args[1]
        elif name == "resolveFallback":
            st["resolved"] = True
        else:
            raise AssertionError(f"unexpected send {name}")
        self.states.append(st)
        self.txs.append(name)
        self.sealed = max(self.sealed, len(self.txs) - 1)
        return len(self.txs).to_bytes(32, "big")

    def seal_one(self):
        self.sealed = min(len(self.txs), self.sealed + 1)

    @staticmethod
    def _hash(i):
        return b"\xb1" + i.to_bytes(31, "big")

    def get_transaction_receipt(self, txh):
        i = int.from_bytes(bytes(txh), "big")
        if i > len(self.txs):
            raise TransactionNotFound("unknown")
        return {"status": 1, "blockNumber": self.BASE_BLOCK + i, "blockHash": self._hash(i) if i <= self.sealed else ZERO}

    def wait_for_transaction_receipt(self, txh, timeout):
        return self.get_transaction_receipt(txh)

    def get_block(self, number):
        if number == "latest":
            return {"number": self.BASE_BLOCK + self.sealed, "timestamp": CLOSE + WINDOW + 60}
        i = int(number) - self.BASE_BLOCK
        if i < 1 or i > self.sealed:
            raise BlockNotFound(f"block {number} not found")
        return {"number": int(number), "hash": self._hash(i)}


class _Coord:
    def agent_address(self, name):
        return ADDRS[name]

    def sign_one(self, name, oracle, chain_id, condition_id, evidence_hash, outcome, deadline):
        return name.encode().ljust(65, b"\x00")  # the fake oracle recovers the agent from this


def _research():
    return {
        "reports": [
            {"agent": name, "outcome": outcome, "evidenceHash": EVIDENCE[name]}
            for name, outcome in (("alpha", 0), ("beta", 0), ("gamma", 1))
        ]
    }


def _fallback(fake, policy="attest"):
    return fallback_mod.run_fallback(
        condition_id=CID, derived=0, research=_research(), coord=_Coord(), chain_api=chain_mod,
        policy=policy, oracle=ORACLE, chain_id=84532,
    )


@pytest.fixture
def oracle(monkeypatch):
    fake = FlashOracle()
    monkeypatch.setattr(chain_mod, "_contract", lambda w3=None: (fake, fake))
    return fake


def test_fallback_pre_fix_flow_misses_the_majority_it_just_attested(oracle, monkeypatch):
    """The pre-fix flow (pre-confirmation receipts taken as final, reads at 'latest') attests alpha and
    beta, then preflights resolveFallback against a sealed state that holds only alpha: 'no agent
    majority', so the market is not resolved this tick (under arbitrate it would even be arbitrated)."""
    monkeypatch.setattr(chain_mod, "PENDING", "latest")
    monkeypatch.setattr(chain_mod, "wait_canonical_receipt", lambda w3, h, t: w3.eth.wait_for_transaction_receipt(h, timeout=t))
    result = _fallback(oracle)
    assert result["attested"] == ["alpha", "beta"]
    assert result["submitted"] is False and result["reason"] == "no agent majority"
    assert oracle.txs == ["submitAttestation", "submitAttestation"]


def test_fallback_attests_then_resolves_in_one_tick(oracle, monkeypatch):
    _use_clock(monkeypatch, _Clock(on_sleep=oracle.seal_one))
    result = _fallback(oracle)
    assert result["attested"] == ["alpha", "beta"] and result["supporters"] == ["alpha", "beta"]
    assert result["submitted"] is True and result["path"] == "fallback"
    assert oracle.txs == ["submitAttestation", "submitAttestation", "resolveFallback"]
    assert oracle.sealed == len(oracle.txs)  # each send returned only at a canonical receipt
    # Slot reads, send preflights and preflight_fallback all ran at the pending block.
    assert {block for _name, block in oracle.reads} == {"pending"}


def test_fallback_pending_reads_alone_see_this_ticks_attestations(oracle, monkeypatch):
    """Even if a node hands back receipts before sealing, the pending-block preflight sees the tick's own
    attestations, so resolveFallback still goes out."""
    monkeypatch.setattr(chain_mod, "wait_canonical_receipt", lambda w3, h, t: w3.eth.wait_for_transaction_receipt(h, timeout=t))
    result = _fallback(oracle)
    assert result["submitted"] is True and result["path"] == "fallback"
    assert oracle.txs == ["submitAttestation", "submitAttestation", "resolveFallback"]


def test_raced_attestation_is_seen_at_pending(oracle, monkeypatch):
    """Another tick's alpha attestation is only pre-confirmed: our preflight (pending) reverts 'dup
    attestation' and the re-read slot (pending) shows it submitted, so it counts instead of failing."""
    _use_clock(monkeypatch, _Clock(on_sleep=oracle.seal_one))
    real_state = chain_mod.fallback_state
    first = {"done": False}

    def state_then_race(cid):
        st = real_state(cid)
        if not first["done"]:
            first["done"] = True
            # The concurrent tick's attestation lands (pre-confirmed) after our first read.
            oracle.built = ("submitAttestation", (b"", 0, b"", 0, b"alpha".ljust(65, b"\x00")))
            oracle.send_raw_transaction(b"")
        return st

    monkeypatch.setattr(chain_mod, "fallback_state", state_then_race)
    result = _fallback(oracle)
    assert "alpha: attested concurrently" in result["notes"]
    assert result["attested"] == ["beta"] and result["submitted"] is True
