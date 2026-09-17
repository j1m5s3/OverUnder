---
title: Phase 1 MVP
status: MIXED
area: roadmap
summary: Vertical slice shipped on Anvil — hybrid books, three-agent oracle, OU NAV, Next.js shell.
last_verified: 2026-09-16
pointers:
  - "[contracts/script/deploy.py : L29-40]"
  - "[scripts/e2e_local.py : L41-70]"
  - "[scripts/run_stack.cmd : L1-56]"
  - "[backend/app/main.py : L23-48]"
---

# Phase 1 MVP

Scope: one operator can list a primary, a generator can seed a wildcard, two traders can fill a CLOB order and quote an AMM, agents can unanimous-resolve, fallback math is tested, OU can request/claim redeem. Local only (Anvil 31337).

## Shipped

- [SHIPPED] Vyper protocol deploy as one graph. [contracts/script/deploy.py : L29-40]
- [SHIPPED] boa tests for Exchange, AMM, oracle, vault, factory.
- [SHIPPED] `scripts/e2e_local.py` happy path (CLOB + wildcard seed) plus fallback. [scripts/e2e_local.py : L41-70]
- [SHIPPED] FastAPI routers and `/health`.
- [SHIPPED] Next.js markets / detail / portfolio / wallet pages.
- [SHIPPED] `scripts/run_stack.cmd` / `stop_stack.cmd` (port-kill, not window titles).

## Explicit stubs inside the slice

- [STUB] SIWE without `ecrecover`; Privy without JWKS.
- [STUB] OrderTicket unsigned orders; matcher may not land `matchOrders`.
- [STUB] Markets API is SQLite, not factory transactions.
- [STUB] AMM UI quotes only.
- [STUB] Oracle API stores attestations off-chain.
- [STUB] Coinbase ramp URL builder without sessions.
- [STUB] Indexer not wired into app lifespan.

## Out of phase 1

- [PHASE2] Flutter, paymaster, Privy JWKS, production relayer, MoonPay/KYC, OU emissions.

## Exit criteria (met)

- [SHIPPED] `pytest` green under Python 3.12 venv for contracts, backend, oracles.
- [SHIPPED] `next build` succeeds with injected-only wagmi.
- [SHIPPED] e2e script exits 0 against in-process boa.
