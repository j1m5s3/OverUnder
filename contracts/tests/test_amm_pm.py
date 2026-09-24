"""MarketAMM v2: static pm-AMM invariant, pricing, fees, bounds, LP add/remove, close gate, seedPoolFor."""

import random

import boa
import pytest
from eth_utils import keccak

from tests.conftest import deploy_protocol
from tests.eip712 import sign_attestation

W = 10**18
PHI0 = 398942280401432678
U_TRADE_MAX = 3719016485455709000
WINDOW = 86_400

G_HARNESS = """#pragma version 0.4.3
from src.lib import NormalMath as nm

@external
@view
def g_cdf(w: int256) -> (uint256, uint256):
    return nm.g_cdf(w)
"""


def _load_isolated(loader, *args):
    """Deploy from a fresh sender so the default deployer's nonce (and every protocol address) is unchanged.

    boa anchors each test and fixture but keeps its storage-write trace per address; shifting the deployer
    nonce would place an AMM at an address a later test uses for the factory and break boa's revert repr.
    """
    with boa.env.prank(boa.env.generate_address()):
        return loader(*args)


@pytest.fixture(scope="module")
def gh():
    return _load_isolated(boa.loads, G_HARNESS)


@pytest.fixture
def proto():
    return deploy_protocol()


_qid = [0]


def _market(proto, seed=200_000_000, close_in=10_000):
    usdc, factory = proto["usdc"], proto["factory"]
    operator = proto["accounts"]["operator"].address
    _qid[0] += 1
    close = boa.env.timestamp + close_in
    with boa.env.prank(operator):
        usdc.faucet(seed)
        usdc.approve(factory.address, seed)
        cid = factory.createPrimaryMarket(keccak(text=f"pm-{_qid[0]}"), close, "pm market", seed)
    return cid, close


def _unseeded(proto, close_in=10_000):
    """Registered (oracle closeTime set) wildcard with no pool."""
    parent, close = _market(proto, close_in=close_in)
    operator = proto["accounts"]["operator"].address
    _qid[0] += 1
    with boa.env.prank(operator):
        child = proto["factory"].createWildcardMarket(keccak(text=f"pm-{_qid[0]}"), parent, close, "child", 0)
    return child, close


def _funded(proto, usdc_amount=10**15):
    usdc, ctf, amm = proto["usdc"], proto["ctf"], proto["amm"]
    who = boa.env.generate_address()
    with boa.env.prank(who):
        usdc.faucet(usdc_amount)
        usdc.approve(amm.address, 2**256 - 1)
        ctf.setApprovalForAll(amm.address, True)
    return who


def _ceil_fee(amount):
    return (amount * 50 + 9_999) // 10_000


def _curve_ok(gh, pool, tol=2):
    """Pool holds at least the curve reserves: L*g(u) <= NO and L*g(-u) <= YES (within tol base units)."""
    x, y, L = pool[0], pool[1], pool[4]
    u = (y - x) * W // L
    gu, _ = gh.g_cdf(u)
    gnu, _ = gh.g_cdf(-u)
    return L * gu // W <= y + tol and L * gnu // W <= x + tol


def _solvent(proto, cid):
    amm, ctf, usdc = proto["amm"], proto["ctf"], proto["usdc"]
    pool = amm.pools(cid)
    assert ctf.balanceOf(amm.address, ctf.positionId(cid, 0)) >= pool[0]
    assert ctf.balanceOf(amm.address, ctf.positionId(cid, 1)) >= pool[1]
    assert usdc.balanceOf(amm.address) >= pool[5]


def _last_event(contract, name):
    return [e for e in contract.get_logs() if type(e).__name__ == name][-1]


def _resolve_arbitrated(proto, cid, close, outcome):
    boa.env.time_travel(seconds=max(0, close + WINDOW - boa.env.timestamp) + 1)
    with boa.env.prank(proto["accounts"]["operator"].address):
        proto["oracle"].resolveArbitrated(cid, outcome)


