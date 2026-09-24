---
title: MarketAMM v2 static pm-AMM with close gate
status: MIXED
area: contracts
summary: MarketAMM v2 replaces the CPMM with a static pm-AMM (price = Phi((no-yes)/L)), keeps the 100 bps 50/50 fee, gives seed LP to the provider (locked until close), halts trading at closeTime on chain, and reaches Base Sepolia through a targeted AMM+Factory redeploy. Dynamic L_t is OU-T015.
last_verified: 2026-09-24
pointers:
  - "[contracts/src/MarketAMM.vy : L1-12]"
  - "[contracts/src/MarketAMM.vy : L60-78]"
  - "[contracts/src/MarketAMM.vy : L105-119]"
  - "[contracts/src/MarketAMM.vy : L126-157]"
  - "[contracts/src/MarketAMM.vy : L159-238]"
  - "[contracts/src/MarketAMM.vy : L240-269]"
  - "[contracts/src/MarketAMM.vy : L271-317]"
  - "[contracts/src/MarketAMM.vy : L319-352]"
  - "[contracts/src/MarketAMM.vy : L354-414]"
  - "[contracts/src/lib/NormalMath.vy : L119-159]"
  - "[contracts/src/lib/NormalMath.vy : L179-229]"
  - "[contracts/src/MarketFactory.vy : L136-141]"
  - "[contracts/src/MarketFactory.vy : L229-241]"
  - "[contracts/script/deploy_v2.py : L178-268]"
  - "[contracts/script/deploy_ci.py : L312-342]"
  - "[.github/workflows/deploy-contracts.yml : L1-55]"
  - "[backend/app/indexer/listener.py : L136-155]"
  - "[backend/app/markets/trading.py : L47-67]"
  - "[backend/app/markets/router.py : L649-686]"
  - "[contracts/tests/test_amm_pm.py : L680-719]"
  - "[contracts/tests/test_amm_pm.py : L389-441]"
---

## Status

Accepted 2026-09-23. Implements [ADR-0007](0007-amm-first-uniform-lvr.md) for OU-T008 and OU-T009; the spike write-up is [uniform-lvr-spike.md](../explore/uniform-lvr-spike.md). The code and tests ship in this change. Base Sepolia keeps running the v1 CPMM until an operator runs the targeted redeploy described under Decision; this ADR records no deployed addresses.

## Context

- ADR-0007 made a uniform-LVR AMM the book of record and left the formula, gas and migration to a spike.
- The v1 CPMM checked only `isResolved`, so pools traded right up to resolution and LPs absorbed in-game information after kickoff. [contracts/tests/fixtures/MarketAMMV1.vy : L121-125]
- Vyper 0.4.3 has no `exp`, `ln` or `erf`.
- CTF, ConsensusOracle, FeeVault, USDC and Exchange on Base Sepolia hold live conditions and fees. The oracle's agent set is immutable, so a full redeploy would strand markets and force an agent-key change.

## Decision

Curve and price
- [SHIPPED] Static pm-AMM (Moallemi and Robinson 2024). With x = YES reserve, y = NO reserve, liquidity L and u = (y − x)/L, the curve is y = L·g(u), x = L·g(−u), where g(w) = w·Φ(w) + φ(w). The YES price is Φ(u) = Φ((no − yes)/L). `priceYes` returns it as a 1e18 WAD, and the indexer uses the same formula, falling back to the CPMM mid when L is 0. [contracts/src/MarketAMM.vy : L1-12] [contracts/src/MarketAMM.vy : L263-269] [backend/app/indexer/listener.py : L136-155]
- [SHIPPED] The v1 ABI is kept; the additions are `seedPoolFor`, `removeLiquidity`, `priceYes`, `closeGate`/`setCloseGate`, `lpLocked`, `seedLocked`, `MIN_LP` and the `LiquidityRemoved` event. One behaviour change: `quoteBuy(…, 0)` now reverts `dust` where v1 returned 0. `Pool` appends `liquidity`, `lpFees` and `closeTime` after the four v1 fields. [contracts/src/MarketAMM.vy : L60-78]
- [SHIPPED] A seed of S USDC splits into S YES + S NO at P = 0.5, with L = floor(S / φ(0)) ≈ 2.5066·S, so Φ⁻¹ is never needed on chain. [contracts/src/MarketAMM.vy : L126-149]

