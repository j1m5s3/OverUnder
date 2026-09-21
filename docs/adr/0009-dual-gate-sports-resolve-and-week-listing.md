---
title: Dual-gate sports resolve and week-roll listing
status: SHIPPED
area: oracles
summary: Auto-submit sports primaries only when LiveScore final matches unanimous research; list next-week winner primaries after every week-N game is final.
last_verified: 2026-09-21
pointers:
  - "[oracles/resolve/run.py : L33-119]"
  - "[oracles/resolve/winner.py : L26-48]"
  - "[oracles/listing/run.py : L29-39]"
  - "[oracles/listing/run.py : L58-137]"
  - "[oracles/job.py : L14-50]"
  - "[oracles/consensus/coordinator.py : L27-43]"
  - "[contracts/src/ConsensusOracle.vy : L144-161]"
  - "[contracts/src/MarketFactory.vy : L81-89]"
---

## Status

Accepted 2026-09-21.

## Context

Score scouts can post a final box score without settling the market. `Coordinator.run` must stay research-only (ADR-0008). Factory listing stays operator-gated (OU-T010). Traders need week N+1 winner primaries only after week N is complete.

## Decision

- [SHIPPED] Dual-gate auto-resolve in `oracles/resolve/`: LiveScore `status == final`, closeTime passed, score-derived winner equals unanimous `Coordinator.run`, then `sign_unanimous` + `submitConsensus`. Fail closed on ties, missing scores, research mismatch, missing `AGENT_*_KEY`, or already resolved. [oracles/resolve/run.py : L33-119]
- [SHIPPED] User “done” maps to existing LiveScore `final`. No `done` status.
- [SHIPPED] Score scout still never submits consensus. `Coordinator.run` still never submits. [oracles/consensus/coordinator.py : L27-43]
- [SHIPPED] Schedule scout extracts current NFL week and next week; operator `POST /markets/schedule` persists it. Listing waits until every week-W game is `final`, then operator `POST /markets` for week W+1 winner primaries with `close_time = kickoff`. [oracles/listing/run.py : L58-137]
- [SHIPPED] Winner question `{home} vs {away}: {home} win?`. Factory remains permissioned. No `resolveFallback` daemon this slice.

## Consequences

- A listed sports primary with a 3/3 final score and matching research pays out without an operator click.
- Negative: a wrong 3/3 box score plus agreeing research can pay out immediately; postponed games stall N+1 listing; operator plus all three agent keys can still resolve any closed market.
