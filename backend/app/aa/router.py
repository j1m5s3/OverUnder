from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models import User
from app.auth.router import get_current_user

router = APIRouter(prefix="/aa", tags=["aa"])

# Function selectors (first 4 bytes of keccak256)
SELECTOR_APPROVE = bytes.fromhex("095ea7b3")
SELECTOR_SPLIT = bytes.fromhex("5c1bba38")
SELECTOR_SET_APPROVAL = bytes.fromhex("a22cb465")
SELECTOR_BUY_USDC = bytes.fromhex("8b7a7fb9")
SELECTOR_SELL_USDC = bytes.fromhex("3d0d6e3f")
SELECTOR_ADD_LIQ = bytes.fromhex("2f4f5cc5")
SELECTOR_CAST_VOTE = bytes.fromhex("4d6a3158")
SELECTOR_CANCEL_ORDER = bytes.fromhex("514fcac7")
SELECTOR_INC_NONCE = bytes.fromhex("627cdcb9")
SELECTOR_REQUEST_REDEEM = bytes.fromhex("7bde82f2")
SELECTOR_CLAIM = bytes.fromhex("4e71d92d")
SELECTOR_MATCH_ORDERS = bytes.fromhex("8a920150")
SELECTOR_EXECUTE = bytes.fromhex("b61d27f6")
SELECTOR_EXECUTE_BATCH = bytes.fromhex("47e1da2a")


class UserOpRequest(BaseModel):
    """Request to sponsor a UserOperation"""
    sender: str
    callData: str  # hex string
    # For MVP, we only need sender and callData to validate policy
    # Full UserOp fields (nonce, gas limits, etc.) would be included in production


class UserOpResponse(BaseModel):
    """Response with paymaster data to sponsor the UserOp"""
    paymasterAndData: str  # hex string


def _extract_selector(call_data: bytes) -> bytes:
    """Extract function selector (first 4 bytes) from calldata"""
    if len(call_data) < 4:
        raise ValueError("calldata too short")
    return call_data[:4]


def _extract_address(call_data: bytes, offset: int) -> str:
    """Extract address from calldata at byte offset"""
    if len(call_data) < offset + 32:
        raise ValueError("calldata too short for address")
    # Address is right-padded in the 32-byte word
    word = call_data[offset:offset + 32]
    addr_int = int.from_bytes(word, "big") & ((1 << 160) - 1)
    return "0x" + addr_int.to_bytes(20, "big").hex()


def _validate_call(to: str, call_data: bytes, settings) -> bool:
    """
    Validate a single call against allowlist.
    Returns True if allowed, False otherwise.
    """
    if len(call_data) < 4:
        return False
    
    selector = _extract_selector(call_data)
    to_lower = to.lower()
    
    # Block matchOrders explicitly
    if selector == SELECTOR_MATCH_ORDERS:
        return False
    
    # USDC approve: check spender is ctf/amm/feeVault
    if to_lower == settings.usdc_address.lower() and selector == SELECTOR_APPROVE:
        if len(call_data) < 36:
            return False
        spender = _extract_address(call_data, 4).lower()
        allowed = [
            settings.ctf_address.lower(),
            settings.amm_address.lower(),
            settings.fee_vault_address.lower(),
        ]
        return spender in allowed
    
    # CTF split/setApprovalForAll
    if to_lower == settings.ctf_address.lower():
        if selector in [SELECTOR_SPLIT, SELECTOR_SET_APPROVAL]:
            return True
    
    # AMM buy/sell/addLiquidity
    if to_lower == settings.amm_address.lower():
        if selector in [SELECTOR_BUY_USDC, SELECTOR_SELL_USDC, SELECTOR_ADD_LIQ]:
            return True
    
    # Oracle castVote
    if to_lower == settings.oracle_address.lower() and selector == SELECTOR_CAST_VOTE:
        return True
    
    # Exchange cancelOrder/incrementNonce (NOT matchOrders)
    if to_lower == settings.exchange_address.lower():
        if selector in [SELECTOR_CANCEL_ORDER, SELECTOR_INC_NONCE]:
            return True
    
    # FeeVault requestRedeem/claim
    if to_lower == settings.fee_vault_address.lower():
        if selector in [SELECTOR_REQUEST_REDEEM, SELECTOR_CLAIM]:
            return True
    
    # Deny unknown
    return False


def _validate_call_data(call_data: bytes, settings) -> bool:
    """
    Validate UserOp callData. Handles:
    - AA execute(address,uint256,bytes)
    
    Returns True if allowed, False otherwise.
    """
    if len(call_data) < 4:
        return False
    
    selector = _extract_selector(call_data)
    
    # Simple execute(address,uint256,bytes)
    if selector == SELECTOR_EXECUTE:
        if len(call_data) < 100:
            return False
        
        # Extract target address (offset 4, first param)
        target = _extract_address(call_data, 4)
        
        # Extract inner calldata offset (at offset 68 = 4 + 32 + 32)
        # Inner data length is at offset 100
        if len(call_data) < 132:
            return False
        
        inner_len_bytes = call_data[100:132]
        inner_len = int.from_bytes(inner_len_bytes, "big")
        
        if inner_len < 4 or len(call_data) < 132 + inner_len:
            return False
        
        # Inner calldata starts at offset 132
        inner_data = call_data[132:132 + inner_len]
        return _validate_call(target, inner_data, settings)
    
    # executeBatch: too complex for slice 5, deny for safety
    if selector == SELECTOR_EXECUTE_BATCH:
        return False
    
    # Direct protocol calls: deny for safety (should use execute wrapper)
    return False


@router.post("/userop", response_model=UserOpResponse)
async def sponsor_userop(
    body: UserOpRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Validate UserOp against paymaster policy and return paymasterAndData.
    
    Requires JWT authentication. Re-checks the same allowlist as the on-chain
    paymaster. Never sponsors auth routes or matchOrders.
    """
    settings = get_settings()
    
    # Parse callData hex
    call_data_hex = body.callData.removeprefix("0x")
    try:
        call_data = bytes.fromhex(call_data_hex)
    except ValueError:
        raise HTTPException(400, "Invalid callData hex")
    
    # Validate against allowlist
    if not _validate_call_data(call_data, settings):
        raise HTTPException(403, "Operation not allowed by paymaster policy")
    
    # In production, would construct full paymasterAndData with:
    # - paymaster address (20 bytes)
    # - verificationGasLimit (uint128)
    # - postOpGasLimit (uint128)
    # - signature/data for paymaster
    #
    # For MVP/testing, return a placeholder that indicates sponsorship approved
    paymaster_addr = settings.paymaster_address
    if not paymaster_addr:
        raise HTTPException(503, "Paymaster not configured")
    
    # Simple paymasterAndData: just the paymaster address
    # In production, would include gas limits and validation signature
    paymaster_and_data = paymaster_addr
    
    return UserOpResponse(paymasterAndData=paymaster_and_data)
