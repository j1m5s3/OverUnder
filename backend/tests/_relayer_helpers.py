"""Shared fakes for the OU-T003 relayer tests (imported by module name, not a conftest)."""

from __future__ import annotations

import os
import secrets
import time
from contextlib import ExitStack, contextmanager
from typing import Any
from unittest.mock import patch

from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from eth_account.typed_transactions import TypedTransaction
from eth_utils import keccak, to_checksum_address
from hexbytes import HexBytes
from web3 import Web3

from app.config import Settings
from app.contract_addresses import load_abi
from app.orderbook.eip712 import OrderFields, order_hash_hex
from app.relayer.chain import ORDER_FILLED_TOPIC

EXCHANGE = "0x" + "02" * 20
CHAIN = 31337

SETTINGS_MODULES = (
    "app.orderbook.router",
    "app.orderbook.matcher",
    "app.relayer.router",
    "app.emissions.router",
    "app.relayer.redact",
)
DOMAIN_MODULES = (
    "app.orderbook.eip712",
    "app.orderbook.router",
    "app.relayer.worker",
)
CHAIN_MODULES = (
    "app.relayer.chain",
    "app.orderbook.router",
    "app.relayer.router",
    "app.relayer.worker",
)


def make_settings(**kw: Any) -> Settings:
    """Settings that ignore the repo .env so a local RELAYER_PRIVATE_KEY never leaks in."""
    base: dict[str, Any] = {"relayer_enabled": False, "relayer_worker_enabled": False}
    base.update(kw)
    return Settings(_env_file=None, **base)


@contextmanager
def relayer_env(settings: Settings, chain: Any, domain: tuple[str, int] | None = (EXCHANGE, CHAIN)):
    """Patch get_settings / get_exchange_domain / get_chain_client everywhere the relayer reads them."""
    with ExitStack() as st:
        for m in SETTINGS_MODULES:
            st.enter_context(patch(f"{m}.get_settings", return_value=settings))
        for m in DOMAIN_MODULES:
            st.enter_context(patch(f"{m}.get_exchange_domain", return_value=domain))
        for m in CHAIN_MODULES:
            st.enter_context(patch(f"{m}.get_chain_client", return_value=chain))
        yield


def rand_cid() -> str:
    return "0x" + secrets.token_hex(32)


def sign_order(key: Any, exchange: str, chain_id: int, fields: dict) -> str:
    """Independent EIP-712 signer (eth_account typed data), copied from contracts/tests/eip712.py."""
    msg = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Order": [
                {"name": "maker", "type": "address"},
                {"name": "isBuy", "type": "bool"},
                {"name": "conditionId", "type": "bytes32"},
                {"name": "outcome", "type": "uint8"},
                {"name": "price", "type": "uint256"},
                {"name": "amount", "type": "uint256"},
                {"name": "salt", "type": "uint256"},
                {"name": "nonce", "type": "uint256"},
                {"name": "expiry", "type": "uint256"},
            ],
        },
        "primaryType": "Order",
        "domain": {
            "name": "OverUnder Exchange",
            "version": "1",
            "chainId": chain_id,
            "verifyingContract": to_checksum_address(exchange),
        },
        "message": fields,
    }
    signed = Account.sign_message(encode_typed_data(full_message=msg), key)
    return "0x" + bytes(signed.signature).hex()


