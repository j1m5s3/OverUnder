#pragma version 0.4.3

interface IERC20:
    def transfer(to: address, amount: uint256) -> bool: nonpayable
    def transferFrom(owner: address, to: address, amount: uint256) -> bool: nonpayable
    def approve(spender: address, amount: uint256) -> bool: nonpayable

interface ICTF:
    def splitPosition(conditionId: bytes32, amount: uint256): nonpayable
    def mergePositions(conditionId: bytes32, amount: uint256): nonpayable
    def safeTransferFrom(sender: address, receiver: address, id: uint256, amount: uint256, data: Bytes[1024]): nonpayable
    def positionId(conditionId: bytes32, outcome: uint8) -> uint256: view
    def isResolved(conditionId: bytes32) -> bool: view
    def setApprovalForAll(operator: address, approved: bool): nonpayable

event PoolSeeded:
    conditionId: indexed(bytes32)
    usdcAmount: uint256

event Swap:
    trader: indexed(address)
    conditionId: indexed(bytes32)
    buyYes: bool
    usdcIn: uint256
    tokensOut: uint256
    vaultFee: uint256

event LiquidityAdded:
    provider: indexed(address)
    conditionId: indexed(bytes32)
    usdcAmount: uint256
    lpMinted: uint256

struct Pool:
    yesReserve: uint256
    noReserve: uint256
    lpSupply: uint256
    exists: bool

AMM_FEE_BPS: public(constant(uint256)) = 100
BPS_DENOM: constant(uint256) = 10_000

ctf: public(address)
usdc: public(address)
feeVault: public(address)
operator: public(address)
factory: public(address)
pools: public(HashMap[bytes32, Pool])
lpBalance: public(HashMap[bytes32, HashMap[address, uint256]])

@deploy
def __init__(ctf: address, usdc: address, feeVault: address, operator: address):
    assert ctf != empty(address)
    self.ctf = ctf
    self.usdc = usdc
    self.feeVault = feeVault
    self.operator = operator
    assert extcall IERC20(usdc).approve(ctf, max_value(uint256))

@external
def setFactory(factory: address):
    assert msg.sender == self.operator
    self.factory = factory

@internal
def _yes(conditionId: bytes32) -> uint256:
    return staticcall ICTF(self.ctf).positionId(conditionId, 0)

@internal
def _no(conditionId: bytes32) -> uint256:
    return staticcall ICTF(self.ctf).positionId(conditionId, 1)

@external
def seedPool(conditionId: bytes32, usdcAmount: uint256):
    assert msg.sender == self.factory or msg.sender == self.operator
    assert usdcAmount > 0
    assert not self.pools[conditionId].exists
    assert extcall IERC20(self.usdc).transferFrom(msg.sender, self, usdcAmount)
    extcall ICTF(self.ctf).splitPosition(conditionId, usdcAmount)
    self.pools[conditionId] = Pool(yesReserve=usdcAmount, noReserve=usdcAmount, lpSupply=usdcAmount, exists=True)
    self.lpBalance[conditionId][msg.sender] = usdcAmount
    log PoolSeeded(conditionId=conditionId, usdcAmount=usdcAmount)

@external
@view
def quoteBuy(conditionId: bytes32, buyYes: bool, usdcIn: uint256) -> uint256:
    p: Pool = self.pools[conditionId]
    vault_fee: uint256 = usdcIn * 50 // BPS_DENOM
    lp_fee: uint256 = usdcIn * 50 // BPS_DENOM
    trade: uint256 = usdcIn - vault_fee - lp_fee
    y: uint256 = p.yesReserve + lp_fee
    n: uint256 = p.noReserve + lp_fee
    k: uint256 = y * n
    if buyYes:
        new_n: uint256 = n + trade
        new_y: uint256 = k // new_n
        return trade + (y - new_y)
    new_y: uint256 = y + trade
    new_n: uint256 = k // new_y
    return trade + (n - new_n)

