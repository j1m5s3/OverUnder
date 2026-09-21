#pragma version 0.4.3

interface IEntryPoint:
    def depositTo(account: address): payable
    def getDepositInfo(account: address) -> (uint256, bool, uint112, uint48, uint48): view

interface IERC20:
    def balanceOf(account: address) -> uint256: view
    def allowance(owner: address, spender: address) -> uint256: view
    def transferFrom(owner: address, to: address, amount: uint256) -> bool: nonpayable

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

struct DailySpend:
    windowStart: uint256
    spent: uint256

ETH_MSG: constant(Bytes[28]) = b"\x19Ethereum Signed Message:\n32"
WINDOW: constant(uint256) = 86400
DEFAULT_WEI_PER_USDC: constant(uint256) = 10 ** 15
DEFAULT_DAILY_CAP: constant(uint256) = 50 * 10 ** 6

SELECTOR_APPROVE: constant(bytes4) = 0x095ea7b3
SELECTOR_SPLIT: constant(bytes4) = 0xa3d7da1d
SELECTOR_SET_APPROVAL: constant(bytes4) = 0xa22cb465
SELECTOR_BUY_USDC: constant(bytes4) = 0xa9c98025
SELECTOR_SELL_USDC: constant(bytes4) = 0xd4bd65f0
SELECTOR_ADD_LIQ: constant(bytes4) = 0x3b57e2bc
SELECTOR_CAST_VOTE: constant(bytes4) = 0xb4b0713e
SELECTOR_CANCEL_ORDER: constant(bytes4) = 0xdd707492
SELECTOR_INC_NONCE: constant(bytes4) = 0x627cdcb9
SELECTOR_REQUEST_REDEEM: constant(bytes4) = 0xaa2f892d
SELECTOR_CLAIM: constant(bytes4) = 0x4e71d92d
SELECTOR_MATCH_ORDERS: constant(bytes4) = 0xe9f2cd3e
SELECTOR_EXECUTE: constant(bytes4) = 0xb61d27f6
SELECTOR_EXECUTE_BATCH: constant(bytes4) = 0x47e1da2a

entryPoint: public(address)
operator: public(address)
allowedFactories: public(HashMap[address, bool])
allowedSenders: public(HashMap[address, bool])
usdc: public(address)
ctf: public(address)
amm: public(address)
exchange: public(address)
oracle: public(address)
feeVault: public(address)
weiPerUsdc: public(uint256)
feeRecipient: public(address)
dailyCapUsdc: public(uint256)
dailySpend: public(HashMap[address, DailySpend])

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
    self.feeRecipient = operator
    self.weiPerUsdc = DEFAULT_WEI_PER_USDC
    self.dailyCapUsdc = DEFAULT_DAILY_CAP

@external
@payable
def deposit():
    assert msg.sender == self.operator, "operator only"
    extcall IEntryPoint(self.entryPoint).depositTo(self, value=msg.value)
    log PaymasterDeposited(amount=msg.value)

@external
def addFactory(factory: address):
    assert msg.sender == self.operator, "operator only"
    self.allowedFactories[factory] = True

@external
def removeFactory(factory: address):
    assert msg.sender == self.operator, "operator only"
    self.allowedFactories[factory] = False

@external
def addSender(sender: address):
    assert msg.sender == self.operator, "operator only"
    self.allowedSenders[sender] = True

@external
def removeSender(sender: address):
    assert msg.sender == self.operator, "operator only"
    self.allowedSenders[sender] = False

@external
def setWeiPerUsdc(weiPerUsdc: uint256):
    assert msg.sender == self.operator, "operator only"
    assert weiPerUsdc != 0, "weiPerUsdc"
    self.weiPerUsdc = weiPerUsdc

@external
def setFeeRecipient(recipient: address):
    assert msg.sender == self.operator, "operator only"
    assert recipient != empty(address), "recipient required"
    self.feeRecipient = recipient

@external
def setDailyCapUsdc(cap: uint256):
    assert msg.sender == self.operator, "operator only"
    self.dailyCapUsdc = cap

@internal
@pure
def _extractSelector(callData: Bytes[8192]) -> bytes4:
    assert len(callData) >= 4, "calldata too short"
    return convert(slice(callData, 0, 4), bytes4)

@internal
@pure
def _extractAddress(callData: Bytes[8192], offset: uint256) -> address:
    assert len(callData) >= offset + 32, "calldata too short for address"
    word: bytes32 = convert(slice(callData, offset, 32), bytes32)
    return convert(convert(word, uint256) & convert(max_value(uint160), uint256), address)

@internal
@pure
def _extractUint(callData: Bytes[8192], offset: uint256) -> uint256:
    assert len(callData) >= offset + 32, "calldata too short for uint"
    return convert(convert(slice(callData, offset, 32), bytes32), uint256)

