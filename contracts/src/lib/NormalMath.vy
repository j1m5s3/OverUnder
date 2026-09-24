#pragma version 0.4.3
"""
@title NormalMath
@notice Stateless WAD (1e18) fixed-point helpers for the static pm-AMM in MarketAMM.
        exp: Solady expWad port. ln: Solady lnWad port. Phi(-a): Hart 5666 / West (2005) rational form.
        g(w) = w*Phi(w) + phi(w) = E[(w+Z)^+], g' = Phi, g'' = phi, g(w) - g(-w) = w.
        Pool reserves on the curve: NO = L*g(u), YES = L*g(-u), u = (NO - YES)/L, YES price = Phi(u).
"""

UWAD: constant(uint256) = 10 ** 18
INV_SQRT2PI: constant(uint256) = 398942280401432678     # phi(0) = 1/sqrt(2*pi), floor
U_MAX: constant(uint256) = 6 * 10 ** 18                 # numeric |w| domain (Phi in ~[1e-9, 1-1e-9])

# Hart 5666 numerator (A0..A6) and denominator (B0..B7) coefficients, WAD.
A0: constant(uint256) = 220206867912376000000
A1: constant(uint256) = 221213596169931000000
A2: constant(uint256) = 112079291497871000000
A3: constant(uint256) = 33912866078383000000
A4: constant(uint256) = 6373962203531650000
A5: constant(uint256) = 700383064443688000
A6: constant(uint256) = 35262496599891100
B0: constant(uint256) = 440413735824752000000
B1: constant(uint256) = 793826512519948000000
B2: constant(uint256) = 637333633378831000000
B3: constant(uint256) = 296564248779674000000
B4: constant(uint256) = 86780732202946100000
B5: constant(uint256) = 16064177579207000000
B6: constant(uint256) = 1755667163182640000
B7: constant(uint256) = 88388347648318400

SOLVER_ITERS: constant(uint256) = 10
SOLVER_TOL: constant(int256) = 10 ** 9                  # stop when a Newton step is <= 1e-9


@internal
@pure
def exp_wad(x: int256) -> uint256:
    """exp(x) for WAD x (Solady expWad). Returns 0 below ~-41.4; max abs error < 1 wei on [-25, 0]."""
    if x <= -41446531673892822313:
        return 0
    assert x < 135305999368893231589, "exp overflow"
    t: int256 = unsafe_div(x << 78, 3814697265625)  # 5**18
    k: int256 = (unsafe_div(t << 96, 54916777467707473351141471128) + 2 ** 95) >> 96
    t = t - k * 54916777467707473351141471128
    y: int256 = t + 1346386616545796478920950773328
    y = (unsafe_mul(y, t) >> 96) + 57155421227552351082224309758442
    p: int256 = y + t - 94201549194550492254356042504812
    p = (unsafe_mul(p, y) >> 96) + 28719021644029726153956944680412240
    p = unsafe_mul(p, t) + (4385272521454847904659076985693276 << 96)
    q: int256 = t - 2855989394907223263936484059900
    q = (unsafe_mul(q, t) >> 96) + 50020603652535783019961831881945
    q = (unsafe_mul(q, t) >> 96) - 533845033583426703283633433725380
    q = (unsafe_mul(q, t) >> 96) + 3604857256930695427073651918091429
    q = (unsafe_mul(q, t) >> 96) - 14423608567350463180887372962807573
    q = (unsafe_mul(q, t) >> 96) + 26449188498355588339934803723976023
    r: int256 = unsafe_div(p, q)
    return unsafe_mul(convert(r, uint256), 3822833074963236453042738258902158003155416615667) >> convert(195 - k, uint256)


@internal
@pure
def _log2(x: uint256) -> uint256:
    r: uint256 = 0
    v: uint256 = x
    if v >= 1 << 128:
        v >>= 128
        r += 128
    if v >= 1 << 64:
        v >>= 64
        r += 64
    if v >= 1 << 32:
        v >>= 32
        r += 32
    if v >= 1 << 16:
        v >>= 16
        r += 16
    if v >= 1 << 8:
        v >>= 8
        r += 8
    if v >= 1 << 4:
        v >>= 4
        r += 4
    if v >= 1 << 2:
        v >>= 2
        r += 2
    if v >= 1 << 1:
        r += 1
    return r


@internal
@pure
def ln_wad(x: uint256) -> int256:
    """ln(x) for WAD x > 0 (Remco Bloemen / Solady lnWad)."""
    assert x > 0, "ln undefined"
    k: int256 = convert(self._log2(x), int256) - 96
    t: int256 = convert((x << convert(159 - k, uint256)) >> 159, int256)
    p: int256 = t + 3273285459638523848632254066296
    p = (unsafe_mul(p, t) >> 96) + 24828157081833163892658089445524
    p = (unsafe_mul(p, t) >> 96) + 43456485725739037958740375743393
    p = (unsafe_mul(p, t) >> 96) - 11111509109440967052023855526967
    p = (unsafe_mul(p, t) >> 96) - 45023709667254063763336534515857
    p = (unsafe_mul(p, t) >> 96) - 14706773417378608786704636184526
    p = unsafe_mul(p, t) - (795164235651350426258249787498 << 96)
    q: int256 = t + 5573035233440673466300451813936
    q = (unsafe_mul(q, t) >> 96) + 71694874799317883764090561454958
    q = (unsafe_mul(q, t) >> 96) + 283447036172924575727196451306956
    q = (unsafe_mul(q, t) >> 96) + 401686690394027663651624208769553
    q = (unsafe_mul(q, t) >> 96) + 204048457590392012362485061816622
    q = (unsafe_mul(q, t) >> 96) + 31853899698501571402653359427138
    q = (unsafe_mul(q, t) >> 96) + 909429971244387300277376558375
    r: int256 = unsafe_div(p, q)
    r = unsafe_mul(r, 1677202110996718588342820967067443963516166)
    r = unsafe_add(r, unsafe_mul(16597577552685614221487285958193947469193820559219878177908093499208371, k))
    r = unsafe_add(r, 600920179829731861736702779321621459595472258049074101567377883020018308)
    return r >> 174


