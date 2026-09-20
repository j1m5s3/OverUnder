# OverUnder — JIT Plan (AMM-first docs pivot)

Archived 2026-09-18 after docs-only execute.

Docs-only. No contract, backend, web, or test edits. Product direction: ADR-0007.

## Decisions (locked)

1. Uniform-LVR family (pm-AMM / Moallemi–Robinson–Zhu 2026) is the protocol target. Static uniform invariant is the default pool. Time-based liquidity and dynamic spreads are LP/fee policy.
2. Niche is long-tail + AI wildcards. Perpetuals, B2B embed, and CLOB vampire rebates are unscheduled.
3. Shipped CPMM and Exchange CLOB stay `[SHIPPED]` until a later implementation cycle. CLOB is leftover overlay.

## Pointers map (docs)

- [docs/adr/0007-amm-first-uniform-lvr.md] — new accepted decision
- [docs/adr/0001-hybrid-clob-amm.md : L14-16] — Status superseded only
- [docs/intent/vision.md] [docs/intent/glossary.md]
- [docs/architecture/overview.md] [docs/architecture/contracts.md] [docs/architecture/backend.md] [docs/architecture/web.md] [docs/architecture/data-flow.md] [docs/architecture/oracles.md]
- [docs/roadmap/phase-1-mvp.md] [docs/roadmap/phase-2.md] [docs/TODOS.md]
- [AGENTS.md] [README.md] [mobile/README.md]

## Micro-steps

1. ADR-0007 + supersede 0001 Status; nits 0004/0005.
2. Vision, glossary, explore stance boxes.
3. Architecture + docs map.
4. Phase 1 note, phase 2 rewrite, mobile, TODOs T001/T003/T004 + T008–T012.
5. Entrypoints, JIT index, archive this plan.

## Acceptance

- No product-thesis “hybrid CLOB / CLOB primaries / copy Polymarket” outside historical ADR-0001 Decision.
- ADR-0007 exists; 0001 Decision unchanged.
- MIXED bullets tagged; YAML TODOs valid.
- Vision non-goals: perps, embed, CLOB-as-default.
- Phase 2: no maker-rebate program; no required CLOB depth-chart milestone.
