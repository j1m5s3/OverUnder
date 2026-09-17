---
title: OU NAV token is not a savings vault
status: SHIPPED
area: contracts
summary: OU is 100M fixed supply; fees accrue as USDC and redeem burns OU at NAV. No public mint.
last_verified: 2026-09-16
pointers:
  - "[contracts/src/RevenueToken.vy : L13-32]"
  - "[contracts/src/FeeVault.vy : L44-50]"
  - "[contracts/src/FeeVault.vy : L61-83]"
---

## Status

Accepted 2026-09-16.

## Context

Aave-style revenue tokens often invite “deposit USDC, mint share, earn yield” UX. That collides with a prediction-market fee sink: unlimited mint would dilute traders who already bought OU, and a savings vault would compete with holding outcome tokens.

## Decision

- Mint 100M OU once to treasury. No `mint` function. [contracts/src/RevenueToken.vy : L13-32]
- FeeVault holds protocol USDC. `nav()` is `usdc * 1e18 / supply`. [contracts/src/FeeVault.vy : L44-50]
- Exit is `requestRedeem` + cooldown + `claim` which burns OU. [contracts/src/FeeVault.vy : L61-83]
- Emissions, if any, are a phase-2 treasury or separate minter — not this vault.

## Consequences

- Fee accrual raises NAV per remaining OU as supply burns on redeem.
- Treasury can grant OU without touching USDC NAV (grants dilute NAV).
- Negative: with 100M × 1e18 supply and MVP-scale fees, `nav()` integer-divides to 0, so UI cannot show a meaningful price until volume is large or display uses a different scale (e.g. USDC per 1 OU using `previewRedeem`).
