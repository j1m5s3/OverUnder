---
title: Trusted MVP keys
status: SHIPPED
area: cross
summary: Operator, relayer, three agents, treasury and wildcard generator are trusted EOAs (well-known Anvil keys locally only). The operator key signs API market txs, oracle job sends and the v2 migration. The relayer is off by default and fails closed.
last_verified: 2026-09-23
pointers:
  - "[contracts/script/deploy.py : L18-27]"
  - "[contracts/script/deploy.py : L104-114]"
  - "[contracts/src/MarketFactory.vy : L143-150]"
  - "[contracts/src/MarketFactory.vy : L243-248]"
  - "[contracts/src/ConsensusOracle.vy : L219-226]"
  - "[backend/app/relayer/queue.py : L30-38]"
  - "[backend/app/orderbook/router.py : L104-128]"
  - "[oracles/resolve/chain.py : L184-255]"
  - "[oracles/tick_lease.py : L1-16]"
  - "[oracles/listing/questions.py : L21-26]"
---

## Status

Accepted 2026-09-16.

Amended 2026-09-23. Key roles and guards:
- Relayer (OU-T003):
  - Off by default: `RELAYER_ENABLED=false`, and a relayer key alone never turns it on.
  - While it is off, the matcher records fills as `offchain` only.
  - When it is on, a missing or unparseable key or Exchange makes `POST /orders` return 503 `relayer misconfigured`, and deploy-gcp refuses to deploy without `OU_RELAYER_PRIVATE_KEY`.
  - Order signatures are EIP-712-verified at the API edge.
  [backend/app/config.py : L107-110] [backend/app/relayer/queue.py : L30-38] [backend/app/orderbook/router.py : L104-128] [backend/app/orderbook/matcher.py : L1-5]
- One operator key (`OU_OPERATOR_PRIVATE_KEY`) signs:
  - API create and pause transactions;
  - every oracle-job send: `submitConsensus`, relayed `submitAttestation`, `resolveFallback` and `resolveArbitrated` (the job has no other sender key);
  - the `deploy-contracts.yml` v2 migration, which pauses the oracle scheduler first to avoid nonce races.
  The operator also holds the new switches: `closeGate` ([ADR-0011](0011-pm-amm-v2-close-gate.md)), and listing config, `permissionless` and listers ([ADR-0012](0012-loosely-gated-user-listing.md)). [backend/app/markets/router.py : L358-370] [oracles/resolve/chain.py : L184-255] [.github/workflows/deploy-contracts.yml : L141-160]
- Single flight: before any stage, the oracle job lists its own Cloud Run executions and skips the tick while an older one is still running. The guard fails open on API errors. [oracles/tick_lease.py : L1-16] [oracles/tick_lease.py : L70-102] [oracles/job.py : L60-86]
- Optional secret `OU_QUESTION_ID_KEY` turns operator listing question ids into an HMAC so they cannot be squatted. [oracles/listing/questions.py : L21-26]
- `deploy.py` refuses missing role keys and the public Anvil keys on any chain other than 31337. [contracts/script/deploy.py : L104-114]

## Context

A fully decentralized listing + matching + oracle stack is out of range for the first vertical slice. Tests and `run_stack` need deterministic keys.

## Decision

- Deploy uses well-known Anvil accounts on chain 31337: operator, relayer, alpha/beta/gamma, treasury, generator. [contracts/script/deploy.py : L18-27]
- Operator: create/pause primaries, set generator, arbitrate. User listing no longer needs the operator (ADR-0012). [contracts/src/MarketFactory.vy : L143-150] [contracts/src/MarketFactory.vy : L243-248] [contracts/src/ConsensusOracle.vy : L219-226]
- Relayer key is optional in the API. With the relayer off (the default), matches are recorded off-chain; see Status for the fail-closed relayer. [backend/app/orderbook/matcher.py : L1-5]
- Agent keys are the only valid attestation signers.

## Consequences

- Local e2e can resolve and trade without governance.
- Negative: any leak of these keys (or reuse on a public testnet) lets an attacker list markets, arbitrate outcomes, and relay malicious fills. Production must rotate to KMS/HSM and split operator vs arbiter.
- Negative: one hot operator key now sends API, oracle and migration transactions. Concurrent senders can collide on nonces (hence the scheduler pause and single-flight guard), and a leak of that key controls resolution, the trading halt and listing policy.
- Negative: the CLOB `Exchange` verifies makers with `ecrecover` only, so CDP smart accounts (EIP-1271) cannot make orders. Nothing in the API cancels an exposed order on chain; `Exchange.cancelOrder` exists but has no caller (OU-T016). [contracts/src/Exchange.vy : L93-100] [contracts/src/Exchange.vy : L116-121]
