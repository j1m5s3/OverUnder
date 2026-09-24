#pragma version 0.4.3

interface ICTF:
    def prepareCondition(oracle: address, questionId: bytes32) -> bytes32: nonpayable

interface IOracle:
    def registerMarket(conditionId: bytes32, closeTime: uint256): nonpayable

interface IAMM:
    def seedPoolFor(conditionId: bytes32, usdcAmount: uint256, provider: address): nonpayable

interface IERC20:
    def transferFrom(owner: address, to: address, amount: uint256) -> bool: nonpayable
    def transfer(to: address, amount: uint256) -> bool: nonpayable
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

event UserMarketListed:
    conditionId: indexed(bytes32)
    creator: indexed(address)
    questionId: bytes32
    criteriaHash: bytes32
    seedUsdc: uint256
    listingFee: uint256

event ListingConfigSet:
    minSeedUsdc: uint256
    listingFeeUsdc: uint256
    feeRecipient: address
    minLeadTime: uint256
    maxHorizon: uint256
    listingCooldown: uint256

event PermissionlessSet:
    enabled: bool

event ListerSet:
    account: indexed(address)
    allowed: bool

event LegacyMarketImported:
    conditionId: indexed(bytes32)
    legacyFactory: indexed(address)

struct Market:
    conditionId: bytes32
    parentConditionId: bytes32
    closeTime: uint256
    marketType: uint8
    paused: bool
    question: String[256]

interface ILegacyFactory:
    def markets(conditionId: bytes32) -> Market: view
    def marketExists(conditionId: bytes32) -> bool: view

MARKET_TYPE_USER: public(constant(uint8)) = 2
MIN_QUESTION_BYTES: constant(uint256) = 10
MAX_HORIZON_CAP: constant(uint256) = 31_622_400
MIN_LEAD_FLOOR: constant(uint256) = 600

ctf: public(address)
oracle: public(address)
amm: public(address)
usdc: public(address)
operator: public(address)
wildcardGenerator: public(address)
markets: public(HashMap[bytes32, Market])
marketExists: public(HashMap[bytes32, bool])

# User listing (marketType 2). Closed at deploy: only allowlisted listers until the operator flips permissionless.
permissionless: public(bool)
listerAllowed: public(HashMap[address, bool])
minSeedUsdc: public(uint256)
listingFeeUsdc: public(uint256)
feeRecipient: public(address)
minLeadTime: public(uint256)
maxHorizon: public(uint256)
listingCooldown: public(uint256)
lastListedAt: public(HashMap[address, uint256])
creatorOf: public(HashMap[bytes32, address])
seedOf: public(HashMap[bytes32, uint256])
criteriaHashOf: public(HashMap[bytes32, bytes32])

@deploy
def __init__(ctf: address, oracle: address, amm: address, usdc: address, operator: address, wildcardGenerator: address):
    assert ctf != empty(address) and oracle != empty(address) and operator != empty(address)
    self.ctf = ctf
    self.oracle = oracle
    self.amm = amm
    self.usdc = usdc
    self.operator = operator
    self.wildcardGenerator = wildcardGenerator
    self.minSeedUsdc = 10_000_000
    self.minLeadTime = 3600
    self.maxHorizon = 7_776_000
    self.listingCooldown = 3600

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

@internal
def _seed(conditionId: bytes32, seedUsdc: uint256):
    # USDC is already held by the factory; LP shares go to the caller. MarketAMM locks the seed shares until
    # closeTime or resolution (MIN_LP of them forever), so a listing seed cannot be withdrawn right after listing.
    assert extcall IERC20(self.usdc).approve(self.amm, seedUsdc)
    extcall IAMM(self.amm).seedPoolFor(conditionId, seedUsdc, msg.sender)

@external
def createPrimaryMarket(questionId: bytes32, closeTime: uint256, question: String[256], seedUsdc: uint256) -> bytes32:
    assert msg.sender == self.operator, "not operator"
    assert seedUsdc > 0, "seed required"
    cid: bytes32 = self._create(questionId, empty(bytes32), closeTime, 0, question)
    assert extcall IERC20(self.usdc).transferFrom(msg.sender, self, seedUsdc)
    self._seed(cid, seedUsdc)
    return cid

@external
def createWildcardMarket(questionId: bytes32, parentConditionId: bytes32, closeTime: uint256, question: String[256], seedUsdc: uint256) -> bytes32:
    assert msg.sender == self.wildcardGenerator or msg.sender == self.operator, "not generator"
    cid: bytes32 = self._create(questionId, parentConditionId, closeTime, 1, question)
    if seedUsdc > 0:
        assert extcall IERC20(self.usdc).transferFrom(msg.sender, self, seedUsdc)
        self._seed(cid, seedUsdc)
    return cid

