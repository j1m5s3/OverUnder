"""EIP-712 order hashing and signature checks, byte-for-byte with Exchange.vy.

The contract recovers with raw ecrecover after `if v < 27: v += 27`, so only
v in {0, 1, 27, 28} can ever pass on chain. eth-account also accepts EIP-155
style v >= 35; we reject those explicitly so the API never stores an order the
Exchange would bounce with "bad sig".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from eth_abi import encode
from eth_keys import keys
from eth_utils import is_address, keccak, to_checksum_address

from app.config import get_settings
from app.contract_addresses import get_contract_addresses

DOMAIN_TYPEHASH = keccak(
    text="EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)
ORDER_TYPEHASH = keccak(
    text=(
        "Order(address maker,bool isBuy,bytes32 conditionId,uint8 outcome,"
        "uint256 price,uint256 amount,uint256 salt,uint256 nonce,uint256 expiry)"
    )
)
NAME_HASH = keccak(text="OverUnder Exchange")
VERSION_HASH = keccak(text="1")

U256_MAX = 2**256 - 1
_VALID_V = {0, 1, 27, 28}


class BadOrderSignature(ValueError):
    """Signature is malformed, unrecoverable, or not from the maker."""


def u256(v: Any) -> int:
    """Parse an int, decimal string or 0x-hex string into a uint256."""
    if isinstance(v, bool):
        raise ValueError("bool is not a uint256")
    if isinstance(v, int):
        n = v
    elif isinstance(v, str):
        s = v.strip()
        if not s:
            raise ValueError("empty uint256")
        n = int(s, 16) if s.lower().startswith("0x") else int(s, 10)
    else:
        raise ValueError(f"unsupported uint256 type {type(v).__name__}")
    if n < 0 or n > U256_MAX:
        raise ValueError("uint256 out of range")
    return n


def _bytes32(v: str | bytes) -> bytes:
    if isinstance(v, bytes):
        out = v
    else:
        s = v[2:] if v.lower().startswith("0x") else v
        out = bytes.fromhex(s)
    if len(out) != 32:
        raise ValueError("expected 32 bytes")
    return out


@dataclass(frozen=True)
class OrderFields:
    maker: str
    is_buy: bool
    condition_id: bytes
    outcome: int
    price: int
    amount: int
    salt: int
    nonce: int
    expiry: int

    @classmethod
    def from_model(cls, o: Any) -> "OrderFields":
        return cls(
            maker=to_checksum_address(o.maker),
            is_buy=bool(o.is_buy),
            condition_id=_bytes32(o.condition_id),
            outcome=int(o.outcome),
            price=int(o.price),
            amount=int(o.amount),
            salt=u256(o.salt),
            nonce=int(o.nonce),
            expiry=int(o.expiry),
        )

    @classmethod
    def from_body(cls, b: Any) -> "OrderFields":
        return cls(
            maker=to_checksum_address(b.maker),
            is_buy=bool(b.isBuy),
            condition_id=_bytes32(b.conditionId),
            outcome=int(b.outcome),
            price=int(b.price),
            amount=int(b.amount),
            salt=u256(b.salt),
            nonce=int(b.nonce),
            expiry=int(b.expiry),
        )

    def as_tuple(self) -> tuple:
        return (
            self.maker,
            self.is_buy,
            self.condition_id,
            self.outcome,
            self.price,
            self.amount,
            self.salt,
            self.nonce,
            self.expiry,
        )


def get_exchange_domain() -> tuple[str, int] | None:
    """(checksummed Exchange address, chain id) or None when not configured."""
    s = get_settings()
    try:
        addr = get_contract_addresses(s.chain_id).get("Exchange", "")
    except (OSError, ValueError):
        return None
    if not addr or not is_address(addr):
        return None
    return to_checksum_address(addr), int(s.chain_id)


def domain_separator(exchange: str, chain_id: int) -> bytes:
    return keccak(
        encode(
            ["bytes32", "bytes32", "bytes32", "uint256", "address"],
            [DOMAIN_TYPEHASH, NAME_HASH, VERSION_HASH, chain_id, to_checksum_address(exchange)],
        )
    )


def order_struct_hash(o: OrderFields) -> bytes:
    return keccak(
        encode(
            ["bytes32", "address", "bool", "bytes32", "uint8", "uint256", "uint256", "uint256", "uint256", "uint256"],
            [ORDER_TYPEHASH, *o.as_tuple()],
        )
    )


def order_digest(o: OrderFields, exchange: str, chain_id: int) -> bytes:
    return keccak(b"\x19\x01" + domain_separator(exchange, chain_id) + order_struct_hash(o))


def order_hash_hex(o: OrderFields, exchange: str, chain_id: int) -> str:
    return "0x" + order_digest(o, exchange, chain_id).hex()


def _sig_bytes(sig: bytes | str) -> bytes:
    if isinstance(sig, (bytes, bytearray)):
        return bytes(sig)
    if not isinstance(sig, str):
        raise BadOrderSignature("signature must be hex")
    s = sig[2:] if sig.lower().startswith("0x") else sig
    try:
        return bytes.fromhex(s)
    except ValueError as exc:
        raise BadOrderSignature("signature is not hex") from exc


def recover_order_signer(digest: bytes, sig: bytes | str) -> str:
    raw = _sig_bytes(sig)
    if len(raw) != 65:
        raise BadOrderSignature("signature must be 65 bytes")
    v = raw[64]
    if v not in _VALID_V:
        raise BadOrderSignature("signature v must be 0, 1, 27 or 28")
    r = int.from_bytes(raw[0:32], "big")
    s = int.from_bytes(raw[32:64], "big")
    try:
        pub = keys.Signature(vrs=(v - 27 if v >= 27 else v, r, s)).recover_public_key_from_msg_hash(digest)
    except Exception as exc:
        raise BadOrderSignature("signature does not recover") from exc
    return pub.to_checksum_address()


def verify_order_signature(o: OrderFields, sig: bytes | str, exchange: str, chain_id: int) -> bytes:
    """Return the digest when `sig` recovers to `o.maker`; raise BadOrderSignature otherwise."""
    digest = order_digest(o, exchange, chain_id)
    signer = recover_order_signer(digest, sig)
    if signer.lower() != o.maker.lower():
        raise BadOrderSignature("signer is not maker")
    return digest
