---
title: Web
status: MIXED
area: web
summary: Next.js feature modules for markets, CLOB, AMM quotes, wallet stubs, and oracle status.
last_verified: 2026-09-16
pointers:
  - "[web/src/app/providers.tsx : L10-17]"
  - "[web/src/features/wallet/ConnectBar.tsx : L13-46]"
  - "[web/src/features/trade/OrderTicket.tsx : L18-39]"
  - "[web/src/features/trade/AmmSwap.tsx : L11-16]"
  - "[web/src/features/oracle/OraclePanel.tsx : L6-17]"
  - "[web/src/features/wallet/RampCard.tsx : L8-13]"
  - "[web/src/shared/api/client.ts : L1-11]"
  - "[web/src/app/page.tsx : L1-10]"
  - "[mobile/README.md : L1-25]"
---

# Web

Next.js App Router under `web/`. Feature folders are the Flutter carryover map.

## Shell

- [SHIPPED] Markets home, market detail, portfolio, wallet routes.
- [SHIPPED] `api()` prefixes `NEXT_PUBLIC_API_URL` and attaches `ou_token` bearer. [web/src/shared/api/client.ts : L1-11]
- [SHIPPED] wagmi config: Anvil/Base Sepolia/Base, **injected connector only** (Coinbase/x402 barrel omitted to keep `next build` green). [web/src/app/providers.tsx : L10-17]

## Wallet

- [STUB] Injected connect triggers SIWE with `signature: "0x"` and a constructed nonce message. [web/src/features/wallet/ConnectBar.tsx : L13-30]
- [STUB] Email path hashes the email string into a 20-byte demo address and `POST /auth/privy` with token `privy-demo`. [web/src/features/wallet/ConnectBar.tsx : L32-46]
- [STUB] RampCard fetches a Coinbase URL; no MoonPay, no KYC gate. [web/src/features/wallet/RampCard.tsx : L8-13]
- [PHASE2] Privy embedded wallet + email OTP, EIP-1193 provider, SIWE with `personal_sign`, Coinbase Smart Wallet, and ERC-4337 session keys.

## Trade

- [STUB] OrderTicket posts unsigned orders (`signature: "0x"`, synthetic `orderHash`). [web/src/features/trade/OrderTicket.tsx : L18-39]
- [SHIPPED] OrderTicket loads the off-chain book and shows 75 bps copy.
- [STUB] AmmSwap quotes only; it does not send `buyWithUSDC`. [web/src/features/trade/AmmSwap.tsx : L11-16]
- [PHASE2] EIP-712 typed-data sign for Exchange orders, allowance UX (USDC + setApprovalForAll), AMM swap via wallet, and smart-wallet batching (approve+swap).

## Oracle UI

- [SHIPPED] OraclePanel polls `/oracle/{id}/status` and lists agent summaries. [web/src/features/oracle/OraclePanel.tsx : L6-17]
- [PHASE2] Cast vote, evidence links, countdown to WINDOW, arbitration banner.

## Mobile

- [PHASE2] Do not implement Flutter in this tree yet. Feature map and shared tokens/OpenAPI: [mobile/README.md](../../mobile/README.md).
