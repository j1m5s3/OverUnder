---
title: Uniform-LVR spike (OU-T008)
status: MIXED
area: contracts
summary: Why MarketAMM v2 is a static pm-AMM with an on-chain close gate. Covers the Vyper fixed-point math, measured accuracy, re-measured gas and bytecode on the final contract, seed-lock LP rules, and the LP value simulations that motivate dynamic L_t (OU-T015). Decision record is ADR-0011.
last_verified: 2026-09-23
pointers:
  - "[contracts/src/lib/NormalMath.vy : L35-57]"
  - "[contracts/src/lib/NormalMath.vy : L91-116]"
  - "[contracts/src/lib/NormalMath.vy : L119-137]"
  - "[contracts/src/lib/NormalMath.vy : L140-159]"
  - "[contracts/src/lib/NormalMath.vy : L179-229]"
  - "[contracts/src/MarketAMM.vy : L1-12]"
  - "[contracts/src/MarketAMM.vy : L60-78]"
  - "[contracts/src/MarketAMM.vy : L105-119]"
  - "[contracts/src/MarketAMM.vy : L126-157]"
  - "[contracts/src/MarketAMM.vy : L159-238]"
  - "[contracts/src/MarketAMM.vy : L240-269]"
  - "[contracts/src/MarketAMM.vy : L319-352]"
  - "[contracts/src/MarketAMM.vy : L354-414]"
  - "[contracts/tests/test_normal_math.py : L129-195]"
  - "[contracts/tests/test_amm_pm.py : L680-719]"
  - "[contracts/tests/test_amm_pm.py : L744-783]"
  - "[oracles/listing/run.py : L291-307]"
---

# Uniform-LVR spike (OU-T008)

Outcome: MarketAMM v2 replaces the CPMM with the **static** pm-AMM of Moallemi & Robinson (2024). It keeps the v1 ABI (additions only) and halts trading at `closeTime` on chain. Dynamic liquidity `L_t` stays phase 2 (OU-T015). The decision record is [ADR-0011](../adr/0011-pm-amm-v2-close-gate.md); the book of record is [ADR-0007](../adr/0007-amm-first-uniform-lvr.md). The code ships in this change, and Base Sepolia keeps the v1 CPMM until the targeted AMM + Factory redeploy runs (ADR-0011).

## Formula

- [SHIPPED] Invariant: with x = YES reserve, y = NO reserve, liquidity L and u = (y − x)/L, the curve is y = L·g(u), x = L·g(−u), where g(w) = w·Φ(w) + φ(w) = E[(w+Z)⁺]. Then g' = Φ, g'' = φ and g(w) − g(−w) = w. [contracts/src/MarketAMM.vy : L1-12]
- [SHIPPED] YES price = Φ(u), exposed as `priceYes` (WAD). Buying YES adds the NO leg to y and removes YES from x, so u and the YES price rise. Pool value at price P is L·φ(Φ⁻¹(P)). [contracts/src/MarketAMM.vy : L263-269]
- [SHIPPED] Seed at P = 0.5: S USDC splits into S YES + S NO, and L = floor(S / φ(0)) ≈ 2.5066·S. Φ⁻¹ is never needed on chain. The seed must be at least `MIN_LP` = 10⁴ base units. [contracts/src/MarketAMM.vy : L126-149]
- [SHIPPED] Safe side: F(x, y; L) = y − L·g(u) grows with both reserves and falls with L, so every rounding keeps reserves at or above the curve. Reserve payouts round down; L grows by floor and shrinks by ceil. Trades price off L·g(w0) rather than the raw reserves, so the rounding surplus above the curve stays with LPs and a later deposit cannot hand it to a trader. [contracts/src/MarketAMM.vy : L181-194] [contracts/src/MarketAMM.vy : L212-225]
- [SHIPPED] Fees use the complete-set mechanic: ceil(50 bps) goes to FeeVault and ceil(50 bps) to a per-pool USDC accumulator `lpFees` outside the invariant (`AMM_FEE_BPS` stays 100). Amounts at or below the two fees revert `dust`, so dust round trips are no longer free. [contracts/src/MarketAMM.vy : L69-72] [contracts/src/MarketAMM.vy : L164-167]

## Fixed point (Vyper 0.4.3)

Vyper has no exp, ln or erf, so `contracts/src/lib/NormalMath.vy` is a stateless module imported as `nm`.

