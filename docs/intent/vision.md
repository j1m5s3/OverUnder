---
title: Product vision
status: MIXED
area: intent
summary: AMM-first prediction markets on Base with AI oracles, a seeded uniform-LVR AMM (static pm-AMM, halts at close), loosely gated user listing and an OU revenue token.
last_verified: 2026-09-23
pointers: []
---

# Product vision

OverUnder is a prediction market where users trade YES/NO on real-world events, settled in USDC on Base. The product wedge is long-tail plus AI-generated wildcards and user-listed markets that trade on a seeded AMM without recruiting professional makers. Three independent AI agents replace human UMA oracles. See [ADR-0007](../adr/0007-amm-first-uniform-lvr.md).

## Who it is for

- [SHIPPED] Traders who sign in with email and receive a CDP smart account.
- [SHIPPED] Listers who seed a market of their own and own its LP (web `/list`).
- [SHIPPED] Flutter app mirroring web markets/trade/wallet/oracle. [mobile/README.md](../../mobile/README.md)
- [SHIPPED] Card on-ramp after KYC (MoonPay; Coinbase URL fallback) to the smart account.
- [SHIPPED] Gasless swaps and listings via Coinbase CDP Paymaster (ADR-0010). OverUnderPaymaster is leftover.

## What “done” means for the protocol

- [SHIPPED] Binary markets with CTF split/merge/redeem.
- [SHIPPED] Seeded uniform-LVR AMM (static pm-AMM, MarketAMM v2) on every market type at 100 bps (50/50 vault/LP), trading halted at closeTime on chain and in the API ([ADR-0011](../adr/0011-pm-amm-v2-close-gate.md)). Base Sepolia moves from the v1 CPMM to v2 with the post-merge redeploy.
- [SHIPPED] Loosely gated user listing (OU-T010) with seed, horizon, criteria and cooldown gates ([ADR-0012](../adr/0012-loosely-gated-user-listing.md)).
- [SHIPPED] Unanimous 3-agent resolve, else after 24 h matching agents attest and it resolves 2/3 (token-weighted votes can force arbitration), else operator.
- [SHIPPED] Protocol USDC fees accrue; OU redeems at NAV after cooldown.
- [SHIPPED] Exchange CLOB remains deployed leftover overlay with a production relayer (OU-T003); it is not the product path.
- [SHIPPED] ERC-4337 OverUnderPaymaster leftover (OU-T001); app path is CDP Paymaster (ADR-0010).
- [SHIPPED] Production email AA via Coinbase CDP (`validateAccessToken`).
- [STUB] Refund settlement for cancelled or unanswerable markets (OU-T014).
- [PHASE2] Dynamic liquidity L_t (OU-T015); smart-account CLOB makers (OU-T016).

## Non-goals (MVP)

- [SHIPPED] Decision: OU is not a savings vault and has no public mint. See [ADR-0003](../adr/0003-ou-nav-token-not-savings-vault.md).
- [SHIPPED] Decision: user-facing wallets are Coinbase CDP embedded smart accounts. See [ADR-0010](../adr/0010-cdp-embedded-wallets.md). Historical injected path: [ADR-0005](../adr/0005-injected-wallet-not-full-privy-aa.md).
- [SHIPPED] Decision: no CLOB-as-default, no vampire maker rebates, no prediction perps, no B2B embed SDK. See [ADR-0007](../adr/0007-amm-first-uniform-lvr.md).
- [PHASE2] Multi-outcome categorical markets, parlay combinators, and sports-book cash-out.

## Equal-depth future

Remaining Phase 2 work is in [phase-2.md](../roadmap/phase-2.md) and [TODOS.md](../TODOS.md): invalid/refund outcome (OU-T014), dynamic L_t (OU-T015) and smart-account CLOB makers (OU-T016). Agents must not describe those as implemented. OU-T001–T013 are done, including the relayer, the pm-AMM and user listing.
