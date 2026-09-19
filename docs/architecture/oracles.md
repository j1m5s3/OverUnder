---
title: Oracles
status: MIXED
area: oracles
summary: Three research agents, coordinator unanimity, 24h fallback, and wildcard proposal gates.
last_verified: 2026-09-19
pointers:
  - "[oracles/agents/base.py : L10-63]"
  - "[oracles/agents/alpha.py : L10-110]"
  - "[oracles/agents/beta.py : L1-115]"
  - "[oracles/agents/gamma.py : L1-115]"
  - "[oracles/consensus/coordinator.py : L36-62]"
  - "[oracles/consensus/fallback.py : L6-28]"
  - "[oracles/wildcard/gates.py : L19-58]"
  - "[oracles/wildcard/generator.py : L8-31]"
  - "[contracts/src/ConsensusOracle.vy : L144-161]"
  - "[contracts/src/ConsensusOracle.vy : L197-217]"
---

# Oracles

Resolution is off-chain research plus on-chain ConsensusOracle. Agents never hold the CTF oracle role individually; only the consensus contract calls `reportPayouts`.

## Agents

- [SHIPPED] Shared `Attestation` hashes `{outcome, urls, summary}` with SHA-256. [oracles/agents/base.py : L10-33]
- [SHIPPED] `MockSearch` returns canned results with mock.local URLs; enabled via `OU_ORACLE_MOCK=1` in pytest/CI/anvil only. [oracles/agents/base.py : L36-63]
- [SHIPPED] Alpha uses Claude+Tavily; hard-fails if keys missing without mock flag. Evidence URLs filtered from search hits only. [oracles/agents/alpha.py : L10-110]
- [SHIPPED] Beta uses GPT+Brave; hard-fails if keys missing without mock flag. Evidence URLs filtered from search hits only. [oracles/agents/beta.py : L1-115]
- [SHIPPED] Gamma uses Gemini+Exa; hard-fails if keys missing without mock flag. Evidence URLs filtered from search hits only. [oracles/agents/gamma.py : L1-115]
- [SHIPPED] `evidence_urls` are validated to be a subset of search hit URLs; no invented URLs accepted. [oracles/agents/alpha.py : L105-108]
- [PHASE2] Disagreement critique rounds. Keep search vendors swappable.

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
