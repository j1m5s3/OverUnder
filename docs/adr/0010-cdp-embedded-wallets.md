---
title: Coinbase CDP embedded wallets
status: SHIPPED
area: web
summary: User-facing wallets are Coinbase CDP smart accounts with email OTP and CDP Paymaster. Session JWT stays HS256 with sub from validateAccessToken. OverUnderPaymaster remains in-tree leftover.
last_verified: 2026-09-21
pointers:
  - "[web/src/app/providers.tsx : L21-36]"
  - "[web/src/features/wallet/ConnectBar.tsx : L35-57]"
  - "[web/src/features/trade/AmmSwap.tsx : L187-211]"
  - "[backend/app/cdp.py : L21-25]"
  - "[backend/app/cdp.py : L116-134]"
  - "[backend/app/cdp.py : L168-179]"
  - "[backend/app/auth/router.py : L144-173]"
  - "[backend/app/aa/router.py : L97-99]"
  - "[backend/app/aa/router.py : L102-142]"
  - "[mobile/lib/services/api_client.dart : L102-140]"
---

## Status

Accepted 2026-09-21. Supersedes the user-facing wallet path of [ADR-0005](0005-injected-wallet-not-full-privy-aa.md).

## Context

ADR-0005 shipped injected wagmi plus an in-house ERC-4337 paymaster for EOA-owned SimpleAccounts. Email login was a demo hex address. Privy JWKS never shipped. Users still pasted keys or used a browser extension. Flutter has no Coinbase CDP SDK, so mobile cannot call CDP from the client.

## Decision

- [SHIPPED] Web uses `@coinbase/cdp-hooks`. `CDPHooksProvider` sets `ethereum.createOnLogin: "smart"`. Email OTP via `useSignInWithEmail` / `useVerifyEmailOTP`. [web/src/app/providers.tsx : L21-36] [web/src/features/wallet/ConnectBar.tsx : L59-87]
- [SHIPPED] Web trades call `useSendUserOperation` on `base-sepolia` with `useCdpPaymaster: true`. Never a paymaster URL in client code. One user op may batch USDC `approve`, CTF `setApprovalForAll`, `buyWithUSDC`, and `sellToUSDC` with `value` 0. [web/src/features/trade/AmmSwap.tsx : L187-211] [web/src/features/trade/AmmSwap.tsx : L239-263]
- [SHIPPED] Flutter calls only OverUnder API: `POST /auth/cdp/email`, `POST /auth/cdp/verify`, `POST /aa/cdp-send`. Backend sends `POST /v2/embedded-wallet-api/end-users/{userId}/evm/smart-accounts/{address}/send` with `useCdpPaymaster: true`. Dart does not send transactions with web3dart. [backend/app/cdp.py : L168-179] [mobile/lib/services/api_client.dart : L102-140]
- [SHIPPED] `POST /auth/cdp` calls `validateAccessToken`. Session JWT remains HS256. `sub` is the smart-account address. Client-supplied address is ignored. CDP users are never operators. [backend/app/auth/router.py : L144-173] [backend/app/cdp.py : L116-134]
- [SHIPPED] SIWE stays for operator/dev JWT only. `OPERATOR_PRIVATE_KEY` still gates operator routes. [backend/app/auth/router.py : L86-141] [backend/app/auth/router.py : L68-71]
- [SHIPPED] `POST /aa/userop` returns 410. Allowlist on `/aa/cdp-send` rejects `matchOrders`, nonzero `value`, and anything else with 403. [backend/app/aa/router.py : L97-99] [backend/app/aa/router.py : L70-94]
- [SHIPPED] Fail closed on CDP routes when `CDP_PROJECT_ID` or `CDP_API_KEY_SECRET` is missing. Tests mock the CDP client. [backend/app/cdp.py : L21-25]
- [SHIPPED] Local fallback may read `.secrets/cb_keys.json` keys `PROJECT_ID`, `API_KEY_ID`, `API_SECRET` only if env vars are unset. No wallet secret unless a SDK call fails without it. [backend/app/config.py : L11-31]
- [SHIPPED] Onramp destination is the smart account. [web/src/app/wallet/page.tsx : L6-13]
- [SHIPPED] `OverUnderPaymaster.vy` and `SimpleAccount.vy` stay in the repo and are not redeployed for this path. The app does not call them.

## Consequences

- Email users get a Base Sepolia smart account without a pasted key or extension.
- Negative: Flutter send depends on Coinbase REST plus developer JWT; a CDP outage or missing `X-Wallet-Auth`/delegation blocks mobile trades while web can still sign in the browser SDK.
- Negative: leftover OverUnderPaymaster ETH tank is unused by the app and can still confuse operators who refill it.