@internal
@view
def _user_question_id(creator: address, salt: bytes32) -> bytes32:
    # Namespaced by creator so a lister can never squat an operator (sha256) questionId.
    return keccak256(abi_encode(creator, salt))

@external
@view
def userQuestionId(creator: address, salt: bytes32) -> bytes32:
    return self._user_question_id(creator, salt)

@external
@view
def userConditionId(creator: address, salt: bytes32) -> bytes32:
    return keccak256(abi_encode(self.oracle, self._user_question_id(creator, salt)))

@external
def createPermissionlessMarket(salt: bytes32, closeTime: uint256, question: String[256], criteriaHash: bytes32, seedUsdc: uint256) -> bytes32:
    assert self.permissionless or self.listerAllowed[msg.sender], "listing closed"
    assert seedUsdc >= self.minSeedUsdc, "seed below min"
    assert closeTime >= block.timestamp + self.minLeadTime, "close too soon"
    assert closeTime <= block.timestamp + self.maxHorizon, "close too far"
    assert len(question) >= MIN_QUESTION_BYTES, "question too short"
    assert criteriaHash != empty(bytes32), "criteria required"
    assert block.timestamp >= self.lastListedAt[msg.sender] + self.listingCooldown, "cooldown"
    self.lastListedAt[msg.sender] = block.timestamp
    questionId: bytes32 = self._user_question_id(msg.sender, salt)
    cid: bytes32 = self._create(questionId, empty(bytes32), closeTime, MARKET_TYPE_USER, question)
    self.creatorOf[cid] = msg.sender
    self.seedOf[cid] = seedUsdc
    self.criteriaHashOf[cid] = criteriaHash
    fee: uint256 = self.listingFeeUsdc
    assert extcall IERC20(self.usdc).transferFrom(msg.sender, self, seedUsdc + fee)
    if fee > 0:
        # FeeVault NAV reads its USDC balance, so a plain transfer accrues to OU holders.
        assert extcall IERC20(self.usdc).transfer(self.feeRecipient, fee)
    self._seed(cid, seedUsdc)
    log UserMarketListed(conditionId=cid, creator=msg.sender, questionId=questionId, criteriaHash=criteriaHash, seedUsdc=seedUsdc, listingFee=fee)
    return cid

@external
def setListingConfig(minSeedUsdc: uint256, listingFeeUsdc: uint256, feeRecipient: address, minLeadTime: uint256, maxHorizon: uint256, listingCooldown: uint256):
    assert msg.sender == self.operator, "not operator"
    assert minSeedUsdc > 0, "min seed"
    assert listingFeeUsdc == 0 or feeRecipient != empty(address), "fee recipient"
    assert minLeadTime >= MIN_LEAD_FLOOR, "lead floor"
    assert maxHorizon > minLeadTime and maxHorizon <= MAX_HORIZON_CAP, "horizon"
    self.minSeedUsdc = minSeedUsdc
    self.listingFeeUsdc = listingFeeUsdc
    self.feeRecipient = feeRecipient
    self.minLeadTime = minLeadTime
    self.maxHorizon = maxHorizon
    self.listingCooldown = listingCooldown
    log ListingConfigSet(minSeedUsdc=minSeedUsdc, listingFeeUsdc=listingFeeUsdc, feeRecipient=feeRecipient, minLeadTime=minLeadTime, maxHorizon=maxHorizon, listingCooldown=listingCooldown)

@external
def setPermissionless(enabled: bool):
    assert msg.sender == self.operator, "not operator"
    self.permissionless = enabled
    log PermissionlessSet(enabled=enabled)

@external
def setLister(account: address, allowed: bool):
    assert msg.sender == self.operator, "not operator"
    assert account != empty(address), "zero account"
    self.listerAllowed[account] = allowed
    log ListerSet(account=account, allowed=allowed)

@external
def importLegacyMarkets(legacyFactory: address, conditionIds: DynArray[bytes32, 50]):
    # Copies v1 rows (paused flag included) so pause and idempotent-create lookups keep working after a factory swap.
    assert msg.sender == self.operator, "not operator"
    assert legacyFactory != empty(address) and legacyFactory != self, "bad legacy"
    for cid: bytes32 in conditionIds:
        if self.marketExists[cid]:
            continue
        assert staticcall ILegacyFactory(legacyFactory).marketExists(cid), "not in legacy"
        m: Market = staticcall ILegacyFactory(legacyFactory).markets(cid)
        self.markets[cid] = m
        self.marketExists[cid] = True
        log LegacyMarketImported(conditionId=cid, legacyFactory=legacyFactory)

@external
def setPaused(conditionId: bytes32, paused: bool):
    assert msg.sender == self.operator, "not operator"
    assert self.marketExists[conditionId]
    self.markets[conditionId].paused = paused
    log MarketPaused(conditionId=conditionId, paused=paused)
