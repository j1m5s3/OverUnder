---
title: Hybrid CLOB and AMM
status: SHIPPED
area: contracts
summary: Primaries use an off-chain CLOB with on-chain EIP-712 settlement; wildcards use a CPMM.
last_verified: 2026-09-16
pointers:
  - "[contracts/src/Exchange.vy : L33]"
  - "[contracts/src/Exchange.vy : L128-159]"
  - "[contracts/src/MarketAMM.vy : L40]"
  - "[contracts/src/MarketFactory.vy : L81-94]"
---

## Status

Accepted 2026-09-16.

## Context

Polymarket-class books need tight spreads on liquid events and standalone liquidity on long-tail children. A single AMM on NFL game winners would leak value to LPs and invent prices; a CLOB on every “Kelce fumble” child would be empty.

## Decision

- Type 0 primaries: off-chain matching, on-chain `Exchange.matchOrders`, 75 bps taker. [contracts/src/Exchange.vy : L33] [contracts/src/Exchange.vy : L128-159]
- Type 1 wildcards: `MarketAMM` CPMM seeded at creation, 100 bps (50/50 vault/LP). [contracts/src/MarketAMM.vy : L40] [contracts/src/MarketFactory.vy : L86-94]
- Both share ConditionalTokens + ConsensusOracle so resolution and redeem are identical.

## Consequences

- Operators and market makers can quote primaries without inventory in an AMM.
- Wildcards are immediately tradable after a USDC seed.
- Negative: two fee schedules and two UIs; a user who confuses CLOB price (1e6 = $1) with AMM quote (tokens out) will mis-size. Liquidity cannot move automatically from a primary book into its children.
