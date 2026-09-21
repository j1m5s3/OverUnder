---
title: Architecture overview
status: MIXED
area: cross
summary: Layered map of contracts, FastAPI, oracles, and Next.js with MVP trust boundaries.
last_verified: 2026-09-20
pointers:
  - "[contracts/src/MarketFactory.vy : L82-89]"
  - "[contracts/src/MarketFactory.vy : L92-99]"
  - "[contracts/src/MarketAMM.vy : L122-159]"
  - "[contracts/src/MarketAMM.vy : L162-201]"
  - "[contracts/src/Exchange.vy : L128-159]"
  - "[backend/app/main.py : L22-39]"
  - "[oracles/consensus/coordinator.py : L36-52]"
  - "[docs/adr/0008-cursor-runtime-oracles.md : L18-32]"
  - "[web/src/app/providers.tsx : L10-17]"
---

# Architecture overview

OverUnder is a Base-chain prediction market. Collateral is USDC (6 decimals). Outcomes are binary YES/NO ERC-1155 positions. All markets trade on a seeded CPMM (`MarketAMM`). Resolution is a three-agent oracle. Book of record: [ADR-0007](../adr/0007-amm-first-uniform-lvr.md).

## Layers

- [SHIPPED] Vyper contracts under `contracts/src/` deploy as one graph: MockUSDC, ConditionalTokens, MarketFactory, Exchange, MarketAMM, ConsensusOracle, FeeVault, RevenueToken.
- [SHIPPED] FastAPI under `backend/app/` exposes `/api/v1` plus `/health`.
- [SHIPPED] Python oracles under `oracles/` research questions with Cursor agents and collect 3/3 attestations. Score scout auto-POSTs LiveScore on 3/3. [docs/adr/0008-cursor-runtime-oracles.md : L18-32]
- [SHIPPED] Next.js web under `web/` lists markets, executes AMM swaps, shows oracle status.
- [SHIPPED] Flutter app under `mobile/` mirrors web feature modules. See [mobile/README.md](../../mobile/README.md).

## Market types

- [SHIPPED] Type 0 primary: operator-created via `createPrimaryMarket` with required seed. Trades on MarketAMM. [contracts/src/MarketFactory.vy : L82-89]
- [SHIPPED] Type 1 wildcard: generator-created child with optional USDC seed into MarketAMM. [contracts/src/MarketFactory.vy : L92-99]
- [SHIPPED] Exchange CLOB (`matchOrders`) remains deployed leftover overlay; it is not required to trade. [contracts/src/Exchange.vy : L128-159]
- [PHASE2] Uniform-LVR default pool (OU-T008/T009), permissionless listing (OU-T010). A CLOB overlay may be scheduled later; it is not a required Phase 2 destination.

## Trust boundaries (MVP)

- [SHIPPED] `operator` creates primaries, pauses markets, sets factory/generator, arbitrates after the window. [contracts/src/ConsensusOracle.vy : L219-226]
- [SHIPPED] Off-chain matcher may call leftover `matchOrders` with `relayer_private_key`. Anyone who holds both EIP-712 signatures can settle; the relayer is convenience, not a unique privilege on-chain. This is not the happy path. [backend/app/orderbook/matcher.py : L88-136]
- [SHIPPED] Three agent EOAs are the only attestors. Unanimous `submitConsensus` resolves immediately. [contracts/src/ConsensusOracle.vy : L144-161]
- [STUB] Auth treats Privy as an unverified token + address pair. [backend/app/auth/router.py : L90-102]
- [PHASE2] ERC-4337 paymaster, Privy JWKS, production relayer with nonce/gas policy.

## Settlement

- [SHIPPED] AMM fee 100 bps split 50 vault / 50 LP on all markets. [contracts/src/MarketAMM.vy : L40]
- [SHIPPED] Leftover CLOB taker fee 75 bps to FeeVault when Exchange matching is used. [contracts/src/Exchange.vy : L33]
- [SHIPPED] OU is 100M fixed supply; FeeVault NAV is `usdc_balance * 1e18 / ou_supply`. [contracts/src/FeeVault.vy : L44-50]
- [SHIPPED] OU emissions transfer from treasury; they do not mint into FeeVault.

## Read next

- [SHIPPED] [contracts.md](contracts.md)
- [SHIPPED] [backend.md](backend.md)
- [SHIPPED] [oracles.md](oracles.md)
- [SHIPPED] [web.md](web.md)
- [SHIPPED] [data-flow.md](data-flow.md)
