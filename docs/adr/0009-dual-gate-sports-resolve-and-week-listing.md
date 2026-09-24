---
title: Dual-gate sports resolve and week-roll listing
status: SHIPPED
area: oracles
summary: Sports primaries auto-submit only when a LiveScore final matches unanimous research. Next-week winner primaries are listed once every week-N game is final, postponed, cancelled or stale past a grace period. Wildcards and non-sports markets go to a separate general resolver.
last_verified: 2026-09-23
pointers:
  - "[oracles/resolve/run.py : L147-348]"
  - "[oracles/resolve/run.py : L55-110]"
  - "[oracles/resolve/markets.py : L39-63]"
  - "[oracles/resolve/winner.py : L26-48]"
  - "[oracles/resolve/chain.py : L102-121]"
  - "[oracles/resolve/cooldown.py : L1-138]"
  - "[oracles/resolve/general.py : L99-157]"
  - "[oracles/listing/run.py : L35-107]"
  - "[oracles/listing/run.py : L172-379]"
  - "[oracles/listing/run.py : L242-277]"
  - "[oracles/schedule/scout.py : L147-169]"
  - "[oracles/scores/job.py : L120-250]"
  - "[oracles/scores/scout.py : L230-240]"
  - "[oracles/agents/cursor_runtime.py : L173-199]"
  - "[oracles/job.py : L1-68]"
  - "[oracles/job.py : L242-250]"
  - "[oracles/consensus/coordinator.py : L31-57]"
  - "[contracts/src/ConsensusOracle.vy : L144-161]"
  - "[contracts/src/MarketFactory.vy : L143-150]"
---

## Status

Accepted 2026-09-21.

Amended 2026-09-23 (live findings: a week-2 game stuck at `scheduled` blocked week-3 listing; three listed markets came from an older oracle; one registered final market sat unresolved for about 36 h).