- [SHIPPED] `exp_wad` is a Solady expWad port and `ln_wad` a Solady lnWad port. [contracts/src/lib/NormalMath.vy : L35-57] [contracts/src/lib/NormalMath.vy : L91-116]
- [SHIPPED] Φ(−a) = e^(−a²/2)·N(a)/D(a) with the Hart 5666 / West (2005) rational coefficients. [contracts/src/lib/NormalMath.vy : L119-137]
- [SHIPPED] `g_cdf` returns (g, Φ) from one exp. It computes g(−a) = e·(1/√(2π) − a·N/D), which avoids the φ − aΦ cancellation in the tail. [contracts/src/lib/NormalMath.vy : L140-159]

Measured against a 50-digit Decimal reference (about 520 points on |w| ≤ 6). `test_normal_math.py` asserts ≤ 200 wei for g and ≤ 100 wei for Φ. [contracts/tests/test_normal_math.py : L129-137]

| Function | Max abs error | Gas (harness call) |
|---|---|---|
| `exp_wad` on [−25, 0] | 0.99 wei | 1,308 |
| `ln_wad` on [1e-18, 1e18] | 0.98 wei | 1,972 |
| g(w) | 99 wei (1e-16) | 3,188 (`g_cdf`) |
| Φ(w) | 41 wei | 2,783 (`cdf`) |

g is monotone on 1e-6 grids near w = ±6 and on a ~1e-15 grid around 0, and convex on a 0.05 grid.

## Solver

Buys need u' with g(u') = y'/L. Sells are closed form: the pool receives s tokens and merges m sets, so y − x moves by exactly −s.

- [SHIPPED] `solve_g(c, w0, g0, p0)` returns w at or below the root and verifies g(w) ≤ c. [contracts/src/lib/NormalMath.vy : L179-229]
  - Root < 0 (c < φ(0)): Newton on ln g from the left. ln g is concave, so iterates rise monotonically and stay left of the root. It stops once a step is ≤ 1e-9.
  - Root ≥ 0: Newton on g (convex) from the right, starting at min(c, tangent at w0). It then steps 1e-12 left and verifies, widening to 1e-9 and 1e-6 if needed.
  - At most 10 iterations per branch. A failed verify reverts `solver`, which never happened in 3,000 random and grid cases.
- [SHIPPED] Error: w ends at most 2.4e-9 below the true root (pool-favourable), about 0.6 base units of tokens at a $100 seed. Harness gas: median 13.7k, max 39.4k (left branch, w0 ≈ −3.7 to c just under φ(0)). The test asserts < 45k. [contracts/tests/test_normal_math.py : L175-195]
- [SHIPPED] Quotes and execution call the same `_buy` / `_sell`, so `quoteBuy`/`quoteSell` equal the executed amounts exactly. [contracts/src/MarketAMM.vy : L159-238] [contracts/src/MarketAMM.vy : L240-261]

## Bounds

- [SHIPPED] Numeric domain |u| ≤ 6 (Φ ∈ [1e-9, 1 − 1e-9]); `g_cdf` reverts `u bound` outside it.
- [SHIPPED] Economic bound `U_TRADE_MAX` = 3.719016485455709 (Φ⁻¹(1 − 1e-4)). Buys check y'/L ≤ g(`U_TRADE_MAX`) and sells check u' ≥ −`U_TRADE_MAX` (oriented per side), reverting `price bound`, so prices stay in [1e-4, 1 − 1e-4]. Without the bound the curve gets thin: the prototype sold 252 NO for $0.001 at P_YES = 0.99999986. [contracts/src/MarketAMM.vy : L76-77]
- [SHIPPED] From P = 0.5 the largest single YES buy is about 8.4× the seed. Tiny trades on both sides still quote and execute at the edge.

## Gas and size (final contracts)

Re-measured on the final contracts on 2026-09-23. Method: boa `get_gas_used` (excludes the 21k intrinsic cost and calldata), run as a scratch script with the same flow for the v1 CPMM (`contracts/tests/fixtures/MarketAMMV1.vy`) and v2. Setup: 200 USDC seeds, 10 USDC trades, and 300 random later buys of 100 base units to 60 USDC.

| Operation | CPMM v1 | pm-AMM v2 |
|---|---|---|
| First swap on a new deployment (FeeVault USDC balance 0) | 90,844 | 129,829 |
| Buy YES, first trade on a pool, new trader | 68,944 | 88,029 |
| Buy NO, new trader | 68,949 | 68,035 |
| Buy, later trades (repeat trader, 300 random) | 47,044 | min 42,798 / median 46,229 / p95 52,576 / max 57,892 |
| Buy, worst constructed case (fresh trader, P_YES 1e-4 → 0.5) | — | 90,763 |
| Buy, first trade on 16 fresh pools of random size | — | max 91,437 |
| `sellToUSDC` | 57,546 | 34,751 (34,734–34,893 over 100 random sells) |
| `addLiquidity`, first / repeat deposit by an account | 59,327 / 37,427 | 69,110 / 25,310 |
| `removeLiquidity` (unlocked LP) | — | 27,263 |
| `quoteBuy` | 1,419–1,429 | 16,986–32,080 (median 20,428) |
| `quoteSell` | 2,070 | 9,718 |
| `priceYes` | — | 4,002 |

