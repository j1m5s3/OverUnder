#pragma version 0.4.3

interface IERC20:
    def transfer(to: address, amount: uint256) -> bool: nonpayable
    def transferFrom(owner: address, to: address, amount: uint256) -> bool: nonpayable

event TransferSingle:
    operator: indexed(address)
    sender: indexed(address)
    receiver: indexed(address)
    id: uint256
    value: uint256

event ApprovalForAll:
    owner: indexed(address)
    operator: indexed(address)
    approved: bool

event ConditionPrepared:
    oracle: indexed(address)
    questionId: indexed(bytes32)
    conditionId: indexed(bytes32)

event ConditionResolved:
    conditionId: indexed(bytes32)
    payoutYes: uint256
    payoutNo: uint256

collateral: public(address)
balanceOf: public(HashMap[address, HashMap[uint256, uint256]])
isApprovedForAll: public(HashMap[address, HashMap[address, bool]])
oracles: public(HashMap[bytes32, address])
outcomeSlots: public(HashMap[bytes32, uint8])
payoutNumerators: public(HashMap[bytes32, uint256[2]])
payoutDenominator: public(HashMap[bytes32, uint256])

@deploy
def __init__(collateral: address):
    assert collateral != empty(address)
    self.collateral = collateral

@external
@pure
def positionId(conditionId: bytes32, outcome: uint8) -> uint256:
    return self._pid(conditionId, outcome)

@internal
@pure
def _pid(conditionId: bytes32, outcome: uint8) -> uint256:
    return convert(keccak256(abi_encode(conditionId, outcome)), uint256)

@external
def prepareCondition(oracle: address, questionId: bytes32) -> bytes32:
    assert oracle != empty(address)
    cid: bytes32 = keccak256(abi_encode(oracle, questionId))
    assert self.outcomeSlots[cid] == 0, "already prepared"
    self.outcomeSlots[cid] = 2
    self.oracles[cid] = oracle
    log ConditionPrepared(oracle=oracle, questionId=questionId, conditionId=cid)
    return cid

@external
def splitPosition(conditionId: bytes32, amount: uint256):
    assert self.outcomeSlots[conditionId] == 2, "unknown condition"
    assert self.payoutDenominator[conditionId] == 0, "resolved"
    assert extcall IERC20(self.collateral).transferFrom(msg.sender, self, amount)
    yes_id: uint256 = self._pid(conditionId, 0)
    no_id: uint256 = self._pid(conditionId, 1)
    self.balanceOf[msg.sender][yes_id] += amount
    self.balanceOf[msg.sender][no_id] += amount
    log TransferSingle(operator=msg.sender, sender=empty(address), receiver=msg.sender, id=yes_id, value=amount)
    log TransferSingle(operator=msg.sender, sender=empty(address), receiver=msg.sender, id=no_id, value=amount)

@external
def mergePositions(conditionId: bytes32, amount: uint256):
    assert self.payoutDenominator[conditionId] == 0, "resolved"
    yes_id: uint256 = self._pid(conditionId, 0)
    no_id: uint256 = self._pid(conditionId, 1)
    self.balanceOf[msg.sender][yes_id] -= amount
    self.balanceOf[msg.sender][no_id] -= amount
    log TransferSingle(operator=msg.sender, sender=msg.sender, receiver=empty(address), id=yes_id, value=amount)
    log TransferSingle(operator=msg.sender, sender=msg.sender, receiver=empty(address), id=no_id, value=amount)
    assert extcall IERC20(self.collateral).transfer(msg.sender, amount)

@external
def redeemPositions(conditionId: bytes32, outcome: uint8, amount: uint256):
    denom: uint256 = self.payoutDenominator[conditionId]
    assert denom > 0, "not resolved"
    assert outcome < 2
    pid: uint256 = self._pid(conditionId, outcome)
    self.balanceOf[msg.sender][pid] -= amount
    log TransferSingle(operator=msg.sender, sender=msg.sender, receiver=empty(address), id=pid, value=amount)
    payout: uint256 = amount * self.payoutNumerators[conditionId][outcome] // denom
    if payout > 0:
        assert extcall IERC20(self.collateral).transfer(msg.sender, payout)

@external
def reportPayouts(conditionId: bytes32, payouts: uint256[2]):
    assert msg.sender == self.oracles[conditionId], "not oracle"
    assert self.payoutDenominator[conditionId] == 0, "already resolved"
    denom: uint256 = payouts[0] + payouts[1]
    assert denom > 0, "zero payouts"
    self.payoutNumerators[conditionId] = payouts
    self.payoutDenominator[conditionId] = denom
    log ConditionResolved(conditionId=conditionId, payoutYes=payouts[0], payoutNo=payouts[1])

@external
def setApprovalForAll(operator: address, approved: bool):
    self.isApprovedForAll[msg.sender][operator] = approved
    log ApprovalForAll(owner=msg.sender, operator=operator, approved=approved)

@external
def safeTransferFrom(sender: address, receiver: address, id: uint256, amount: uint256, data: Bytes[1024]):
    assert receiver != empty(address)
    assert msg.sender == sender or self.isApprovedForAll[sender][msg.sender], "not approved"
    self.balanceOf[sender][id] -= amount
    self.balanceOf[receiver][id] += amount
    log TransferSingle(operator=msg.sender, sender=sender, receiver=receiver, id=id, value=amount)

@external
@view
def isResolved(conditionId: bytes32) -> bool:
    return self.payoutDenominator[conditionId] > 0
