---
title: Three-agent oracle consensus
status: SHIPPED
area: oracles
summary: Resolve on 3/3 agent signatures; after 24h use 2/3 agents plus token votes, else operator.
last_verified: 2026-09-16
pointers:
  - "[contracts/src/ConsensusOracle.vy : L37-38]"
  - "[contracts/src/ConsensusOracle.vy : L144-161]"
  - "[contracts/src/ConsensusOracle.vy : L197-226]"
  - "[oracles/consensus/coordinator.py : L36-52]"
---

## Status

Accepted 2026-09-16.

## Context

Human UMA disputes are slow and expensive for sports props. A single LLM oracle is a single point of hallucination. The product needs a deterministic on-chain rule that still allows disagreement.

## Decision

- Exactly three agent EOAs. Unanimous `submitConsensus` resolves immediately. [contracts/src/ConsensusOracle.vy : L144-161]
- If not unanimous, wait `WINDOW = 86400`. `resolveFallback` requires 2/3 agent majority. If voted weight ≥2/3 opposes that majority, revert and require `resolveArbitrated` (operator). [contracts/src/ConsensusOracle.vy : L37-38] [contracts/src/ConsensusOracle.vy : L197-226]
- Off-chain coordinator collects research and signatures. [oracles/consensus/coordinator.py : L36-52]

## Consequences

- Honest disagreement delays payout by at least 24h instead of blocking forever.
- Token holders can force arbitration when agents look captured.
- Negative: operator remains a last-resort dictator; a compromised operator can resolve any stuck market after the window without a further check. Agent key theft of all three keys can resolve immediately to a false outcome.
