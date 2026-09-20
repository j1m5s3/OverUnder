---
title: Docs map
status: SHIPPED
area: cross
summary: Index of AI-consumable OverUnder docs and the required reading order.
last_verified: 2026-09-20
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
6. ADRs 0001–0007 (0001 superseded; 0007 is book of record)
7. [roadmap/phase-1-mvp.md](roadmap/phase-1-mvp.md) then [roadmap/phase-2.md](roadmap/phase-2.md)
8. [TODOS.md](TODOS.md)
9. [runbooks/local-dev.md](runbooks/local-dev.md)

## Files

- [architecture/overview.md](architecture/overview.md) — layers and trust boundaries
- [architecture/contracts.md](architecture/contracts.md) — Vyper contract map
- [architecture/backend.md](architecture/backend.md) — FastAPI map
- [architecture/oracles.md](architecture/oracles.md) — agent consensus map
- [architecture/web.md](architecture/web.md) — Next.js feature map
- [architecture/data-flow.md](architecture/data-flow.md) — AMM happy path and leftover CLOB
- [intent/vision.md](intent/vision.md) — product intent including phase 2
- [intent/glossary.md](intent/glossary.md) — domain terms
- [adr/0001-hybrid-clob-amm.md](adr/0001-hybrid-clob-amm.md) — historical hybrid (superseded)
- [adr/0002-three-agent-oracle-consensus.md](adr/0002-three-agent-oracle-consensus.md) — oracle quorum
- [adr/0003-ou-nav-token-not-savings-vault.md](adr/0003-ou-nav-token-not-savings-vault.md) — OU NAV
- [adr/0004-trusted-mvp-keys.md](adr/0004-trusted-mvp-keys.md) — operator trust
- [adr/0005-injected-wallet-not-full-privy-aa.md](adr/0005-injected-wallet-not-full-privy-aa.md) — wallets
- [adr/0006-docs-system-conventions.md](adr/0006-docs-system-conventions.md) — this schema
- [adr/0007-amm-first-uniform-lvr.md](adr/0007-amm-first-uniform-lvr.md) — AMM-first book of record
- [explore/amm.md](explore/amm.md) — uniform-LVR literature (not spec)
- [explore/edge_opportunities.md](explore/edge_opportunities.md) — niche stance (not spec)
- [roadmap/phase-1-mvp.md](roadmap/phase-1-mvp.md) — shipped AMM checklist
- [roadmap/phase-2.md](roadmap/phase-2.md) — uniform-LVR, listing, paymaster, JWKS; leftover CLOB overlay
- [TODOS.md](TODOS.md) — machine-parseable gaps
- [runbooks/local-dev.md](runbooks/local-dev.md) — run/test/stop commands

## Status tags

- `[SHIPPED]` implemented and tested in MVP
- `[STUB]` code exists but is a placeholder
- `[PHASE2]` no implementation yet; spec only