def test_seed_state_and_mid_price(proto):
    amm, factory = proto["amm"], proto["factory"]
    seed = 200_000_000
    cid, close = _market(proto, seed=seed)
    assert amm.pools(cid) == (seed, seed, seed, True, seed * W // PHI0, 0, close)
    assert abs(amm.priceYes(cid) - W // 2) <= 1
    # Factory v2 seeds through seedPoolFor: LP goes to the creating operator, none to the factory.
    assert amm.lpBalance(cid, proto["accounts"]["operator"].address) == seed
    assert amm.lpBalance(cid, factory.address) == 0
    assert amm.closeGate() is True


@pytest.mark.parametrize("seed", [1_000_000, 200_000_000, 10**12])
def test_random_trades_keep_invariant_price_and_solvency(proto, gh, seed):
    amm, ctf = proto["amm"], proto["ctf"]
    cid, _ = _market(proto, seed=seed)
    yes_id, no_id = ctf.positionId(cid, 0), ctf.positionId(cid, 1)
    trader = _funded(proto)
    rng = random.Random(seed)
    trades = 0
    for _ in range(110):
        side = rng.random() < 0.5
        p_before = amm.priceYes(cid)
        with boa.env.prank(trader):
            if rng.random() < 0.55:
                amt = int(seed * 10 ** rng.uniform(-5, 0.5))
                try:
                    q = amm.quoteBuy(cid, side, amt)
                except boa.BoaError:
                    continue
                out = amm.buyWithUSDC(cid, side, amt, q)
                assert out == q
                assert out >= amt - 2 * _ceil_fee(amt)
                p_after = amm.priceYes(cid)
                assert (p_after > p_before) if side else (p_after < p_before)
            else:
                bal = ctf.balanceOf(trader, yes_id if side else no_id)
                if bal == 0:
                    continue
                s = max(1, int(bal * rng.uniform(0.01, 1.0)))
                try:
                    q = amm.quoteSell(cid, side, s)
                except boa.BoaError:
                    continue
                got = amm.sellToUSDC(cid, side, s, q)
                assert got == q
                p_after = amm.priceYes(cid)
                assert (p_after < p_before) if side else (p_after > p_before)
        trades += 1
        assert 0 < amm.priceYes(cid) < W
        assert _curve_ok(gh, amm.pools(cid))
        _solvent(proto, cid)
    assert trades >= 60


def test_no_trades_symmetric(proto):
    amm = proto["amm"]
    cid_a, _ = _market(proto)
    cid_b, _ = _market(proto)
    trader = _funded(proto)
    with boa.env.prank(trader):
        out_yes = amm.buyWithUSDC(cid_a, True, 30_000_000, 0)
        out_no = amm.buyWithUSDC(cid_b, False, 30_000_000, 0)
    assert abs(out_yes - out_no) <= 1
    pa, pb = amm.pools(cid_a), amm.pools(cid_b)
    assert abs(pa[0] - pb[1]) <= 1 and abs(pa[1] - pb[0]) <= 1
    assert abs((amm.priceYes(cid_a) + amm.priceYes(cid_b)) - W) <= 2


def test_round_trip_always_loses(proto):
    amm = proto["amm"]
    seed = 200_000_000
    cid, _ = _market(proto, seed=seed)
    trader = _funded(proto)
    for d in [3, 4, 7, 50, 199, 1_000, 123_457, 2_000_000, 20_000_000, 150_000_000, 900_000_000]:
        for side in (True, False):
            with boa.env.prank(trader):
                out = amm.buyWithUSDC(cid, side, d, 0)
                try:
                    back = amm.sellToUSDC(cid, side, out, 0)
                except boa.BoaError:
                    back = 0  # dust tokens that cannot be sold back: a total loss
            assert back < d, (d, side, out, back)
            if d >= seed // 100:
                assert d - back >= d * 19 // 1000, (d, back)


def test_random_sequences_make_no_free_money(proto):
    amm, ctf, usdc = proto["amm"], proto["ctf"], proto["usdc"]
    rng = random.Random(42)
    for _ in range(4):
        seed = rng.choice([1_000_000, 200_000_000])
        cid, _ = _market(proto, seed=seed)
        yes_id, no_id = ctf.positionId(cid, 0), ctf.positionId(cid, 1)
        start = 10**13
        trader = _funded(proto, start)
        with boa.env.prank(trader):
            for _ in range(20):
                side = rng.random() < 0.5
                if rng.random() < 0.6:
                    try:
                        amm.buyWithUSDC(cid, side, int(seed * 10 ** rng.uniform(-6, 0.3)), 0)
                    except boa.BoaError:
                        pass
                else:
                    bal = ctf.balanceOf(trader, yes_id if side else no_id)
                    if bal:
                        try:
                            amm.sellToUSDC(cid, side, max(1, int(bal * rng.random())), 0)
                        except boa.BoaError:
                            pass
            for side, pid in ((True, yes_id), (False, no_id)):
                bal = ctf.balanceOf(trader, pid)
                if bal:
                    try:
                        amm.sellToUSDC(cid, side, bal, 0)
                    except boa.BoaError:
                        pass
        leftover = max(ctf.balanceOf(trader, yes_id), ctf.balanceOf(trader, no_id))
        assert usdc.balanceOf(trader) + leftover <= start


def test_fees_ceil_to_vault_and_lp_accumulator(proto):
    amm, usdc, vault = proto["amm"], proto["usdc"], proto["vault"]
    cid, _ = _market(proto)
    trader = _funded(proto)
    usdc_in = 10_000_001
    fee = _ceil_fee(usdc_in)
    with boa.env.prank(trader):
        out = amm.buyWithUSDC(cid, True, usdc_in, 0)
        swap = _last_event(amm, "Swap")
    assert usdc.balanceOf(vault.address) == fee
    assert amm.pools(cid)[5] == fee
    assert (swap.buyYes, swap.usdcIn, swap.tokensOut, swap.vaultFee) == (True, usdc_in, out, fee)

    vault_before = usdc.balanceOf(vault.address)
    lp_before = amm.pools(cid)[5]
    with boa.env.prank(trader):
        got = amm.sellToUSDC(cid, True, out // 3, 0)
        swap = _last_event(amm, "Swap")
    sell_fee = usdc.balanceOf(vault.address) - vault_before
    merged = got + 2 * sell_fee  # m = paid out + vault fee + LP fee, both fees ceil(50 bps of m)
    assert sell_fee == _ceil_fee(merged)
    assert amm.pools(cid)[5] - lp_before == sell_fee
    # v1 sell quirk kept: buyYes = not sellYes, usdcIn = USDC paid out, tokensOut = tokens sold.
    assert (swap.buyYes, swap.usdcIn, swap.tokensOut, swap.vaultFee) == (False, got, out // 3, sell_fee)
    assert usdc.balanceOf(amm.address) >= amm.pools(cid)[5]


def test_dust_trades_revert(proto):
    amm = proto["amm"]
    cid, _ = _market(proto)
    trader = _funded(proto)
    with boa.env.prank(trader):
        for amt in (1, 2):
            with boa.reverts("dust"):
                amm.quoteBuy(cid, True, amt)
            with boa.reverts("dust"):
                amm.buyWithUSDC(cid, True, amt, 0)
        assert amm.buyWithUSDC(cid, True, 3, 0) >= 1
        with boa.reverts("dust"):
            amm.quoteSell(cid, True, 1)


def test_quotes_revert_without_pool(proto):
    amm = proto["amm"]
    missing = b"\x99" * 32
    with boa.reverts("no pool"):
        amm.quoteBuy(missing, True, 1_000_000)
    with boa.reverts("no pool"):
        amm.quoteSell(missing, True, 1_000_000)
    with boa.reverts("no pool"):
        amm.priceYes(missing)


def test_price_bound_and_extreme_quotes(proto, gh):
    amm, ctf = proto["amm"], proto["ctf"]
    seed = 100_000_000
    cid, _ = _market(proto, seed=seed)
    trader = _funded(proto)
    with boa.reverts("price bound"):
        amm.quoteBuy(cid, True, 100 * seed)
    # Largest YES buy that still quotes: walks the price to just under the 1 - 1e-4 bound.
    lo, hi = 1, 100 * seed
    while hi - lo > 1:
        mid = (lo + hi) // 2
        try:
            amm.quoteBuy(cid, True, mid)
            lo = mid
        except boa.BoaError:
            hi = mid
    with boa.env.prank(trader):
        amm.buyWithUSDC(cid, True, lo, 0)
        with boa.reverts("price bound"):
            amm.buyWithUSDC(cid, True, seed // 100, 0)
    p = amm.priceYes(cid)
    assert W - 10**14 - 10**9 <= p < W
    pool = amm.pools(cid)
    assert (pool[1] - pool[0]) * W // pool[4] <= U_TRADE_MAX
    assert _curve_ok(gh, pool)
    # Tiny trades on both sides still quote and execute at the extreme.
    with boa.env.prank(trader):
        for amt in (1_000, 1_000_000):
            assert amm.quoteBuy(cid, False, amt) > amt
            q = amm.quoteBuy(cid, False, amt)
            assert amm.buyWithUSDC(cid, False, amt, q) == q
        yes_bal = ctf.balanceOf(trader, ctf.positionId(cid, 0))
        q = amm.quoteSell(cid, True, 1_000_000)
        assert amm.sellToUSDC(cid, True, 1_000_000, q) == q
        # Selling every YES back would push past the lower bound.
        with boa.reverts("price bound"):
            amm.quoteSell(cid, True, yes_bal * 50)
    assert _curve_ok(gh, amm.pools(cid))
    _solvent(proto, cid)


def test_add_liquidity_keeps_price_and_refunds_short_side(proto, gh):
    amm, ctf, usdc = proto["amm"], proto["ctf"], proto["usdc"]
    cid, _ = _market(proto, seed=100_000_000)
    trader = _funded(proto)
    with boa.env.prank(trader):
        amm.buyWithUSDC(cid, True, 60_000_000, 0)
    p0 = amm.priceYes(cid)
    pool0 = amm.pools(cid)
    lp = _funded(proto)
    usdc_before = usdc.balanceOf(lp)
    amount = 50_000_000
    with boa.env.prank(lp):
        minted = amm.addLiquidity(cid, amount)
    pool1 = amm.pools(cid)
    # Reserves are integer base units, so u = (NO - YES)/L can only move by ~1/L per unit of rounding.
    assert abs(amm.priceYes(cid) - p0) <= 3 * W // pool0[4]
    assert minted == pool0[2] * amount // (max(pool0[0], pool0[1]) + pool0[5])
    assert amm.lpBalance(cid, lp) == minted
    assert pool1[2] == pool0[2] + minted
    assert usdc_before - usdc.balanceOf(lp) == amount
    # YES was bought, so YES is the short reserve: the depositor gets the unused YES leg back.
    assert ctf.balanceOf(lp, ctf.positionId(cid, 0)) > 0
    assert ctf.balanceOf(lp, ctf.positionId(cid, 1)) == 0
    assert _curve_ok(gh, pool1)
    _solvent(proto, cid)


def test_add_then_remove_returns_deposit(proto):
    amm, ctf, usdc = proto["amm"], proto["ctf"], proto["usdc"]
    cid, _ = _market(proto, seed=100_000_000)
    trader = _funded(proto)
    with boa.env.prank(trader):
        amm.buyWithUSDC(cid, False, 40_000_000, 0)
    lp = _funded(proto)
    start = usdc.balanceOf(lp)
    amount = 25_000_000
    p0 = amm.priceYes(cid)
    L0 = amm.pools(cid)[4]
    with boa.env.prank(lp):
        minted = amm.addLiquidity(cid, amount)
        got = amm.removeLiquidity(cid, minted)
        removed = _last_event(amm, "LiquidityRemoved")
    assert amm.lpBalance(cid, lp) == 0
    assert abs(amm.priceYes(cid) - p0) <= 3 * W // L0
    yes = ctf.balanceOf(lp, ctf.positionId(cid, 0))
    no = ctf.balanceOf(lp, ctf.positionId(cid, 1))
    assert abs(yes - no) <= 2  # leftover pool leg plus refund form complete sets
    spent = start - usdc.balanceOf(lp)
    assert spent == amount - got
    assert amount - 4 <= got + min(yes, no) <= amount
    assert (removed.provider, removed.lpBurned, removed.usdcOut) == (lp, minted, got)
    # NO was bought, so YES is the long reserve: the exit pays the YES excess, the NO came from the add refund.
    assert (removed.yesOut, removed.noOut) == (yes, 0)
    _solvent(proto, cid)


def test_remove_liquidity_checks_balance(proto):
    amm = proto["amm"]
    cid, _ = _market(proto)
    stranger = _funded(proto)
    with boa.env.prank(stranger):
        with boa.reverts("lp balance"):
            amm.removeLiquidity(cid, 1)
        with boa.reverts("lp balance"):
            amm.removeLiquidity(cid, 0)


def test_seed_pool_for_credits_provider_who_exits_after_resolution(proto):
    amm, ctf, usdc = proto["amm"], proto["ctf"], proto["usdc"]
    operator = proto["accounts"]["operator"].address
    cid, close = _unseeded(proto)
    provider = boa.env.generate_address()
    seed = 80_000_000
    with boa.env.prank(operator):
        usdc.faucet(seed)
        usdc.approve(amm.address, seed)
        amm.seedPoolFor(cid, seed, provider)
    assert amm.lpBalance(cid, provider) == seed
    assert amm.lpBalance(cid, operator) == 0
    assert amm.pools(cid)[6] == close
    trader = _funded(proto)
    with boa.env.prank(trader):
        out = amm.buyWithUSDC(cid, True, 20_000_000, 0)
        amm.sellToUSDC(cid, True, out // 4, 0)
    pool = amm.pools(cid)
    fees = pool[5]
    assert fees > 0
    with boa.env.prank(provider):
        with boa.reverts("seed locked"):
            amm.removeLiquidity(cid, 1)
    _resolve_arbitrated(proto, cid, close, 0)
    with boa.env.prank(trader):
        with boa.reverts():
            amm.buyWithUSDC(cid, True, 1_000_000, 0)
    min_lp = amm.MIN_LP()
    exit_lp = seed - min_lp
    assert amm.lpLocked(cid, provider) == min_lp
    with boa.env.prank(provider):
        with boa.reverts("seed locked"):
            amm.removeLiquidity(cid, seed)
        got = amm.removeLiquidity(cid, exit_lp)
    assert got == fees * exit_lp // seed
    yes_out, no_out = pool[0] * exit_lp // seed, pool[1] * exit_lp // seed
    yes_id, no_id = ctf.positionId(cid, 0), ctf.positionId(cid, 1)
    assert ctf.balanceOf(provider, yes_id) == yes_out
    assert ctf.balanceOf(provider, no_id) == no_out
    # MIN_LP stays locked forever, so the pool never reaches lpSupply 0.
    after = amm.pools(cid)
    assert after[2] == min_lp and after[4] > 0 and after[3] is True
    with boa.env.prank(provider):
        ctf.redeemPositions(cid, 0, yes_out)
    assert usdc.balanceOf(provider) == got + yes_out
    _solvent(proto, cid)


def test_seed_below_min_lp_reverts(proto):
    amm, usdc = proto["amm"], proto["usdc"]
    operator = proto["accounts"]["operator"].address
    cid, _ = _unseeded(proto)
    min_lp = amm.MIN_LP()
    with boa.env.prank(operator):
        usdc.faucet(min_lp)
        usdc.approve(amm.address, min_lp)
        with boa.reverts("seed below min lp"):
            amm.seedPoolFor(cid, min_lp - 1, operator)
        with boa.reverts("seed below min lp"):
            amm.seedPool(cid, 0)
        amm.seedPool(cid, min_lp)
    assert amm.pools(cid)[2] == min_lp


def _drain_seed_after_close(proto, seed=10_000_000):
    """User-listing shaped pool (provider != operator), past closeTime with the gate off, seed drained to MIN_LP."""
    amm, usdc = proto["amm"], proto["usdc"]
    operator = proto["accounts"]["operator"].address
    cid, close = _unseeded(proto, close_in=1_000)
    provider = _funded(proto, 10**12)
    with boa.env.prank(operator):
        usdc.faucet(seed)
        usdc.approve(amm.address, seed)
        amm.seedPoolFor(cid, seed, provider)
    min_lp = amm.MIN_LP()
    with boa.env.prank(provider):
        with boa.reverts("seed locked"):
            amm.removeLiquidity(cid, seed - min_lp)
    boa.env.time_travel(seconds=close - boa.env.timestamp + 1)
    with boa.env.prank(operator):
        amm.setCloseGate(False)
    with boa.env.prank(provider):
        with boa.reverts("seed locked"):
            amm.removeLiquidity(cid, seed - min_lp + 1)
        amm.removeLiquidity(cid, seed - min_lp)
    pool = amm.pools(cid)
    assert pool[2] == min_lp and pool[4] > 0
    return cid, close, provider


def _victim_round_trip(proto, cid, deposit=1_000 * 10**6):
    """Victim adds `deposit`, a trader round-trips a 3-unit YES buy, the victim exits. Returns (trader PnL, victim loss)."""
    amm, ctf, usdc = proto["amm"], proto["ctf"], proto["usdc"]
    yes_id, no_id = ctf.positionId(cid, 0), ctf.positionId(cid, 1)
    victim = _funded(proto, deposit)
    with boa.env.prank(victim):
        minted = amm.addLiquidity(cid, deposit)
    trader = _funded(proto, 10**9)
    start = usdc.balanceOf(trader)
    with boa.env.prank(trader):
        out = amm.buyWithUSDC(cid, True, 3, 0)
        try:
            amm.sellToUSDC(cid, True, out, 0)
        except boa.BoaError:
            pass  # dust that cannot be sold back is a loss for the trader
        # Sell side: split complete sets and dump both legs; a seller must not merge the surplus either.
        sets = 10 * 10**6
        usdc.approve(ctf.address, sets)
        ctf.splitPosition(cid, sets)
        amm.sellToUSDC(cid, True, sets, 0)
        amm.sellToUSDC(cid, False, sets, 0)
    left = ctf.balanceOf(trader, yes_id)
    trader_pnl = usdc.balanceOf(trader) + left - start  # YES valued at 1 USDC: an upper bound
    with boa.env.prank(victim):
        amm.removeLiquidity(cid, minted)
    yes, no = ctf.balanceOf(victim, yes_id), ctf.balanceOf(victim, no_id)
    sets = min(yes, no)
    p = amm.priceYes(cid)
    value = usdc.balanceOf(victim) + sets + (yes - sets) * p // W + (no - sets) * (W - p) // W
    return trader_pnl, deposit - value


def test_min_lp_lock_stops_dust_pool_surplus_theft(proto, gh):
    """Regression: last LP left 1 share (Pool 1,1,L=2), victim added 1000 USDC, a 3-unit buy took ~376 USDC of YES."""
    amm = proto["amm"]
    cid, _, _ = _drain_seed_after_close(proto)
    assert abs(amm.priceYes(cid) - W // 2) <= W // 10**4
    assert _curve_ok(gh, amm.pools(cid))
    trader_pnl, victim_loss = _victim_round_trip(proto, cid)
    assert trader_pnl <= 2, trader_pnl
    assert victim_loss <= 1_000, victim_loss  # <= 1e-6 of the deposit
    _solvent(proto, cid)


def test_rounding_surplus_stays_with_lps(proto, gh):
    """Single-share exits leave complete sets above the curve; a later deposit must not scale them into a trader's fill."""
    amm = proto["amm"]
    cid, _, _ = _drain_seed_after_close(proto)
    grinder = _funded(proto, 10**9)
    with boa.env.prank(grinder):
        minted = amm.addLiquidity(cid, amm.MIN_LP())
        for _ in range(min(minted, 150)):
            amm.removeLiquidity(cid, 1)
    x, y, _, _, L, _, _ = amm.pools(cid)
    u = (y - x) * W // L
    gu, _ = gh.g_cdf(u)
    surplus = y - L * gu // W
    assert surplus >= 20, surplus  # the grind really did push the pool above the curve
    trader_pnl, victim_loss = _victim_round_trip(proto, cid)
    assert trader_pnl <= 2, trader_pnl
    assert victim_loss <= 1_000, victim_loss
    assert _curve_ok(gh, amm.pools(cid))
    _solvent(proto, cid)


def test_pool_cannot_be_drained_dead(proto, gh):
    """Regression: a full LP exit used to leave Pool(0,0,0,exists) that no trade, deposit or seed could revive."""
    amm, usdc = proto["amm"], proto["usdc"]
    operator = proto["accounts"]["operator"].address
    cid, close, provider = _drain_seed_after_close(proto)
    min_lp = amm.MIN_LP()
    with boa.env.prank(provider):
        with boa.reverts("seed locked"):
            amm.removeLiquidity(cid, min_lp)
    # Still tradeable and refillable at the last price.
    p0 = amm.priceYes(cid)
    lp = _funded(proto)
    with boa.env.prank(lp):
        minted = amm.addLiquidity(cid, 50_000_000)
    assert abs(amm.priceYes(cid) - p0) <= 3 * W // min_lp
    trader = _funded(proto)
    with boa.env.prank(trader):
        out = amm.buyWithUSDC(cid, True, 5_000_000, 0)
        assert amm.sellToUSDC(cid, True, out, 0) > 0
    with boa.env.prank(lp):
        amm.removeLiquidity(cid, minted)
    assert amm.pools(cid)[2] == min_lp
    assert _curve_ok(gh, amm.pools(cid))
    with boa.env.prank(operator):
        usdc.faucet(1_000_000)
        usdc.approve(amm.address, 1_000_000)
        with boa.reverts("pool exists"):
            amm.seedPool(cid, 1_000_000)
    # Deposits stay gated: closed (gate on) and resolved pools take no new liquidity.
    with boa.env.prank(operator):
        amm.setCloseGate(True)
    with boa.env.prank(lp):
        with boa.reverts("market closed"):
            amm.addLiquidity(cid, 1_000_000)
    _resolve_arbitrated(proto, cid, close, 1)
    with boa.env.prank(operator):
        amm.setCloseGate(False)
    with boa.env.prank(lp):
        with boa.reverts():
            amm.addLiquidity(cid, 1_000_000)
    _solvent(proto, cid)


def test_seed_pool_auth_and_registration(proto):
    amm, usdc = proto["amm"], proto["usdc"]
    operator = proto["accounts"]["operator"].address
    cid, _ = _unseeded(proto)
    stranger = _funded(proto)
    with boa.env.prank(stranger):
        with boa.reverts("not authorized"):
            amm.seedPoolFor(cid, 1_000_000, stranger)
        with boa.reverts("not authorized"):
            amm.seedPool(cid, 1_000_000)
    with boa.env.prank(operator):
        usdc.faucet(10_000_000)
        usdc.approve(amm.address, 10_000_000)
        with boa.reverts("zero provider"):
            amm.seedPoolFor(cid, 1_000_000, "0x0000000000000000000000000000000000000000")
        with boa.reverts("not registered"):
            amm.seedPool(b"\x77" * 32, 1_000_000)
        amm.seedPool(cid, 1_000_000)
        assert amm.lpBalance(cid, operator) == 1_000_000
        with boa.reverts("pool exists"):
            amm.seedPool(cid, 1_000_000)
    bare = _load_isolated(boa.load, "src/MarketAMM.vy", proto["ctf"].address, usdc.address, proto["vault"].address, operator)
    with boa.env.prank(operator):
        with boa.reverts("not registered"):
            bare.seedPool(cid, 1_000_000)


def test_close_gate_halts_trades_but_not_quotes_or_exit(proto):
    amm, usdc = proto["amm"], proto["usdc"]
    operator = proto["accounts"]["operator"].address
    cid, close = _unseeded(proto, close_in=1_000)
    provider = boa.env.generate_address()
    with boa.env.prank(operator):
        usdc.faucet(50_000_000)
        usdc.approve(amm.address, 50_000_000)
        amm.seedPoolFor(cid, 50_000_000, provider)
    trader = _funded(proto)
    with boa.env.prank(trader):
        out = amm.buyWithUSDC(cid, True, 5_000_000, 0)
    boa.env.time_travel(seconds=close - boa.env.timestamp)
    assert boa.env.timestamp == close
    with boa.env.prank(trader):
        with boa.reverts("market closed"):
            amm.buyWithUSDC(cid, True, 1_000_000, 0)
        with boa.reverts("market closed"):
            amm.sellToUSDC(cid, True, out // 2, 0)
        with boa.reverts("market closed"):
            amm.addLiquidity(cid, 1_000_000)
        assert amm.quoteBuy(cid, True, 1_000_000) > 0
        assert amm.quoteSell(cid, True, out // 2) > 0
        assert 0 < amm.priceYes(cid) < W
    with boa.env.prank(provider):
        assert amm.removeLiquidity(cid, 10_000_000) > 0
    with boa.env.prank(trader):
        with boa.reverts("not operator"):
            amm.setCloseGate(False)
    with boa.env.prank(operator):
        amm.setCloseGate(False)
    assert amm.closeGate() is False
    with boa.env.prank(trader):
        q = amm.quoteSell(cid, True, out // 2)
        assert amm.sellToUSDC(cid, True, out // 2, q) == q
    with boa.env.prank(operator):
        amm.setCloseGate(True)
    with boa.env.prank(trader):
        with boa.reverts("market closed"):
            amm.buyWithUSDC(cid, False, 1_000_000, 0)


def test_trades_before_close_then_consensus_resolution(proto):
    amm, oracle = proto["amm"], proto["oracle"]
    cid, close = _market(proto, close_in=60)
    trader = _funded(proto)
    with boa.env.prank(trader):
        amm.buyWithUSDC(cid, True, 5_000_000, 0)
    boa.env.time_travel(seconds=61)
    evidence = b"\x11" * 32
    deadline = boa.env.timestamp + 1_000
    sigs = [
        sign_attestation(proto["accounts"][n].key, oracle.address, proto["chain_id"], cid, 0, evidence, deadline)
        for n in ("alpha", "beta", "gamma")
    ]
    oracle.submitConsensus(cid, 0, evidence, deadline, sigs)
    operator = proto["accounts"]["operator"].address
    with boa.env.prank(operator):
        amm.setCloseGate(False)
    with boa.env.prank(trader):
        with boa.reverts():
            amm.buyWithUSDC(cid, True, 1_000_000, 0)


def test_buy_gas_worst_case_under_budget(proto):
    """Fresh trader, left-tail solver path (P_YES ~ 1e-4 bought back to ~0.5): the costliest buy."""
    amm, usdc = proto["amm"], proto["usdc"]
    seed = 200_000_000
    cid, _ = _market(proto, seed=seed)
    whale = _funded(proto)
    lo, hi = 1, 100 * seed
    while hi - lo > 1:
        mid = (lo + hi) // 2
        try:
            amm.quoteBuy(cid, False, mid)
            lo = mid
        except boa.BoaError:
            hi = mid
    with boa.env.prank(whale):
        amm.buyWithUSDC(cid, False, lo, 0)
    assert amm.priceYes(cid) < 2 * 10**14
    x, y, _, _, L, _, _ = amm.pools(cid)
    trade = (PHI0 - 10**6) * L // W - y
    usdc_in = trade * 10_000 // 9_900
    while usdc_in - 2 * _ceil_fee(usdc_in) < trade:
        usdc_in += 1
    fresh = boa.env.generate_address()
    with boa.env.prank(fresh):
        usdc.faucet(usdc_in)
        usdc.approve(amm.address, usdc_in)
        amm.buyWithUSDC(cid, True, usdc_in, 0)
        gas = amm._computation.get_gas_used()
    assert gas < 150_000, gas
    assert abs(amm.priceYes(cid) - W // 2) < 10**12


def test_first_buy_gas_under_budget():
    proto = deploy_protocol()
    amm = proto["amm"]
    cid, _ = _market(proto)
    trader = _funded(proto)
    with boa.env.prank(trader):
        amm.buyWithUSDC(cid, True, 10_000_000, 0)
        assert amm._computation.get_gas_used() < 150_000
