---
title: Hybrid CLOB and AMM
status: MIXED
area: contracts
summary: Historical hybrid CLOB-primary / AMM-wildcard decision. Superseded by ADR-0007.
last_verified: 2026-09-20
pointers:
  - "[contracts/src/MarketFactory.vy : L82-90]"
  - "[contracts/src/MarketAMM.vy : L40]"
  - "[contracts/src/MarketAMM.vy : L103-140]"
  - "[contracts/src/Exchange.vy : L33]"
  - "[contracts/src/Exchange.vy : L128-159]"
---

## Status

Superseded 2026-09-20 by [ADR-0007](0007-amm-first-uniform-lvr.md). CLOB is leftover overlay, not a required Phase 2 destination. Decision body below is historical.

## Context

Polymarket-class books need tight spreads on liquid events and standalone liquidity on long-tail children. A single AMM on NFL game winners would leak value to LPs and invent prices; a CLOB on every "Kelce fumble" child would be empty.

Original design used CLOB for primaries and AMM for wildcards. MVP path now uses AMM for all markets to ship faster without relayer infrastructure.

## Decision

- **Type 0 primaries**: `MarketAMM` CPMM seeded at creation (seed required), 100 bps (50/50 vault/LP). [contracts/src/MarketFactory.vy : L82-90] [contracts/src/MarketAMM.vy : L40]
- **Type 1 wildcards**: `MarketAMM` CPMM seeded at creation, 100 bps (50/50 vault/LP). [contracts/src/MarketAMM.vy : L40] [contracts/src/MarketFactory.vy : L86-94]
- [PHASE2] CLOB via `Exchange.matchOrders` with off-chain matcher, 75 bps taker. [contracts/src/Exchange.vy : L33] [contracts/src/Exchange.vy : L128-159]
- Both market types share ConditionalTokens + ConsensusOracle so resolution and redeem are identical.

## Consequences

- Every live market has immediate liquidity from operator seed; no external market makers required for MVP.
- Single fee schedule (100 bps) and single UX (AMM swap with slippage) across all markets.
- Negative: Operator must provide seed capital for every primary. No tight spreads from professional makers until Phase 2 CLOB.
- Positive: Simpler MVP; no relayer signing, no EIP-712 order UX, faster ship.
