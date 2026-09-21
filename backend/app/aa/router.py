from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.aa.bundler import (
    hex_to_bytes,
    stamp_paymaster_and_data,
    submit_handle_ops,
    verify_operator_stamp,
)
from app.config import get_settings
from app.contract_addresses import load_abi
from app.db import get_db
from app.models import User
from app.auth.router import get_current_user

router = APIRouter(prefix="/aa", tags=["aa"])

SELECTOR_APPROVE = bytes.fromhex("095ea7b3")
SELECTOR_SPLIT = bytes.fromhex("a3d7da1d")
SELECTOR_SET_APPROVAL = bytes.fromhex("a22cb465")
SELECTOR_BUY_USDC = bytes.fromhex("a9c98025")
SELECTOR_SELL_USDC = bytes.fromhex("d4bd65f0")
SELECTOR_ADD_LIQ = bytes.fromhex("3b57e2bc")
SELECTOR_CAST_VOTE = bytes.fromhex("b4b0713e")
SELECTOR_CANCEL_ORDER = bytes.fromhex("dd707492")
SELECTOR_INC_NONCE = bytes.fromhex("627cdcb9")
SELECTOR_REQUEST_REDEEM = bytes.fromhex("aa2f892d")
SELECTOR_CLAIM = bytes.fromhex("4e71d92d")
SELECTOR_MATCH_ORDERS = bytes.fromhex("e9f2cd3e")
SELECTOR_EXECUTE = bytes.fromhex("b61d27f6")
SELECTOR_EXECUTE_BATCH = bytes.fromhex("47e1da2a")


class UserOpRequest(BaseModel):
    sender: str
    nonce: int = 0
    initCode: str = "0x"
    callData: str
    accountGasLimits: str = Field(default="0x" + "00" * 32)
    preVerificationGas: int = 21000
    gasFees: str = Field(default="0x" + "00" * 32)
    paymasterAndData: str = "0x"
    signature: str = "0x"


class UserOpResponse(BaseModel):
    paymasterAndData: str
    txHash: str | None = None


def _extract_selector(call_data: bytes) -> bytes:
    if len(call_data) < 4:
        raise ValueError("calldata too short")
    return call_data[:4]


def _extract_address(call_data: bytes, offset: int) -> str:
    if len(call_data) < offset + 32:
        raise ValueError("calldata too short for address")
    word = call_data[offset : offset + 32]
    addr_int = int.from_bytes(word, "big") & ((1 << 160) - 1)
    return "0x" + addr_int.to_bytes(20, "big").hex()


def _extract_uint(call_data: bytes, offset: int) -> int:
    if len(call_data) < offset + 32:
        raise ValueError("calldata too short for uint")
    return int.from_bytes(call_data[offset : offset + 32], "big")


def _validate_call(to: str, call_data: bytes, settings) -> bool:
    if len(call_data) < 4:
        return False
    selector = _extract_selector(call_data)
    to_lower = to.lower()
    if selector == SELECTOR_MATCH_ORDERS:
        return False
    if to_lower == settings.usdc_address.lower() and selector == SELECTOR_APPROVE:
        if len(call_data) < 36:
            return False
        spender = _extract_address(call_data, 4).lower()
        allowed = [
            settings.ctf_address.lower(),
            settings.exchange_address.lower(),
            settings.amm_address.lower(),
            settings.fee_vault_address.lower(),
            settings.paymaster_address.lower(),
        ]
        return spender in allowed
    if to_lower == settings.ctf_address.lower() and selector == SELECTOR_SPLIT:
        return True
    if to_lower == settings.ctf_address.lower() and selector == SELECTOR_SET_APPROVAL:
        if len(call_data) < 68:
            return False
        operator = _extract_address(call_data, 4).lower()
        allowed_operators = [
            settings.amm_address.lower(),
            settings.exchange_address.lower(),
            settings.fee_vault_address.lower(),
        ]
        return operator in allowed_operators
    if to_lower == settings.amm_address.lower():
        if selector in [SELECTOR_BUY_USDC, SELECTOR_SELL_USDC, SELECTOR_ADD_LIQ]:
            return True
    if to_lower == settings.oracle_address.lower() and selector == SELECTOR_CAST_VOTE:
        return True
    if to_lower == settings.exchange_address.lower():
        if selector in [SELECTOR_CANCEL_ORDER, SELECTOR_INC_NONCE]:
            return True
    if to_lower == settings.fee_vault_address.lower():
        if selector in [SELECTOR_REQUEST_REDEEM, SELECTOR_CLAIM]:
            return True
    return False


