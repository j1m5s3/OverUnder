---
title: Phase 1 MVP
status: MIXED
area: roadmap
summary: Vertical slice shipped on Anvil — seeded AMM on all market types (a CPMM then; MarketAMM v2 is now a static pm-AMM), three-agent oracle, OU NAV, Next.js AMM ticket.
last_verified: 2026-09-24
pointers:
  - "[contracts/script/deploy.py : L160-242]"
  - "[scripts/e2e_local.py : L27-113]"
  - "[scripts/run_stack.cmd : L1-56]"
  - "[backend/app/main.py : L27-61]"
  - "[contracts/src/MarketFactory.vy : L143-150]"
  - "[web/src/features/trade/AmmSwap.tsx : L54-70]"
---

# Phase 1 MVP

Scope: one operator can list a seeded primary, a generator can seed a wildcard, a trader can `buyWithUSDC` and `sellToUSDC` on the seeded AMM, agents can unanimous-resolve, fallback math is tested, OU can request/claim redeem. Local only (Anvil 31337). CLOB `matchOrders` is leftover overlay, not this happy path. Phase 1 shipped the AMM as a CPMM; OU-T009 later replaced it with MarketAMM v2 behind the same ABI.

## Shipped

- [SHIPPED] Vyper protocol deploy as one graph. [contracts/script/deploy.py : L160-242]
- [SHIPPED] boa tests for AMM, oracle, vault, factory required seed; leftover Exchange tests stay in `test_exchange.py`.
- [SHIPPED] `scripts/e2e_local.py` happy path (seeded AMM buy+sell) plus fallback. [scripts/e2e_local.py : L27-113]
- [SHIPPED] FastAPI routers, factory-backed `POST /markets`, `/health`.
- [SHIPPED] Indexer loop started from app lifespan. [backend/app/main.py : L27-61]
- [SHIPPED] Next.js markets / detail / portfolio / wallet pages with AmmSwap as the only ticket. [web/src/features/trade/AmmSwap.tsx : L54-70]
- [SHIPPED] `scripts/run_stack.cmd` / `stop_stack.cmd` (port-kill, not window titles).

## Explicit stubs inside the slice

- [SHIPPED] The unrendered web OrderTicket was deleted (2026-09-23); there is no web CLOB ticket.
- [STUB] Oracle API stores attestations and votes off-chain only (the oracle job sends on-chain txs itself); since 2026-09-23 attest is operator-only and vote weight is read from the CTF.
- [STUB] Coinbase ramp URL builder remains as MoonPay fallback.

## Out of phase 1

- [SHIPPED] Production leftover-CLOB relayer, uniform-LVR MarketAMM v2 and loosely gated user listing are already done (OU-T003, OU-T008–T010); not this closeout.
- [SHIPPED] Flutter, OU emissions, live LLM agents, MoonPay/KYC are already done (OU-T004–T007); not this closeout.

## Exit criteria (met)

- [SHIPPED] `pytest` green under Python 3.12 venv for contracts, backend, oracles.
- [SHIPPED] `next build` succeeds with injected-only wagmi.
- [SHIPPED] e2e script exits 0 against in-process boa with zero `matchOrders` in `scripts/`.
