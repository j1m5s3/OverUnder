---
title: Web
status: MIXED
area: web
summary: Next.js feature modules for markets, CLOB, AMM quotes, wallet stubs, and oracle status.
last_verified: 2026-09-20
pointers:
  - "[web/src/app/providers.tsx : L10-17]"
  - "[web/src/features/wallet/ConnectBar.tsx : L13-46]"
  - "[web/src/features/trade/OrderTicket.tsx : L18-39]"
  - "[web/src/features/trade/AmmSwap.tsx : L11-16]"
  - "[web/src/features/oracle/OraclePanel.tsx : L6-17]"
  - "[web/src/features/wallet/RampCard.tsx : L8-13]"
  - "[web/src/shared/api/client.ts : L1-11]"
  - "[web/src/features/markets/MarketList.tsx : L36-160]"
  - "[web/src/features/markets/eventHub.ts : L26-69]"
  - "[web/src/features/markets/MarketDetail.tsx : L48-140]"
  - "[web/src/features/trade/AmmSwap.tsx : L43-52]"
  - "[mobile/README.md : L1-25]"
---

# Web

Next.js App Router under `web/`. Feature folders are the Flutter carryover map.

## Shell

- [SHIPPED] Markets home, market detail, portfolio, wallet routes.
- [SHIPPED] Market list consumes EventCard[]; `childCount` is `children.length`. Tabs categorize the primary only. Missing or paused parents stay as standalone EventCards from the API. List child links stay on the primary route with `?m=`. [web/src/features/markets/eventHub.ts : L26-45]
- [SHIPPED] Primary detail is the event hub. When `children.length > 0`, a board lists `hubRoster` (primary then children) and the label uses that count. Row click sets `activeConditionId` and writes or clears `?m=` on the primary path. AmmSwap remounts on `activeConditionId` plus `initialSide` and shows the active question. MatchupHero, MarketInfo, and OraclePanel stay on the primary. [web/src/features/markets/MarketDetail.tsx : L83-140]
- [SHIPPED] `api()` prefixes `NEXT_PUBLIC_API_URL` and attaches `ou_token` bearer. [web/src/shared/api/client.ts : L1-11]
- [SHIPPED] wagmi config: Anvil/Base Sepolia/Base, **injected connector only** (Coinbase/x402 barrel omitted to keep `next build` green). [web/src/app/providers.tsx : L10-17]

## Wallet

- [STUB] Injected connect triggers SIWE with `signature: "0x"` and a constructed nonce message. [web/src/features/wallet/ConnectBar.tsx : L13-30]
- [STUB] Email path hashes the email string into a 20-byte demo address and `POST /auth/privy` with token `privy-demo`. [web/src/features/wallet/ConnectBar.tsx : L32-46]
- [STUB] RampCard fetches a Coinbase URL; no MoonPay, no KYC gate. [web/src/features/wallet/RampCard.tsx : L8-13]
- [PHASE2] Privy embedded wallet + email OTP, EIP-1193 provider, SIWE with `personal_sign`, Coinbase Smart Wallet, and ERC-4337 session keys.

## Trade

- [SHIPPED] AmmSwap executes wallet swaps: approve USDC, `buyWithUSDC` with slippage protection. [web/src/features/trade/AmmSwap.tsx : L81-102]
- [SHIPPED] AmmSwap is the only ticket; used for all markets (primaries and wildcards). The hub passes the active question into the ticket.
- [PHASE2] OrderTicket posts unsigned orders (`signature: "0x"`, synthetic `orderHash`). [web/src/features/trade/OrderTicket.tsx : L18-39]
- [PHASE2] EIP-712 typed-data sign for Exchange orders, allowance UX (USDC + setApprovalForAll), and smart-wallet batching (approve+swap).

## Oracle UI

- [SHIPPED] OraclePanel polls `/oracle/{id}/status` and lists agent summaries. [web/src/features/oracle/OraclePanel.tsx : L6-17]
- [PHASE2] Cast vote, evidence links, countdown to WINDOW, arbitration banner.

## Mobile

- [PHASE2] Do not implement Flutter in this tree yet. Feature map and shared tokens/OpenAPI: [mobile/README.md](../../mobile/README.md).
