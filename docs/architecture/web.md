---
title: Web and mobile
status: MIXED
area: web
summary: Next.js feature modules for markets, AMM swaps with the trading-closed state, user listing at /list, CDP wallets and oracle status, plus the Flutter app that mirrors them.
last_verified: 2026-09-23
pointers:
  - "[web/src/app/providers.tsx : L21-45]"
  - "[web/src/features/wallet/ConnectBar.tsx : L36-89]"
  - "[web/src/features/trade/AmmSwap.tsx : L54-130]"
  - "[web/src/features/trade/AmmSwap.tsx : L244-367]"
  - "[web/src/features/trade/AmmSwap.tsx : L410-423]"
  - "[web/src/features/trade/tradingWindow.ts : L1-99]"
  - "[web/src/features/wallet/userOpOutcome.ts : L45-126]"
  - "[web/src/features/listing/ListMarketForm.tsx : L66-344]"
  - "[web/src/features/listing/listing.ts : L167-349]"
  - "[web/src/features/wallet/RampCard.tsx : L41-98]"
  - "[web/src/shared/api/client.ts : L1-65]"
  - "[web/src/features/markets/MarketList.tsx : L36-160]"
  - "[web/src/features/markets/eventHub.ts : L24-37]"
  - "[web/src/features/markets/eventHub.ts : L95-114]"
  - "[web/src/features/markets/MarketDetail.tsx : L52-176]"
  - "[web/src/features/markets/MarketInfo.tsx : L31-103]"
  - "[web/src/features/markets/MarketCard.tsx : L28-91]"
  - "[web/src/features/markets/MatchupHero.tsx : L1-75]"
  - "[web/src/features/markets/PriceChart.tsx : L16-101]"
  - "[web/src/features/oracle/OraclePanel.tsx : L8-64]"
  - "[web/src/features/oracle/oracleStatus.ts : L49-74]"
  - "[mobile/lib/config/app_config.dart : L1-24]"
  - "[mobile/lib/models/models.dart : L150-166]"
  - "[mobile/lib/models/deployments.dart : L77-90]"
  - "[mobile/lib/services/api_client.dart : L98-133]"
  - "[mobile/lib/services/api_client.dart : L197-213]"
  - "[mobile/lib/features/trade/amm_swap_widget.dart : L81-144]"
---

# Web and mobile

Next.js App Router under `web/`. Feature folders are the Flutter carryover map; the Flutter app is under Mobile below.

## Shell

- [SHIPPED] Markets home, market detail, portfolio, wallet and `/list` routes; the nav links "List a market".
- [SHIPPED] Market list consumes EventCard[]; primaries are marketType 0 and 2, `childCount` is `children.length`, and tabs categorize the primary only. List child links stay on the primary route with `?m=`. [web/src/features/markets/MarketList.tsx : L36-160] [web/src/features/markets/eventHub.ts : L95-114]
- [SHIPPED] Cards and hub rows show the live YES price (`yesPriceMicros` when strictly between 0 and 1e6, else `suggestedProbability`) and "User listed" / "Resolved" tags. [web/src/features/markets/eventHub.ts : L24-37] [web/src/features/markets/MarketCard.tsx : L28-91]
- [SHIPPED] `LiveScore` on `MarketDetailData.score` (optional, `null` when absent). MatchupHero is box-score only. [web/src/features/markets/MatchupHero.tsx : L1-75]
- [SHIPPED] Primary detail is the event hub: row click sets `activeConditionId` and writes or clears `?m=`; AmmSwap remounts on the active market and gets its close and halt fields. A 404 shows "Market not found" (unknown id or unconfirmed user listing); demo data appears only when the API is unreachable. [web/src/features/markets/MarketDetail.tsx : L52-176]
- [SHIPPED] MarketInfo shows resolution criteria, "Listed by" plus seed for user markets, and the shared resolution-policy copy (agents agree, else after 24 h matching agents attest and it resolves 2-of-3; votes can force arbitration). [web/src/features/markets/MarketInfo.tsx : L31-103]
- [SHIPPED] PriceChart reads `GET /markets/{id}/history` only and follows `activeConditionId`; empty history renders an empty state. [web/src/features/markets/PriceChart.tsx : L16-101]
- [SHIPPED] `api()` prefixes `NEXT_PUBLIC_API_URL`, attaches the `ou_token` bearer, and throws `ApiError` with `status` and `body`; ConnectBar fires an `ou-auth` event when the session changes. [web/src/shared/api/client.ts : L1-65]
- [SHIPPED] `CDPHooksProvider` with `ethereum.createOnLogin: "smart"`; without `NEXT_PUBLIC_CDP_PROJECT_ID` the app renders with an inert signed-out context. wagmi stays for injected operator/dev connectors. [web/src/app/providers.tsx : L21-45]

## Wallet

- [SHIPPED] Email OTP via CDP hooks; `POST /auth/cdp` exchanges the access token for an HS256 session. JWT `sub` is the smart account. [web/src/features/wallet/ConnectBar.tsx : L36-89]
- [SHIPPED] RampCard validates the amount, checks KYC, then opens MoonPay; the Coinbase URL is the fallback to the smart account. [web/src/features/wallet/RampCard.tsx : L41-98]
- [SHIPPED] User-facing wallets are CDP embedded smart accounts. See [ADR-0010](../adr/0010-cdp-embedded-wallets.md).

## Trade

