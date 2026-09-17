---
title: Architecture overview
status: MIXED
area: cross
summary: Layered map of contracts, FastAPI, oracles, and Next.js with MVP trust boundaries.
last_verified: 2026-09-16
pointers:
  - "[contracts/src/MarketFactory.vy : L81-94]"
  - "[contracts/src/Exchange.vy : L128-159]"
  - "[contracts/src/MarketAMM.vy : L103-140]"
  - "[backend/app/main.py : L23-48]"
  - "[oracles/consensus/coordinator.py : L36-52]"
  - "[web/src/app/providers.tsx : L10-17]"
---

# Architecture overview

OverUnder is a Base-chain prediction market. Collateral is USDC (6 decimals). Outcomes are binary YES/NO ERC-1155 positions. Primaries trade on a CLOB. Wildcards trade on a CPMM. Resolution is a three-agent oracle.

## Layers

- [SHIPPED] Vyper contracts under `contracts/src/` deploy as one graph: MockUSDC, ConditionalTokens, MarketFactory, Exchange, MarketAMM, ConsensusOracle, FeeVault, RevenueToken.
- [SHIPPED] FastAPI under `backend/app/` exposes `/api/v1` plus `/health`.
- [SHIPPED] Python oracles under `oracles/` research questions and collect 3/3 attestations.
- [SHIPPED] Next.js web under `web/` lists markets, posts CLOB orders, quotes AMM, shows oracle status.
- [PHASE2] Flutter app under `mobile/` is a carryover contract only. See [mobile/README.md](../../mobile/README.md).

## Market types

- [SHIPPED] Type 0 primary: operator-created via `createPrimaryMarket`. Trades on Exchange CLOB. [contracts/src/MarketFactory.vy : L81-84]
- [SHIPPED] Type 1 wildcard: generator-created child with optional USDC seed into MarketAMM. [contracts/src/MarketFactory.vy : L86-94]
- [PHASE2] Permissionless primary listing, sports-book UMA-style disputes, and multi-outcome (n>2) markets.

## Trust boundaries (MVP)

- [SHIPPED] `operator` creates primaries, pauses markets, sets factory/generator, arbitrates after the window. [contracts/src/ConsensusOracle.vy : L219-226]
- [SHIPPED] Off-chain matcher may call `matchOrders` with `relayer_private_key`. Anyone who holds both EIP-712 signatures can settle; the relayer is convenience, not a unique privilege on-chain. [backend/app/orderbook/matcher.py : L88-136]
- [SHIPPED] Three agent EOAs are the only attestors. Unanimous `submitConsensus` resolves immediately. [contracts/src/ConsensusOracle.vy : L144-161]
- [STUB] Auth treats Privy as an unverified token + address pair. [backend/app/auth/router.py : L90-102]
- [PHASE2] ERC-4337 paymaster, Privy JWKS, production relayer with nonce/gas policy, and KYC-gated ramps.

## Settlement

- [SHIPPED] CLOB taker fee 75 bps to FeeVault. [contracts/src/Exchange.vy : L33]
- [SHIPPED] AMM fee 100 bps split 50 vault / 50 LP. [contracts/src/MarketAMM.vy : L40]
- [SHIPPED] OU is 100M fixed supply; FeeVault NAV is `usdc_balance * 1e18 / ou_supply`. [contracts/src/FeeVault.vy : L44-50]
- [PHASE2] OU emissions, staking boosts, and public mint are out of scope for the vault model.

## Read next

- [SHIPPED] [contracts.md](contracts.md)
- [SHIPPED] [backend.md](backend.md)
- [SHIPPED] [oracles.md](oracles.md)
- [SHIPPED] [web.md](web.md)
- [SHIPPED] [data-flow.md](data-flow.md)
