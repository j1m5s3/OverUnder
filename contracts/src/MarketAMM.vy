#pragma version 0.4.3
"""
@title MarketAMM v2
@notice Static pm-AMM (uniform-LVR, Moallemi & Robinson 2024) over CTF YES/NO positions, settled in USDC.
        x = yesReserve, y = noReserve, L = liquidity, u = (y - x)/L.
        Curve: y = L*g(u), x = L*g(-u) with g(w) = w*Phi(w) + phi(w). YES price = Phi(u).
        Every rounding keeps the pool on or above the curve; quotes and execution share one code path.
        Rounding surplus (complete sets above the curve) stays with the LPs: trades price off L*g(w0),
        never off the raw reserves, so a later proportional deposit cannot hand that surplus to a trader.
        Seed LP is locked until closeTime or resolution, and MIN_LP of it forever, so lpSupply never
        drops to zero or to dust while the pool exists.
"""
from lib import NormalMath as nm

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

interface IFactoryView:
    def oracle() -> address: view

interface IOracleView:
    def closeTime(conditionId: bytes32) -> uint256: view

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

event LiquidityRemoved:
    provider: indexed(address)
    conditionId: indexed(bytes32)
    lpBurned: uint256
    yesOut: uint256
    noOut: uint256
    usdcOut: uint256

struct Pool:
    yesReserve: uint256
    noReserve: uint256
    lpSupply: uint256
    exists: bool
    liquidity: uint256
    lpFees: uint256
    closeTime: uint256

AMM_FEE_BPS: public(constant(uint256)) = 100
VAULT_FEE_BPS: constant(uint256) = 50
LP_FEE_BPS: constant(uint256) = 50
BPS_DENOM: constant(uint256) = 10_000
WAD: constant(uint256) = 10 ** 18
IWAD: constant(int256) = 10 ** 18
PHI0: constant(uint256) = 398942280401432678            # phi(0) in WAD; seed L = S / phi(0)
U_TRADE_MAX: constant(int256) = 3719016485455709000     # Phi^-1(1 - 1e-4): trades keep P in [1e-4, 1 - 1e-4]
G_TRADE_MAX: constant(uint256) = 3719040431773923363    # g(U_TRADE_MAX), floor
MIN_LP: public(constant(uint256)) = 10 ** 4             # seed shares the seed provider can never withdraw

ctf: public(address)
usdc: public(address)
feeVault: public(address)
operator: public(address)
factory: public(address)
pools: public(HashMap[bytes32, Pool])
lpBalance: public(HashMap[bytes32, HashMap[address, uint256]])
closeGate: public(bool)
seedLocked: public(HashMap[bytes32, HashMap[address, uint256]])

@deploy
def __init__(ctf: address, usdc: address, feeVault: address, operator: address):
    assert ctf != empty(address)
    self.ctf = ctf
    self.usdc = usdc
    self.feeVault = feeVault
    self.operator = operator
    self.closeGate = True
    assert extcall IERC20(usdc).approve(ctf, max_value(uint256))

@external
def setFactory(factory: address):
    assert msg.sender == self.operator
    self.factory = factory

@external
def setCloseGate(enabled: bool):
    assert msg.sender == self.operator, "not operator"
    self.closeGate = enabled

@internal
@view
def _pid(conditionId: bytes32, yes: bool) -> uint256:
    return staticcall ICTF(self.ctf).positionId(conditionId, 0 if yes else 1)

@internal
@view
def _assert_open(closeTime: uint256):
    if self.closeGate and closeTime != 0:
        assert block.timestamp < closeTime, "market closed"

@internal
@pure
def _fee(amount: uint256, bps: uint256) -> uint256:
    return (amount * bps + BPS_DENOM - 1) // BPS_DENOM