- A pool's first v2 swap also creates its `lpFees` slot.
- Sells cost about 3.5k more than in the first v2 draft (31.2k), mainly because they now evaluate the curve surplus with one extra `g_cdf`.
- The tests assert that the worst constructed buy and a first buy stay under 150k. [contracts/tests/test_amm_pm.py : L744-783]
- Runtime bytecode: MarketAMM v2 is 17,571 bytes (the first draft was 16,185) and MarketFactory v2 is 7,415 bytes; the limit is 24,576.
- A user listing (`createPermissionlessMarket`, 120-byte question, seed included) costs about 720k execution gas.

## LP accounting

- [SHIPPED] `seedPoolFor` credits seed LP to a named provider (factory or operator only). MarketFactory v2 passes the operator, the generator or the lister, so seed LP never sits in the factory. [contracts/src/MarketAMM.vy : L126-157]
- [SHIPPED] `addLiquidity` is proportional at constant price, with a one-side refund. Reserve legs round up; L and minted shares round down. It is blocked after close like trades. [contracts/src/MarketAMM.vy : L319-352]
- [SHIPPED] `removeLiquidity` is pro rata. Before resolution it merges min(YES, NO) complete sets to USDC; after resolution it returns tokens. It is not gated by `closeTime`, but three locks apply:
  - The seed provider's seed shares cannot be burned before `closeTime` or resolution (`seed locked`).
  - After that, `MIN_LP` = 10⁴ shares of each seed stay locked forever.
  - Total `lpSupply` never drops below `MIN_LP` (`min liquidity`).

  Shares from `addLiquidity` are always withdrawable, and `lpLocked(cid, account)` reports the locked amount. The seed lock makes a listing seed real skin in the game and stops a dust pool from being drained to zero. [contracts/src/MarketAMM.vy : L354-414]

## LP value and model fit

Float sims (seed value 1, no fees, perfect arbitrage; 3,000 Gaussian-score paths, 400 steps). Expected LP terminal wealth:

| Scenario | Static pm-AMM | CPMM | Dynamic pm-AMM |
|---|---|---|---|
| Gaussian score, trading until the end | 0.043 | 0.069 | 0.49 |
| Trading gate at T/2 | 0.70 | 0.78 | 0.49 |
| Trading gate at 0.9T | 0.30 | 0.38 | 0.49 |
| Jump to 0.999 / 0.001 before resolution | 0.0007 | 0.032 | — |

Pool value at a price, both starting at 1: P = 0.9 gives 0.44 (pm) vs 0.60 (CPMM); P = 0.99 gives 0.067 vs 0.199.

- At equal starting value, the static pm-AMM makes LVR uniform across prices; it does not lower total loss. LP protection comes from `L_t` shrinking with time or from freezing the pool.
- Sports scores fit the Gaussian model only between plays. In-game news (injuries, scores) and count wildcards such as "Kelce fumble ≥ 1" are jumps, and both static curves lose almost everything on them.

## Why static plus close gate now

- [SHIPPED] Operator listing sets `closeTime` to kickoff, and wildcards cannot close after their parent. The default-on gate therefore freezes the pool before in-game information arrives, so only pre-game line drift reaches the LPs, the regime where the static curve behaves well. [oracles/listing/run.py : L291-307]
- [SHIPPED] `closeGate` defaults to true, and the operator can toggle it with `setCloseGate`. Buys, sells and `addLiquidity` revert `market closed` at or after `closeTime`; quotes, `priceYes` and `removeLiquidity` stay open. [contracts/src/MarketAMM.vy : L105-119] [contracts/tests/test_amm_pm.py : L680-719]
- [SHIPPED] The static curve has a closed-form seed, a one-dimensional solve and no time state per trade, so every rounding is easy to bound and quotes equal execution.
- [SHIPPED] The LP accounting that dynamic L_t will need already exists: proportional deposits, pro-rata withdrawals, provider-owned seeds and the seed lock (see LP accounting).
- [PHASE2] Dynamic pm-AMM (OU-T015). L_t = L0·√((T − t)/(T − t0)) and V_t = L_t·φ(u), with expected LVR rate V_t / (2(T − t)), uniform in price and time. On each touch it would scale x, y and L by k = √((T − now)/(T − tLast)) (`isqrt` on WAD) and credit the released tokens to LPs. It needs a trusted end time T, which close gates and oracle closeTime already provide.
