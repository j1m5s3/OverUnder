"""Async chain access for the relayer.

`ChainClient` is the seam tests replace with a fake. `Web3ChainClient` wraps a
synchronous web3 v7+ client and pushes every call onto a worker thread so the
event loop never blocks on RPC.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal, Protocol

import requests
from web3 import Web3
from web3.exceptions import ContractLogicError, TransactionNotFound
from web3.providers.rpc.utils import REQUEST_RETRY_ALLOWLIST, ExceptionRetryConfiguration

from app.chain_tx import is_canonical
from app.config import get_settings
from app.contract_addresses import load_abi

RpcErrorKind = Literal["nonce_low", "known", "underpriced", "insufficient_funds", "revert", "transient"]

# keccak("OrderFilled(bytes32,bytes32,uint256,uint256,uint256)")
ORDER_FILLED_TOPIC = Web3.to_hex(Web3.keccak(text="OrderFilled(bytes32,bytes32,uint256,uint256,uint256)"))


class ChainClient(Protocol):
    async def pending_nonce(self, addr: str) -> int: ...
    async def latest_nonce(self, addr: str) -> int: ...
    async def latest_block(self) -> dict: ...
    async def max_priority_fee(self) -> int: ...
    async def gas_price(self) -> int: ...
    async def eth_balance(self, addr: str) -> int: ...
    async def estimate_gas(self, tx: dict) -> int: ...
    async def send_raw(self, raw: bytes) -> str: ...
    async def get_receipt(self, tx_hash: str) -> dict | None: ...
    async def get_logs(self, address: str, topics: list, from_block: int) -> list[dict]: ...
    async def usdc_balance(self, addr: str) -> int: ...
    async def usdc_allowance(self, owner: str, spender: str) -> int: ...
    async def ctf_balance(self, addr: str, condition_id: bytes, outcome: int) -> int: ...
    async def ctf_approved(self, owner: str, operator: str) -> bool: ...
    async def ctf_resolved(self, condition_id: bytes) -> bool: ...
    async def ex_nonce(self, maker: str) -> int: ...
    async def ex_filled(self, order_hash: bytes) -> int: ...
    async def ex_cancelled(self, order_hash: bytes) -> bool: ...
    def encode_match(self, taker: tuple, maker: tuple, fill: int, taker_sig: bytes, maker_sig: bytes) -> bytes: ...


def classify_rpc_error(exc: BaseException) -> RpcErrorKind:
    """Map node error text (geth, reth, anvil, Base sequencer) onto the relayer's retry policy."""
    if isinstance(exc, ContractLogicError):
        return "revert"
    msg = str(exc).lower()
    if "nonce too low" in msg or "already been used" in msg or "nonce is too low" in msg or "oldnonce" in msg:
        return "nonce_low"
    if "already known" in msg or "known transaction" in msg or "alreadyknown" in msg:
        return "known"
    if "underpriced" in msg or "fee too low" in msg:
        return "underpriced"
    if "insufficient funds" in msg:
        return "insufficient_funds"
    if "execution reverted" in msg:
        return "revert"
    return "transient"


def _log_dict(lg: Any) -> dict:
    return {
        "address": str(lg["address"]).lower(),
        "topics": [Web3.to_hex(t).lower() for t in lg["topics"]],
        "transactionHash": Web3.to_hex(lg["transactionHash"]).lower() if lg.get("transactionHash") else "",
        "blockNumber": int(lg["blockNumber"]) if lg.get("blockNumber") is not None else None,
    }


def _no_send_retry() -> ExceptionRetryConfiguration:
    """web3's default HTTP retry re-posts eth_sendRawTransaction after a timeout, so one broadcast can
    both land and come back 'nonce too low'. Retry reads only; a send timeout surfaces as 'transient'
    and the worker's write-ahead rebroadcast path owns the retry."""
    return ExceptionRetryConfiguration(
        errors=(ConnectionError, requests.HTTPError, requests.Timeout),
        method_allowlist=[m for m in REQUEST_RETRY_ALLOWLIST if m != "eth_sendRawTransaction"],
    )


def _receipt_dict(r: Any) -> dict:
    logs = []
    for lg in r.get("logs", []) or []:
        logs.append(
            {
                "address": str(lg["address"]).lower(),
                "topics": [Web3.to_hex(t).lower() for t in lg["topics"]],
            }
        )
    return {
        "status": int(r["status"]),
        "blockNumber": int(r["blockNumber"]),
        "transactionHash": Web3.to_hex(r["transactionHash"]).lower(),
        "logs": logs,
    }


