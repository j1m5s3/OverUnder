# OverUnder — JIT Plan archive (Phase 1 AMM closeout)

Archived 2026-09-20 after execute.

Picked up grokbots. Scope: AMM-only e2e, land ADR-0007, refresh stale docs. No uniform-LVR math, paymaster, JWKS, or Exchange deletion.

## Decisions

1. Seeded CPMM is the Phase 1 book; primaries require seed.
2. e2e buys/sells via MarketAMM; CLOB stays in `test_exchange.py` (and paymaster negatives) only.
3. Deleted unused `onchain.py` and `web/src/shared/contracts.ts`.
4. ADR-0007 landed; ADR-0001 Status superseded. TODOs T008–T010 only. T004–T007 stay done.

## Verify

- Factory + AMM + Exchange: 8 passed.
- `scripts/e2e_local.py` exit 0; `rg matchOrders scripts/` empty.
- Oracles: 7 passed, 1 skipped.
- Backend suite has pre-existing SIWE/portfolio/MoonPay fixture failures; this closeout did not edit those routers. Create remains fail-closed without RPC.
