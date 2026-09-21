#pragma version 0.4.3

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

interface IAccount:
    def validateUserOp(userOp: PackedUserOperation, userOpHash: bytes32, missingAccountFunds: uint256) -> uint256: nonpayable

interface IPaymaster:
    def validatePaymasterUserOp(userOp: PackedUserOperation, userOpHash: bytes32, maxCost: uint256) -> (Bytes[160], uint256): nonpayable
    def postOp(mode: uint8, context: Bytes[160], actualGasCost: uint256, actualUserOpFeePerGas: uint256): nonpayable

deposits: public(HashMap[address, uint256])
nonces: public(HashMap[address, uint256])

@external
@payable
def depositTo(account: address):
    self.deposits[account] += msg.value

@external
@view
def getDepositInfo(account: address) -> (uint256, bool, uint112, uint48, uint48):
    return (self.deposits[account], False, 0, 0, 0)

@external
@view
def balanceOf(account: address) -> uint256:
    return self.deposits[account]

@external
@view
def getNonce(sender: address, key: uint256) -> uint256:
    return self.nonces[sender]

@internal
@pure
def _addr20(data: Bytes[8192], off: uint256) -> address:
    return convert(convert(slice(data, off, 20), bytes20), address)

@internal
@pure
def _u128(data: Bytes[8192], off: uint256) -> uint256:
    return convert(convert(slice(data, off, 16), bytes16), uint256)

@internal
@pure
def _hashOp(op: PackedUserOperation) -> bytes32:
    return keccak256(
        abi_encode(
            op.sender,
            op.nonce,
            keccak256(op.initCode),
            keccak256(op.callData),
            op.accountGasLimits,
            op.preVerificationGas,
            op.gasFees,
            keccak256(op.paymasterAndData),
        )
    )

@external
@view
def getUserOpHash(userOp: PackedUserOperation) -> bytes32:
    return keccak256(abi_encode(self._hashOp(userOp), self, chain.id))

@internal
@pure
def _maxCost(op: PackedUserOperation) -> uint256:
    limits: uint256 = convert(op.accountGasLimits, uint256)
    fees: uint256 = convert(op.gasFees, uint256)
    callGas: uint256 = limits % (2 ** 128)
    verifGas: uint256 = limits // (2 ** 128)
    maxFee: uint256 = fees % (2 ** 128)
    pmVerif: uint256 = 0
    pmPost: uint256 = 0
    if len(op.paymasterAndData) >= 52:
        pmVerif = self._u128(op.paymasterAndData, 20)
        pmPost = self._u128(op.paymasterAndData, 36)
    cost: uint256 = (verifGas + callGas + op.preVerificationGas + pmVerif + pmPost) * maxFee
    if cost == 0:
        return 1
    return cost

@external
def handleOps(ops: DynArray[PackedUserOperation, 8], beneficiary: address):
    for i: uint256 in range(8):
        if i >= len(ops):
            break
        op: PackedUserOperation = ops[i]
        if len(op.initCode) > 0:
            assert len(op.initCode) >= 20, "bad initCode"
            factory: address = self._addr20(op.initCode, 0)
            factoryCall: Bytes[8192] = slice(op.initCode, 20, len(op.initCode) - 20)
            raw_call(factory, factoryCall)
        assert op.nonce == self.nonces[op.sender], "bad nonce"
        userOpHash: bytes32 = keccak256(abi_encode(self._hashOp(op), self, chain.id))
        vd: uint256 = extcall IAccount(op.sender).validateUserOp(op, userOpHash, 0)
        assert vd == 0, "account validation"
        assert len(op.paymasterAndData) >= 20, "paymaster required"
        pm: address = self._addr20(op.paymasterAndData, 0)
        maxCost: uint256 = self._maxCost(op)
        context: Bytes[160] = b""
        pvd: uint256 = 0
        context, pvd = extcall IPaymaster(pm).validatePaymasterUserOp(op, userOpHash, maxCost)
        assert pvd % 2 == 0, "paymaster validation"
        raw_call(op.sender, op.callData)
        actual: uint256 = maxCost
        extcall IPaymaster(pm).postOp(0, context, actual, 1)
        assert self.deposits[pm] >= actual, "paymaster deposit"
        self.deposits[pm] -= actual
        self.nonces[op.sender] += 1
