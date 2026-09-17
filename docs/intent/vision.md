---
title: Product vision
status: MIXED
area: intent
summary: Polymarket-class prediction markets on Base with AI oracles, hybrid books, and an OU revenue token.
last_verified: 2026-09-16
pointers: []
---

# Product vision

OverUnder is a prediction market where users trade YES/NO on real-world events, settled in USDC on Base. The product copies Polymarket’s liquidity pattern (CLOB on flagship events, AMM on long-tail) and replaces human UMA oracles with three independent AI agents.

## Who it is for

- [SHIPPED] Traders who already hold (or can mint locally) USDC and an injected EOA.
- [STUB] Email users who get a demo address without a real smart wallet.
- [PHASE2] Mobile-first bettors (Flutter), users who never touch a seed phrase (Privy AA + paymaster), and users who buy USDC with a card after KYC.

## What “done” means for the protocol

- [SHIPPED] Binary markets with CTF split/merge/redeem.
- [SHIPPED] Primary CLOB settlement at 75 bps taker.
- [SHIPPED] Wildcard CPMM at 100 bps (50/50 vault/LP).
- [SHIPPED] Unanimous 3-agent resolve, else 24h 2/3 + token-weighted votes, else operator.
- [SHIPPED] Protocol USDC fees accrue; OU redeems at NAV after cooldown.
- [PHASE2] Production wallets, gasless orders, KYC ramps, Flutter, Polymarket-parity UX, and an emissions program that does not break NAV accounting.

## Non-goals (MVP)

- [SHIPPED] Decision: OU is not a savings vault and has no public mint. See [ADR-0003](../adr/0003-ou-nav-token-not-savings-vault.md).
- [SHIPPED] Decision: wallets are injected, not full Privy AA. See [ADR-0005](../adr/0005-injected-wallet-not-full-privy-aa.md).
- [PHASE2] Multi-outcome categorical markets, parlay combinators, and sports-book cash-out.

## Equal-depth future

Phase 2 is specified at the same grain as the shipped MVP in [phase-2.md](../roadmap/phase-2.md): Flutter module map, CLOB UX parity, ERC-4337 paymaster, Privy JWKS, MoonPay/KYC, and OU emissions. Agents must not describe those as implemented.
