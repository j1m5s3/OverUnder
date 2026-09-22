---
title: Injected wallet not full Privy AA
status: MIXED
area: web
summary: Historical injected-wagmi and OverUnderPaymaster app path. User-facing wallets are superseded by ADR-0010 CDP embedded wallets.
last_verified: 2026-09-21
pointers:
  - "[docs/adr/0010-cdp-embedded-wallets.md : L28-39]"
  - "[contracts/src/OverUnderPaymaster.vy : L322-355]"
  - "[contracts/src/SimpleAccount.vy : L40-49]"
  - "[backend/app/auth/router.py : L86-141]"
---

## Status

Superseded 2026-09-21 for the **user-facing wallet path** by [ADR-0010](0010-cdp-embedded-wallets.md). Decision body below is historical. SIWE for operator/dev JWT and leftover OverUnderPaymaster/SimpleAccount source remain.

## Context

The product goal included email/AA wallets and gasless flow. Coinbase connector bundling (x402) and Privy app credentials blocked email AA. An injected EOA was enough to own a `SimpleAccount` and submit sponsored UserOps.

## Decision

- [SHIPPED] Historical: wagmi `injected()` only, ERC-4337 paymaster + SimpleAccount for injected EOA owners. Contracts remain in-tree; the app no longer calls them. [contracts/src/OverUnderPaymaster.vy : L322-355]
- [SHIPPED] SIWE with `ecrecover` remains for operator/dev JWT only. [backend/app/auth/router.py : L86-141]
- [SHIPPED] User-facing login and gasless swaps: Coinbase CDP embedded smart accounts. See [ADR-0010](0010-cdp-embedded-wallets.md).

## Consequences

- [SHIPPED] `next build` stayed free of unused Privy/x402 connector barrels.
- Negative: demo email-hex login trained a false auth model; that path is removed.
- Gasless swaps no longer imply an OverUnderPaymaster tank or Privy JWKS.
