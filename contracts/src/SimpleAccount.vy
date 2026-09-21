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

ETH_MSG: constant(Bytes[28]) = b"\x19Ethereum Signed Message:\n32"

entryPoint: public(address)
owner: public(address)
initialized: public(bool)

@external
def initialize(_entryPoint: address, _owner: address):
    assert not self.initialized, "already initialized"
    assert _entryPoint != empty(address), "entrypoint required"
    assert _owner != empty(address), "owner required"
    self.entryPoint = _entryPoint
    self.owner = _owner
    self.initialized = True

@internal
@pure
def _recover(digest: bytes32, sig: Bytes[65]) -> address:
    r: bytes32 = convert(slice(sig, 0, 32), bytes32)
    s: bytes32 = convert(slice(sig, 32, 32), bytes32)
    v: uint256 = convert(slice(sig, 64, 1), uint256)
    if v < 27:
        v += 27
    return ecrecover(digest, v, r, s)

@external
def validateUserOp(userOp: PackedUserOperation, userOpHash: bytes32, missingAccountFunds: uint256) -> uint256:
    assert msg.sender == self.entryPoint, "not entrypoint"
    assert len(userOp.signature) >= 65, "bad signature"
    digest: bytes32 = keccak256(concat(ETH_MSG, userOpHash))
    sig: Bytes[65] = slice(userOp.signature, 0, 65)
    if self._recover(digest, sig) != self.owner:
        return 1
    if missingAccountFunds > 0:
        raw_call(self.entryPoint, b"", value=missingAccountFunds)
    return 0

@external
def execute(target: address, amount: uint256, data: Bytes[8192]) -> Bytes[8192]:
    assert msg.sender == self.entryPoint or msg.sender == self.owner, "not authorized"
    ret: Bytes[8192] = b""
    if len(data) == 0:
        raw_call(target, b"", value=amount)
        return ret
    ret = raw_call(target, data, max_outsize=8192, value=amount)
    return ret
