---
title: Agent entrypoint
status: SHIPPED
area: cross
summary: Read this first. Routes agents into docs, pointer rules, and the TODO registry.
last_verified: 2026-09-16
pointers: []
---

# OverUnder — agent entrypoint

Prediction markets on Base. Primaries trade on an off-chain CLOB settled in USDC. Wildcards are AI-generated child AMM markets. Three AI oracles must agree to resolve; otherwise 24h majority plus participant votes. Protocol fees accrue to a vault; OU redeems for USDC at NAV.

## Read next

1. [docs/README.md](docs/README.md) — map
2. [docs/architecture/overview.md](docs/architecture/overview.md) — layers
3. Area file for the subsystem you will edit
4. [docs/TODOS.md](docs/TODOS.md) — open gaps
5. [docs/adr/](docs/adr/) — why, not how

## Conventions

- Pointer format: `[path/from/repo/root : Lstart-Lend]`
- MIXED docs prefix bullets `[SHIPPED]` / `[STUB]` / `[PHASE2]`
- Do not dump source into docs. Quote at most 3 lines.
- When you change a cited function, update `pointers` and `last_verified` on the matching doc.
- Do not invent ERC-4337, Privy JWKS, Flutter app, or emissions as shipped; they are PHASE2 or STUB.

## Local commands

- [docs/runbooks/local-dev.md](docs/runbooks/local-dev.md)

## JIT

- [.cursor/JIT_INDEX.md](.cursor/JIT_INDEX.md)
- [.cursor/JIT_PLAN.md](.cursor/JIT_PLAN.md)