@internal
@pure
def _addr20(data: Bytes[8192], off: uint256) -> address:
    return convert(convert(slice(data, off, 20), bytes20), address)

@internal
@pure
def _u48(data: Bytes[8192], off: uint256) -> uint48:
    return convert(convert(convert(slice(data, off, 6), bytes6), uint256), uint48)

@internal
@pure
def _recover(digest: bytes32, sig: Bytes[65]) -> address:
    r: bytes32 = convert(slice(sig, 0, 32), bytes32)
    s: bytes32 = convert(slice(sig, 32, 32), bytes32)
    v: uint256 = convert(slice(sig, 64, 1), uint256)
    if v < 27:
        v += 27
    return ecrecover(digest, v, r, s)

@internal
@view
def _validateCall(to: address, callData: Bytes[8192]) -> bool:
    if len(callData) < 4:
        return False
    selector: bytes4 = self._extractSelector(callData)
    if selector == SELECTOR_MATCH_ORDERS:
        return False
    if to == self.usdc and selector == SELECTOR_APPROVE:
        if len(callData) < 36:
            return False
        spender: address = self._extractAddress(callData, 4)
        return spender in [self.ctf, self.exchange, self.amm, self.feeVault, self]
    if to == self.ctf and selector == SELECTOR_SPLIT:
        return True
    if to == self.ctf and selector == SELECTOR_SET_APPROVAL:
        if len(callData) < 68:
            return False
        operator: address = self._extractAddress(callData, 4)
        return operator in [self.amm, self.exchange, self.feeVault]
    if to == self.amm:
        if selector in [SELECTOR_BUY_USDC, SELECTOR_SELL_USDC, SELECTOR_ADD_LIQ]:
            return True
    if to == self.oracle and selector == SELECTOR_CAST_VOTE:
        return True
    if to == self.exchange:
        if selector in [SELECTOR_CANCEL_ORDER, SELECTOR_INC_NONCE]:
            return True
    if to == self.feeVault:
        if selector in [SELECTOR_REQUEST_REDEEM, SELECTOR_CLAIM]:
            return True
    return False

@internal
@view
def _innerCall(callData: Bytes[8192]) -> (address, uint256, Bytes[8192], bool):
    if len(callData) < 4:
        return empty(address), 0, b"", False
    selector: bytes4 = self._extractSelector(callData)
    if selector == SELECTOR_EXECUTE_BATCH:
        return empty(address), 0, b"", False
    if selector != SELECTOR_EXECUTE:
        return empty(address), 0, b"", False
    if len(callData) < 100:
        return empty(address), 0, b"", False
    target: address = self._extractAddress(callData, 4)
    amount: uint256 = self._extractUint(callData, 36)
    dataOffsetBytes: bytes32 = convert(slice(callData, 68, 32), bytes32)
    dataOffset: uint256 = convert(dataOffsetBytes, uint256)
    absoluteDataOffset: uint256 = 4 + dataOffset
    if dataOffset < 96 or absoluteDataOffset + 32 > len(callData):
        return empty(address), 0, b"", False
    dataLenBytes: bytes32 = convert(slice(callData, absoluteDataOffset, 32), bytes32)
    dataLen: uint256 = convert(dataLenBytes, uint256)
    if dataLen < 4 or absoluteDataOffset + 32 + dataLen > len(callData):
        return empty(address), 0, b"", False
    innerDataStart: uint256 = absoluteDataOffset + 32
    innerData: Bytes[8192] = slice(callData, innerDataStart, dataLen)
    return target, amount, innerData, True

@internal
@view
def _validateCallData(callData: Bytes[8192]) -> bool:
    target: address = empty(address)
    amount: uint256 = 0
    inner: Bytes[8192] = b""
    ok: bool = False
    target, amount, inner, ok = self._innerCall(callData)
    if not ok:
        return False
    if amount != 0:
        return False
    return self._validateCall(target, inner)

@internal
@view
def _isPaymasterApprove(callData: Bytes[8192], fee: uint256) -> bool:
    target: address = empty(address)
    amount: uint256 = 0
    inner: Bytes[8192] = b""
    ok: bool = False
    target, amount, inner, ok = self._innerCall(callData)
    if not ok:
        return False
    if target != self.usdc:
        return False
    if len(inner) < 68:
        return False
    if self._extractSelector(inner) != SELECTOR_APPROVE:
        return False
    spender: address = self._extractAddress(inner, 4)
    approved: uint256 = self._extractUint(inner, 36)
    return spender == self and approved >= fee

@internal
def _chargeCap(sender: address, fee: uint256):
    rec: DailySpend = self.dailySpend[sender]
    if rec.windowStart == 0 or block.timestamp >= rec.windowStart + WINDOW:
        rec.windowStart = block.timestamp
        rec.spent = 0
    rec.spent += fee
    assert rec.spent <= self.dailyCapUsdc, "paymaster: daily cap"
    self.dailySpend[sender] = rec