@external
def buyWithUSDC(conditionId: bytes32, buyYes: bool, usdcIn: uint256, minOut: uint256) -> uint256:
    assert usdcIn > 0
    assert not staticcall ICTF(self.ctf).isResolved(conditionId)
    p: Pool = self.pools[conditionId]
    assert p.exists
    vault_fee: uint256 = usdcIn * 50 // BPS_DENOM
    lp_fee: uint256 = usdcIn * 50 // BPS_DENOM
    trade: uint256 = usdcIn - vault_fee - lp_fee
    assert extcall IERC20(self.usdc).transferFrom(msg.sender, self, usdcIn)
    if vault_fee > 0:
        assert extcall IERC20(self.usdc).transfer(self.feeVault, vault_fee)
    extcall ICTF(self.ctf).splitPosition(conditionId, trade + lp_fee)
    y: uint256 = p.yesReserve + lp_fee
    n: uint256 = p.noReserve + lp_fee
    k: uint256 = y * n
    out: uint256 = 0
    empty_data: Bytes[1024] = b""
    if buyYes:
        new_n: uint256 = n + trade
        new_y: uint256 = k // new_n
        from_pool: uint256 = y - new_y
        out = trade + from_pool
        assert out >= minOut, "slippage"
        p.yesReserve = new_y
        p.noReserve = new_n
        extcall ICTF(self.ctf).safeTransferFrom(self, msg.sender, self._yes(conditionId), out, empty_data)
    else:
        new_y: uint256 = y + trade
        new_n: uint256 = k // new_y
        from_pool: uint256 = n - new_n
        out = trade + from_pool
        assert out >= minOut, "slippage"
        p.yesReserve = new_y
        p.noReserve = new_n
        extcall ICTF(self.ctf).safeTransferFrom(self, msg.sender, self._no(conditionId), out, empty_data)
    self.pools[conditionId] = p
    log Swap(trader=msg.sender, conditionId=conditionId, buyYes=buyYes, usdcIn=usdcIn, tokensOut=out, vaultFee=vault_fee)
    return out

@external
def sellToUSDC(conditionId: bytes32, sellYes: bool, tokenAmount: uint256, minUsdc: uint256) -> uint256:
    assert tokenAmount > 0
    assert not staticcall ICTF(self.ctf).isResolved(conditionId)
    p: Pool = self.pools[conditionId]
    assert p.exists
    y: uint256 = p.yesReserve
    n: uint256 = p.noReserve
    s: uint256 = tokenAmount
    empty_data: Bytes[1024] = b""
    token_in: uint256 = self._yes(conditionId) if sellYes else self._no(conditionId)
    extcall ICTF(self.ctf).safeTransferFrom(msg.sender, self, token_in, s, empty_data)
    disc: uint256 = 0
    x: uint256 = 0
    if sellYes:
        disc = (y + n + s) * (y + n + s) - 4 * n * s
        x = (y + n + s - isqrt(disc)) // 2
        assert x > 0 and x < n and x <= s
        p.yesReserve = y + s - x
        p.noReserve = n - x
    else:
        disc = (n + y + s) * (n + y + s) - 4 * y * s
        x = (n + y + s - isqrt(disc)) // 2
        assert x > 0 and x < y and x <= s
        p.noReserve = n + s - x
        p.yesReserve = y - x
    extcall ICTF(self.ctf).mergePositions(conditionId, x)
    vault_fee: uint256 = x * 50 // BPS_DENOM
    lp_keep: uint256 = x * 50 // BPS_DENOM
    usdc_out: uint256 = x - vault_fee - lp_keep
    assert usdc_out >= minUsdc, "slippage"
    if lp_keep > 0:
        extcall ICTF(self.ctf).splitPosition(conditionId, lp_keep)
        p.yesReserve += lp_keep
        p.noReserve += lp_keep
    if vault_fee > 0:
        assert extcall IERC20(self.usdc).transfer(self.feeVault, vault_fee)
    assert extcall IERC20(self.usdc).transfer(msg.sender, usdc_out)
    self.pools[conditionId] = p
    log Swap(trader=msg.sender, conditionId=conditionId, buyYes=not sellYes, usdcIn=usdc_out, tokensOut=tokenAmount, vaultFee=vault_fee)
    return usdc_out

@external
def addLiquidity(conditionId: bytes32, usdcAmount: uint256) -> uint256:
    p: Pool = self.pools[conditionId]
    assert p.exists
    assert extcall IERC20(self.usdc).transferFrom(msg.sender, self, usdcAmount)
    extcall ICTF(self.ctf).splitPosition(conditionId, usdcAmount)
    minted: uint256 = 0
    if p.lpSupply == 0:
        minted = usdcAmount
        p.yesReserve += usdcAmount
        p.noReserve += usdcAmount
    else:
        minted = usdcAmount * p.lpSupply // (p.yesReserve if p.yesReserve < p.noReserve else p.noReserve)
        p.yesReserve += usdcAmount
        p.noReserve += usdcAmount
    p.lpSupply += minted
    self.lpBalance[conditionId][msg.sender] += minted
    self.pools[conditionId] = p
    log LiquidityAdded(provider=msg.sender, conditionId=conditionId, usdcAmount=usdcAmount, lpMinted=minted)
    return minted
