"""Canonical receipts on Flashblocks chains (Base, Base Sepolia).

Base RPCs serve Flashblocks pre-confirmation receipts: blockHash is all zeros and
the block is not sealed yet, while eth_call at 'latest' still reads the previous
sealed block. web3's wait_for_transaction_receipt returns such a receipt at once,
so a send whose caller then reads state (or treats the receipt as final) must wait
until the receipt's block is the canonical block at its height.

Reads that decide whether to send a follow-up tx in the same tick use
block_identifier="pending" (resolve/chain.py). Mirrors backend/app/chain_tx.py.
"""

from __future__ import annotations

import time
from typing import Any

from web3.exceptions import BlockNotFound, TransactionNotFound

ZERO_HASH = b"\x00" * 32
POLL_SECONDS = 0.25


def _hash_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, str):
        raw = value[2:] if value[:2] in ("0x", "0X") else value
        try:
            return bytes.fromhex(raw)
        except ValueError:
            return b""
    try:
        return bytes(value)
    except TypeError:
        return b""


def is_preconfirmation(receipt: Any) -> bool:
    """A Flashblocks pre-confirmation (or otherwise unsealed) receipt: no real block hash yet."""
    block_hash = _hash_bytes(receipt.get("blockHash"))
    return len(block_hash) != 32 or block_hash == ZERO_HASH or receipt.get("blockNumber") is None


def is_canonical(w3: Any, receipt: Any) -> bool:
    """True when the receipt's block is sealed and is the canonical block at its number."""
    if receipt is None or is_preconfirmation(receipt):
        return False
    try:
        block = w3.eth.get_block(int(receipt["blockNumber"]))
    except BlockNotFound:
        return False
    if block is None:
        return False
    return _hash_bytes(block.get("hash")) == _hash_bytes(receipt.get("blockHash"))


def wait_canonical_receipt(w3: Any, tx_hash: Any, timeout: float, poll: float = POLL_SECONDS) -> Any:
    """Wait (at most `timeout` seconds in total) for the tx's receipt in a canonical block.

    web3's TimeExhausted propagates when no receipt appears at all; TimeoutError is raised
    when one appears but its block never becomes canonical within the timeout.
    """
    deadline = time.monotonic() + timeout
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)
    while not is_canonical(w3, receipt):
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError(f"transaction receipt not in a canonical block after {timeout:g}s")
        time.sleep(min(poll, left))
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            receipt = None  # a pre-confirmation can be dropped; keep polling until the deadline
    return receipt