@internal
def _seed(conditionId: bytes32, usdcAmount: uint256, provider: address):
    assert msg.sender == self.factory or msg.sender == self.operator, "not authorized"
    assert usdcAmount >= MIN_LP, "seed below min lp"
    assert provider != empty(address), "zero provider"
    assert not self.pools[conditionId].exists, "pool exists"
    assert self.factory != empty(address), "not registered"
    oracle: address = staticcall IFactoryView(self.factory).oracle()
    close: uint256 = staticcall IOracleView(oracle).closeTime(conditionId)
    assert close > 0, "not registered"
    assert extcall IERC20(self.usdc).transferFrom(msg.sender, self, usdcAmount)
    extcall ICTF(self.ctf).splitPosition(conditionId, usdcAmount)
    self.pools[conditionId] = Pool(
        yesReserve=usdcAmount,
        noReserve=usdcAmount,
        lpSupply=usdcAmount,
        exists=True,
        liquidity=usdcAmount * WAD // PHI0,
        lpFees=0,
        closeTime=close,
    )
    self.lpBalance[conditionId][provider] = usdcAmount
    self.seedLocked[conditionId][provider] = usdcAmount
    log PoolSeeded(conditionId=conditionId, usdcAmount=usdcAmount)

@external
def seedPool(conditionId: bytes32, usdcAmount: uint256):
    self._seed(conditionId, usdcAmount, msg.sender)

@external
def seedPoolFor(conditionId: bytes32, usdcAmount: uint256, provider: address):
    self._seed(conditionId, usdcAmount, provider)