- [SHIPPED] AmmSwap quotes, then executes a batched CDP user op: USDC `approve` + `buyWithUSDC`, or CTF `setApprovalForAll` + `sellToUSDC`, `useCdpPaymaster: true`, never a paymaster URL. Addresses come from `NEXT_PUBLIC_AMM_ADDRESS`, `NEXT_PUBLIC_USDC_ADDRESS` and `NEXT_PUBLIC_CTF_ADDRESS`. [web/src/features/trade/AmmSwap.tsx : L244-367]
- [SHIPPED] Trading closed: the server's `tradingHaltsAt` wins when present (`NEXT_PUBLIC_TRADING_HALT_AT_CLOSE` plus `closeTime` only when the field is missing); `resolved`, `tradingOpen: false`, a quote 409 or a `"market closed"` revert also close the ticket. A one-shot timer flips it at the halt time. [web/src/features/trade/tradingWindow.ts : L1-99] [web/src/features/trade/AmmSwap.tsx : L54-130]
- [SHIPPED] Closed state: no quoting, banner ("Trading closed at …", "Market resolved" or listing not confirmed), inputs and button disabled. Other quote errors show short text, not raw JSON. [web/src/features/trade/AmmSwap.tsx : L410-423]
- [SHIPPED] Success shows only once the user op is confirmed; a lost status poll keeps the ticket locked while it re-polls (up to 150 s) instead of reporting a failure. [web/src/features/wallet/userOpOutcome.ts : L45-126]
- [SHIPPED] AmmSwap is the only ticket, for every market type. The unrendered OrderTicket was deleted; there is no web CLOB ticket.
- [PHASE2] Add/remove-liquidity panel.

## List a market (OU-T010)

- [SHIPPED] `/list` loads `/markets/listing/config` (disabled state when `enabled` is false), waits for the API session, then checks eligibility (invite-only, cooldown, pending cap). [web/src/features/listing/ListMarketForm.tsx : L66-344]
- [SHIPPED] Client-side gates mirror the backend (question bytes and "?", subjective words, criteria length, close window with a 5-minute margin, minimum seed), show fee and total and require an acknowledgement. [web/src/features/listing/listing.ts : L167-349]
- [SHIPPED] Flow: prepare, refuse any call whose target is not the config's USDC or factory, send approve + `createPermissionlessMarket` as one user op on `base-sepolia` with `useCdpPaymaster: true`, wait for inclusion, then confirm. Confirm retries only 409 "not on chain yet" and 5xx with backoff (at most 12 calls); a 422 "listing rejected: …" is terminal; the batch is never re-sent once it has a user-op hash. Sponsorship needs the CDP Portal paymaster policy to allow USDC approve to the factory and `createPermissionlessMarket` (about 800k gas per op), a portal setting outside the repo.

## Oracle UI

- [SHIPPED] OraclePanel polls `/oracle/{id}/status`, drops `kind: research` rows (a missing kind counts as a resolution report, as on the server and mobile), keeps each agent's latest remaining row, labels outcome 2 "undetermined", computes unanimity itself and shows the vote count. Agreeing low-confidence or failed-send research rows therefore never show "unanimous … settling". [web/src/features/oracle/OraclePanel.tsx : L8-64] [web/src/features/oracle/oracleStatus.ts : L49-74]
- [PHASE2] Cast vote, evidence links, countdown to WINDOW, arbitration banner.

## Mobile

- [SHIPPED] Flutter app mirrors the web markets, trade, wallet and oracle modules; no listing UI. Email OTP and trades go through the OverUnder API (`/auth/cdp/*`, `/aa/cdp-send`). Shared tokens and OpenAPI: [mobile/README.md](../../mobile/README.md).
- [SHIPPED] Build-time `--dart-define`: `API_BASE_URL`, `CHAIN_ID` (default 31337) and `TRADING_HALT_AT_CLOSE` (fallback only when the API omits `tradingHaltsAt`). [mobile/lib/config/app_config.dart : L1-24]
- [SHIPPED] Models read camelCase with snake_case fallback; types 0 and 2 head event cards; `yesProbability` prefers `yesPriceMicros`; `isTradingClosed` is true when resolved, `tradingOpen` is false, or now ≥ `tradingHaltsAt`. [mobile/lib/models/models.dart : L150-166]
- [SHIPPED] Trade targets come from `GET /api/v1/chain/addresses`, else the bundled `assets/deployments/<CHAIN_ID>.json`; resolution fails closed on a chain mismatch or when neither exists. Regenerate the asset with `scripts/sync_mobile_deployments.py` after every AMM/Factory redeploy; it refuses simulated, failed or partial v2 uploads unless `--force` is passed. [mobile/lib/models/deployments.dart : L77-90]
- [SHIPPED] Quotes send `buy_yes`/`usdc_in` or `sell_yes`/`token_amount` in base units; a 409 from the quote or `/aa/cdp-send` becomes `TradingClosedException`. [mobile/lib/services/api_client.dart : L98-119] [mobile/lib/services/api_client.dart : L197-213]
- [SHIPPED] The swap widget shows a closed banner, disables its controls, flips at `tradingHaltsAt` with a timer and surfaces a failed address load on execute. [mobile/lib/features/trade/amm_swap_widget.dart : L81-144]
- [SHIPPED] Oracle status reads `attestations` (dropping `kind: research` rows) and `votes`.
- [STUB] `mobile/assets/deployments/84532.json` is untracked and stale; it must be regenerated after the v2 redeploy.
- [PHASE2] No `android/` or `ios/` folders yet; `flutter create .` comes first for store builds.
