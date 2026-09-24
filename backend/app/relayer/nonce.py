"""Process-wide nonce allocation for the relayer EOA.

Every sender that signs with RELAYER_PRIVATE_KEY (the worker and the
emissions route) must hold `NonceManager.lock` across reserve -> sign ->
broadcast so two coroutines can never sign the same nonce.
"""

from __future__ import annotations

import asyncio

from app.relayer.chain import ChainClient


class NonceManager:
    def __init__(self, chain: ChainClient, address: str):
        self.chain = chain
        self.address = address
        self.lock = asyncio.Lock()
        self._next: int | None = None
        self._loop = asyncio.get_running_loop()

    async def reserve(self, floor: int | None = None) -> int:
        """Next nonce to sign with. Callers hold `lock`.

        `floor` is one past the highest nonce held by an in-flight job, so a
        write-ahead tx that has not reached the mempool yet (crash between
        commit and broadcast) is never re-issued to another job.
        """
        if self._next is None:
            self._next = await self.chain.pending_nonce(self.address)
        if floor is not None and floor > self._next:
            self._next = floor
        n = self._next
        self._next += 1
        return n

    async def resync(self) -> int:
        """Re-read the node's pending count, e.g. after 'nonce too low'."""
        self._next = await self.chain.pending_nonce(self.address)
        return self._next

    def release(self, n: int) -> None:
        """Hand back `n` when the node definitively rejected a tx signed with it."""
        if self._next is not None and n == self._next - 1:
            self._next = n

    def invalidate(self) -> None:
        """Forget the cache so the next reserve() re-reads the node (e.g. on gaining leadership)."""
        self._next = None

    def peek(self) -> int | None:
        return self._next


_REGISTRY: dict[str, NonceManager] = {}


def get_nonce_manager(chain: ChainClient, address: str) -> NonceManager:
    """One manager per relayer address per event loop."""
    key = address.lower()
    loop = asyncio.get_running_loop()
    nm = _REGISTRY.get(key)
    if nm is None or nm._loop is not loop:
        nm = NonceManager(chain, address)
        _REGISTRY[key] = nm
    return nm


def peek_nonce_manager(address: str) -> NonceManager | None:
    return _REGISTRY.get(address.lower())