Fixed-point math
- [SHIPPED] `contracts/src/lib/NormalMath.vy` is a stateless module holding Solady `expWad`/`lnWad` ports, the Hart 5666 / West (2005) rational Φ tail, `g_cdf` (g and Φ from one `exp`, with a cancellation-free g(−a)), `cdf` and `solve_g`. [contracts/src/lib/NormalMath.vy : L35-57] [contracts/src/lib/NormalMath.vy : L119-159]
- [SHIPPED] Measured accuracy against a 50-digit reference: exp and ln within 1 wei, g within 99 wei, Φ within 41 wei. The tests assert ≤ 200 and ≤ 100 wei. [contracts/tests/test_normal_math.py : L129-137]
- [SHIPPED] Buys solve g(w) = c. Left of 0 the solver runs Newton on ln g from the left. Right of 0 it runs Newton on g from the right, then steps left and verifies g(w) ≤ c. Each branch is capped at 10 iterations and reverts `solver` if verification fails; that never happened in about 3,000 random and grid cases. The result lands at most 2.4e-9 below the root (pool-favourable). Sells are closed form. [contracts/src/lib/NormalMath.vy : L179-229] [contracts/src/MarketAMM.vy : L159-238]
- [SHIPPED] Every rounding keeps the pool on or above the curve. Trades price off L·g(w0) instead of the raw reserves, so rounding surplus stays with LPs. [contracts/src/MarketAMM.vy : L181-194]
- [SHIPPED] Two bounds apply. The numeric domain is |u| ≤ 6 (`u bound`). The economic bound is `U_TRADE_MAX` = Φ⁻¹(1 − 1e-4) ≈ 3.719 (`price bound`), which keeps every post-trade price inside [1e-4, 1 − 1e-4]. [contracts/src/MarketAMM.vy : L76-77] [contracts/src/MarketAMM.vy : L185] [contracts/src/MarketAMM.vy : L221]

Fees and quotes
- [SHIPPED] `AMM_FEE_BPS` stays 100: ceil(50 bps) goes to FeeVault and ceil(50 bps) to the pool's USDC accumulator `lpFees`, which sits outside the invariant. Amounts at or below the two fees revert `dust`. Sells charge on the merged USDC. [contracts/src/MarketAMM.vy : L69-72] [contracts/src/MarketAMM.vy : L164-167] [contracts/src/MarketAMM.vy : L232-235]
- [SHIPPED] `quoteBuy`/`quoteSell` run the same `_buy`/`_sell` as execution, so a quote equals the executed amount. [contracts/src/MarketAMM.vy : L240-261]

LP ownership
- [SHIPPED] `seedPoolFor(cid, usdc, provider)` can be called only by the factory or the operator. It requires a factory and a registered oracle `closeTime` (`not registered`), caches that `closeTime`, and credits the LP shares to `provider`. `seedPool` is the same call with `provider = msg.sender`. MarketFactory v2 passes `msg.sender` as the provider, so LP goes to the operator (primaries), the generator (wildcards) or the lister (user markets), never to the factory. [contracts/src/MarketAMM.vy : L126-157] [contracts/src/MarketFactory.vy : L136-141]
- [SHIPPED] `addLiquidity` deposits at constant price. YES, NO, `lpFees`, L and `lpSupply` all grow by usdc / (max(YES, NO) + lpFees), and the unused short-side tokens are refunded. Reserve legs round up; L and minted shares round down. Like trades, it is blocked after close. An optional third argument `minLpOut` (default 0, so the v1 two-argument selector still works) reverts `slippage` when fewer shares are minted; direct LPs should pass it, because a front-run buy shrinks the minted shares and makes an unbounded deposit worth sandwiching. [contracts/src/MarketAMM.vy : L319-352] [contracts/tests/test_amm_pm.py : L389-441]
- [SHIPPED] `removeLiquidity` pays pro-rata YES, NO and `lpFees`. Before resolution it merges min(YES, NO) complete sets to USDC; after resolution it returns the tokens for CTF redemption. It is not gated by `closeTime`. [contracts/src/MarketAMM.vy : L370-414]
- [SHIPPED] Seed lock. The seed provider's seed shares (`seedLocked`) cannot be burned before `closeTime` or resolution (`seed locked`). After that, `MIN_LP` = 10⁴ shares of each seed stay locked forever, and total `lpSupply` never drops below `MIN_LP` (`min liquidity`). Shares from `addLiquidity` are always withdrawable. `lpLocked(cid, account)` reports the locked amount. [contracts/src/MarketAMM.vy : L78] [contracts/src/MarketAMM.vy : L354-368] [contracts/src/MarketAMM.vy : L383-384]

Trading halt at closeTime
- [SHIPPED] `closeGate` is set true in the constructor, and the operator can toggle it with `setCloseGate`. While it is on and `closeTime` ≠ 0, `buyWithUSDC`, `sellToUSDC` and `addLiquidity` revert `market closed` once `block.timestamp ≥ closeTime`, the same instant ConsensusOracle starts accepting attestations. `quoteBuy`, `quoteSell`, `priceYes` and `removeLiquidity` are never gated. [contracts/src/MarketAMM.vy : L105-119] [contracts/src/MarketAMM.vy : L271-317] [contracts/tests/test_amm_pm.py : L680-719]
- [SHIPPED] The API and UI enforce the same halt behind `TRADING_HALT_AT_CLOSE` (default true; deploy-gcp passes true). Quotes, `/aa/cdp-send` trades and CLOB `POST /orders` return 409 (`market closed`, `market resolved` or `listing not confirmed`), and web and mobile disable the ticket. [backend/app/markets/trading.py : L47-67] [backend/app/amm/router.py : L60-63] [web/src/features/trade/tradingWindow.ts : L29-47]