def order_body(
    acct: Any,
    *,
    cid: str,
    is_buy: bool,
    price: int = 500_000,
    amount: int = 10_000_000,
    outcome: int = 0,
    salt: int | None = None,
    nonce: int = 0,
    expiry: int | None = None,
    signer: Any = None,
    exchange: str = EXCHANGE,
    chain_id: int = CHAIN,
) -> dict:
    """A POST /orders body with a real orderHash and EIP-712 signature."""
    salt = secrets.randbits(128) if salt is None else salt
    expiry = int(time.time()) + 3600 if expiry is None else expiry
    fields = {
        "maker": acct.address,
        "isBuy": is_buy,
        "conditionId": bytes.fromhex(cid[2:]),
        "outcome": outcome,
        "price": price,
        "amount": amount,
        "salt": salt,
        "nonce": nonce,
        "expiry": expiry,
    }
    of = OrderFields(
        maker=acct.address, is_buy=is_buy, condition_id=fields["conditionId"], outcome=outcome,
        price=price, amount=amount, salt=salt, nonce=nonce, expiry=expiry,
    )
    return {
        "maker": acct.address,
        "isBuy": is_buy,
        "conditionId": cid,
        "outcome": outcome,
        "price": price,
        "amount": amount,
        "salt": salt,
        "nonce": nonce,
        "expiry": expiry,
        "signature": sign_order((signer or acct).key, exchange, chain_id, fields),
        "orderHash": order_hash_hex(of, exchange, chain_id),
    }