@internal
@view
def _operatorDigest(
    userOp: PackedUserOperation,
    validUntil: uint48,
    validAfter: uint48
) -> bytes32:
    return keccak256(
        abi_encode(
            userOp.sender,
            userOp.nonce,
            keccak256(userOp.initCode),
            keccak256(userOp.callData),
            userOp.accountGasLimits,
            userOp.preVerificationGas,
            userOp.gasFees,
            convert(validUntil, uint256),
            convert(validAfter, uint256),
            self,
            chain.id,
        )
    )

@internal
@view
def _checkOperatorSig(userOp: PackedUserOperation) -> (uint48, uint48):
    assert len(userOp.paymasterAndData) >= 129, "paymaster: paymasterAndData"
    pm: address = self._addr20(userOp.paymasterAndData, 0)
    assert pm == self, "paymaster: prefix"
    validUntil: uint48 = self._u48(userOp.paymasterAndData, 52)
    validAfter: uint48 = self._u48(userOp.paymasterAndData, 58)
    sig: Bytes[65] = slice(userOp.paymasterAndData, 64, 65)
    digest: bytes32 = keccak256(concat(ETH_MSG, self._operatorDigest(userOp, validUntil, validAfter)))
    assert self._recover(digest, sig) == self.operator, "paymaster: bad operator sig"
    return validUntil, validAfter

@external
def validatePaymasterUserOp(
    userOp: PackedUserOperation,
    userOpHash: bytes32,
    maxCost: uint256
) -> (Bytes[160], uint256):
    assert msg.sender == self.entryPoint, "entrypoint only"
    assert self.weiPerUsdc != 0, "paymaster: weiPerUsdc"
    if len(userOp.initCode) > 0:
        assert len(userOp.initCode) >= 20, "paymaster: initCode"
        factory: address = self._addr20(userOp.initCode, 0)
        if not self.allowedFactories[factory]:
            log UserOpRejected(sender=userOp.sender, reason="factory not allowed")
            raise "paymaster: factory not allowed"
        self.allowedSenders[userOp.sender] = True
    elif not self.allowedSenders[userOp.sender]:
        log UserOpRejected(sender=userOp.sender, reason="sender not allowed")
        raise "paymaster: sender not from allowed factory"
    validUntil: uint48 = 0
    validAfter: uint48 = 0
    validUntil, validAfter = self._checkOperatorSig(userOp)
    if not self._validateCallData(userOp.callData):
        log UserOpRejected(sender=userOp.sender, reason="calldata denied")
        raise "paymaster: operation not allowed"
    usdcFee: uint256 = (maxCost * 10 ** 6 + self.weiPerUsdc - 1) // self.weiPerUsdc
    assert staticcall IERC20(self.usdc).balanceOf(userOp.sender) >= usdcFee, "paymaster: USDC balance"
    if not self._isPaymasterApprove(userOp.callData, usdcFee):
        assert staticcall IERC20(self.usdc).allowance(userOp.sender, self) >= usdcFee, "paymaster: USDC allowance"
    self._chargeCap(userOp.sender, usdcFee)
    depositInfo: (uint256, bool, uint112, uint48, uint48) = staticcall IEntryPoint(self.entryPoint).getDepositInfo(self)
    assert depositInfo[0] >= maxCost, "paymaster: insufficient deposit"
    log UserOpSponsored(sender=userOp.sender, userOpHash=userOpHash)
    ctx: Bytes[160] = concat(convert(userOp.sender, bytes20), convert(usdcFee, bytes32))
    validationData: uint256 = convert(validUntil, uint256) * (2 ** 160) + convert(validAfter, uint256) * (2 ** 208)
    return ctx, validationData

@external
def postOp(mode: uint8, context: Bytes[160], actualGasCost: uint256, actualUserOpFeePerGas: uint256):
    assert msg.sender == self.entryPoint, "entrypoint only"
    assert self.weiPerUsdc != 0, "paymaster: weiPerUsdc"
    assert len(context) >= 52, "paymaster: context"
    sender: address = convert(convert(slice(context, 0, 20), bytes20), address)
    capped: uint256 = convert(convert(slice(context, 20, 32), bytes32), uint256)
    fee: uint256 = (actualGasCost * 10 ** 6 + self.weiPerUsdc - 1) // self.weiPerUsdc
    if fee > capped:
        fee = capped
    if fee > 0:
        assert extcall IERC20(self.usdc).transferFrom(sender, self.feeRecipient, fee), "paymaster: fee"

@external
@view
def getDepositInfo() -> (uint256, bool, uint112, uint48, uint48):
    return staticcall IEntryPoint(self.entryPoint).getDepositInfo(self)