def _validate_call_data(call_data: bytes, settings) -> bool:
    if len(call_data) < 4:
        return False
    selector = _extract_selector(call_data)
    if selector == SELECTOR_EXECUTE:
        if len(call_data) < 100:
            return False
        target = _extract_address(call_data, 4)
        value = _extract_uint(call_data, 36)
        if value != 0:
            return False
        data_offset = int.from_bytes(call_data[68:100], "big")
        absolute_data_offset = 4 + data_offset
        if data_offset < 96 or absolute_data_offset + 32 > len(call_data):
            return False
        data_len = int.from_bytes(call_data[absolute_data_offset : absolute_data_offset + 32], "big")
        if data_len < 4 or absolute_data_offset + 32 + data_len > len(call_data):
            return False
        inner_data = call_data[absolute_data_offset + 32 : absolute_data_offset + 32 + data_len]
        return _validate_call(target, inner_data, settings)
    if selector == SELECTOR_EXECUTE_BATCH:
        return False
    return False


def _signature_present(signature: str) -> bool:
    raw = hex_to_bytes(signature)
    return any(b != 0 for b in raw)


def _owned_by_user(user: User, sender: str, settings) -> bool:
    if not sender:
        return False
    try:
        w3 = Web3(Web3.HTTPProvider(settings.anvil_rpc_url))
        if not w3.is_connected():
            return False
        sender_cs = Web3.to_checksum_address(sender)
        user_cs = Web3.to_checksum_address(user.address)
        try:
            account = w3.eth.contract(address=sender_cs, abi=load_abi("SimpleAccount"))
            owner = account.functions.owner().call()
            if str(owner).lower() == user.address.lower():
                return True
        except Exception:
            pass
        if settings.account_factory_address:
            factory = w3.eth.contract(
                address=Web3.to_checksum_address(settings.account_factory_address),
                abi=load_abi("SimpleAccountFactory"),
            )
            predicted = factory.functions.getAddress(user_cs, b"\x00" * 32).call()
            if str(predicted).lower() == sender.lower():
                return True
    except Exception:
        return False
    return False


@router.post("/userop", response_model=UserOpResponse)
async def sponsor_userop(
    body: UserOpRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings = get_settings()
    if not settings.paymaster_address:
        raise HTTPException(503, "Paymaster not configured")
    if not _owned_by_user(user, body.sender, settings):
        raise HTTPException(403, "Sender is not the authenticated user's account")
    try:
        call_data = hex_to_bytes(body.callData)
    except ValueError:
        raise HTTPException(400, "Invalid callData hex")
    if not _validate_call_data(call_data, settings):
        raise HTTPException(403, "Operation not allowed by paymaster policy")
    init_code = hex_to_bytes(body.initCode)
    account_gas_limits = hex_to_bytes(body.accountGasLimits)
    gas_fees = hex_to_bytes(body.gasFees)
    signature = hex_to_bytes(body.signature)
    if not _signature_present(body.signature):
        try:
            stamped = stamp_paymaster_and_data(
                sender=body.sender,
                nonce=body.nonce,
                init_code=init_code,
                call_data=call_data,
                account_gas_limits=account_gas_limits,
                pre_verification_gas=body.preVerificationGas,
                gas_fees=gas_fees,
                settings=settings,
            )
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
        return UserOpResponse(paymasterAndData=stamped, txHash=None)
    paymaster_and_data = hex_to_bytes(body.paymasterAndData)
    if not verify_operator_stamp(
        sender=body.sender,
        nonce=body.nonce,
        init_code=init_code,
        call_data=call_data,
        account_gas_limits=account_gas_limits,
        pre_verification_gas=body.preVerificationGas,
        gas_fees=gas_fees,
        paymaster_and_data=paymaster_and_data,
        settings=settings,
    ):
        raise HTTPException(403, "Invalid paymasterAndData")
    tx_hash = submit_handle_ops(
        sender=body.sender,
        nonce=body.nonce,
        init_code=init_code,
        call_data=call_data,
        account_gas_limits=account_gas_limits,
        pre_verification_gas=body.preVerificationGas,
        gas_fees=gas_fees,
        paymaster_and_data=paymaster_and_data,
        signature=signature,
        settings=settings,
    )
    return UserOpResponse(paymasterAndData=body.paymasterAndData, txHash=tx_hash)
