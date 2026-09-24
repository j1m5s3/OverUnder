"""app/chain_tx.py and the relayer's receipt lookup on Flashblocks chains (Base pre-confirmations)."""

import time
from types import SimpleNamespace

import pytest
from web3.exceptions import BlockNotFound, TimeExhausted, TransactionNotFound

from app import chain_tx

TX = b"\x11" * 32
ZERO = b"\x00" * 32
SEALED = bytes.fromhex("b1" * 32)
REORGED = bytes.fromhex("c2" * 32)


def _receipt(block_hash: bytes, *, number: int = 100, status: int = 1) -> dict:
    return {"transactionHash": TX, "status": status, "blockNumber": number, "blockHash": block_hash, "logs": []}


class _Eth:
    """Receipts come from a script (one entry per lookup, the last one repeats); blocks from `blocks`."""

    def __init__(self, receipts, blocks=None):
        self.receipts = list(receipts)
        self.blocks = dict(blocks or {})
        self.lookups = 0

    def _next(self):
        self.lookups += 1
        r = self.receipts[0] if len(self.receipts) == 1 else self.receipts.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def wait_for_transaction_receipt(self, tx_hash, timeout):
        return self._next()

    def get_transaction_receipt(self, tx_hash):
        return self._next()

    def get_block(self, number):
        block = self.blocks.get(int(number))
        if block is None:
            raise BlockNotFound(f"block {number} not found")
        return {"number": int(number), "hash": block}


class _Clock:
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


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(chain_tx, "time", SimpleNamespace(monotonic=c.monotonic, sleep=c.sleep))
    return c


def test_preconfirmation_and_canonical_detection():
    eth = _Eth([_receipt(SEALED)], {100: SEALED})
    w3 = SimpleNamespace(eth=eth)
    assert chain_tx.is_preconfirmation(_receipt(ZERO))
    assert chain_tx.is_preconfirmation({**_receipt(SEALED), "blockHash": None})
    assert chain_tx.is_preconfirmation({**_receipt(SEALED), "blockNumber": None})
    assert not chain_tx.is_preconfirmation(_receipt(SEALED))
    assert not chain_tx.is_preconfirmation(_receipt("0x" + "b1" * 32))
    assert chain_tx.is_canonical(w3, _receipt(SEALED))
    assert chain_tx.is_canonical(w3, _receipt("0x" + "B1" * 32))
    assert not chain_tx.is_canonical(w3, _receipt(ZERO))
    assert not chain_tx.is_canonical(w3, _receipt(REORGED))  # a different block holds that height
    assert not chain_tx.is_canonical(w3, _receipt(SEALED, number=101))  # block not sealed yet
    assert not chain_tx.is_canonical(w3, None)


def test_wait_returns_only_once_the_receipt_block_is_canonical(clock):
    eth = _Eth([_receipt(ZERO), _receipt(ZERO), _receipt(SEALED)], {})
    clock.on_sleep = lambda: eth.blocks.setdefault(100, SEALED) if eth.lookups >= 2 else None
    receipt = chain_tx.wait_canonical_receipt(SimpleNamespace(eth=eth), TX, timeout=60)
    assert receipt["blockHash"] == SEALED
    assert eth.lookups == 3 and clock.sleeps == 2


def test_wait_polls_past_a_reorged_or_dropped_receipt(clock):
    eth = _Eth([_receipt(REORGED), TransactionNotFound("gone"), _receipt(SEALED, number=101)], {100: SEALED, 101: SEALED})
    receipt = chain_tx.wait_canonical_receipt(SimpleNamespace(eth=eth), TX, timeout=60)
    assert receipt["blockNumber"] == 101 and eth.lookups == 3


def test_wait_times_out_on_a_receipt_that_never_seals(clock):
    eth = _Eth([_receipt(ZERO)], {})
    with pytest.raises(TimeoutError):
        chain_tx.wait_canonical_receipt(SimpleNamespace(eth=eth), TX, timeout=2)
    assert clock.t == pytest.approx(2)  # bounded by the timeout, never past it


def test_wait_propagates_web3_timeout_when_no_receipt_appears(clock):
    eth = _Eth([TimeExhausted("not in the chain")])
    with pytest.raises(TimeExhausted):
        chain_tx.wait_canonical_receipt(SimpleNamespace(eth=eth), TX, timeout=2)


def test_wait_is_bounded_by_real_time_too():
    eth = _Eth([_receipt(ZERO)], {})
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        chain_tx.wait_canonical_receipt(SimpleNamespace(eth=eth), TX, timeout=0.3, poll=0.05)
    assert time.monotonic() - started < 2


# --- relayer: a pre-confirmation is not a mined receipt ---------------------------------------


def _client(eth):
    from app.relayer.chain import Web3ChainClient

    c = Web3ChainClient("http://127.0.0.1:1", None, timeout=1)
    c.w3 = SimpleNamespace(eth=eth)
    return c


async def test_relayer_get_receipt_is_none_for_a_preconfirmation():
    assert await _client(_Eth([_receipt(ZERO)], {100: SEALED})).get_receipt("0x" + "11" * 32) is None


async def test_relayer_get_receipt_is_none_until_the_block_is_sealed_and_canonical():
    eth = _Eth([_receipt(SEALED)], {})
    c = _client(eth)
    assert await c.get_receipt("0x" + "11" * 32) is None  # eth_getBlockByNumber: not found yet
    eth.blocks[100] = REORGED
    assert await c.get_receipt("0x" + "11" * 32) is None  # another block won that height
    eth.blocks[100] = SEALED
    r = await c.get_receipt("0x" + "11" * 32)
    assert r == {"status": 1, "blockNumber": 100, "transactionHash": "0x" + "11" * 32, "logs": []}


async def test_relayer_get_receipt_is_none_for_an_unknown_tx():
    assert await _client(_Eth([TransactionNotFound("unknown")])).get_receipt("0x" + "11" * 32) is None
