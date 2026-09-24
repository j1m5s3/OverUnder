---
title: Oracles
status: MIXED
area: oracles
summary: One Cloud Run Job tick (lease, auth preflight, scores, resolve, resolve_general, schedule, listing) with MCP-only Cursor agents, dual-gate sports resolve, a general resolver, the ADR-0002 24h fallback (OU_FALLBACK_POLICY=attest), research cooldown and week-roll winner listing.
last_verified: 2026-09-23
pointers:
  - "[oracles/job.py : L60-163]"
  - "[oracles/operator_auth.py : L89-185]"
  - "[oracles/tick_lease.py : L1-16]"
  - "[oracles/budget.py : L1-8]"
  - "[oracles/redact.py : L18-31]"
  - "[oracles/agents/base.py : L11-72]"
  - "[oracles/agents/cursor_runtime.py : L48-86]"
  - "[oracles/agents/cursor_runtime.py : L122-236]"
  - "[oracles/agents/cursor_runtime.py : L250-332]"
  - "[oracles/agents/alpha.py : L9-27]"
  - "[oracles/consensus/coordinator.py : L31-84]"
  - "[oracles/consensus/fallback.py : L6-28]"
  - "[oracles/resolve/chain.py : L75-151]"
  - "[oracles/resolve/run.py : L43-63]"
  - "[oracles/resolve/run.py : L118-298]"
  - "[oracles/resolve/fallback.py : L16-151]"
  - "[oracles/resolve/general.py : L1-19]"
  - "[oracles/resolve/general.py : L211-409]"
  - "[oracles/resolve/cooldown.py : L1-8]"
  - "[oracles/resolve/publish.py : L47-82]"
  - "[oracles/schedule/scout.py : L123-202]"
  - "[oracles/listing/run.py : L74-86]"
  - "[oracles/listing/run.py : L139-294]"
  - "[oracles/listing/questions.py : L21-26]"
  - "[oracles/scores/job.py : L120-172]"
  - "[oracles/scores/scout.py : L308-347]"
  - "[oracles/wildcard/gates.py : L19-58]"
  - "[oracles/wildcard/generator.py : L8-31]"
  - "[contracts/src/ConsensusOracle.vy : L144-161]"
  - "[contracts/src/ConsensusOracle.vy : L197-226]"
---

# Oracles

Resolution is off-chain research plus on-chain ConsensusOracle. Agents never hold the CTF oracle role individually; only the consensus contract calls `reportPayouts`. Runtime book of record: [ADR-0008](../adr/0008-cursor-runtime-oracles.md). Dual-gate sports resolve and week-roll listing: [ADR-0009](../adr/0009-dual-gate-sports-resolve-and-week-listing.md). Fallback quorum: [ADR-0002](../adr/0002-three-agent-oracle-consensus.md).

Operational notes (2026-09-23): the Cursor account is out of quota until 2026-10-01 unless a spend limit is set, so every research-dependent path (dual gate, fallback, general resolver, scouts) fails until then. Three live markets from an older deployment are not registered on the configured oracle; archive them with operator `POST /api/v1/markets/{cid}/archive`. `0x24c901…` is final and overdue, waiting on research. Procedures: [runbooks/operations.md](../runbooks/operations.md).

## Tick

