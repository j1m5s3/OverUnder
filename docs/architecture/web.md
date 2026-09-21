---
title: Web
status: MIXED
area: web
summary: Next.js feature modules for markets, AMM swaps, wallet stubs, and oracle status.
last_verified: 2026-09-21
pointers:
  - "[web/src/app/providers.tsx : L10-17]"
  - "[web/src/features/wallet/ConnectBar.tsx : L13-46]"
  - "[web/src/features/trade/OrderTicket.tsx : L18-39]"
  - "[web/src/features/trade/AmmSwap.tsx : L9-19]"
  - "[web/src/features/trade/AmmSwap.tsx : L181-238]"
  - "[web/src/features/trade/AmmSwap.tsx : L243-268]"
  - "[web/src/features/aa/userOp.ts : L30-36]"
  - "[web/src/features/aa/userOp.ts : L83-155]"
  - "[web/src/features/wallet/RampCard.tsx : L70-99]"
  - "[web/src/shared/api/client.ts : L1-11]"
  - "[web/src/features/markets/MarketList.tsx : L36-160]"
  - "[web/src/features/markets/eventHub.ts : L39-87]"
  - "[web/src/features/markets/eventHub.ts : L21-37]"
  - "[web/src/features/markets/MarketDetail.tsx : L83-140]"
  - "[web/src/features/markets/MarketInfo.tsx : L15-60]"
  - "[web/src/features/markets/MatchupHero.tsx : L1-75]"
  - "[web/src/features/markets/PriceChart.tsx : L16-90]"
  - "[web/src/features/oracle/OraclePanel.tsx : L6-17]"
  - "[mobile/README.md : L1-25]"
---

# Web

Next.js App Router under `web/`. Feature folders are the Flutter carryover map.

## Shell

- [SHIPPED] Markets home, market detail, portfolio, wallet routes.
- [SHIPPED] Market list consumes EventCard[]; `childCount` is `children.length`. Tabs categorize the primary only. Missing or paused parents stay as standalone EventCards from the API. List child links stay on the primary route with `?m=`. [web/src/features/markets/eventHub.ts : L44-63]
- [SHIPPED] `LiveScore` on `MarketDetailData.score` (optional, `null` when absent). MatchupHero is box-score only. MarketInfo renders one muted facts line from `MarketDetail.facts`. List cards still have no score. Scout auto-POSTs on 3/3 (OU-T011). [web/src/features/markets/eventHub.ts : L21-37] [web/src/features/markets/MatchupHero.tsx : L1-75] [web/src/features/markets/MarketInfo.tsx : L15-60]
- [SHIPPED] Primary detail is the event hub. When `children.length > 0`, a board lists `hubRoster` (primary then children) and the label uses that count. Row click sets `activeConditionId` and writes or clears `?m=` on the primary path. AmmSwap remounts on `activeConditionId` plus `initialSide` and shows the active question. MatchupHero, MarketInfo, and OraclePanel stay on the primary. [web/src/features/markets/MarketDetail.tsx : L83-140]
- [SHIPPED] PriceChart reads `GET /markets/{id}/history` only and follows `activeConditionId`; empty history renders a clean empty state, a live quote appears as a text marker, never a polyline. [web/src/features/markets/PriceChart.tsx : L16-90]
- [SHIPPED] `api()` prefixes `NEXT_PUBLIC_API_URL` and attaches `ou_token` bearer. [web/src/shared/api/client.ts : L1-11]
- [SHIPPED] wagmi config: Anvil/Base Sepolia/Base, **injected connector only** (Coinbase/x402 barrel omitted to keep `next build` green). [web/src/app/providers.tsx : L10-17]

## Wallet

- [STUB] Injected connect triggers SIWE with `signature: "0x"` and a constructed nonce message. [web/src/features/wallet/ConnectBar.tsx : L13-30]
- [STUB] Email path hashes the email string into a 20-byte demo address and `POST /auth/privy` with token `privy-demo`. [web/src/features/wallet/ConnectBar.tsx : L32-46]
- [SHIPPED] RampCard checks KYC then opens MoonPay; Coinbase URL is fallback. [web/src/features/wallet/RampCard.tsx : L70-99]
- [PHASE2] Privy embedded wallet + email OTP, EIP-1193 provider, SIWE with `personal_sign`, Coinbase Smart Wallet. ERC-4337 paymaster for injected EOA SimpleAccounts is shipped; email AA users are not.

## Trade

- [SHIPPED] AmmSwap quotes then executes wallet swaps: `quoteBuy`/`quoteSell`, approve USDC or CTF, `buyWithUSDC` / `sellToUSDC` with slippage. [web/src/features/trade/AmmSwap.tsx : L243-268]
- [SHIPPED] When `NEXT_PUBLIC_PAYMASTER_ADDRESS`, `NEXT_PUBLIC_ACCOUNT_FACTORY`, and `NEXT_PUBLIC_ENTRYPOINT` are set, AmmSwap uses separate sponsored UserOps (approve-paymaster, approve-amm, swap). EOA `writeContract` remains when those envs are unset. [web/src/features/aa/userOp.ts : L83-155] [web/src/features/trade/AmmSwap.tsx : L181-238]
- [SHIPPED] AmmSwap is the only ticket; used for all markets (primaries and wildcards). The hub passes the active question into the ticket. [web/src/features/markets/MarketDetail.tsx : L106-111]
- [PHASE2] OrderTicket remains on disk, unrendered leftover overlay. [web/src/features/trade/OrderTicket.tsx : L18-39]
- [PHASE2] EIP-712 typed-data sign for leftover Exchange orders. Not a required Phase 2 destination.

## Oracle UI

- [SHIPPED] OraclePanel polls `/oracle/{id}/status` and lists agent summaries. [web/src/features/oracle/OraclePanel.tsx : L6-17]
- [PHASE2] Cast vote, evidence links, countdown to WINDOW, arbitration banner.

## Mobile

- [SHIPPED] Flutter app mirrors web feature modules. Shared tokens/OpenAPI: [mobile/README.md](../../mobile/README.md). This closeout does not edit `mobile/`.
