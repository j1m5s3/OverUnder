---
title: Dual-gate sports resolve and week-roll listing
status: SHIPPED
area: oracles
summary: Sports primaries auto-submit only when a LiveScore final matches unanimous research. Next-week winner primaries are listed once every week-N game is final, postponed, cancelled or stale past a grace period. Wildcards and non-sports markets go to a separate general resolver.
last_verified: 2026-09-23
pointers:
  - "[oracles/resolve/run.py : L118-298]"
  - "[oracles/resolve/run.py : L43-82]"
  - "[oracles/resolve/winner.py : L26-48]"
  - "[oracles/resolve/chain.py : L98-117]"
  - "[oracles/resolve/cooldown.py : L1-82]"
  - "[oracles/resolve/general.py : L91-149]"
  - "[oracles/listing/run.py : L24-97]"
  - "[oracles/listing/run.py : L139-294]"
  - "[oracles/schedule/scout.py : L147-169]"
  - "[oracles/scores/job.py : L120-172]"
  - "[oracles/job.py : L1-19]"
  - "[oracles/job.py : L145-153]"
  - "[oracles/consensus/coordinator.py : L31-55]"
  - "[contracts/src/ConsensusOracle.vy : L144-161]"
  - "[contracts/src/MarketFactory.vy : L143-150]"
---

## Status

Accepted 2026-09-21.

Amended 2026-09-23 (live findings: a week-2 game stuck at `scheduled` blocked week-3 listing; three listed markets came from an older oracle; one registered final market sat unresolved for about 36 h).

Week gate and listing
- Final, postponed and cancelled games count as done. So does a stale row (`scheduled` or `in_progress`) once kickoff + `OU_LISTING_STALE_GRACE_SECONDS` (default 8 h) has passed, because the schedule scout only refreshes the current and next week. [oracles/listing/run.py : L24-29] [oracles/listing/run.py : L70-97]
- Games that kick off within 600 s are skipped. [oracles/listing/run.py : L229-232]
- Each game's `POST /markets` is isolated: a failure is logged and the others continue. A 409 (condition prepared outside the factory) is reported under `squatted`, not as a stage error. [oracles/listing/run.py : L244-262]
- The schedule is published per game only when all three reports agree on it (3/3). [oracles/schedule/scout.py : L147-169]

Resolve
- Markets whose on-chain `closeTime` is 0 (created on another oracle) are skipped as `not registered`. [oracles/resolve/run.py : L172-177]
- A config guard checks the chain id, oracle code, and agent and operator addresses against the env before any send. [oracles/resolve/chain.py : L98-117]
- Timing uses max(wall clock, chain timestamp). [oracles/resolve/run.py : L146-154]
- Markets are processed oldest closeTime first. Markets already resolved on chain are mirrored into the DB from the CTF payout. [oracles/resolve/run.py : L43-82]
- A research attempt that does not resolve is recorded, and the market is not researched again for `OU_RESEARCH_RETRY_SECONDS` (default 6 h). [oracles/resolve/cooldown.py : L1-82]
- The score scout serves recent kickoffs first, then a rotating backlog. [oracles/scores/job.py : L120-172]
- Wildcards, user markets and non-sports primaries go to `oracles/resolve/general.py`. Wildcard children wait for the parent's score to be final or cancelled, or for the parent to resolve on chain. [oracles/resolve/general.py : L91-149]
- The 24 h fallback runs in the job ([ADR-0002](0002-three-agent-oracle-consensus.md)).
- Stage order is scores → resolve → resolve_general → schedule → listing, and the tick exits 1 if any stage reports `ok: false`. [oracles/job.py : L1-19] [oracles/job.py : L145-153]

## Context

Score scouts can post a final box score without settling the market. `Coordinator.run` must stay research-only (ADR-0008). At acceptance, factory listing was operator-gated; user listing later shipped separately ([ADR-0012](0012-loosely-gated-user-listing.md)). Traders need week N+1 winner primaries only after week N is complete.

## Decision

- [SHIPPED] Dual-gate auto-resolve in `oracles/resolve/` requires four things: LiveScore `status == final`, closeTime passed, a score-derived winner equal to the unanimous `Coordinator.run` result, and a passing config guard. It then calls `sign_unanimous` + `submitConsensus`. It fails closed on ties, missing scores, research mismatch, missing `AGENT_*_KEY`, not-registered markets, config mismatch, or markets already resolved. [oracles/resolve/run.py : L118-298] [oracles/resolve/chain.py : L98-117]
- [SHIPPED] User “done” maps to existing LiveScore `final`. No `done` status.
- [SHIPPED] Score scout still never submits consensus. `Coordinator.run` still never submits. [oracles/consensus/coordinator.py : L31-55]
- [SHIPPED] The schedule scout extracts the current and next NFL week, and the operator `POST /markets/schedule` persists it. Listing waits until every week-W game is final, postponed, cancelled or stale past the grace period, then calls the operator `POST /markets` for week W+1 winner primaries with `close_time = kickoff`. [oracles/listing/run.py : L139-294]
- [SHIPPED] Winner question `{home} vs {away}: {home} win?`. Operator primaries still go through the operator (`createPrimaryMarket`). The 24 h fallback runs under `OU_FALLBACK_POLICY` (ADR-0002). [contracts/src/MarketFactory.vy : L143-150]

## Consequences

- A listed sports primary with a 3/3 final score and matching research pays out without an operator click.
- Negative: a wrong 3/3 box score plus agreeing research can pay out immediately. Operator plus all three agent keys can still resolve any closed market.
- Negative: cancelled games have no payout path. ConsensusOracle pays only [1,0] or [0,1], so they wait for operator handling (OU-T014). The stale grace can list week N+1 before a late game resolves.
- Negative: every research-dependent stage stops while Cursor is unavailable or out of quota (ADR-0008).
