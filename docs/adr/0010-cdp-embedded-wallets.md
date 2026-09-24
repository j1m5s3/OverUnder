---
title: Coinbase CDP embedded wallets
status: SHIPPED
area: web
summary: User-facing wallets are Coinbase CDP smart accounts with email OTP and CDP Paymaster. Session JWT stays HS256 with sub from validateAccessToken. /aa/cdp-send also sponsors prepared user listings and refuses halted trades (409). OverUnderPaymaster remains in-tree leftover.
last_verified: 2026-09-23
pointers:
  - "[web/src/app/providers.tsx : L21-26]"
  - "[web/src/features/wallet/ConnectBar.tsx : L36-89]"
  - "[web/src/features/trade/AmmSwap.tsx : L267-291]"
  - "[backend/app/cdp.py : L21-25]"
  - "[backend/app/cdp.py : L116-134]"
  - "[backend/app/cdp.py : L169-180]"
  - "[backend/app/auth/router.py : L144-173]"
  - "[backend/app/aa/router.py : L75-160]"
  - "[backend/app/aa/router.py : L163-222]"
  - "[backend/app/chain/router.py : L36-48]"
  - "[mobile/lib/services/api_client.dart : L170-213]"
---

## Status

Accepted 2026-09-21. Supersedes the user-facing wallet path of [ADR-0005](0005-injected-wallet-not-full-privy-aa.md).

Amended 2026-09-23 (user listing, [ADR-0012](0012-loosely-gated-user-listing.md); trading halt, [ADR-0011](0011-pm-amm-v2-close-gate.md)). `/aa/cdp-send` changes:
- The allowlist adds two calls: USDC `approve` with the configured MarketFactory as spender (the AMM is still allowed), and the factory's `createPermissionlessMarket` (`0x4e7d1a32`). Operator-only factory calls and `matchOrders` stay rejected. [backend/app/aa/router.py : L75-104]
- A listing call is sponsored only if it matches a `prepared` MarketListing row of the same user: same salt, question, criteria hash, closeTime and seed. Otherwise it gets 403 `listing not prepared` or `listing differs from the prepared listing`. The web `/list` flow sends its batch straight to CDP instead, so `/confirm` and the indexer review remain the real guard. [backend/app/aa/router.py : L117-160]
- AMM buy and sell calls on a market that is closed, resolved or an unconfirmed user listing get 409 before anything is sent. [backend/app/aa/router.py : L202-205]

Also new: public `GET /api/v1/chain/addresses` returns `{chainId, MockUSDC, ConditionalTokens, MarketAMM, MarketFactory, ConsensusOracle, FeeVault, Exchange}`. The values come from Settings only (the same fields the allowlist uses), in EIP-55 form, null when unset, so mobile targets exactly what the API will sponsor. [backend/app/chain/router.py : L1-48]

The CDP Portal paymaster policy is a manual step after the v2 redeploy. It must allow the v2 AMM and factory, `createPermissionlessMarket`, and a per-op gas cap of about 750k.

## Context

ADR-0005 shipped injected wagmi plus an in-house ERC-4337 paymaster for EOA-owned SimpleAccounts. Email login was a demo hex address. Privy JWKS never shipped. Users still pasted keys or used a browser extension. Flutter has no Coinbase CDP SDK, so mobile cannot call CDP from the client.

## Decision

- [SHIPPED] Web uses `@coinbase/cdp-hooks`. `CDPHooksProvider` sets `ethereum.createOnLogin: "smart"`. Email OTP via `useSignInWithEmail` / `useVerifyEmailOTP`. [web/src/app/providers.tsx : L21-26] [web/src/features/wallet/ConnectBar.tsx : L61-89]
- [SHIPPED] Web trades call `useSendUserOperation` on `base-sepolia` with `useCdpPaymaster: true`. No paymaster URL ever appears in client code. One user op may batch USDC `approve`, CTF `setApprovalForAll`, `buyWithUSDC` and `sellToUSDC` with `value` 0. The web listing batch (`approve` + `createPermissionlessMarket`) uses the same hook. [web/src/features/trade/AmmSwap.tsx : L267-291] [web/src/features/trade/AmmSwap.tsx : L335-359] [web/src/features/listing/ListMarketForm.tsx : L328-333]
- [SHIPPED] Flutter calls only the OverUnder API: `POST /auth/cdp/email`, `POST /auth/cdp/verify`, `POST /aa/cdp-send`. The backend sends `POST /v2/embedded-wallet-api/end-users/{userId}/evm/smart-accounts/{address}/send` with `useCdpPaymaster: true`. Dart does not send transactions with web3dart. [backend/app/cdp.py : L169-180] [mobile/lib/services/api_client.dart : L170-213]
- [SHIPPED] `POST /auth/cdp` calls `validateAccessToken`. Session JWT remains HS256. `sub` is the smart-account address. Client-supplied address is ignored. CDP users are never operators. [backend/app/auth/router.py : L144-173] [backend/app/cdp.py : L116-134]
- [SHIPPED] SIWE stays for operator/dev JWT only. `OPERATOR_PRIVATE_KEY` still gates operator routes. [backend/app/auth/router.py : L86-141] [backend/app/auth/router.py : L68-71]
- [SHIPPED] `POST /aa/userop` returns 410. The `/aa/cdp-send` allowlist rejects `matchOrders`, nonzero `value` and anything not listed above with 403. [backend/app/aa/router.py : L163-165] [backend/app/aa/router.py : L75-104]
- [SHIPPED] Fail closed on CDP routes when `CDP_PROJECT_ID` or `CDP_API_KEY_SECRET` is missing. Tests mock the CDP client. [backend/app/cdp.py : L21-25]
- [SHIPPED] Local fallback may read `.secrets/cb_keys.json` keys `PROJECT_ID`, `API_KEY_ID`, `API_SECRET` only if env vars are unset. No wallet secret unless a SDK call fails without it. [backend/app/config.py : L12-32]
- [SHIPPED] Onramp destination is the smart account. [web/src/app/wallet/page.tsx : L7-16]
- [SHIPPED] `OverUnderPaymaster.vy` and `SimpleAccount.vy` stay in the repo and are not redeployed for this path. The app does not call them.

## Consequences

- Email users get a Base Sepolia smart account without a pasted key or extension.
- Negative: Flutter send depends on Coinbase REST plus developer JWT; a CDP outage or missing `X-Wallet-Auth`/delegation blocks mobile trades while web can still sign in the browser SDK.
- Negative: leftover OverUnderPaymaster ETH tank is unused by the app and can still confuse operators who refill it.
- Negative: the web sends sponsored batches straight to CDP. Server-side checks on `/aa/cdp-send` therefore cover only mobile. The CDP Portal policy and the backend's `/confirm` and indexer review are the controls for web listings.
- Negative: smart accounts sign through EIP-1271, which the leftover CLOB `Exchange` does not accept (OU-T016).
