from __future__ import annotations

import time

from eth_abi import encode
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from app.config import Settings
from app.contract_addresses import load_abi

VERIFICATION_GAS = 100_000
POST_OP_GAS = 80_000
VALID_AFTER = 0
VALID_UNTIL_SKEW = 300


def hex_to_bytes(value: str | None) -> bytes:
    raw = (value or "").removeprefix("0x")
    if not raw:
        return b""
    if len(raw) % 2:
        raw = "0" + raw
    return bytes.fromhex(raw)


def to_hex(data: bytes) -> str:
    return "0x" + data.hex()


def pad32(value: bytes) -> bytes:
    if len(value) >= 32:
        return value[-32:]
    return value.rjust(32, b"\x00")


def operator_digest(
    sender: str,
    nonce: int,
    init_code: bytes,
    call_data: bytes,
    account_gas_limits: bytes,
    pre_verification_gas: int,
    gas_fees: bytes,
    valid_until: int,
    valid_after: int,
    paymaster: str,
    chain_id: int,
) -> bytes:
    inner = encode(
        [
            "address",
            "uint256",
            "bytes32",
            "bytes32",
            "bytes32",
            "uint256",
            "bytes32",
            "uint256",
            "uint256",
            "address",
            "uint256",
        ],
        [
            Web3.to_checksum_address(sender),
            nonce,
            Web3.keccak(init_code),
            Web3.keccak(call_data),
            pad32(account_gas_limits),
            pre_verification_gas,
            pad32(gas_fees),
            valid_until,
            valid_after,
            Web3.to_checksum_address(paymaster),
            chain_id,
        ],
    )
    return bytes(Web3.keccak(inner))


def stamp_paymaster_and_data(
    *,
    sender: str,
    nonce: int,
    init_code: bytes,
    call_data: bytes,
    account_gas_limits: bytes,
    pre_verification_gas: int,
    gas_fees: bytes,
    settings: Settings,
) -> str:
    if not settings.paymaster_address:
        raise ValueError("paymaster not configured")
    if not settings.operator_private_key:
        raise ValueError("operator key not configured")
    valid_until = int(time.time()) + VALID_UNTIL_SKEW
    digest = operator_digest(
        sender,
        nonce,
        init_code,
        call_data,
        account_gas_limits,
        pre_verification_gas,
        gas_fees,
        valid_until,
        VALID_AFTER,
        settings.paymaster_address,
        settings.chain_id,
    )
    operator = Account.from_key(settings.operator_private_key)
    sig = bytes(operator.sign_message(encode_defunct(primitive=digest)).signature)
    paymaster = hex_to_bytes(settings.paymaster_address)
    if len(paymaster) != 20:
        raise ValueError("paymaster address")
    packed = (
        paymaster
        + VERIFICATION_GAS.to_bytes(16, "big")
        + POST_OP_GAS.to_bytes(16, "big")
        + valid_until.to_bytes(6, "big")
        + VALID_AFTER.to_bytes(6, "big")
        + sig
    )
    return to_hex(packed)


def verify_operator_stamp(
    *,
    sender: str,
    nonce: int,
    init_code: bytes,
    call_data: bytes,
    account_gas_limits: bytes,
    pre_verification_gas: int,
    gas_fees: bytes,
    paymaster_and_data: bytes,
    settings: Settings,
) -> bool:
    if len(paymaster_and_data) < 129 or not settings.paymaster_address or not settings.operator_private_key:
        return False
    prefix = paymaster_and_data[:20]
    if prefix.hex() != hex_to_bytes(settings.paymaster_address).hex():
        return False
    valid_until = int.from_bytes(paymaster_and_data[52:58], "big")
    valid_after = int.from_bytes(paymaster_and_data[58:64], "big")
    sig = paymaster_and_data[64:129]
    digest = operator_digest(
        sender,
        nonce,
        init_code,
        call_data,
        account_gas_limits,
        pre_verification_gas,
        gas_fees,
        valid_until,
        valid_after,
        settings.paymaster_address,
        settings.chain_id,
    )
    try:
        recovered = Account.recover_message(encode_defunct(primitive=digest), signature=sig)
    except Exception:
        return False
    operator = Account.from_key(settings.operator_private_key)
    return recovered.lower() == operator.address.lower()


def _op_tuple(
    sender: str,
    nonce: int,
    init_code: bytes,
    call_data: bytes,
    account_gas_limits: bytes,
    pre_verification_gas: int,
    gas_fees: bytes,
    paymaster_and_data: bytes,
    signature: bytes,
) -> tuple:
    return (
        Web3.to_checksum_address(sender),
        nonce,
        init_code,
        call_data,
        pad32(account_gas_limits),
        pre_verification_gas,
        pad32(gas_fees),
        paymaster_and_data,
        signature,
    )


def submit_handle_ops(
    *,
    sender: str,
    nonce: int,
    init_code: bytes,
    call_data: bytes,
    account_gas_limits: bytes,
    pre_verification_gas: int,
    gas_fees: bytes,
    paymaster_and_data: bytes,
    signature: bytes,
    settings: Settings,
) -> str | None:
    if not settings.operator_private_key or not settings.entrypoint_address or not settings.anvil_rpc_url:
        return None
    w3 = Web3(Web3.HTTPProvider(settings.anvil_rpc_url))
    if not w3.is_connected():
        return None
    operator = Account.from_key(settings.operator_private_key)
    abi = load_abi("EntryPoint")
    entry = w3.eth.contract(address=Web3.to_checksum_address(settings.entrypoint_address), abi=abi)
    op = _op_tuple(
        sender,
        nonce,
        init_code,
        call_data,
        account_gas_limits,
        pre_verification_gas,
        gas_fees,
        paymaster_and_data,
        signature,
    )
    tx = entry.functions.handleOps([op], operator.address).build_transaction(
        {
            "from": operator.address,
            "nonce": w3.eth.get_transaction_count(operator.address),
            "chainId": settings.chain_id,
            "gas": 2_000_000,
            "gasPrice": w3.eth.gas_price,
        }
    )
    signed = operator.sign_transaction(tx)
    txh = w3.eth.send_raw_transaction(signed.raw_transaction)
    return txh.to_0x_hex() if hasattr(txh, "to_0x_hex") else "0x" + txh.hex()
