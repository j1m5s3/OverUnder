# OverUnder — JIT Plan archive (AMM-first Phase 1 slice)

Archived 2026-09-18 after execute.

Approved 2026-09-18. Scope: CPMM happy path for all market types. No uniform-LVR, paymaster, JWKS, or permissionless listing.

## Decisions

1. `createPrimaryMarket(..., seedUsdc)` seeds CPMM like wildcards; 0 means no pool.
2. e2e buys/sells via MarketAMM; CLOB stays in `test_exchange.py` only.
3. Web AmmSwap wallet writes; OrderTicket file retained, unrendered.
4. Backend factory-backed create with SQLite fallback; indexer in lifespan.
5. `quoteSell` view shares sell math with `sellToUSDC`.

## Acceptance (met)

- Factory seed tests passed (14 contract tests).
- `scripts/e2e_local.py` has zero `matchOrders`; exit 0.
- Backend pytest 2 passed; oracles 3 passed; `next build` green.
- AmmSwap is the default ticket; Exchange/orderbook not deleted.
- Browser click-through of wallet swap was not run (no browser tools in this session).