async def siwe_login(client: Any, acct: Any) -> str:
    n = await client.get(f"/api/v1/auth/nonce/{acct.address}")
    nonce = n.json()["nonce"]
    message = f"Sign in to OverUnder\n\nAddress: {acct.address}\nNonce: {nonce}"
    signed = acct.sign_message(encode_defunct(text=message))
    r = await client.post(
        "/api/v1/auth/siwe",
        json={"address": acct.address, "signature": "0x" + bytes(signed.signature).hex(), "message": message},
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


def order_filled_log(taker_hash: str, maker_hash: str, exchange: str = EXCHANGE) -> dict:
    return {"address": exchange.lower(), "topics": [ORDER_FILLED_TOPIC.lower(), taker_hash.lower(), maker_hash.lower()]}


class FakeChain:
    """In-memory ChainClient: balances, approvals, Exchange state, a mempool counter and receipts."""

    def __init__(self, now: int | None = None):
        self.now = int(time.time()) if now is None else now
        self.block_number = 100
        self.usdc: dict[str, int] = {}
        self.allowances: dict[tuple[str, str], int] = {}
        self.ctf: dict[tuple[str, str, int], int] = {}
        self.approvals: set[tuple[str, str]] = set()
        self.resolved: set[str] = set()
        self.ex_nonces: dict[str, int] = {}
        self.filled: dict[str, int] = {}
        self.cancelled: set[str] = set()
        self.base_fee: int | None = 100_000_000
        self.tip = 1_000_000
        self.legacy_gas_price = 200_000_000
        self.pending = 7
        # Mined ("latest") nonce count; None means "same as pending".
        self.latest: int | None = None
        # OrderFilled-style logs visible to get_logs (mine(..., logs=...) appends here too).
        self.logs: list[dict] = []
        self.log_calls: list[dict] = []
        self.estimate: int | BaseException = 200_000
        self.balance = 10**18
        self.sent: list[dict] = []
        self.send_errors: list[BaseException] = []
        self.receipts: dict[str, dict] = {}
        self.view_error: BaseException | None = None
        self.estimate_calls: list[dict] = []
        self._contract = Web3().eth.contract(abi=load_abi("Exchange"))

    # -- setup helpers
    def fund_buyer(self, addr: str, amount: int, exchange: str = EXCHANGE) -> None:
        self.usdc[addr.lower()] = amount
        self.allowances[(addr.lower(), exchange.lower())] = amount

    def fund_seller(self, addr: str, cid: str, outcome: int, amount: int, exchange: str = EXCHANGE) -> None:
        self.ctf[(addr.lower(), cid.lower(), outcome)] = amount
        self.approvals.add((addr.lower(), exchange.lower()))

    def mine(self, tx_hash: str, status: int = 1, logs: list | None = None, block: int | None = None) -> None:
        r = {"status": status, "blockNumber": block or self.block_number, "transactionHash": tx_hash.lower()}
        if logs is not None:
            r["logs"] = logs
            for lg in logs:
                self.logs.append({**lg, "transactionHash": tx_hash.lower(), "blockNumber": r["blockNumber"]})
        self.receipts[tx_hash.lower()] = r

    def _view(self) -> None:
        if self.view_error is not None:
            raise self.view_error

    # -- ChainClient
    async def pending_nonce(self, addr: str) -> int:
        return self.pending

    async def latest_nonce(self, addr: str) -> int:
        return self.pending if self.latest is None else self.latest

    async def latest_block(self) -> dict:
        return {"number": self.block_number, "timestamp": self.now, "baseFeePerGas": self.base_fee}

    async def max_priority_fee(self) -> int:
        return self.tip

    async def gas_price(self) -> int:
        return self.legacy_gas_price

    async def eth_balance(self, addr: str) -> int:
        return self.balance

    async def estimate_gas(self, tx: dict) -> int:
        self.estimate_calls.append(tx)
        if isinstance(self.estimate, BaseException):
            raise self.estimate
        return self.estimate

    async def send_raw(self, raw: bytes) -> str:
        if self.send_errors:
            raise self.send_errors.pop(0)
        h = "0x" + keccak(raw).hex()
        if any(s["hash"] == h for s in self.sent):
            raise ValueError("already known")
        tx = TypedTransaction.from_bytes(HexBytes(raw)).as_dict() if raw[0] < 0x80 else {"legacy": True}
        tx["hash"] = h
        tx["raw"] = "0x" + bytes(raw).hex()
        self.sent.append(tx)
        if "nonce" in tx:
            self.pending = max(self.pending, int(tx["nonce"]) + 1)
        return h

    async def get_receipt(self, tx_hash: str) -> dict | None:
        return self.receipts.get(tx_hash.lower())

    async def get_logs(self, address: str, topics: list, from_block: int) -> list[dict]:
        self.log_calls.append({"address": address, "topics": topics, "from_block": from_block})
        out = []
        for lg in self.logs:
            if str(lg.get("address", "")).lower() != address.lower():
                continue
            if int(lg.get("blockNumber") or 0) < from_block:
                continue
            lt = [t.lower() for t in lg.get("topics", [])]
            if all(t is None or (i < len(lt) and lt[i] == t.lower()) for i, t in enumerate(topics)):
                out.append(lg)
        return out

    async def usdc_balance(self, addr: str) -> int:
        self._view()
        return self.usdc.get(addr.lower(), 0)

    async def usdc_allowance(self, owner: str, spender: str) -> int:
        self._view()
        return self.allowances.get((owner.lower(), spender.lower()), 0)

    async def ctf_balance(self, addr: str, condition_id: bytes, outcome: int) -> int:
        self._view()
        return self.ctf.get((addr.lower(), "0x" + condition_id.hex(), outcome), 0)

    async def ctf_approved(self, owner: str, operator: str) -> bool:
        self._view()
        return (owner.lower(), operator.lower()) in self.approvals

    async def ctf_resolved(self, condition_id: bytes) -> bool:
        self._view()
        return "0x" + condition_id.hex() in self.resolved

    async def ex_nonce(self, maker: str) -> int:
        self._view()
        return self.ex_nonces.get(maker.lower(), 0)

    async def ex_filled(self, order_hash: bytes) -> int:
        self._view()
        return self.filled.get("0x" + order_hash.hex(), 0)

    async def ex_cancelled(self, order_hash: bytes) -> bool:
        self._view()
        return "0x" + order_hash.hex() in self.cancelled

    def encode_match(self, taker: tuple, maker: tuple, fill: int, taker_sig: bytes, maker_sig: bytes) -> bytes:
        data = self._contract.encode_abi("matchOrders", args=[taker, maker, fill, taker_sig, maker_sig])
        return bytes.fromhex(data[2:])


def contracts_dir() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "contracts"))
