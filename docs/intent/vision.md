---
title: Product vision
status: MIXED
area: intent
summary: AMM-first prediction markets on Base with AI oracles, seeded CPMM, and an OU revenue token.
last_verified: 2026-09-21
pointers: []
---

# Product vision

OverUnder is a prediction market where users trade YES/NO on real-world events, settled in USDC on Base. The product wedge is long-tail plus AI-generated wildcards that trade on a seeded AMM without recruiting professional makers. Three independent AI agents replace human UMA oracles. See [ADR-0007](../adr/0007-amm-first-uniform-lvr.md).

## Who it is for

- [SHIPPED] Traders who sign in with email and receive a CDP smart account.
- [SHIPPED] Flutter app mirroring web markets/trade/wallet/oracle. [mobile/README.md](../../mobile/README.md)
- [SHIPPED] Card on-ramp after KYC (MoonPay; Coinbase URL fallback) to the smart account.
- [SHIPPED] Gasless swaps via Coinbase CDP Paymaster (ADR-0010). OverUnderPaymaster is leftover.

## What “done” means for the protocol

- [SHIPPED] Binary markets with CTF split/merge/redeem.
- [SHIPPED] Seeded CPMM on every market type at 100 bps (50/50 vault/LP).
- [SHIPPED] Unanimous 3-agent resolve, else 24h 2/3 + token-weighted votes, else operator.
- [SHIPPED] Protocol USDC fees accrue; OU redeems at NAV after cooldown.
- [SHIPPED] Exchange CLOB remains deployed leftover overlay; it is not the product path.
- [PHASE2] Uniform-LVR default pool (OU-T008/T009). Permissionless listing (OU-T010).
- [SHIPPED] ERC-4337 OverUnderPaymaster leftover (OU-T001); app path is CDP Paymaster (ADR-0010).
- [SHIPPED] Production email AA via Coinbase CDP (`validateAccessToken`). CLOB overlay is optional, not required.

## Non-goals (MVP)

- [SHIPPED] Decision: OU is not a savings vault and has no public mint. See [ADR-0003](../adr/0003-ou-nav-token-not-savings-vault.md).
- [SHIPPED] Decision: user-facing wallets are Coinbase CDP embedded smart accounts. See [ADR-0010](../adr/0010-cdp-embedded-wallets.md). Historical injected path: [ADR-0005](../adr/0005-injected-wallet-not-full-privy-aa.md).
- [SHIPPED] Decision: no CLOB-as-default, no vampire maker rebates, no prediction perps, no B2B embed SDK. See [ADR-0007](../adr/0007-amm-first-uniform-lvr.md).
- [PHASE2] Multi-outcome categorical markets, parlay combinators, and sports-book cash-out.

## Equal-depth future

Phase 2 remaining work is specified in [phase-2.md](../roadmap/phase-2.md): uniform-LVR, permissionless listing, leftover CLOB overlay. Agents must not describe those as implemented. Flutter, emissions, live LLM agents, MoonPay/KYC, CDP wallets, and leftover OverUnderPaymaster are already shipped (OU-T001, OU-T002, OU-T004–T007).
