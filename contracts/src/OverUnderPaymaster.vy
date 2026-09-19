#pragma version 0.4.3

# OverUnderPaymaster: ERC-4337 v0.7 paymaster for gasless AA user operations
# Whitelists approve/split/AMM/vote/cancel UserOps; blocks matchOrders

interface IEntryPoint:
    def depositTo(account: address): payable
    def getDepositInfo(account: address) -> (uint256, bool, uint112, uint48, uint48): view

struct PackedUserOperation:
    sender: address
    nonce: uint256
    initCode: Bytes[8192]
    callData: Bytes[8192]
    accountGasLimits: bytes32
    preVerificationGas: uint256
    gasFees: bytes32
    paymasterAndData: Bytes[8192]
    signature: Bytes[8192]

entryPoint: public(address)
operator: public(address)
allowedFactories: public(HashMap[address, bool])

# Protocol contract addresses for allowlist validation
usdc: public(address)
ctf: public(address)
amm: public(address)
exchange: public(address)
oracle: public(address)
feeVault: public(address)

# Function selectors (first 4 bytes of keccak256)
# USDC: approve(address,uint256)
SELECTOR_APPROVE: constant(bytes4) = 0x095ea7b3
# CTF: splitPosition(address,bytes32,uint256)
SELECTOR_SPLIT: constant(bytes4) = 0x5c1bba38
# CTF: setApprovalForAll(address,bool)
SELECTOR_SET_APPROVAL: constant(bytes4) = 0xa22cb465
# AMM: buyWithUSDC(bytes32,uint8,uint256,uint256)
SELECTOR_BUY_USDC: constant(bytes4) = 0x8b7a7fb9
# AMM: sellToUSDC(bytes32,uint8,uint256,uint256)
SELECTOR_SELL_USDC: constant(bytes4) = 0x3d0d6e3f
# AMM: addLiquidity(bytes32,uint256)
SELECTOR_ADD_LIQ: constant(bytes4) = 0x2f4f5cc5
# Oracle: castVote(bytes32,uint8)
SELECTOR_CAST_VOTE: constant(bytes4) = 0x4d6a3158
# Exchange: cancelOrder((address,bool,bytes32,uint8,uint256,uint256,uint256,uint256,uint256))
SELECTOR_CANCEL_ORDER: constant(bytes4) = 0x514fcac7
# Exchange: incrementNonce()
SELECTOR_INC_NONCE: constant(bytes4) = 0x627cdcb9
# FeeVault: requestRedeem(uint256)
SELECTOR_REQUEST_REDEEM: constant(bytes4) = 0x7bde82f2
# FeeVault: claim()
SELECTOR_CLAIM: constant(bytes4) = 0x4e71d92d
# Exchange: matchOrders - BLOCKED
SELECTOR_MATCH_ORDERS: constant(bytes4) = 0x8a920150
# AA execute(address,uint256,bytes)
SELECTOR_EXECUTE: constant(bytes4) = 0xb61d27f6
# AA executeBatch(address[],uint256[],bytes[])
SELECTOR_EXECUTE_BATCH: constant(bytes4) = 0x47e1da2a

event PaymasterDeposited:
    amount: uint256

event UserOpSponsored:
    sender: indexed(address)
    userOpHash: indexed(bytes32)

event UserOpRejected:
    sender: indexed(address)
    reason: String[100]

@deploy
def __init__(
    entryPoint: address,
    operator: address,
    usdc: address,
    ctf: address,
    amm: address,
    exchange: address,
    oracle: address,
    feeVault: address
):
    assert entryPoint != empty(address), "entrypoint required"
    assert operator != empty(address), "operator required"
    self.entryPoint = entryPoint
    self.operator = operator
    self.usdc = usdc
    self.ctf = ctf
    self.amm = amm
    self.exchange = exchange
    self.oracle = oracle
    self.feeVault = feeVault

@external
@payable
def deposit():
    """Operator deposits ETH to sponsor UserOps"""
    assert msg.sender == self.operator, "operator only"
    extcall IEntryPoint(self.entryPoint).depositTo(self, value=msg.value)
    log PaymasterDeposited(amount=msg.value)

@external
def addFactory(factory: address):
    """Add an allowed AA factory (Privy, SimpleAccountFactory, etc.)"""
    assert msg.sender == self.operator, "operator only"
    self.allowedFactories[factory] = True

@external
def removeFactory(factory: address):
    """Remove an allowed AA factory"""
    assert msg.sender == self.operator, "operator only"
    self.allowedFactories[factory] = False

@internal
@view
def _extractSelector(callData: Bytes[8192]) -> bytes4:
    """Extract function selector (first 4 bytes) from calldata"""
    assert len(callData) >= 4, "calldata too short"
    return convert(slice(callData, 0, 4), bytes4)

@internal
@view
def _extractAddress(callData: Bytes[8192], offset: uint256) -> address:
    """Extract address from calldata at byte offset"""
    assert len(callData) >= offset + 32, "calldata too short for address"
    # Address is right-padded in the 32-byte word
    word: bytes32 = convert(slice(callData, offset, 32), bytes32)
    return convert(convert(word, uint256) & convert(max_value(uint160), uint256), address)

