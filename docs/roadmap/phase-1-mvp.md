---
title: Phase 1 MVP
status: MIXED
area: roadmap
summary: Vertical slice shipped on Anvil — seeded CPMM on all market types, three-agent oracle, OU NAV, Next.js AMM ticket.
last_verified: 2026-09-20
pointers:
  - "[contracts/script/deploy.py : L29-40]"
  - "[scripts/e2e_local.py : L27-114]"
  - "[scripts/run_stack.cmd : L1-56]"
  - "[backend/app/main.py : L22-38]"
  - "[contracts/src/MarketFactory.vy : L82-89]"
  - "[web/src/features/trade/AmmSwap.tsx : L163-272]"
---

# Phase 1 MVP

Scope: one operator can list a seeded primary, a generator can seed a wildcard, a trader can `buyWithUSDC` and `sellToUSDC` on the seeded CPMM, agents can unanimous-resolve, fallback math is tested, OU can request/claim redeem. Local only (Anvil 31337). CLOB `matchOrders` is leftover overlay, not this happy path.

## Shipped

- [SHIPPED] Vyper protocol deploy as one graph. [contracts/script/deploy.py : L29-40]
- [SHIPPED] boa tests for AMM, oracle, vault, factory required seed; leftover Exchange tests stay in `test_exchange.py`.
- [SHIPPED] `scripts/e2e_local.py` happy path (seeded AMM buy+sell) plus fallback. [scripts/e2e_local.py : L27-114]
- [SHIPPED] FastAPI routers, factory-backed `POST /markets`, `/health`.
- [SHIPPED] Indexer loop started from app lifespan. [backend/app/main.py : L22-38]
- [SHIPPED] Next.js markets / detail / portfolio / wallet pages with AmmSwap as the only ticket.
- [SHIPPED] `scripts/run_stack.cmd` / `stop_stack.cmd` (port-kill, not window titles).

## Explicit stubs inside the slice

- [STUB] SIWE without `ecrecover`; Privy without JWKS.
- [STUB] OrderTicket file retained, unrendered leftover overlay.
- [STUB] Oracle API stores attestations off-chain.
- [STUB] Coinbase ramp URL builder remains as MoonPay fallback.

## Out of phase 1

- [PHASE2] Paymaster, Privy JWKS, production relayer, uniform-LVR (OU-T008/T009), permissionless listing (OU-T010).
- [SHIPPED] Flutter, OU emissions, live LLM agents, MoonPay/KYC are already done (OU-T004–T007); not this closeout.

## Exit criteria (met)

- [SHIPPED] `pytest` green under Python 3.12 venv for contracts, backend, oracles.
- [SHIPPED] `next build` succeeds with injected-only wagmi.
- [SHIPPED] e2e script exits 0 against in-process boa with zero `matchOrders` in `scripts/`.
