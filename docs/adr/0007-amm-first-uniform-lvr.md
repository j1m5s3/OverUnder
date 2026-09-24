---
title: AMM-first uniform-LVR
status: MIXED
area: contracts
summary: The book of record is a uniform-LVR AMM for every market. MarketAMM v2 (static pm-AMM, ADR-0011) and loosely gated user listing (ADR-0012) implement it. Dynamic liquidity stays phase 2, and the CLOB is an optional overlay.
last_verified: 2026-09-23
pointers:
  - "[contracts/src/MarketAMM.vy : L1-12]"
  - "[contracts/src/MarketAMM.vy : L69-78]"
  - "[contracts/src/MarketAMM.vy : L271-317]"
  - "[contracts/src/MarketFactory.vy : L177-199]"
  - "[contracts/src/Exchange.vy : L128-159]"
  - "[contracts/tests/fixtures/MarketAMMV1.vy : L121-201]"
  - "[docs/adr/0011-pm-amm-v2-close-gate.md : L40-73]"
  - "[docs/adr/0012-loosely-gated-user-listing.md : L42-112]"
---

## Status

Accepted 2026-09-18. Supersedes [ADR-0001](0001-hybrid-clob-amm.md).

Implemented 2026-09-23 by [ADR-0011](0011-pm-amm-v2-close-gate.md) (OU-T008 and OU-T009: static pm-AMM MarketAMM v2 with an on-chain close gate) and [ADR-0012](0012-loosely-gated-user-listing.md) (OU-T010: loosely gated user listing). Base Sepolia switches over only after the targeted AMM + Factory redeploy in ADR-0011. Dynamic liquidity L_t is still open (OU-T015).

## Context

Polymarket-class CLOBs need professional market makers. That works on flagship events and fails on the long tail OverUnder wants (hyper-local, niche, AI-generated children). The MVP shipped a hybrid: Type 0 primaries on `Exchange.matchOrders`, Type 1 wildcards on a YES/NO CPMM. [contracts/src/Exchange.vy : L128-159] [contracts/tests/fixtures/MarketFactoryV1.vy : L81-89]

A CPMM on outcome tokens is a poor long-term book. Tokens expire at $0 or $1; LVR concentrates at extremes; LPs can be drained as the market becomes certain. LMSR has the same non-uniform loss shape under Gaussian-score dynamics.

Prediction-native AMMs address that. Paradigm pm-AMM (Moallemi and Robinson, 2024) is a uniform-LVR invariant for Gaussian score dynamics. Moallemi, Robinson, and Zhu (2026) generalize uniform-loss AMMs to win-martingales and prescribed loss schedules over time ([arXiv:2607.17428](https://arxiv.org/abs/2607.17428)). Dynamic spreads are a fee overlay, not a second book.

OverUnder already seeds AMM wildcards and resolves with three AI agents. The product wedge is long-tail markets that do not need third-party makers, not a CLOB that competes with Polymarket on institutional flow.

## Decision

- [SHIPPED] The book of record for every market type is a uniform-LVR AMM from the pm-AMM / 2026 uniform-loss family. The default pool is the static uniform invariant: MarketAMM v2 prices YES at Φ((no − yes)/L). [contracts/src/MarketAMM.vy : L1-12] [contracts/src/MarketAMM.vy : L271-317]
- [PHASE2] Time-based liquidity withdrawal (dynamic L_t, OU-T015) and dynamic bid-ask spreads are LP and fee policy, not a second venue.
- [SHIPPED] The CPMM is replaced, and fees are unchanged: `AMM_FEE_BPS = 100`, 50 to the vault and 50 to LPs, with the LP half now a per-pool USDC accumulator. The v1 CPMM source survives only as a test fixture for the migration tests. [contracts/src/MarketAMM.vy : L69-78] [contracts/tests/fixtures/MarketAMMV1.vy : L121-201]
- [SHIPPED] `Exchange` CLOB settlement remains deployed. It is leftover MVP code, not the product path. A CLOB overlay may be scheduled later; it is not required to trade. [contracts/src/Exchange.vy : L128-159]
- [SHIPPED] Listing is loosely gated, so an AMM seed rather than operator-recruited makers bootstraps a market: `createPermissionlessMarket` with seed, fee, close window, cooldown and a permissionless flag or lister allowlist (ADR-0012). [contracts/src/MarketFactory.vy : L177-199]
- [PHASE2] Product niche is long-tail plus AI wildcards. Prediction perpetuals, B2B embed SDKs, and CLOB vampire maker rebates are unscheduled.

The Vyper formula, gas and migration were settled by the OU-T008 spike ([uniform-lvr-spike.md](../explore/uniform-lvr-spike.md)) and are recorded in ADR-0011.

## Consequences

- Long-tail and wildcard markets can trade after a USDC seed without recruiting market makers.
- One trade UI (AMM swap) and one product fee schedule (vault/LP split) once the overlay is dormant.
- Negative: no native HFT or order-gateway compatibility until an overlay ships.
- Negative: the uniform-LVR math (fixed-point normal PDF/CDF, a Newton solver) costs gas and code size.
- Negative: Gaussian-score uniformity fits jump events (one-shot news, disasters) poorly, so some markets still suffer non-uniform LVR.
- Negative: the static curve spreads loss evenly across prices but does not lower it. It relies on the closeTime halt until dynamic L_t ships (ADR-0011).