@internal
@view
def _validateCall(to: address, callData: Bytes[8192]) -> bool:
    """
    Validate a single call against allowlist.
    Returns True if allowed, False otherwise.
    """
    if len(callData) < 4:
        return False
    
    selector: bytes4 = self._extractSelector(callData)
    
    # Block matchOrders explicitly
    if selector == SELECTOR_MATCH_ORDERS:
        return False
    
    # USDC approve: check spender is ctf/amm/feeVault
    if to == self.usdc and selector == SELECTOR_APPROVE:
        if len(callData) < 36:
            return False
        spender: address = self._extractAddress(callData, 4)
        return spender in [self.ctf, self.amm, self.feeVault]
    
    # CTF split/setApprovalForAll
    if to == self.ctf:
        if selector in [SELECTOR_SPLIT, SELECTOR_SET_APPROVAL]:
            return True
    
    # AMM buy/sell/addLiquidity
    if to == self.amm:
        if selector in [SELECTOR_BUY_USDC, SELECTOR_SELL_USDC, SELECTOR_ADD_LIQ]:
            return True
    
    # Oracle castVote
    if to == self.oracle and selector == SELECTOR_CAST_VOTE:
        return True
    
    # Exchange cancelOrder/incrementNonce (NOT matchOrders)
    if to == self.exchange:
        if selector in [SELECTOR_CANCEL_ORDER, SELECTOR_INC_NONCE]:
            return True
    
    # FeeVault requestRedeem/claim
    if to == self.feeVault:
        if selector in [SELECTOR_REQUEST_REDEEM, SELECTOR_CLAIM]:
            return True
    
    # Deny unknown
    return False

@internal
@view
def _validateCallData(callData: Bytes[8192]) -> bool:
    """
    Validate UserOp callData. Handles:
    - Direct calls to protocol contracts
    - AA execute(address,uint256,bytes)
    - AA executeBatch(address[],uint256[],bytes[])
    
    Returns True if all nested calls are allowed, False otherwise.
    """
    if len(callData) < 4:
        return False
    
    selector: bytes4 = self._extractSelector(callData)
    
    # Simple execute(address,uint256,bytes)
    if selector == SELECTOR_EXECUTE:
        if len(callData) < 100:  # 4 + 32 + 32 + 32 (selector + to + value + data offset)
            return False
        
        # Extract target address (offset 4)
        target: address = self._extractAddress(callData, 4)
        
        # Extract inner calldata offset (offset 68 = 4 + 32 + 32)
        # Inner data starts at offset 100 (4 + 32 + 32 + 32)
        # For simplicity, validate the inner selector if present
        if len(callData) < 104:
            return False
        
        # Extract length of inner calldata (at offset 100)
        innerLenBytes: bytes32 = convert(slice(callData, 100, 32), bytes32)
        innerLen: uint256 = convert(innerLenBytes, uint256)
        
        if innerLen < 4 or len(callData) < 132 + innerLen:
            return False
        
        # Inner calldata starts at offset 132
        innerData: Bytes[8192] = slice(callData, 132, innerLen)
        return self._validateCall(target, innerData)
    
    # executeBatch: too complex for slice 5, deny for safety
    # In production, would decode array and validate each call
    if selector == SELECTOR_EXECUTE_BATCH:
        return False
    
    # Direct protocol call: extract target from UserOp.sender context
    # For direct calls, callData is sent to the AA account itself,
    # which then forwards to protocol. This is unusual; typically
    # AA wraps calls in execute(). Deny for safety.
    return False

@external
def validatePaymasterUserOp(
    userOp: PackedUserOperation,
    userOpHash: bytes32,
    maxCost: uint256
) -> (bytes32, uint256):
    """
    EntryPoint v0.7 hook: validate UserOp before sponsoring.
    Returns (context, validationData) where:
    - context: opaque bytes for postOp (unused here)
    - validationData: 0 = valid, 1 = invalid signature/time
    
    Reverts on deny (gas-efficient rejection).
    """
    assert msg.sender == self.entryPoint, "entrypoint only"
    
    # Check sender is from allowed factory
    # In production, would validate account code or factory via initCode
    # For MVP, we trust any sender (operator adds factories manually)
    # TODO: parse initCode and validate factory in userOp.initCode[:20]
    
    # Validate callData allowlist
    if not self._validateCallData(userOp.callData):
        log UserOpRejected(sender=userOp.sender, reason="calldata denied")
        raise "paymaster: operation not allowed"
    
    # Check deposit is sufficient
    # deposit: uint256, staked: bool, stake: uint112, unstakeDelay: uint48, withdrawTime: uint48
    depositInfo: (uint256, bool, uint112, uint48, uint48) = staticcall IEntryPoint(self.entryPoint).getDepositInfo(self)
    assert depositInfo[0] >= maxCost, "paymaster: insufficient deposit"
    
    log UserOpSponsored(sender=userOp.sender, userOpHash=userOpHash)
    
    # validationData = 0 (valid), context = empty
    return (empty(bytes32), 0)

@external
@view
def getDepositInfo() -> (uint256, bool, uint112, uint48, uint48):
    """Query paymaster's deposit on EntryPoint"""
    return staticcall IEntryPoint(self.entryPoint).getDepositInfo(self)