- [SHIPPED] `python -m job` runs one tick: single-flight lease, operator auth preflight, then scores → resolve → resolve_general → schedule → listing. Stage modules import lazily, so one broken import fails only its stage. [oracles/job.py : L60-142]
- [SHIPPED] Lease: on Cloud Run the job lists its own executions and exits 0 with `{"skipped": "running"}` while an older one runs; `OU_TICK_SINGLE_FLIGHT=0` disables it and API errors fail open. The job SA needs `run.executions.list/get`. [oracles/tick_lease.py : L1-16]
- [SHIPPED] Budget: `OU_TICK_BUDGET_SECONDS` (default 780) bounds the tick; stages and markets past it are recorded `skipped: budget` (ok). The workflow sets the task timeout to budget + 60 s. [oracles/budget.py : L1-8]
- [SHIPPED] Auth preflight probes `POST /api/v1/markets/schedule` with `[]` and a minted operator JWT. On 401 "unknown user" or 403 "operator only" it runs one SIWE bootstrap with `OPERATOR_PRIVATE_KEY` and probes again. Failure skips scores, schedule and listing (they need the JWT); resolve stages still run. `cause` names the likely misconfiguration without key text. [oracles/operator_auth.py : L89-185]
- [SHIPPED] Exit code 1 when the preflight fails or any stage returns `ok: false`; stdout is one sorted, redacted JSON summary with `exitCode`. [oracles/job.py : L145-163]
- [SHIPPED] Redaction: every stage stderr line goes through `log_error`, masking key env values (with or without 0x), RPC/MCP URL paths and queries (scheme://host kept), bearer tokens and JWTs. [oracles/redact.py : L18-31]

## Agents

- [SHIPPED] Shared `Attestation` hashes `{outcome, urls, summary}` with SHA-256. `MockSearch` is enabled via `OU_ORACLE_MOCK=1` in pytest/CI/anvil only. [oracles/agents/base.py : L11-72]
- [SHIPPED] Live path is `cursor-sdk==1.0.32`. Local mode (the Cloud Run default, `OU_CURSOR_RUNTIME=local`) runs agents with `tools=["mcp"]`, so injected web content cannot reach shell, file or env tools. Cloud mode uses a real `CloudEnvironment` (the old `CloudAgentOptions(repos=[])` silently ran a local agent with every tool). MCP is remote HTTP (`CURSOR_SEARCH_MCP_URL`). [oracles/agents/cursor_runtime.py : L48-86]
- [SHIPPED] Run errors the SDK only streams are surfaced (with model and mode, redacted); a watchdog closes an agent once the tick budget runs out. [oracles/agents/cursor_runtime.py : L250-332]
- [SHIPPED] Prompts fence the question and context as untrusted (`sanitize_untrusted` strips fence tokens and control characters), render `as_of` as trusted guidance, and allow outcome 2 = not concluded. `parse_verdict` accepts only outcome 0, 1, 2 and confidence in [0, 1]. [oracles/agents/cursor_runtime.py : L122-236]
- [SHIPPED] Alpha/beta/gamma default to `composer-2.5` / `grok-4.6` / `gpt-5.1`; `research(question, context, as_of)`. Evidence URLs must be a subset of `search_hits`. [oracles/agents/alpha.py : L9-27]
- [SHIPPED] Live path hard-fails if `CURSOR_API_KEY` or `CURSOR_SEARCH_MCP_URL` is missing.
- [PHASE2] Disagreement critique rounds.

## Coordinator

- [SHIPPED] `Coordinator.run(question, context, as_of)` researches with all three agents, sets `unanimous` if outcomes match, and reports each agent's confidence. It never submits. `sign_unanimous` takes the chain-aware `now` for its deadline; `sign_one` signs a single agent attestation. [oracles/consensus/coordinator.py : L31-84]

## Chain guard

- [SHIPPED] `config_check` runs once per resolve stage: `CHAIN_ID` equals the RPC chain, code exists at `ORACLE_ADDRESS`, the three `AGENT_*_KEY` addresses are distinct registered agents and `OPERATOR_PRIVATE_KEY` is `oracle.operator()`. A mismatch sends nothing and fails the stage. [oracles/resolve/chain.py : L75-117]
- [SHIPPED] The effective clock is max(wall clock, latest block timestamp), so a chain ahead of the wall clock never leaves a market "not closed" or signs an expired deadline. [oracles/resolve/chain.py : L149-151]

## Dual-gate sports resolve

- [SHIPPED] Owns sports primaries whose question names a YES team ("X win?"), oldest closeTime first; other sports questions go to the general resolver. [oracles/resolve/run.py : L43-63]
- [SHIPPED] Per market: mirror a chain-resolved market from CTF payouts; skip `not registered` (on-chain closeTime 0, market from another deployment); wait for close and LiveScore `final`; derive the winner; then config guard, research cooldown, budget and `OU_RESOLVE_MAX_MARKETS` cap (default 3). [oracles/resolve/run.py : L118-298]
- [SHIPPED] `submitConsensus` only when unanimous research matches the score-derived winner. A concurrent resolution is mirrored as `resolved concurrently`, not a tx error. The job persists attestations and calls operator `POST /oracle/resolved` after the receipt.
- [SHIPPED] Stage `ok` is false only on a config mismatch or a tx error; research errors and `not registered` do not fail the tick.

## Fallback (ADR-0002, 24 h)

- [SHIPPED] `OU_FALLBACK_POLICY`: `attest` (default), `arbitrate` or `manual`. [oracles/resolve/fallback.py : L16-39]
- [SHIPPED] Past closeTime + 86400 without matching unanimous research, `attest` has each agent whose research matches the derived outcome `submitAttestation` (the operator relays), then sends permissionless `resolveFallback` after its preflight passes. It never attests another outcome and stops when agents already on chain hold a conflicting majority. `arbitrate` also calls operator `resolveArbitrated` when there is no agent majority or votes force arbitration. [oracles/resolve/fallback.py : L47-151]
- [SHIPPED] Only agents whose on-chain slot matches are persisted as attestations on the fallback path.
- [SHIPPED] Off-chain `majority` is 2 of 3; `combine` returns `arbitrate` when ≥2/3 voted weight opposes. The contract encodes the same rule. [oracles/consensus/fallback.py : L6-28] [contracts/src/ConsensusOracle.vy : L197-226]
- [PHASE2] Participant vote UI, evidence explorer, published arbitration playbook.

## General resolver

- [SHIPPED] Research-only resolve for wildcard children, type-2 user markets and type-0 primaries the dual gate does not own. [oracles/resolve/general.py : L1-19]
- [SHIPPED] Timing: event-gated markets (wildcards, sports props) wait for the parent's or own score to be final or cancelled (or the parent resolved on chain), then `OU_GENERAL_RESOLVE_GATED_DELAY_SECONDS` (3600); others wait `resolveAfter` or closeTime + `OU_GENERAL_RESOLVE_DELAY_SECONDS` (86400). [oracles/resolve/general.py : L211-409]
- [SHIPPED] `submitConsensus` needs 3/3 with every confidence ≥ `OU_GENERAL_RESOLVE_MIN_CONFIDENCE` (0.8); any outcome 2 is `undetermined`. Past 24 h under attest or arbitrate a confident 2/3 majority attests and `resolveFallback` runs; `resolveArbitrated` is never called here. Cap `OU_GENERAL_RESOLVE_MAX_MARKETS` (2).
- [STUB] Undetermined or cancelled markets never settle: there is no invalid outcome on chain (OU-T014).

## Research cooldown

- [SHIPPED] Research that does not resolve is persisted through `/oracle/attest` as a `research` record; both resolvers skip a market while its newest record is younger than `OU_RESEARCH_RETRY_SECONDS` (21600, 0 disables). Skips do not count against the cap; a failed status read fails open. [oracles/resolve/cooldown.py : L1-8] [oracles/resolve/publish.py : L47-82]

## Unanimous path

- [SHIPPED] Three distinct recovered agents, same outcome, then `_resolve` → `reportPayouts`. [contracts/src/ConsensusOracle.vy : L144-161]
- [PHASE2] Evidence storage (IPFS/S3) keyed by `evidenceHash`.

## Wildcards

- [SHIPPED] Gates: schema (question ≤256, criteria present, p in (0,1)), resolvability (ban subjective words), child close ≤ parent, uniqueness via token overlap. [oracles/wildcard/gates.py : L19-58]
- [SHIPPED] Generator fills three sports templates (fumble, first score, total points) and keeps those that pass gates. [oracles/wildcard/generator.py : L8-31]
- [STUB] Templates are string formatters, not an LLM proposer.
- [PHASE2] LLM proposer per primary, operator review queue, automatic `createWildcardMarket` + seed from a protocol inventory.

## Score scout

- [SHIPPED] `ScoreCoordinator` runs three extracts and auto-POSTs only on 3/3 via the operator JWT; it never resolves. Postponed and cancelled are scoreless statuses; scheduled games never invent 0–0. [oracles/scores/scout.py : L308-347]
- [SHIPPED] Targets: skip resolved and not-yet-kicked-off markets and final or cancelled scores; fill `OU_SCOUT_MAX_MARKETS` (5) with kickoffs within `OU_SCOUT_RECENT_SECONDS` (36 h) first, then a backlog rotated each tick. `OU_SCOUT_MAX_AGE_SECONDS` is an opt-in cutoff. [oracles/scores/job.py : L120-172]
- [PHASE2] Persist disagreement transcripts.

## Schedule scout and week-roll listing

- [SHIPPED] Three agents extract the current and next NFL week; only rows all three agree on (same key, status and kickoff) are published, per game, and an ambiguous matchup is disputed. `OU_MOCK_SCHEDULE` supplies rows in mock mode. [oracles/schedule/scout.py : L123-202]
- [SHIPPED] Week gate: week W is done when every game is final, postponed or cancelled, or stuck past `OU_LISTING_STALE_GRACE_SECONDS` (8 h); a listed game's live status (via `listedConditionId`) wins over the schedule row. [oracles/listing/run.py : L74-86]
- [SHIPPED] Listing: operator `POST /markets` for each week W+1 game (`close_time = kickoff`), skipping listed, inactive and kickoff ≤ now + 600 s games; each POST is isolated, and a 409 is recorded as `squatted` without failing the stage. [oracles/listing/run.py : L139-294]
- [SHIPPED] Question ids are HMAC-SHA256 under the optional `OU_QUESTION_ID_KEY` secret (legacy public sha256 without it), so the public schedule cannot be pre-squatted. [oracles/listing/questions.py : L21-26]
- [SHIPPED] Operator creates stay permissioned; user listing is the factory's type-2 path (see [backend.md](backend.md)).
