---
title: Injected wallet not full Privy AA
status: MIXED
area: web
summary: MVP uses wagmi injected plus demo email hex; Privy JWKS, embedded wallets, and paymasters are phase 2.
last_verified: 2026-09-16
pointers:
  - "[web/src/app/providers.tsx : L10-17]"
  - "[web/src/features/wallet/ConnectBar.tsx : L13-46]"
  - "[backend/app/auth/router.py : L90-102]"
---

## Status

Accepted 2026-09-16.

## Context

The product goal includes email/AA wallets (Privy) and gasless flow. Shipping that in MVP blocked on Coinbase connector bundling (x402) and Privy app credentials. An injected EOA is enough to exercise CLOB + AMM + oracle APIs.

## Decision

- [SHIPPED] wagmi `injected()` only. [web/src/app/providers.tsx : L10-17]
- [STUB] SIWE posts `signature: "0x"`. Email maps a string to a fake address and `POST /auth/privy` without JWKS. [web/src/features/wallet/ConnectBar.tsx : L13-46] [backend/app/auth/router.py : L90-102]
- [PHASE2] Privy embedded wallets, real SIWE, JWKS, and an ERC-4337 paymaster (see [phase-2.md](../roadmap/phase-2.md)).

## Consequences

- [SHIPPED] `next build` stays free of unused connector barrels.
- [STUB] Local demo can log in without a browser extension via email hex.
- [STUB] Negative: demo addresses cannot sign Exchange orders or hold on-chain positions; treating them as users trains a false auth model and must not reach production.
