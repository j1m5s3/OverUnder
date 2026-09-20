---
title: AMM-first uniform-LVR
status: MIXED
area: contracts
summary: Book of record is a uniform-LVR AMM for every market; shipped CPMM and CLOB stay until a later cycle; CLOB is an optional overlay.
last_verified: 2026-09-20
pointers:
  - "[contracts/src/MarketFactory.vy : L82-89]"
  - "[contracts/src/MarketAMM.vy : L40]"
  - "[contracts/src/MarketAMM.vy : L73-82]"
  - "[contracts/src/MarketAMM.vy : L122-159]"
  - "[contracts/src/MarketAMM.vy : L162-201]"
  - "[contracts/src/Exchange.vy : L128-159]"
---

## Status

Accepted 2026-09-18. Supersedes [ADR-0001](0001-hybrid-clob-amm.md).

## Context

Polymarket-class CLOBs need professional market makers. That works on flagship events and fails on the long tail OverUnder wants (hyper-local, niche, AI-generated children). The MVP shipped a hybrid: Type 0 primaries on `Exchange.matchOrders`, Type 1 wildcards on a YES/NO CPMM. [contracts/src/Exchange.vy : L128-159] [contracts/src/MarketFactory.vy : L82-89]

A CPMM on outcome tokens is a poor long-term book. Tokens expire at $0 or $1; LVR concentrates at extremes; LPs can be drained as the market becomes certain. LMSR has the same non-uniform loss shape under Gaussian-score dynamics.

Prediction-native AMMs address that. Paradigm pm-AMM (Moallemi and Robinson, 2024) is a uniform-LVR invariant for Gaussian score dynamics. Moallemi, Robinson, and Zhu (2026) generalize uniform-loss AMMs to win-martingales and prescribed loss schedules over time ([arXiv:2607.17428](https://arxiv.org/abs/2607.17428)). Dynamic spreads are a fee overlay, not a second book.

OverUnder already seeds AMM wildcards and resolves with three AI agents. The product wedge is long-tail markets that do not need third-party makers, not a CLOB that competes with Polymarket on institutional flow.

## Decision

- [PHASE2] The book of record for every market type is a uniform-LVR AMM (pm-AMM / 2026 uniform-loss family). Default pool: static uniform invariant. Time-based liquidity withdrawal and dynamic bid-ask spreads are LP/fee policy, not a second venue.
- [SHIPPED] `MarketAMM` remains a CPMM (`AMM_FEE_BPS = 100`, 50 vault / 50 LP) until OU-T008/OU-T009 land. [contracts/src/MarketAMM.vy : L40] [contracts/src/MarketAMM.vy : L73-82] [contracts/src/MarketAMM.vy : L122-159] [contracts/src/MarketAMM.vy : L162-201]
- [SHIPPED] `Exchange` CLOB settlement remains deployed. It is leftover MVP code, not the product path. A CLOB overlay may be scheduled later; it is not required to trade. [contracts/src/Exchange.vy : L128-159]
- [PHASE2] Listing becomes permissionless or loosely gated so AMM seed—not operator-recruited makers—bootstraps a market (OU-T010).
- [PHASE2] Product niche is long-tail plus AI wildcards. Prediction perpetuals, B2B embed SDKs, and CLOB vampire maker rebates are unscheduled.

Vyper formula, gas, and CPMM migration are a spike (OU-T008), not this ADR’s implementation.

## Consequences

- Long-tail and wildcard markets can trade after a USDC seed without recruiting market makers.
- One trade UI (AMM swap) and one product fee schedule (vault/LP split) once the overlay is dormant.
- Negative: no native HFT/order-gateway compatibility until an overlay ships; uniform-LVR math (normal PDF/CDF, time policy) is non-trivial in Vyper; Gaussian-score uniformity is a poor fit for jump events (one-shot news, disasters), so some markets will still suffer non-uniform LVR.