Amended again 2026-09-23 (PR #30 review: stage budget shares, kickoff-pinned research and scouting, research-error cooldown, orphan relisting, paused markets resolve).

Week gate and listing
- Final, postponed and cancelled games count as done. So does a stale row (`scheduled` or `in_progress`) once kickoff + `OU_LISTING_STALE_GRACE_SECONDS` (default 8 h) has passed, because the schedule scout only refreshes the current and next week. [oracles/listing/run.py : L35-40] [oracles/listing/run.py : L81-107]
- Games that kick off within 600 s are skipped. [oracles/listing/run.py : L291-294]
- Each game's `POST /markets` is isolated: a failure is logged and the others continue. A 409 (condition prepared outside the factory) is reported under `squatted`, not as a stage error. [oracles/listing/run.py : L308-320]
- A schedule link to a market whose on-chain `closeTime` is 0 on the configured oracle (an orphan, often archived) counts as unlisted. The game is relisted; when it cannot be relisted this tick, the link is cleared with a schedule upsert carrying `listedConditionId: ""`. A registered but paused market keeps its link. Without chain config, or when the read fails, links are trusted. [oracles/listing/run.py : L242-277] [oracles/listing/run.py : L349-362]
- The schedule is published per game only when all three reports agree on it (3/3). [oracles/schedule/scout.py : L147-169]

Resolve
- Markets whose on-chain `closeTime` is 0 (created on another oracle) are skipped as `not registered`. [oracles/resolve/run.py : L208-212]
- Both resolvers read the operator market list (`includePaused=1`, operator JWT), so a paused registered market still resolves. On a 4xx they fall back to the public list. [oracles/resolve/markets.py : L39-63]
- A config guard checks the chain id, oracle code, and agent and operator addresses against the env before any send. [oracles/resolve/chain.py : L102-121]
- Timing uses max(wall clock, chain timestamp). [oracles/resolve/run.py : L180-188]
- Markets are processed oldest closeTime first. Markets already resolved on chain are mirrored into the DB from the CTF payout. [oracles/resolve/run.py : L55-110]
- Research gets the kickoff (closeTime), and the score scout prompt names it too. Both require a `game_date`. A research verdict for another date becomes outcome 2, and a played or cancelled score report for another date is rejected. So both gates cannot agree on an earlier meeting of the same teams. [oracles/resolve/run.py : L244-255] [oracles/agents/cursor_runtime.py : L173-199] [oracles/scores/scout.py : L230-240]
- A research attempt that runs and does not resolve is recorded, and the market is not researched again for `OU_RESEARCH_RETRY_SECONDS` (default 6 h). Research that raises records an `oracle-job` failure marker with a shorter `OU_RESEARCH_ERROR_RETRY_SECONDS` (default 1 h) and does not use a cap slot. A failed send is not recorded, so the next tick retries it. [oracles/resolve/cooldown.py : L1-138]
- The score scout serves recent kickoffs first, then a rotating backlog. [oracles/scores/job.py : L120-250]
- Wildcards, user markets and non-sports primaries go to `oracles/resolve/general.py`. Wildcard children wait for the parent's score to be final or cancelled, or for the parent to resolve on chain. [oracles/resolve/general.py : L99-157]
- The 24 h fallback runs in the job ([ADR-0002](0002-three-agent-oracle-consensus.md)).
- Stage order is scores → resolve → resolve_general → schedule → listing, and the tick exits 1 if any stage reports `ok: false`. Scores is capped at 40% of the tick budget (`OU_STAGE_SHARE_SCORES`), and earlier stages leave `OU_LISTING_RESERVE_SECONDS` for listing. A budget skip of resolve or listing reports `ok: false`. [oracles/job.py : L1-68] [oracles/job.py : L242-250]

## Context

Score scouts can post a final box score without settling the market. `Coordinator.run` must stay research-only (ADR-0008). At acceptance, factory listing was operator-gated; user listing later shipped separately ([ADR-0012](0012-loosely-gated-user-listing.md)). Traders need week N+1 winner primaries only after week N is complete.

## Decision

- [SHIPPED] Dual-gate auto-resolve in `oracles/resolve/` requires four things: LiveScore `status == final`, closeTime passed, a score-derived winner equal to the unanimous `Coordinator.run` result, and a passing config guard. It then calls `sign_unanimous` + `submitConsensus`. It fails closed on ties, missing scores, research mismatch, missing `AGENT_*_KEY`, not-registered markets, config mismatch, or markets already resolved. [oracles/resolve/run.py : L147-348] [oracles/resolve/chain.py : L102-121]
- [SHIPPED] User “done” maps to existing LiveScore `final`. No `done` status.
- [SHIPPED] Score scout still never submits consensus. `Coordinator.run` still never submits. [oracles/consensus/coordinator.py : L31-57]
- [SHIPPED] The schedule scout extracts the current and next NFL week, and the operator `POST /markets/schedule` persists it. Listing waits until every week-W game is final, postponed, cancelled or stale past the grace period, then calls the operator `POST /markets` for week W+1 winner primaries with `close_time = kickoff`. [oracles/listing/run.py : L172-379]
- [SHIPPED] Winner question `{home} vs {away}: {home} win?`. Operator primaries still go through the operator (`createPrimaryMarket`). The 24 h fallback runs under `OU_FALLBACK_POLICY` (ADR-0002). [contracts/src/MarketFactory.vy : L143-150]

## Consequences

- A listed sports primary with a 3/3 final score and matching research pays out without an operator click.
- Negative: a wrong 3/3 box score plus agreeing research can pay out immediately. Operator plus all three agent keys can still resolve any closed market.
- Negative: cancelled games have no payout path. ConsensusOracle pays only [1,0] or [0,1], so they wait for operator handling (OU-T014). The stale grace can list week N+1 before a late game resolves.
- Negative: every research-dependent stage stops while Cursor is unavailable or out of quota (ADR-0008).