@internal
@view
def _buy(p: Pool, buyYes: bool, usdcIn: uint256) -> (uint256, uint256, uint256, uint256, uint256):
    """Returns (tokensOut, newYes, newNo, vaultFee, lpFee)."""
    assert p.exists and p.liquidity > 0, "no pool"
    vault_fee: uint256 = self._fee(usdcIn, VAULT_FEE_BPS)
    lp_fee: uint256 = self._fee(usdcIn, LP_FEE_BPS)
    assert usdcIn > vault_fee + lp_fee, "dust"
    trade: uint256 = usdcIn - vault_fee - lp_fee
    # Oriented reserves: the pool keeps the `trade` complete-set leg of the opposite outcome (r_in)
    # and pays out of the bought outcome (r_out). w = (r_in - r_out)/L is u for YES buys, -u for NO.
    r_in: uint256 = p.yesReserve
    r_out: uint256 = p.noReserve
    if buyYes:
        r_in = p.noReserve
        r_out = p.yesReserve
    L: uint256 = p.liquidity
    r_in_new: uint256 = r_in + trade
    w0: int256 = (convert(r_in, int256) - convert(r_out, int256)) * IWAD // convert(L, int256)
    g0: uint256 = 0
    p0: uint256 = 0
    g0, p0 = nm.g_cdf(w0)
    # Surplus sp = r_in - L*g(w0) = r_out - L*g(-w0) (g(w) - g(-w) = w) is rounding dust held above the curve.
    # Solve from the curve point plus the trade so sp stays in both reserves instead of paying the trader.
    sp: uint256 = r_in - min(r_in, (L * g0 + WAD - 1) // WAD)
    c: uint256 = (r_in_new - sp) * WAD // L
    assert c <= G_TRADE_MAX, "price bound"
    r_out_new: uint256 = r_out
    if g0 < c:
        # w <= root of g(w) = c, so L*g(w) <= r_in_new - sp; r_out_new = r_in_new - L*w rounded up keeps sp.
        w: int256 = nm.solve_g(c, w0, g0, p0)
        if w >= 0:
            r_out_new = r_in_new - L * convert(w, uint256) // WAD
        else:
            r_out_new = r_in_new + (L * convert(-w, uint256) + WAD - 1) // WAD
        r_out_new = min(r_out_new, r_out)
    assert r_out_new > 0, "price bound"
    out: uint256 = trade + (r_out - r_out_new)
    if buyYes:
        return out, r_out_new, r_in_new, vault_fee, lp_fee
    return out, r_in_new, r_out_new, vault_fee, lp_fee

@internal
@view
def _sell(p: Pool, sellYes: bool, s: uint256) -> (uint256, uint256, uint256, uint256, uint256, uint256):
    """Returns (usdcOut, newYes, newNo, merged, vaultFee, lpFee)."""
    assert p.exists and p.liquidity > 0, "no pool"
    r_same: uint256 = p.noReserve
    r_other: uint256 = p.yesReserve
    if sellYes:
        r_same = p.yesReserve
        r_other = p.noReserve
    L: uint256 = p.liquidity
    # Surplus above the curve at the current state (see _buy); it is added back so the seller never merges it.
    wo: int256 = (convert(r_other, int256) - convert(r_same, int256)) * IWAD // convert(L, int256)
    go: uint256 = 0
    po: uint256 = 0
    go, po = nm.g_cdf(wo)
    sp: uint256 = r_other - min(r_other, (L * go + WAD - 1) // WAD)
    # The pool takes s tokens and merges m sets, so (r_other - r_same) moves by exactly -s.
    # New oriented coordinate w' = (r_other - r_same - s)/L, rounded up (g is increasing).
    wn: int256 = (convert(r_other, int256) - convert(r_same, int256) - convert(s, int256)) * IWAD // convert(L, int256) + 1
    assert wn >= -U_TRADE_MAX, "price bound"
    gw: uint256 = 0
    pw: uint256 = 0
    gw, pw = nm.g_cdf(wn)
    r_other_new: uint256 = (L * gw + WAD - 1) // WAD + 1 + sp
    assert r_other_new < r_other, "dust"
    m: uint256 = r_other - r_other_new
    if m > s:
        m = s
        r_other_new = r_other - s
    r_same_new: uint256 = r_same + s - m
    vault_fee: uint256 = self._fee(m, VAULT_FEE_BPS)
    lp_fee: uint256 = self._fee(m, LP_FEE_BPS)
    assert m > vault_fee + lp_fee, "dust"
    usdc_out: uint256 = m - vault_fee - lp_fee
    if sellYes:
        return usdc_out, r_same_new, r_other_new, m, vault_fee, lp_fee
    return usdc_out, r_other_new, r_same_new, m, vault_fee, lp_fee

@external
@view
def quoteBuy(conditionId: bytes32, buyYes: bool, usdcIn: uint256) -> uint256:
    out: uint256 = 0
    ny: uint256 = 0
    nn: uint256 = 0
    vf: uint256 = 0
    lf: uint256 = 0
    out, ny, nn, vf, lf = self._buy(self.pools[conditionId], buyYes, usdcIn)
    return out

@external
@view
def quoteSell(conditionId: bytes32, sellYes: bool, tokenAmount: uint256) -> uint256:
    out: uint256 = 0
    ny: uint256 = 0
    nn: uint256 = 0
    m: uint256 = 0
    vf: uint256 = 0
    lf: uint256 = 0
    out, ny, nn, m, vf, lf = self._sell(self.pools[conditionId], sellYes, tokenAmount)
    return out

@external
@view
def priceYes(conditionId: bytes32) -> uint256:
    p: Pool = self.pools[conditionId]
    assert p.exists and p.liquidity > 0, "no pool"
    u: int256 = (convert(p.noReserve, int256) - convert(p.yesReserve, int256)) * IWAD // convert(p.liquidity, int256)
    return nm.cdf(u)

@external
def buyWithUSDC(conditionId: bytes32, buyYes: bool, usdcIn: uint256, minOut: uint256) -> uint256:
    assert usdcIn > 0
    assert not staticcall ICTF(self.ctf).isResolved(conditionId)
    p: Pool = self.pools[conditionId]
    self._assert_open(p.closeTime)
    out: uint256 = 0
    ny: uint256 = 0
    nn: uint256 = 0
    vault_fee: uint256 = 0
    lp_fee: uint256 = 0
    out, ny, nn, vault_fee, lp_fee = self._buy(p, buyYes, usdcIn)
    assert out >= minOut, "slippage"
    self.pools[conditionId].yesReserve = ny
    self.pools[conditionId].noReserve = nn
    self.pools[conditionId].lpFees = p.lpFees + lp_fee
    trade: uint256 = usdcIn - vault_fee - lp_fee
    assert extcall IERC20(self.usdc).transferFrom(msg.sender, self, usdcIn)
    assert extcall IERC20(self.usdc).transfer(self.feeVault, vault_fee)
    extcall ICTF(self.ctf).splitPosition(conditionId, trade)
    extcall ICTF(self.ctf).safeTransferFrom(self, msg.sender, self._pid(conditionId, buyYes), out, b"")
    log Swap(trader=msg.sender, conditionId=conditionId, buyYes=buyYes, usdcIn=usdcIn, tokensOut=out, vaultFee=vault_fee)
    return out

@external
def sellToUSDC(conditionId: bytes32, sellYes: bool, tokenAmount: uint256, minUsdc: uint256) -> uint256:
    assert tokenAmount > 0
    assert not staticcall ICTF(self.ctf).isResolved(conditionId)
    p: Pool = self.pools[conditionId]
    self._assert_open(p.closeTime)
    usdc_out: uint256 = 0
    ny: uint256 = 0
    nn: uint256 = 0
    m: uint256 = 0
    vault_fee: uint256 = 0
    lp_fee: uint256 = 0
    usdc_out, ny, nn, m, vault_fee, lp_fee = self._sell(p, sellYes, tokenAmount)
    assert usdc_out >= minUsdc, "slippage"
    self.pools[conditionId].yesReserve = ny
    self.pools[conditionId].noReserve = nn
    self.pools[conditionId].lpFees = p.lpFees + lp_fee
    extcall ICTF(self.ctf).safeTransferFrom(msg.sender, self, self._pid(conditionId, sellYes), tokenAmount, b"")
    extcall ICTF(self.ctf).mergePositions(conditionId, m)
    assert extcall IERC20(self.usdc).transfer(self.feeVault, vault_fee)
    assert extcall IERC20(self.usdc).transfer(msg.sender, usdc_out)
    log Swap(trader=msg.sender, conditionId=conditionId, buyYes=not sellYes, usdcIn=usdc_out, tokensOut=tokenAmount, vaultFee=vault_fee)
    return usdc_out

@external
def addLiquidity(conditionId: bytes32, usdcAmount: uint256) -> uint256:
    """
    Proportional deposit at the current price: every pool component (YES, NO, lpFees, L, lpSupply)
    grows by usdcAmount/(max(YES, NO) + lpFees). The unused short-side tokens are refunded.
    """
    p: Pool = self.pools[conditionId]
    assert p.exists and p.liquidity > 0, "no pool"
    assert usdcAmount > 0
    assert not staticcall ICTF(self.ctf).isResolved(conditionId)
    self._assert_open(p.closeTime)
    mx: uint256 = max(p.yesReserve, p.noReserve)
    denom: uint256 = mx + p.lpFees
    # Reserve legs round up (pool keeps at least its share); L and minted shares round down.
    sets: uint256 = (usdcAmount * mx + denom - 1) // denom
    dx: uint256 = (p.yesReserve * usdcAmount + denom - 1) // denom
    dy: uint256 = (p.noReserve * usdcAmount + denom - 1) // denom
    minted: uint256 = p.lpSupply * usdcAmount // denom
    assert minted > 0, "dust"
    p.yesReserve += dx
    p.noReserve += dy
    p.liquidity += p.liquidity * usdcAmount // denom
    p.lpFees += usdcAmount - sets
    p.lpSupply += minted
    self.pools[conditionId] = p
    self.lpBalance[conditionId][msg.sender] += minted
    assert extcall IERC20(self.usdc).transferFrom(msg.sender, self, usdcAmount)
    extcall ICTF(self.ctf).splitPosition(conditionId, sets)
    if sets > dx:
        extcall ICTF(self.ctf).safeTransferFrom(self, msg.sender, self._pid(conditionId, True), sets - dx, b"")
    if sets > dy:
        extcall ICTF(self.ctf).safeTransferFrom(self, msg.sender, self._pid(conditionId, False), sets - dy, b"")
    log LiquidityAdded(provider=msg.sender, conditionId=conditionId, usdcAmount=usdcAmount, lpMinted=minted)
    return minted

@internal
@view
def _lp_locked(conditionId: bytes32, account: address, closeTime: uint256) -> uint256:
    locked: uint256 = self.seedLocked[conditionId][account]
    if locked == 0:
        return 0
    if block.timestamp < closeTime and not staticcall ICTF(self.ctf).isResolved(conditionId):
        return locked
    return min(locked, MIN_LP)

@external
@view
def lpLocked(conditionId: bytes32, account: address) -> uint256:
    """LP shares of `account` that removeLiquidity cannot burn right now."""
    return self._lp_locked(conditionId, account, self.pools[conditionId].closeTime)

@external
def removeLiquidity(conditionId: bytes32, lpAmount: uint256) -> uint256:
    """
    Burns lpAmount and pays the pro-rata YES, NO and lpFees. Before resolution min(YES, NO) complete
    sets are merged to USDC; after resolution tokens are returned for CTF redemption. Not gated by closeTime.
    The seed provider's seed shares are locked until closeTime or resolution ("seed locked"), and MIN_LP of
    them forever, so a listing seed is real skin in the game and the pool can never be drained to zero.
    LP added through addLiquidity is always withdrawable. Returns the USDC paid.
    """
    p: Pool = self.pools[conditionId]
    assert p.exists, "no pool"
    bal: uint256 = self.lpBalance[conditionId][msg.sender]
    assert lpAmount > 0 and lpAmount <= bal, "lp balance"
    assert bal - lpAmount >= self._lp_locked(conditionId, msg.sender, p.closeTime), "seed locked"
    assert p.lpSupply - lpAmount >= MIN_LP, "min liquidity"
    # Payouts round down; the L decrement rounds up, so the remaining pool stays on or above the curve.
    dx: uint256 = p.yesReserve * lpAmount // p.lpSupply
    dy: uint256 = p.noReserve * lpAmount // p.lpSupply
    df: uint256 = p.lpFees * lpAmount // p.lpSupply
    dl: uint256 = min((p.liquidity * lpAmount + p.lpSupply - 1) // p.lpSupply, p.liquidity)
    p.yesReserve -= dx
    p.noReserve -= dy
    p.lpFees -= df
    p.liquidity -= dl
    p.lpSupply -= lpAmount
    self.pools[conditionId] = p
    self.lpBalance[conditionId][msg.sender] -= lpAmount
    usdc_out: uint256 = df
    yes_out: uint256 = dx
    no_out: uint256 = dy
    if not staticcall ICTF(self.ctf).isResolved(conditionId):
        sets: uint256 = min(dx, dy)
        if sets > 0:
            extcall ICTF(self.ctf).mergePositions(conditionId, sets)
        usdc_out += sets
        yes_out -= sets
        no_out -= sets
    if yes_out > 0:
        extcall ICTF(self.ctf).safeTransferFrom(self, msg.sender, self._pid(conditionId, True), yes_out, b"")
    if no_out > 0:
        extcall ICTF(self.ctf).safeTransferFrom(self, msg.sender, self._pid(conditionId, False), no_out, b"")
    if usdc_out > 0:
        assert extcall IERC20(self.usdc).transfer(msg.sender, usdc_out)
    log LiquidityRemoved(provider=msg.sender, conditionId=conditionId, lpBurned=lpAmount, yesOut=yes_out, noOut=no_out, usdcOut=usdc_out)
    return usdc_out
