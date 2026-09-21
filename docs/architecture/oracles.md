---
title: Oracles
status: MIXED
area: oracles
summary: Three Cursor-runtime research agents, coordinator unanimity, 24h fallback, wildcard gates, and score scout with auto-POST on 3/3.
last_verified: 2026-09-20
pointers:
  - "[oracles/agents/base.py : L11-72]"
  - "[oracles/agents/cursor_runtime.py : L26-77]"
  - "[oracles/agents/alpha.py : L9-27]"
  - "[oracles/agents/beta.py : L9-27]"
  - "[oracles/agents/gamma.py : L9-27]"
  - "[oracles/consensus/coordinator.py : L36-62]"
  - "[oracles/consensus/fallback.py : L6-28]"
  - "[oracles/wildcard/gates.py : L19-58]"
  - "[oracles/scores/scout.py : L68-77]"
  - "[oracles/scores/scout.py : L268-302]"
  - "[oracles/scores/publish.py : L15-52]"
  - "[oracles/scores/job.py : L116-150]"
  - "[oracles/wildcard/generator.py : L8-31]"
  - "[contracts/src/ConsensusOracle.vy : L144-161]"
  - "[contracts/src/ConsensusOracle.vy : L197-217]"
  - "[docs/adr/0008-cursor-runtime-oracles.md : L18-32]"
---

# Oracles

Resolution is off-chain research plus on-chain ConsensusOracle. Agents never hold the CTF oracle role individually; only the consensus contract calls `reportPayouts`. Book of record for the runtime: [ADR-0008](../adr/0008-cursor-runtime-oracles.md).

## Agents

- [SHIPPED] Shared `Attestation` hashes `{outcome, urls, summary}` with SHA-256. [oracles/agents/base.py : L19-31]
- [SHIPPED] `MockSearch` returns canned results with mock.local URLs; enabled via `OU_ORACLE_MOCK=1` in pytest/CI/anvil only. [oracles/agents/base.py : L38-72]
- [SHIPPED] Live path is Python `cursor-sdk` with lazy import. Local laptop uses `LocalAgentOptions(cwd=oracles/)` and `disallowed_tools=["shell"]`. Cloud Run Job uses `CloudAgentOptions(repos=[])`. MCP is always remote HTTP (`CURSOR_SEARCH_MCP_URL`). [oracles/agents/cursor_runtime.py : L26-77]
- [SHIPPED] Alpha/beta/gamma models default `composer-2.5` / `grok-4.6` / `gpt-5.1`. Injectable `search=` keeps mock heuristic infer for tests. Evidence URLs must be a subset of `search_hits`. [oracles/agents/alpha.py : L9-27]
- [SHIPPED] Live path hard-fails if `CURSOR_API_KEY` or `CURSOR_SEARCH_MCP_URL` is missing.
- [PHASE2] Disagreement critique rounds.

## Coordinator

- [SHIPPED] `Coordinator.run` researches with all three agents, sets `unanimous` if outcomes match, else `outcome: null`. [oracles/consensus/coordinator.py : L36-52]
- [SHIPPED] `sign_unanimous` EIP-712-signs the same digest with `AGENT_*_KEY`. [oracles/consensus/coordinator.py : L54-62]
- [STUB] `run` does not broadcast `submitConsensus`; tests and e2e wire that separately.
- [PHASE2] Daemon: poll closed markets, retry hung agents, persist evidence blobs, auto-submit when 3/3, else enter the 24h window.

## Fallback math

- [SHIPPED] Off-chain `majority` is 2 of 3 agent outcomes. [oracles/consensus/fallback.py : L6-15]
- [SHIPPED] `combine` returns `agent`, `agree`, or `arbitrate` when ≥2/3 voted weight opposes the agent majority. [oracles/consensus/fallback.py : L18-28]
- [SHIPPED] On-chain `resolveFallback` encodes the same rule and reverts `"arbitration required"`. [contracts/src/ConsensusOracle.vy : L197-217]
- [PHASE2] Participant UI for `castVote`, delegation, and public evidence explorer. Operator arbitration playbook with published rationale.

## Unanimous path

- [SHIPPED] Three distinct recovered agents, same outcome, then `_resolve` → `reportPayouts`. [contracts/src/ConsensusOracle.vy : L144-161]
- [PHASE2] Evidence storage (IPFS/S3) keyed by `evidenceHash`; agents must pin before signing.

## Wildcards

- [SHIPPED] Gates: schema (question ≤256, criteria present, p in (0,1)), resolvability (ban subjective words), child close ≤ parent, uniqueness via token overlap. [oracles/wildcard/gates.py : L19-58]
- [SHIPPED] Generator fills three sports templates (fumble, first score, total points) and keeps those that pass gates. [oracles/wildcard/generator.py : L8-31]
- [STUB] Templates are string formatters, not an LLM proposer.
- [PHASE2] LLM proposer per primary, operator review queue, automatic `createWildcardMarket` + seed from a protocol USDC inventory, and per-sport taxonomies (NFL, NBA, elections).

## Score scout

- [SHIPPED] `requested_facts(question)` maps vs-box / fumble / first-score / total. Missing numbers fail closed. Scheduled games never invent 0–0. [oracles/scores/scout.py : L68-77]
- [SHIPPED] `ScoreCoordinator` runs three independent extracts and auto-POSTs only on 3/3 via minted operator JWT. It does **not** resolve markets or call `submitConsensus`. [oracles/scores/scout.py : L268-302] [oracles/scores/publish.py : L15-52]
- [SHIPPED] Cloud Run Job `overunder-oracle` lists sports primaries, skips fresh rows unless `in_progress`, caps `OU_SCOUT_MAX_MARKETS` (default 5). [oracles/scores/job.py : L116-150]
- [SHIPPED] Mock path parses scores from snippets; live path uses `prompt_json`. Evidence URLs must be a subset of search hits.
- [PHASE2] Persist disagreement transcripts and retry policy beyond the 15-minute tick.
