---
title: Trusted MVP keys
status: SHIPPED
area: cross
summary: Local Anvil operator, relayer, three agents, treasury, and wildcard generator are trusted EOAs.
last_verified: 2026-09-16
pointers:
  - "[contracts/script/deploy.py : L14-22]"
  - "[contracts/src/MarketFactory.vy : L81-88]"
  - "[contracts/src/ConsensusOracle.vy : L219-226]"
  - "[backend/app/orderbook/matcher.py : L88-101]"
---

## Status

Accepted 2026-09-16.

## Context

A fully decentralized listing + matching + oracle stack is out of range for the first vertical slice. Tests and `run_stack` need deterministic keys.

## Decision

- Deploy uses well-known Anvil accounts: operator, relayer, alpha/beta/gamma, treasury, generator. [contracts/script/deploy.py : L14-22]
- Operator: create/pause primaries, set generator, arbitrate. [contracts/src/MarketFactory.vy : L81-84] [contracts/src/ConsensusOracle.vy : L219-226]
- Relayer key is optional in the API; matching still records off-chain if submission fails. [backend/app/orderbook/matcher.py : L88-101]
- Agent keys are the only valid attestation signers.

## Consequences

- Local e2e can resolve and trade without governance.
- Negative: any leak of these keys (or reuse on a public testnet) lets an attacker list markets, arbitrate outcomes, and relay malicious fills. Production must rotate to KMS/HSM and split operator vs arbiter.
