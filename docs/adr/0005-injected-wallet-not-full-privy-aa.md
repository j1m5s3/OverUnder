---
title: Injected wallet not full Privy AA
status: MIXED
area: web
summary: MVP uses wagmi injected plus demo email hex; paymaster for SimpleAccount is shipped; Privy JWKS and email AA remain phase 2.
last_verified: 2026-09-21
pointers:
  - "[web/src/app/providers.tsx : L10-17]"
  - "[web/src/features/wallet/ConnectBar.tsx : L13-46]"
  - "[backend/app/auth/router.py : L90-102]"
  - "[contracts/src/OverUnderPaymaster.vy : L322-355]"
  - "[web/src/features/aa/userOp.ts : L83-155]"
---

## Status

Accepted 2026-09-16. Paymaster closeout 2026-09-21.

## Context

The product goal includes email/AA wallets (Privy) and gasless flow. Coinbase connector bundling (x402) and Privy app credentials still block email AA. An injected EOA is enough to own a `SimpleAccount` and submit sponsored UserOps.

## Decision

- [SHIPPED] wagmi `injected()` only. [web/src/app/providers.tsx : L10-17]
- [STUB] SIWE posts `signature: "0x"`. Email maps a string to a fake address and `POST /auth/privy` without JWKS. [web/src/features/wallet/ConnectBar.tsx : L13-46] [backend/app/auth/router.py : L90-102]
- [SHIPPED] ERC-4337 paymaster + SimpleAccount for injected EOA owners. [contracts/src/OverUnderPaymaster.vy : L322-355] [web/src/features/aa/userOp.ts : L83-155]
- [PHASE2] Privy embedded wallets, JWKS, and email AA users (see [phase-2.md](../roadmap/phase-2.md)).

## Consequences

- [SHIPPED] `next build` stays free of unused connector barrels.
- [STUB] Local demo can log in without a browser extension via email hex.
- [STUB] Negative: demo addresses cannot sign Exchange orders or hold on-chain positions; treating them as users trains a false auth model and must not reach production.
- Gasless swaps do not imply Privy email wallets exist.
