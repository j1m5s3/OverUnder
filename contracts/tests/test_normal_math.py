"""NormalMath (pm-AMM fixed-point library) against high-precision references."""

import math
import random
from decimal import Context, Decimal, localcontext

import boa
import pytest

W = 10**18
U_MAX = 6 * W

HARNESS = """#pragma version 0.4.3
from src.lib import NormalMath as nm

@external
@view
def exp_wad(x: int256) -> uint256:
    return nm.exp_wad(x)

@external
@view
def ln_wad(x: uint256) -> int256:
    return nm.ln_wad(x)

@external
@view
def g_cdf(w: int256) -> (uint256, uint256):
    return nm.g_cdf(w)

@external
@view
def cdf(w: int256) -> uint256:
    return nm.cdf(w)

@external
@view
def solve_g(c: uint256, w0: int256) -> int256:
    g0: uint256 = 0
    p0: uint256 = 0
    g0, p0 = nm.g_cdf(w0)
    return nm.solve_g(c, w0, g0, p0)
"""

# vyper locks the global decimal context; the reference runs in its own 50-digit context.
_CTX = Context(prec=50)
_PI = Decimal("3.14159265358979323846264338327950288419716939937510582097494")
_SQRT2PI = (2 * _PI).sqrt(_CTX)


def _pdf(a: Decimal) -> Decimal:
    return (-(a * a) / 2).exp() / _SQRT2PI


def _cdf_neg(a: Decimal) -> Decimal:
    """Phi(-a) for a >= 0: Taylor series below 3, Laplace continued fraction above."""
    if a < 3:
        s, term, n = Decimal(0), a, 0
        while True:
            s += term / (2 * n + 1)
            n += 1
            term = -term * a * a / (2 * n)
            if abs(term) < Decimal(10) ** -45:
                break
        return Decimal(1) / 2 - s / _SQRT2PI
    f = Decimal(0)
    for k in range(1500, 0, -1):
        f = k / (a + f)
    return _pdf(a) / (a + f)


def ref_g_phi(w_wad: int) -> tuple[Decimal, Decimal]:
    """(g(w), Phi(w)) in WAD units, 50-digit reference."""
    with localcontext(_CTX):
        w = Decimal(w_wad) / W
        if w >= 0:
            phi = 1 - _cdf_neg(w)
        else:
            phi = _cdf_neg(-w)
        return (w * phi + _pdf(w)) * W, phi * W


def float_g(w: float) -> float:
    phi = 0.5 * math.erfc(-w / math.sqrt(2))
    return w * phi + math.exp(-w * w / 2) / math.sqrt(2 * math.pi)


def float_root(c: float) -> float:
    lo, hi = -7.0, 7.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if float_g(mid) < c:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


@pytest.fixture(scope="module")
def nm():
    # Fresh sender: keeps the default deployer nonce, and so later tests' contract addresses, unchanged.
    with boa.env.prank(boa.env.generate_address()):
        return boa.loads(HARNESS)


