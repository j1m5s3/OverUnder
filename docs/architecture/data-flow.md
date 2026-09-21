---
title: Data flow
status: MIXED
area: cross
summary: End-to-end traces for seeded AMM swaps, leftover CLOB overlay, and oracle resolution.
last_verified: 2026-09-21
pointers:
  - "[contracts/src/MarketFactory.vy : L82-89]"
  - "[contracts/src/MarketFactory.vy : L92-99]"
  - "[backend/app/markets/router.py : L191-334]"
  - "[backend/app/amm/router.py : L9-28]"
  - "[contracts/src/MarketAMM.vy : L73-82]"
  - "[contracts/src/MarketAMM.vy : L104-119]"
  - "[contracts/src/MarketAMM.vy : L122-159]"
  - "[contracts/src/MarketAMM.vy : L162-201]"
  - "[web/src/features/trade/AmmSwap.tsx : L163-216]"
  - "[web/src/features/trade/AmmSwap.tsx : L219-272]"
  - "[backend/app/orderbook/matcher.py : L37-86]"
  - "[contracts/src/Exchange.vy : L128-159]"
  - "[oracles/consensus/coordinator.py : L27-43]"
  - "[oracles/resolve/run.py : L33-106]"
  - "[oracles/listing/run.py : L58-137]"
  - "[contracts/src/ConsensusOracle.vy : L144-161]"
  - "[contracts/src/ConsensusOracle.vy : L197-226]"
  - "[contracts/src/ConditionalTokens.vy : L86-105]"
  - "[web/src/features/trade/OrderTicket.tsx : L18-39]"
  - "[backend/app/indexer/listener.py : L175-237]"
  - "[backend/app/markets/router.py : L99-116]"
  - "[web/src/features/markets/PriceChart.tsx : L16-90]"
  - "[contracts/src/FeeVault.vy : L61-83]"
---

# Data flow

## Primary AMM swap (MVP)

1. [SHIPPED] Operator calls `createPrimaryMarket` with required `seedUsdc`; factory seeds 50/50 YES/NO pool. [contracts/src/MarketFactory.vy : L82-89] [contracts/src/MarketAMM.vy : L73-82]
2. [SHIPPED] API `POST /markets` submits the factory tx (fail-closed) and upserts SQLite from `MarketCreated`. [backend/app/markets/router.py : L191-334]
3. [SHIPPED] Trader calls `GET /amm/{id}/quote` → `quoteBuy` or `quoteSell`. [backend/app/amm/router.py : L9-28]
4. [SHIPPED] Web AmmSwap buy: approve USDC, then `buyWithUSDC` with `minOut` from quote*(1-slippage). [web/src/features/trade/AmmSwap.tsx : L163-216]
5. [SHIPPED] Web AmmSwap sell: CTF `setApprovalForAll`, then `sellToUSDC` with `minUsdc`. [web/src/features/trade/AmmSwap.tsx : L219-272]
6. [SHIPPED] On-chain `buyWithUSDC` / `sellToUSDC` apply 50 bps vault + 50 bps LP. [contracts/src/MarketAMM.vy : L122-159] [contracts/src/MarketAMM.vy : L162-201]
7. [SHIPPED] Indexer stores pool-mid `PricePoint`s (`PoolSeeded` → 0.5, each `Swap` → `pools(conditionId)` mid); failed ranges hold the checkpoint and retry. `GET /markets/{id}/history` feeds the hub chart, empty until the pool is seeded. [backend/app/indexer/listener.py : L175-237]
8. [PHASE2] Paymaster-sponsored UserOp for gasless swaps.

## Wildcard AMM swap

1. [SHIPPED] Generator or operator `createWildcardMarket` with optional `seedUsdc`; factory seeds 50/50 YES/NO when seed > 0. [contracts/src/MarketFactory.vy : L92-99] [contracts/src/MarketAMM.vy : L73-82]
2. [SHIPPED] Same quote + AmmSwap buy/sell path as primaries. [web/src/features/trade/AmmSwap.tsx : L163-272]
3. [PHASE2] Paymaster-sponsored UserOp for gasless swaps.

## Leftover CLOB overlay (not required)

1. [SHIPPED] `Exchange.matchOrders` and OrderTicket remain in tree as leftover overlay, unused by the happy path. [contracts/src/Exchange.vy : L128-159] [web/src/features/trade/OrderTicket.tsx : L18-39]
2. [PHASE2] A CLOB overlay may be scheduled later; it is not a required destination. Matcher `matchOrders` stays leftover. [backend/app/orderbook/matcher.py : L37-86]

## Resolve market

1. [SHIPPED] After `closeTime`, coordinator `run(question)` gathers three attestations. [oracles/consensus/coordinator.py : L27-43]
2. [SHIPPED] Sports primaries with LiveScore `final`: dual-gate (score-derived winner plus unanimous research) then job `submitConsensus`. [oracles/resolve/run.py : L33-106]
3. [SHIPPED] If unanimous, three EIP-712 sigs → `submitConsensus` → `reportPayouts`. [contracts/src/ConsensusOracle.vy : L144-161]
4. [SHIPPED] Else wait `WINDOW` (86400). Agents may still `submitAttestation`. Holders `castVote`.
5. [SHIPPED] `resolveFallback` uses 2/3 agents; ≥2/3 opposing vote weight reverts to operator `resolveArbitrated`. [contracts/src/ConsensusOracle.vy : L197-226]
6. [PHASE2] Automatic `resolveFallback` daemon.
7. [SHIPPED] Winners `redeemPositions` for USDC. [contracts/src/ConditionalTokens.vy : L86-95]

## Week-roll listing

1. [SHIPPED] Schedule scout 3/3 POSTs current NFL week and next week. [oracles/schedule/scout.py : L128-157]
2. [SHIPPED] When every week-W game is `final`, operator `POST /markets` lists week W+1 winner primaries with `close_time = kickoff`. [oracles/listing/run.py : L58-137]

## OU redeem

1. [SHIPPED] Phase 1 fees into FeeVault are AMM 100 bps (50 vault / 50 LP). Leftover Exchange fills may also pay 75 bps if matching is used.
2. [SHIPPED] Holder `requestRedeem` (24h cooldown at deploy), then `claim` burns OU and pays pro-rata USDC. [contracts/src/FeeVault.vy : L61-83]
3. [SHIPPED] Emissions transfer from treasury; they do not mint into this vault.