class Web3ChainClient:
    def __init__(self, rpc_url: str, exchange: str | None, timeout: float = 15.0):
        self.w3 = Web3(
            Web3.HTTPProvider(
                rpc_url, request_kwargs={"timeout": timeout}, exception_retry_configuration=_no_send_retry()
            )
        )
        self.exchange_address = Web3.to_checksum_address(exchange) if exchange else None
        self._exchange = (
            self.w3.eth.contract(address=self.exchange_address, abi=load_abi("Exchange"))
            if self.exchange_address
            else None
        )
        self._usdc = None
        self._ctf = None

    def _ex(self):
        if self._exchange is None:
            raise RuntimeError("exchange not configured")
        return self._exchange

    def _refs(self):
        # USDC and CTF come from the Exchange itself so they can never drift from config.
        if self._usdc is None or self._ctf is None:
            ex = self._ex()
            self._usdc = self.w3.eth.contract(address=ex.functions.usdc().call(), abi=load_abi("MockUSDC"))
            self._ctf = self.w3.eth.contract(address=ex.functions.ctf().call(), abi=load_abi("ConditionalTokens"))
        return self._usdc, self._ctf

    async def pending_nonce(self, addr: str) -> int:
        return await asyncio.to_thread(
            self.w3.eth.get_transaction_count, Web3.to_checksum_address(addr), "pending"
        )

    async def latest_nonce(self, addr: str) -> int:
        """Mined transaction count: a nonce below it can never be used again."""
        return await asyncio.to_thread(
            self.w3.eth.get_transaction_count, Web3.to_checksum_address(addr), "latest"
        )

    async def latest_block(self) -> dict:
        def _get():
            b = self.w3.eth.get_block("latest")
            base = b.get("baseFeePerGas")
            return {
                "number": int(b["number"]),
                "timestamp": int(b["timestamp"]),
                "baseFeePerGas": int(base) if base is not None else None,
            }

        return await asyncio.to_thread(_get)

    async def max_priority_fee(self) -> int:
        return int(await asyncio.to_thread(lambda: self.w3.eth.max_priority_fee))

    async def gas_price(self) -> int:
        return int(await asyncio.to_thread(lambda: self.w3.eth.gas_price))

    async def eth_balance(self, addr: str) -> int:
        return int(await asyncio.to_thread(self.w3.eth.get_balance, Web3.to_checksum_address(addr)))

    async def estimate_gas(self, tx: dict) -> int:
        return int(await asyncio.to_thread(self.w3.eth.estimate_gas, tx))

    async def send_raw(self, raw: bytes) -> str:
        h = await asyncio.to_thread(self.w3.eth.send_raw_transaction, raw)
        return Web3.to_hex(h).lower()

    async def get_receipt(self, tx_hash: str) -> dict | None:
        """The receipt once its block is canonical; None while there is none or it is only a Flashblocks
        pre-confirmation (zero blockHash, block not sealed), so the worker keeps treating the tx as unmined."""

        def _get():
            try:
                r = self.w3.eth.get_transaction_receipt(tx_hash)
            except TransactionNotFound:
                return None
            if not is_canonical(self.w3, r):
                return None
            return _receipt_dict(r)

        return await asyncio.to_thread(_get)

    async def get_logs(self, address: str, topics: list, from_block: int) -> list[dict]:
        def _get():
            flt = {
                "address": Web3.to_checksum_address(address),
                "topics": topics,
                "fromBlock": int(from_block),
                "toBlock": "latest",
            }
            return [_log_dict(lg) for lg in self.w3.eth.get_logs(flt)]

        return await asyncio.to_thread(_get)

    async def usdc_balance(self, addr: str) -> int:
        def _get():
            usdc, _ = self._refs()
            return int(usdc.functions.balanceOf(Web3.to_checksum_address(addr)).call())

        return await asyncio.to_thread(_get)

    async def usdc_allowance(self, owner: str, spender: str) -> int:
        def _get():
            usdc, _ = self._refs()
            return int(
                usdc.functions.allowance(
                    Web3.to_checksum_address(owner), Web3.to_checksum_address(spender)
                ).call()
            )

        return await asyncio.to_thread(_get)

    async def ctf_balance(self, addr: str, condition_id: bytes, outcome: int) -> int:
        def _get():
            _, ctf = self._refs()
            pid = ctf.functions.positionId(condition_id, outcome).call()
            return int(ctf.functions.balanceOf(Web3.to_checksum_address(addr), pid).call())

        return await asyncio.to_thread(_get)

    async def ctf_approved(self, owner: str, operator: str) -> bool:
        def _get():
            _, ctf = self._refs()
            return bool(
                ctf.functions.isApprovedForAll(
                    Web3.to_checksum_address(owner), Web3.to_checksum_address(operator)
                ).call()
            )

        return await asyncio.to_thread(_get)

    async def ctf_resolved(self, condition_id: bytes) -> bool:
        def _get():
            _, ctf = self._refs()
            return bool(ctf.functions.isResolved(condition_id).call())

        return await asyncio.to_thread(_get)

    async def ex_nonce(self, maker: str) -> int:
        return int(
            await asyncio.to_thread(self._ex().functions.nonces(Web3.to_checksum_address(maker)).call)
        )

    async def ex_filled(self, order_hash: bytes) -> int:
        return int(await asyncio.to_thread(self._ex().functions.filled(order_hash).call))

    async def ex_cancelled(self, order_hash: bytes) -> bool:
        return bool(await asyncio.to_thread(self._ex().functions.cancelled(order_hash).call))

    def encode_match(self, taker: tuple, maker: tuple, fill: int, taker_sig: bytes, maker_sig: bytes) -> bytes:
        data = self._ex().encode_abi("matchOrders", args=[taker, maker, fill, taker_sig, maker_sig])
        return bytes(Web3.to_bytes(hexstr=data))


_CLIENT: Web3ChainClient | None = None
_CLIENT_KEY: tuple | None = None


def get_chain_client() -> ChainClient:
    """Process-wide client for the configured RPC and Exchange (tests patch this)."""
    global _CLIENT, _CLIENT_KEY
    from app.orderbook.eip712 import get_exchange_domain

    s = get_settings()
    dom = get_exchange_domain()
    exchange = dom[0] if dom else None
    key = (s.anvil_rpc_url, exchange, s.relayer_rpc_timeout_seconds)
    if _CLIENT is None or _CLIENT_KEY != key:
        _CLIENT = Web3ChainClient(s.anvil_rpc_url, exchange, timeout=s.relayer_rpc_timeout_seconds)
        _CLIENT_KEY = key
    return _CLIENT
