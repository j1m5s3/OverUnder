---
title: Docs system conventions
status: SHIPPED
area: cross
summary: AI-first docs live under docs/ with required frontmatter, status tags, pointers, and a YAML TODO registry.
last_verified: 2026-09-16
pointers: []
---

## Status

Accepted 2026-09-16.

## Context

- Agents must load intent without dumping source.
- Shipped MVP and unimplemented vision must stay distinguishable.
- Line pointers go stale unless a rule exists to refresh them.

## Decision

- Layout: `docs/architecture/`, `docs/adr/`, `docs/intent/`, `docs/roadmap/`, `docs/runbooks/`, plus `docs/TODOS.md`. Root `AGENTS.md` is the agent entrypoint.
- Frontmatter required: `title`, `status` (`SHIPPED` | `STUB` | `PHASE2` | `MIXED`), `area`, `summary`, `last_verified`, `pointers`.
- MIXED docs prefix every content bullet with `[SHIPPED]`, `[STUB]`, or `[PHASE2]`.
- Pointer format: `[path/from/repo/root : Lstart-Lend]`. No fenced code over 3 lines.
- TODOs: one fenced YAML block in `docs/TODOS.md` with ids `OU-T###`.
- ADRs: `docs/adr/NNNN-slug.md` with Status, Context, Decision, Consequences (at least one negative).

## Consequences

- Agents can route via `AGENTS.md` without reading the whole tree.
- Pointers must be updated when cited line ranges move.
- Negative: extra docs maintenance on every structural code edit.
