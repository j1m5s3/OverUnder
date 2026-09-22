from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.aa.bundler import hex_to_bytes
from app.auth.router import get_current_user
from app.cdp import send_user_operation
from app.config import get_settings
from app.db import get_db
from app.models import User

router = APIRouter(prefix="/aa", tags=["aa"])

SELECTOR_APPROVE = bytes.fromhex("095ea7b3")
SELECTOR_SET_APPROVAL = bytes.fromhex("a22cb465")
SELECTOR_BUY_USDC = bytes.fromhex("a9c98025")
SELECTOR_SELL_USDC = bytes.fromhex("d4bd65f0")
SELECTOR_MATCH_ORDERS = bytes.fromhex("e9f2cd3e")


class UserOpRequest(BaseModel):
    sender: str = ""
    nonce: int = 0
    initCode: str = "0x"
    callData: str = "0x"
    accountGasLimits: str = "0x"
    preVerificationGas: int = 0
    gasFees: str = "0x"
    paymasterAndData: str = "0x"
    signature: str = "0x"


class CdpCall(BaseModel):
    to: str
    data: str
    value: int | str = 0


class CdpSendRequest(BaseModel):
    calls: list[CdpCall]
    address: str | None = None


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


def _value_int(value: int | str) -> int:
    if isinstance(value, int):
        return value
    raw = str(value).strip()
    if not raw:
        return 0
    if raw.startswith(("0x", "0X")):
        return int(raw, 16)
    return int(raw)


def _validate_call(to: str, call_data: bytes, value: int, settings) -> bool:
    if value != 0:
        return False
    if len(call_data) < 4:
        return False
    selector = _extract_selector(call_data)
    if selector == SELECTOR_MATCH_ORDERS:
        return False
    to_lower = to.lower()
    amm = (settings.amm_address or "").lower()
    usdc = (settings.usdc_address or "").lower()
    ctf = (settings.ctf_address or "").lower()
    if not amm:
        return False
    if to_lower == usdc and selector == SELECTOR_APPROVE:
        if len(call_data) < 36:
            return False
        return _extract_address(call_data, 4).lower() == amm
    if to_lower == ctf and selector == SELECTOR_SET_APPROVAL:
        if len(call_data) < 36:
            return False
        return _extract_address(call_data, 4).lower() == amm
    if to_lower == amm and selector in (SELECTOR_BUY_USDC, SELECTOR_SELL_USDC):
        return True
    return False


@router.post("/userop")
async def sponsor_userop(body: UserOpRequest | None = None):
    raise HTTPException(410, "Use Coinbase CDP sponsored user operations")


@router.post("/cdp-send")
async def cdp_send(
    body: CdpSendRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings = get_settings()
    if not body.calls:
        raise HTTPException(400, "calls required")
    payload_calls = []
    for call in body.calls:
        try:
            value = _value_int(call.value)
            data = hex_to_bytes(call.data)
        except ValueError:
            raise HTTPException(400, "Invalid call data")
        if not _validate_call(call.to, data, value, settings):
            raise HTTPException(403, "Operation not allowed")
        data_hex = call.data if call.data.startswith("0x") else "0x" + call.data
        payload_calls.append(
            {
                "to": Web3.to_checksum_address(call.to),
                "value": "0",
                "data": data_hex,
            }
        )
    if not user.cdp_user_id:
        raise HTTPException(403, "CDP user required")
    try:
        sender = Web3.to_checksum_address(user.address)
    except ValueError as exc:
        raise HTTPException(400, "Invalid smart account") from exc
    result = await send_user_operation(
        user_id=user.cdp_user_id,
        address=sender,
        calls=payload_calls,
    )
    return {
        "userOpHash": result.get("userOpHash") or result.get("user_op_hash"),
        "transactionHash": result.get("transactionHash") or result.get("transaction_hash"),
    }
