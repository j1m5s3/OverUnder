#pragma version 0.4.3

interface ICTF:
    def prepareCondition(oracle: address, questionId: bytes32) -> bytes32: nonpayable

interface IOracle:
    def registerMarket(conditionId: bytes32, closeTime: uint256): nonpayable

interface IAMM:
    def seedPool(conditionId: bytes32, usdcAmount: uint256): nonpayable

interface IERC20:
    def transferFrom(owner: address, to: address, amount: uint256) -> bool: nonpayable
    def approve(spender: address, amount: uint256) -> bool: nonpayable

event MarketCreated:
    conditionId: indexed(bytes32)
    parentConditionId: indexed(bytes32)
    creator: indexed(address)
    closeTime: uint256
    marketType: uint8
    question: String[256]

event MarketPaused:
    conditionId: indexed(bytes32)
    paused: bool

struct Market:
    conditionId: bytes32
    parentConditionId: bytes32
    closeTime: uint256
    marketType: uint8
    paused: bool
    question: String[256]

ctf: public(address)
oracle: public(address)
amm: public(address)
usdc: public(address)
operator: public(address)
wildcardGenerator: public(address)
markets: public(HashMap[bytes32, Market])
marketExists: public(HashMap[bytes32, bool])

@deploy
def __init__(ctf: address, oracle: address, amm: address, usdc: address, operator: address, wildcardGenerator: address):
    assert ctf != empty(address) and oracle != empty(address) and operator != empty(address)
    self.ctf = ctf
    self.oracle = oracle
    self.amm = amm
    self.usdc = usdc
    self.operator = operator
    self.wildcardGenerator = wildcardGenerator

@external
def setWildcardGenerator(account: address):
    assert msg.sender == self.operator
    self.wildcardGenerator = account

@internal
def _create(questionId: bytes32, parent: bytes32, closeTime: uint256, marketType: uint8, question: String[256]) -> bytes32:
    assert closeTime > block.timestamp, "close in past"
    if parent != empty(bytes32):
        assert self.marketExists[parent], "parent missing"
        assert closeTime <= self.markets[parent].closeTime, "child after parent"
        assert not self.markets[parent].paused
    cid: bytes32 = extcall ICTF(self.ctf).prepareCondition(self.oracle, questionId)
    extcall IOracle(self.oracle).registerMarket(cid, closeTime)
    self.markets[cid] = Market(
        conditionId=cid,
        parentConditionId=parent,
        closeTime=closeTime,
        marketType=marketType,
        paused=False,
        question=question,
    )
    self.marketExists[cid] = True
    log MarketCreated(conditionId=cid, parentConditionId=parent, creator=msg.sender, closeTime=closeTime, marketType=marketType, question=question)
    return cid

@external
def createPrimaryMarket(questionId: bytes32, closeTime: uint256, question: String[256]) -> bytes32:
    assert msg.sender == self.operator, "not operator"
    return self._create(questionId, empty(bytes32), closeTime, 0, question)

@external
def createWildcardMarket(questionId: bytes32, parentConditionId: bytes32, closeTime: uint256, question: String[256], seedUsdc: uint256) -> bytes32:
    assert msg.sender == self.wildcardGenerator or msg.sender == self.operator, "not generator"
    cid: bytes32 = self._create(questionId, parentConditionId, closeTime, 1, question)
    if seedUsdc > 0:
        assert extcall IERC20(self.usdc).transferFrom(msg.sender, self, seedUsdc)
        assert extcall IERC20(self.usdc).approve(self.amm, seedUsdc)
        extcall IAMM(self.amm).seedPool(cid, seedUsdc)
    return cid

@external
def setPaused(conditionId: bytes32, paused: bool):
    assert msg.sender == self.operator, "not operator"
    assert self.marketExists[conditionId]
    self.markets[conditionId].paused = paused
    log MarketPaused(conditionId=conditionId, paused=paused)
