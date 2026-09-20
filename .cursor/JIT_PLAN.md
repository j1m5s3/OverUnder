# OverUnder — JIT Plan (Phase 1 closeout)

Picking up grokbots: AMM-first Phase 1 closeout. No uniform-LVR math, paymaster, JWKS, or Exchange deletion.

## Product slice

- Operator creates a seeded primary CPMM; generator seeds a wildcard.
- Trader `buyWithUSDC` then partial `sellToUSDC` on the seeded primary.
- Settlement: 3/3 AI oracle unanimity, else 24h agent majority + position-weighted votes.
- USDC on Anvil (MockUSDC) / Base Sepolia. OU redeems from FeeVault at NAV.
- Web: AmmSwap is the only ticket. OrderTicket unrendered leftover.

## Architectural decisions

See ADR-0007. Seeded CPMM is the Phase 1 book. CLOB is leftover overlay, not a required Phase 2 destination. Uniform-LVR is OU-T008/T009.

## Execute order

1. Delete unused `backend/app/markets/onchain.py` and `web/src/shared/contracts.ts`.
2. Rewrite factory tests for required seed.
3. Rewrite `scripts/e2e_local.py` AMM buy+sell; zero `matchOrders`.
4. Land ADR-0007; supersede ADR-0001 Status; TODOs T008–T010 only.
5. Refresh AGENTS, architecture, roadmaps, this index.

## Acceptance

- Factory zero-seed reverts; seed-creates-pool passes.
- e2e seeds, buys, sells, resolves, redeems; `rg matchOrders scripts/` empty.
- ADR-0007 landed; T004–T007 still done; no T011/T012.
- Docs MIXED-tagged; no CLOB happy path / SQLite-only create / quote-only AMM / indexer-not-in-lifespan claims.
