---
title: Web
status: MIXED
area: web
summary: Next.js feature modules for markets, AMM swaps, CDP wallets, and oracle status.
last_verified: 2026-09-21
pointers:
  - "[web/src/app/providers.tsx : L21-36]"
  - "[web/src/features/wallet/ConnectBar.tsx : L35-57]"
  - "[web/src/features/wallet/ConnectBar.tsx : L59-87]"
  - "[web/src/features/trade/OrderTicket.tsx : L18-39]"
  - "[web/src/features/trade/AmmSwap.tsx : L9-19]"
  - "[web/src/features/trade/AmmSwap.tsx : L187-211]"
  - "[web/src/features/trade/AmmSwap.tsx : L239-263]"
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
- [SHIPPED] `CDPHooksProvider` with `ethereum.createOnLogin: "smart"`. wagmi stays for injected operator/dev connectors. [web/src/app/providers.tsx : L21-36]

## Wallet

- [SHIPPED] Email OTP via CDP hooks; `POST /auth/cdp` exchanges the access token for an HS256 session. JWT `sub` is the smart account. [web/src/features/wallet/ConnectBar.tsx : L35-57] [web/src/features/wallet/ConnectBar.tsx : L59-87]
- [SHIPPED] RampCard checks KYC then opens MoonPay; Coinbase URL is fallback to the smart account. [web/src/features/wallet/RampCard.tsx : L70-99] [web/src/app/wallet/page.tsx : L6-13]
- [SHIPPED] User-facing wallets are CDP embedded smart accounts. See [ADR-0010](../adr/0010-cdp-embedded-wallets.md).

## Trade

- [SHIPPED] AmmSwap quotes then executes a batched CDP user op: USDC `approve` + `buyWithUSDC`, or CTF `setApprovalForAll` + `sellToUSDC`, `useCdpPaymaster: true`, never a paymaster URL. [web/src/features/trade/AmmSwap.tsx : L187-211] [web/src/features/trade/AmmSwap.tsx : L239-263]
- [SHIPPED] AmmSwap is the only ticket; used for all markets (primaries and wildcards). The hub passes the active question into the ticket. [web/src/features/markets/MarketDetail.tsx : L106-111]
- [PHASE2] OrderTicket remains on disk, unrendered leftover overlay. [web/src/features/trade/OrderTicket.tsx : L18-39]
- [PHASE2] EIP-712 typed-data sign for leftover Exchange orders. Not a required Phase 2 destination.

## Oracle UI

- [SHIPPED] OraclePanel polls `/oracle/{id}/status` and lists agent summaries. [web/src/features/oracle/OraclePanel.tsx : L6-17]
- [PHASE2] Cast vote, evidence links, countdown to WINDOW, arbitration banner.

## Mobile

- [SHIPPED] Flutter app mirrors web feature modules. Email OTP and trades go through OverUnder API (`/auth/cdp/*`, `/aa/cdp-send`). Shared tokens/OpenAPI: [mobile/README.md](../../mobile/README.md).
