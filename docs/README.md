---
title: Docs map
status: SHIPPED
area: cross
summary: Index of AI-consumable OverUnder docs and the required reading order.
last_verified: 2026-09-16
pointers: []
---

# Docs map

Read [`AGENTS.md`](../AGENTS.md) first, then this file, then area docs.

## Reading order

1. [AGENTS.md](../AGENTS.md)
2. This map
3. [architecture/overview.md](architecture/overview.md)
4. Area architecture (contracts, backend, oracles, web, data-flow)
5. [intent/vision.md](intent/vision.md) and [intent/glossary.md](intent/glossary.md)
6. ADRs 0001–0006
7. [roadmap/phase-1-mvp.md](roadmap/phase-1-mvp.md) then [roadmap/phase-2.md](roadmap/phase-2.md)
8. [TODOS.md](TODOS.md)
9. [runbooks/local-dev.md](runbooks/local-dev.md)

## Files

- [architecture/overview.md](architecture/overview.md) — layers and trust boundaries
- [architecture/contracts.md](architecture/contracts.md) — Vyper contract map
- [architecture/backend.md](architecture/backend.md) — FastAPI map
- [architecture/oracles.md](architecture/oracles.md) — agent consensus map
- [architecture/web.md](architecture/web.md) — Next.js feature map
- [architecture/data-flow.md](architecture/data-flow.md) — CLOB and AMM traces
- [intent/vision.md](intent/vision.md) — product intent including phase 2
- [intent/glossary.md](intent/glossary.md) — domain terms
- [adr/0001-hybrid-clob-amm.md](adr/0001-hybrid-clob-amm.md) — CLOB vs AMM
- [adr/0002-three-agent-oracle-consensus.md](adr/0002-three-agent-oracle-consensus.md) — oracle quorum
- [adr/0003-ou-nav-token-not-savings-vault.md](adr/0003-ou-nav-token-not-savings-vault.md) — OU NAV
- [adr/0004-trusted-mvp-keys.md](adr/0004-trusted-mvp-keys.md) — operator trust
- [adr/0005-injected-wallet-not-full-privy-aa.md](adr/0005-injected-wallet-not-full-privy-aa.md) — wallets
- [adr/0006-docs-system-conventions.md](adr/0006-docs-system-conventions.md) — this schema
- [roadmap/phase-1-mvp.md](roadmap/phase-1-mvp.md) — shipped checklist
- [roadmap/phase-2.md](roadmap/phase-2.md) — Flutter, parity, paymaster, KYC, emissions
- [TODOS.md](TODOS.md) — machine-parseable gaps
- [runbooks/local-dev.md](runbooks/local-dev.md) — run/test/stop commands

## Status tags

- `[SHIPPED]` implemented and tested in MVP
- `[STUB]` code exists but is a placeholder
- `[PHASE2]` no implementation yet; spec only
