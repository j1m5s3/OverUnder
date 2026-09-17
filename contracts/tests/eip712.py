"""Shared EIP-712 helpers for tests and local deploy."""

from typing import Union

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import to_checksum_address


def _domain(name: str, verifying_contract: str, chain_id: int) -> dict:
    return {
        "name": name,
        "version": "1",
        "chainId": chain_id,
        "verifyingContract": to_checksum_address(verifying_contract),
    }


def sign_attestation(
    private_key: Union[bytes, str],
    oracle: str,
    chain_id: int,
    condition_id: bytes,
    outcome: int,
    evidence_hash: bytes,
    deadline: int,
) -> bytes:
    msg = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Attestation": [
                {"name": "conditionId", "type": "bytes32"},
                {"name": "outcome", "type": "uint8"},
                {"name": "evidenceHash", "type": "bytes32"},
                {"name": "deadline", "type": "uint256"},
            ],
        },
        "primaryType": "Attestation",
        "domain": _domain("OverUnder Oracle", oracle, chain_id),
        "message": {
            "conditionId": condition_id,
            "outcome": outcome,
            "evidenceHash": evidence_hash,
            "deadline": deadline,
        },
    }
    encoded = encode_typed_data(full_message=msg)
    signed = Account.sign_message(encoded, private_key)
    return signed.signature


def sign_order(
    private_key: Union[bytes, str],
    exchange: str,
    chain_id: int,
    order: dict,
) -> bytes:
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
        "domain": _domain("OverUnder Exchange", exchange, chain_id),
        "message": order,
    }
    encoded = encode_typed_data(full_message=msg)
    signed = Account.sign_message(encoded, private_key)
    return signed.signature
