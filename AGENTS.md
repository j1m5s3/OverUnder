---
title: Agent entrypoint
status: SHIPPED
area: cross
summary: Read this first. Routes agents into docs, pointer rules, the TODO registry, and the local and production runbooks.
last_verified: 2026-09-24
pointers: []
---

# OverUnder — agent entrypoint

Prediction markets on Base, settled in USDC.

- Operator primaries, wildcard children and user-listed markets trade on `MarketAMM`, a seeded static pm-AMM, and trading halts at `closeTime`. Base Sepolia runs MarketAMM v2 and MarketFactory v2 since 2026-09-24; the live addresses are in [docs/runbooks/operations.md](docs/runbooks/operations.md).
- Resolution: three AI oracles must agree. Otherwise, after 24 h, agent majority plus participant votes decide.
- Protocol fees accrue to a vault, and OU redeems for USDC at NAV.

## Read next

1. [docs/README.md](docs/README.md) — map
2. [docs/architecture/overview.md](docs/architecture/overview.md) — layers
3. Area file for the subsystem you will edit
4. [docs/TODOS.md](docs/TODOS.md) — open gaps
5. [docs/adr/](docs/adr/) — why, not how:
   - book of record: [docs/adr/0007-amm-first-uniform-lvr.md](docs/adr/0007-amm-first-uniform-lvr.md)
   - oracles: [docs/adr/0008-cursor-runtime-oracles.md](docs/adr/0008-cursor-runtime-oracles.md)
   - sports resolve: [docs/adr/0009-dual-gate-sports-resolve-and-week-listing.md](docs/adr/0009-dual-gate-sports-resolve-and-week-listing.md)
   - wallets: [docs/adr/0010-cdp-embedded-wallets.md](docs/adr/0010-cdp-embedded-wallets.md)
   - pm-AMM and close gate: [docs/adr/0011-pm-amm-v2-close-gate.md](docs/adr/0011-pm-amm-v2-close-gate.md)
   - user listing: [docs/adr/0012-loosely-gated-user-listing.md](docs/adr/0012-loosely-gated-user-listing.md)

## Conventions

- Pointer format: `[path/from/repo/root : Lstart-Lend]`
- MIXED docs prefix bullets `[SHIPPED]` / `[STUB]` / `[PHASE2]`
- Do not dump source into docs. Quote at most 3 lines.
- When you change a cited function, update `pointers` and `last_verified` on the matching doc.
- OU-T001–T013 are done. OU-T014–T016 are open (docs/TODOS.md); do not invent them as shipped. MarketAMM is a static pm-AMM with an on-chain close gate (ADR-0011); listing is loosely gated (ADR-0012). User-facing wallets are CDP (ADR-0010); OverUnderPaymaster is leftover.

## Local commands and operations

- [docs/runbooks/local-dev.md](docs/runbooks/local-dev.md) — run, test, stop the local stack
- [docs/runbooks/operations.md](docs/runbooks/operations.md) — production deploy order, audit, overdue markets, relayer, CDP Portal
- [infra/gcp/README.md](infra/gcp/README.md) — GCP secrets, IAM, workflows, error lines

## JIT

- [.cursor/JIT_INDEX.md](.cursor/JIT_INDEX.md)
- [.cursor/JIT_PLAN.md](.cursor/JIT_PLAN.md)