@internal
@pure
def _tail(a: uint256) -> (uint256, uint256, uint256):
    """For 0 <= a <= U_MAX returns (e, N, D) with e = exp(-a^2/2) and Phi(-a) = e*N/D."""
    e: uint256 = self.exp_wad(-convert(unsafe_div(unsafe_mul(a, a), 2 * UWAD), int256))
    n: uint256 = unsafe_div(unsafe_mul(A6, a), UWAD) + A5
    n = unsafe_div(unsafe_mul(n, a), UWAD) + A4
    n = unsafe_div(unsafe_mul(n, a), UWAD) + A3
    n = unsafe_div(unsafe_mul(n, a), UWAD) + A2
    n = unsafe_div(unsafe_mul(n, a), UWAD) + A1
    n = unsafe_div(unsafe_mul(n, a), UWAD) + A0
    d: uint256 = unsafe_div(unsafe_mul(B7, a), UWAD) + B6
    d = unsafe_div(unsafe_mul(d, a), UWAD) + B5
    d = unsafe_div(unsafe_mul(d, a), UWAD) + B4
    d = unsafe_div(unsafe_mul(d, a), UWAD) + B3
    d = unsafe_div(unsafe_mul(d, a), UWAD) + B2
    d = unsafe_div(unsafe_mul(d, a), UWAD) + B1
    d = unsafe_div(unsafe_mul(d, a), UWAD) + B0
    return e, n, d


@internal
@pure
def g_cdf(w: int256) -> (uint256, uint256):
    """(g(w), Phi(w)) in WAD for |w| <= U_MAX, one exp. Abs error ~1e-16 against a 60-digit reference."""
    a: uint256 = convert(abs(w), uint256)
    assert a <= U_MAX, "u bound"
    e: uint256 = 0
    n: uint256 = 0
    d: uint256 = 0
    e, n, d = self._tail(a)
    tail: uint256 = e * n // d
    # g(-a) = phi(a) - a*Phi(-a) = e * (1/sqrt(2pi) - a*N/D): factoring e avoids the phi - a*Phi cancellation.
    an: uint256 = a * n // d
    diff: uint256 = 0
    if INV_SQRT2PI > an:
        diff = INV_SQRT2PI - an
    gneg: uint256 = e * diff // UWAD
    if w >= 0:
        return a + gneg, UWAD - tail
    return gneg, tail


@internal
@pure
def cdf(w: int256) -> uint256:
    """Phi(w) in WAD; saturates outside the numeric domain."""
    a: uint256 = convert(abs(w), uint256)
    if a > U_MAX:
        return UWAD if w > 0 else 0
    e: uint256 = 0
    n: uint256 = 0
    d: uint256 = 0
    e, n, d = self._tail(a)
    tail: uint256 = e * n // d
    if w >= 0:
        return UWAD - tail
    return tail


@internal
@pure
def solve_g(c: uint256, w0: int256, g0: uint256, p0: uint256) -> int256:
    """
    Returns w at or below the root of g(w) = c (within ~1e-9) with g(w) <= c verified by evaluation.
    (w0, g0 = g(w0), p0 = Phi(w0)) must satisfy g0 <= c; the caller handles g0 >= c.
    root < 0 (c < phi(0)): Newton on ln g (concave) from the left; iterates rise monotonically and
                           stay left of the root, so the last evaluated point is already safe.
    root >= 0:             Newton on g (convex) from the right starting at min(c, tangent at w0),
                           then pull left 1e-12 and verify (widening up to 1e-6).
    """
    assert c < U_MAX, "price bound"
    gw: uint256 = g0
    pw: uint256 = p0
    w: int256 = w0
    if c < INV_SQRT2PI:
        lc: int256 = self.ln_wad(c)
        for i: uint256 in range(SOLVER_ITERS):
            st: int256 = (lc - self.ln_wad(max(gw, 1))) * convert(gw, int256) // convert(max(pw, 1), int256)
            if st <= SOLVER_TOL:
                return w
            nw: int256 = w + st
            ng: uint256 = 0
            np: uint256 = 0
            ng, np = self.g_cdf(nw)
            if ng > c:
                return w
            w = nw
            gw = ng
            pw = np
        return w
    w = convert(c, int256)
    if p0 > 0:
        t: int256 = w0 + convert((c - g0) * UWAD // p0, int256)
        if t < w:
            w = t
    for i: uint256 in range(SOLVER_ITERS):
        gw, pw = self.g_cdf(w)
        if gw <= c:
            return w
        step: int256 = convert((gw - c) * UWAD // max(pw, 1), int256)
        w -= step
        if step <= SOLVER_TOL:
            break
    w -= 10 ** 6
    for j: uint256 in range(3):
        gw, pw = self.g_cdf(w)
        if gw <= c:
            return w
        w -= 10 ** 9 * convert(10 ** (3 * j), int256)
    raise "solver"
