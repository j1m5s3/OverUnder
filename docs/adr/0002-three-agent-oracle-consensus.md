---
title: Three-agent oracle consensus
status: SHIPPED
area: oracles
summary: Resolve on 3/3 agent signatures; after 24h use 2/3 agents plus token votes, else operator. The oracle job drives the fallback under OU_FALLBACK_POLICY (attest by default).
last_verified: 2026-09-24
pointers:
  - "[contracts/src/ConsensusOracle.vy : L37-38]"
  - "[contracts/src/ConsensusOracle.vy : L136-161]"
  - "[contracts/src/ConsensusOracle.vy : L197-226]"
  - "[oracles/consensus/coordinator.py : L31-57]"
  - "[oracles/resolve/fallback.py : L1-156]"
  - "[oracles/resolve/run.py : L290-335]"
  - "[oracles/resolve/chain.py : L246-276]"
  - "[backend/app/oracle/router.py : L78-97]"
---

## Status

Accepted 2026-09-16.

Amended 2026-09-23. The 24 h fallback now runs in the `overunder-oracle` job, selected by `OU_FALLBACK_POLICY`:
- `attest` (default; deploy-gcp passes `attest` unless the repo var says otherwise):
  - Trigger: oracle closeTime + 24 h has passed without unanimous matching research.
  - Each agent whose research matches the outcome basis signs an EIP-712 attestation. The basis is the 3/3 final score for sports primaries and the confident research majority for general markets.
  - The operator EOA relays each signature with `submitAttestation`, so the agents need no gas.
  - The job then sends the permissionless `resolveFallback` when its preflight passes.
- `arbitrate`: also sends the operator's `resolveArbitrated` when there is no on-chain agent majority or when votes force arbitration. Only the sports resolver does this; the general resolver never arbitrates ([ADR-0012](0012-loosely-gated-user-listing.md)).
- `manual`: never sends.

[oracles/resolve/fallback.py : L1-156] [oracles/resolve/run.py : L290-335] [oracles/resolve/chain.py : L246-276]

The off-chain `POST /api/v1/oracle/attest` is operator-only. The status API tags research-only rows `kind: research` and leaves them out of `unanimous`. [backend/app/oracle/router.py : L78-97] [backend/app/oracle/router.py : L124-151]

## Context

Human UMA disputes are slow and expensive for sports props. A single LLM oracle is a single point of hallucination. The product needs a deterministic on-chain rule that still allows disagreement.

## Decision

- Exactly three agent EOAs. Unanimous `submitConsensus` resolves immediately. [contracts/src/ConsensusOracle.vy : L144-161]
- If not unanimous, wait `WINDOW = 86400`. `resolveFallback` requires 2/3 agent majority. If voted weight ≥2/3 opposes that majority, revert and require `resolveArbitrated` (operator). [contracts/src/ConsensusOracle.vy : L37-38] [contracts/src/ConsensusOracle.vy : L197-226]
- Off-chain coordinator collects research and signatures. [oracles/consensus/coordinator.py : L31-57]
- Attestations carry agent signatures, and any sender may relay them; the job relays with the operator key. [contracts/src/ConsensusOracle.vy : L136-141] [oracles/resolve/fallback.py : L48-156]

## Consequences

- Honest disagreement delays payout by at least 24h instead of blocking forever.
- Token holders can force arbitration when agents look captured.
- Negative: operator remains a last-resort dictator; a compromised operator can resolve any stuck market after the window without a further check. Agent key theft of all three keys can resolve immediately to a false outcome.
- Negative: fallback depends on the job, live Cursor research and the operator key. While research is unavailable (for example, the Cursor account is out of quota until 2026-10-01 unless a spend limit is set), no fallback attestations are sent, and markets wait for a manual `resolveArbitrated`.