def test_exp_wad_matches_math_exp(nm):
    for x in [-41 * W, -25 * W, -18 * W, -3 * W, -W // 3, -1, 0, 1, W // 7, W, 5 * W, 40 * W]:
        got = nm.exp_wad(x)
        want = math.exp(x / W) * W
        assert abs(got - want) <= max(want * 1e-12, 2), (x, got, want)
    assert nm.exp_wad(-42 * W) == 0
    with boa.reverts("exp overflow"):
        nm.exp_wad(136 * W)


def test_ln_wad_matches_reference(nm):
    for k in range(-30, 31, 3):
        x = int((Decimal(k).exp() * W).to_integral_value())
        got = nm.ln_wad(x)
        want = (Decimal(x) / W).ln() * W
        assert abs(Decimal(got) - want) <= 2, (k, got, want)
    for x in [1, 10**9, W - 1, W, W + 1, 10**30]:
        want = (Decimal(x) / W).ln() * W
        assert abs(Decimal(nm.ln_wad(x)) - want) <= 2
    with boa.reverts("ln undefined"):
        nm.ln_wad(0)


def test_g_and_phi_match_reference(nm):
    rng = random.Random(7)
    points = [i * W // 4 for i in range(-24, 25)] + [rng.randint(-U_MAX, U_MAX) for _ in range(60)]
    for w in points:
        g, phi = nm.g_cdf(w)
        rg, rphi = ref_g_phi(w)
        assert abs(Decimal(g) - rg) <= 200, (w, g, rg)
        assert abs(Decimal(phi) - rphi) <= 100, (w, phi, rphi)
        assert nm.cdf(w) == phi


def test_g_identity_and_domain(nm):
    rng = random.Random(3)
    assert nm.g_cdf(0) == (398942280401432678, W // 2)
    for w in [rng.randint(0, U_MAX) for _ in range(40)]:
        gp, _ = nm.g_cdf(w)
        gn, _ = nm.g_cdf(-w)
        assert abs((gp - gn) - w) <= 200
    with boa.reverts("u bound"):
        nm.g_cdf(U_MAX + 1)
    with boa.reverts("u bound"):
        nm.g_cdf(-U_MAX - 1)
    assert nm.cdf(U_MAX + 1) == W
    assert nm.cdf(-U_MAX - 1) == 0
    assert 0 < nm.cdf(-U_MAX) < nm.cdf(U_MAX) < W


def test_g_monotone_convex_phi_monotone(nm):
    # Coarse grid across the domain: g increasing and convex, Phi increasing.
    grid = [-U_MAX + i * (W // 20) for i in range(241)]
    vals = [nm.g_cdf(w) for w in grid]
    gs = [v[0] for v in vals]
    ps = [v[1] for v in vals]
    assert all(b > a for a, b in zip(gs, gs[1:]))
    assert all(b > a for a, b in zip(ps, ps[1:]))
    assert all(gs[i - 1] + gs[i + 1] >= 2 * gs[i] for i in range(1, len(gs) - 1))
    # Fine grids (1e-6 steps near the left edge, ~1e-15 steps around 0): never decreasing.
    for start, step in [(-U_MAX, 10**12), (-(10**6), 997), (3 * W, 10**12)]:
        prev = None
        for i in range(400):
            g, _ = nm.g_cdf(start + i * step)
            if prev is not None:
                assert g >= prev
            prev = g


def test_solve_g_is_safe_and_accurate(nm):
    rng = random.Random(11)
    worst_gas = 0
    cases = 0
    for _ in range(150):
        w0 = rng.randint(-3_800_000_000_000_000_000, 3_700_000_000_000_000_000)
        g0, _ = nm.g_cdf(w0)
        target = rng.uniform(w0 / W, 3.72)
        c = int(float_g(target) * W)
        if c <= g0:
            continue
        w = nm.solve_g(c, w0)
        worst_gas = max(worst_gas, nm._computation.get_gas_used())
        cases += 1
        g, _ = nm.g_cdf(w)
        assert g <= c, (c, w0, w)
        root = float_root(c / W) * W
        assert w <= root + 10**6, (c, w0, w, root)
        assert root - w <= 10**10, (c, w0, w, root)
    assert cases > 100
    assert worst_gas < 45_000, worst_gas


def test_solve_g_edges(nm):
    # Tiny steps just above the current point, both branches.
    for w0 in [-3_700_000_000_000_000_000, -W, 0, W, 3_700_000_000_000_000_000]:
        g0, _ = nm.g_cdf(w0)
        for bump in [1, 10**6, 10**12]:
            c = g0 + bump
            w = nm.solve_g(c, w0)
            assert w >= w0 - 10**13
            assert nm.g_cdf(w)[0] <= c
    with boa.reverts("price bound"):
        nm.solve_g(U_MAX, 0)
