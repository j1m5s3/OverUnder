---
title: Data flow
status: MIXED
area: cross
summary: End-to-end traces for primary CLOB fills, wildcard AMM swaps, and oracle resolution.
last_verified: 2026-09-16
pointers:
  - "[contracts/src/MarketFactory.vy : L81-84]"
  - "[backend/app/orderbook/router.py : L28-58]"
  - "[backend/app/orderbook/matcher.py : L37-86]"
  - "[contracts/src/Exchange.vy : L128-159]"
  - "[contracts/src/MarketFactory.vy : L86-94]"
  - "[contracts/src/MarketAMM.vy : L74-82]"
  - "[contracts/src/MarketAMM.vy : L103-140]"
  - "[oracles/consensus/coordinator.py : L36-52]"
  - "[contracts/src/ConsensusOracle.vy : L144-161]"
  - "[contracts/src/ConsensusOracle.vy : L197-226]"
  - "[contracts/src/ConditionalTokens.vy : L86-105]"
  - "[web/src/features/trade/OrderTicket.tsx : L18-39]"
  - "[contracts/src/FeeVault.vy : L61-83]"
---

# Data flow

## Primary CLOB fill

1. [SHIPPED] Operator calls `createPrimaryMarket`; factory prepares CTF condition and registers closeTime. [contracts/src/MarketFactory.vy : L81-84]
2. [STUB] API `POST /markets` currently inserts SQLite independently; production must key off the on-chain `conditionId`.
3. [SHIPPED] Maker splits USDC into YES/NO (or holds inventory) and `setApprovalForAll` on Exchange.
4. [STUB] Web OrderTicket posts an unsigned order to `POST /orders`. [web/src/features/trade/OrderTicket.tsx : L18-39]
5. [SHIPPED] Matcher finds a crossing rester, records `Trade`, optionally `matchOrders`. [backend/app/orderbook/matcher.py : L37-86]
6. [SHIPPED] Exchange moves USDC (volume + 75 bps taker fee to FeeVault) and ERC-1155 outcome tokens. [contracts/src/Exchange.vy : L128-159]
7. [PHASE2] Relayer verifies allowances/signatures before ACK; user sees confirmed `txHash` in portfolio.

## Wildcard AMM swap

1. [SHIPPED] Generator or operator `createWildcardMarket` with `seedUsdc`; factory seeds 50/50 YES/NO. [contracts/src/MarketFactory.vy : L86-94] [contracts/src/MarketAMM.vy : L74-82]
2. [SHIPPED] Trader (or UI) calls `GET /amm/{id}/quote` → `quoteBuy`.
3. [SHIPPED] On-chain `buyWithUSDC`: pull USDC, 50 bps vault, 50 bps LP, split remainder+LP into tokens, swap k, send bought outcome. [contracts/src/MarketAMM.vy : L103-140]
4. [STUB] Web AmmSwap stops at quote.
5. [PHASE2] Wallet-submitted swap (or paymaster-sponsored UserOp) with slippage `minOut`.

## Resolve market

1. [SHIPPED] After `closeTime`, coordinator `run(question)` gathers three attestations. [oracles/consensus/coordinator.py : L36-52]
2. [SHIPPED] If unanimous, three EIP-712 sigs → `submitConsensus` → `reportPayouts`. [contracts/src/ConsensusOracle.vy : L144-161]
3. [SHIPPED] Else wait `WINDOW` (86400). Agents may still `submitAttestation`. Holders `castVote`.
4. [SHIPPED] `resolveFallback` uses 2/3 agents; ≥2/3 opposing vote weight reverts to operator `resolveArbitrated`. [contracts/src/ConsensusOracle.vy : L197-226]
5. [SHIPPED] Winners `redeemPositions` for USDC. [contracts/src/ConditionalTokens.vy : L86-95]

## OU redeem

1. [SHIPPED] Fees from Exchange and AMM sit as USDC on FeeVault.
2. [SHIPPED] Holder `requestRedeem` (24h cooldown at deploy), then `claim` burns OU and pays pro-rata USDC. [contracts/src/FeeVault.vy : L61-83]
3. [PHASE2] Emissions do not mint into this vault; see [phase-2.md](../roadmap/phase-2.md).