Redeploy
- [SHIPPED] The redeploy is targeted. `migrate_v2` checks everything first and sends nothing if a check fails: operator ownership, `oracle.factory()` still pointing at the legacy factory, and no earlier migration or v2 source pair. It then deploys AMM v2 and Factory v2 next to the legacy pair, configures them, cuts the shared oracle over with `oracle.setFactory`, and copies legacy factory rows (paused flag included) with `importLegacyMarkets` in chunks of 25 (`IMPORT_CHUNK`; 25 rows with 256-byte questions cost about 8.0M gas, half the 2^24 per-transaction cap). CTF, ConsensusOracle, FeeVault, USDC and Exchange are reused, so existing condition ids keep resolving. The new AMM takes the legacy AMM's on-chain `feeVault()`, and `preflight_v2` refuses a `FEE_VAULT_ADDRESS` repo var that differs from it. [contracts/script/deploy_v2.py : L178-268] [contracts/script/deploy_ci.py : L312-342] [contracts/src/MarketFactory.vy : L229-241]
- [SHIPPED] The CI path is `deploy-contracts.yml`, run by manual dispatch in this order: `verify` → `v2` (fork simulation) → `v2` with broadcast. A broadcast pauses the oracle scheduler first because it signs with the shared operator key. After it: set the `AMM_ADDRESS`, `FACTORY_ADDRESS` and `INDEXER_START_BLOCK` repo vars, run `deploy-gcp.yml`, resume the scheduler and re-verify. Also allowlist the new contracts in the CDP Portal paymaster and regenerate the mobile asset with `scripts/sync_mobile_deployments.py`. The runbook is in `infra/gcp/README.md` (contract deployment). [.github/workflows/deploy-contracts.yml : L1-55] [contracts/script/deploy_ci.py : L64-82]
- [SHIPPED] There is no legacy-AMM routing. The API, indexer, web and mobile read only `AMM_ADDRESS`. Legacy pools stay on the old AMM; imported legacy markets still resolve and redeem through the shared oracle and CTF, but the app cannot quote or trade them (quotes revert `no pool`). Rows created on an older oracle (on-chain `closeTime` 0) are hidden with the operator-only `POST /api/v1/markets/{cid}/archive`. [backend/app/markets/router.py : L649-686]
- [PHASE2] Dynamic pm-AMM liquidity L_t = L0·√((T − t)/(T − t0)), which makes expected LVR uniform in both price and time, is tracked as OU-T015 in [TODOS.md](../TODOS.md).

## Consequences

- Long-tail pools quote exactly, and trading stops at kickoff by default: operator NFL primaries set `closeTime` to kickoff and wildcards cannot close after their parent. As a result, only pre-game drift reaches LPs, the regime where the static curve behaves well. [oracles/listing/run.py : L291-307]
- Seed providers own their LP and can exit after close or resolution, instead of the seed being stuck in the factory as in v1.
- Negative: static pm-AMM spreads loss evenly across prices; it does not lower total loss. In float simulations (3,000 Gaussian-score paths, seed value 1, no fees) the static curve keeps less expected LP value than the CPMM in every scenario: 0.043 vs 0.069 when trading runs to the end, and 0.70 vs 0.78 with a halt at T/2. The default-on gate is what keeps the loss small, so an operator who turns `closeGate` off gets worse-than-CPMM LP economics. A user-listed market can also choose a `closeTime` after its outcome is known.
- Negative: the curve fits jump events poorly. In-game news and count wildcards such as "fumble ≥ 1" are jumps; a jump to 0.999 leaves 0.0007 of LP value (CPMM 0.032). Only dynamic L_t (OU-T015) or an earlier close addresses that.
- Negative: gas and size. Runtime bytecode grows to 17,571 bytes (limit 24,576), including the `addLiquidity` `minLpOut` overload. Figures below are boa execution gas on the final contracts, same flow for v1 and v2, excluding the 21k intrinsic cost and calldata:
  - later buys: median 46.2k (v1 47.0k);
  - first buy on a pool: 88.0k (v1 68.9k);
  - first swap on a new deployment: 129.8k (v1 90.8k);
  - worst constructed buy: 90.8k (tests cap buys at 150k);
  - `quoteBuy`: 17–32k (v1 1.4k);
  - `sellToUSDC`: cheaper at 34.8k (v1 57.5k).
- Negative: after cutover, open legacy markets cannot be traded from the app, and trades on the legacy AMM are no longer indexed (the indexer starts at the new deploy block). On the live stack (audited 2026-09-23) this touches one registered legacy market (`0x24c901…`), which is already past close and waiting on research. Three older rows were never registered on the configured oracle; the operator should archive them with the route above.
- Negative: addresses go stale after the broadcast: repo vars, the CDP Portal paymaster allowlist and `mobile/assets/deployments/84532.json` are all updated by hand, and the API keeps the old factory until `deploy-gcp` finishes.
- Negative: seed capital is locked until close, and 0.01 USDC of shares per pool never comes back. `closeGate` is one more operator-trusted switch (ADR-0004).
