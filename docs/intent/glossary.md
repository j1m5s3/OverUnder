---
title: Glossary
status: MIXED
area: intent
summary: Domain terms for OverUnder markets, books, oracles, and OU.
last_verified: 2026-09-21
pointers: []
---

# Glossary

- [SHIPPED] **Primary** — Operator-created parent market (`marketType=0`) that trades on seeded `MarketAMM`.
- [SHIPPED] **Wildcard** — Child market (`marketType=1`) that must close ≤ parent and trades on the same AMM.
- [SHIPPED] **Condition** — Binary CTF condition identified by `conditionId = keccak256(oracle, questionId)`.
- [SHIPPED] **YES / NO** — Outcomes 0 and 1. ERC-1155 ids are `keccak256(conditionId, outcome)`.
- [SHIPPED] **CLOB** — Leftover overlay: off-chain order book plus on-chain `Exchange.matchOrders` with EIP-712 orders. Not the product path.
- [SHIPPED] **Taker fee** — 75 bps of USDC volume on leftover Exchange fills, paid to FeeVault.
- [SHIPPED] **CPMM** — Constant-product AMM on YES/NO reserves (`MarketAMM`). Phase 1 book of record.
- [PHASE2] **Uniform-LVR** — Prediction-native AMM (pm-AMM / Moallemi–Robinson–Zhu). Protocol target after OU-T008/T009.
- [SHIPPED] **AMM fee** — 100 bps split 50 vault / 50 LP.
- [SHIPPED] **Attestation** — Agent EIP-712 statement of outcome + evidenceHash after closeTime.
- [SHIPPED] **Unanimous consensus** — Three distinct agents, same outcome, `submitConsensus`.
- [SHIPPED] **Fallback window** — 86400 seconds after closeTime before `resolveFallback`.
- [SHIPPED] **Dual-gate sports resolve** — LiveScore `final` plus unanimous research matching the score-derived winner, then `submitConsensus`.
- [PHASE2] **Permissionless listing** — Anyone can list a seeded AMM primary (OU-T010). Factory stays operator-gated until then.
- [SHIPPED] **Participant vote** — Token-weighted `castVote` using YES+NO balances.
- [SHIPPED] **NAV** — USDC on FeeVault per 1e18 OU (`balance * 1e18 / supply`).
- [SHIPPED] **OU** — Fixed-supply revenue token (100M). Redeem burns OU for USDC.
- [STUB] **Privy login** — API accepts a token without JWKS; web synthesizes a hex address from email.
- [STUB] **Relayer** — Optional `relayer_private_key` that submits leftover `matchOrders`; fills may stay off-chain.
- [SHIPPED] **Paymaster** — ERC-4337 contract that sponsors UserOps (approvals, swaps) and pulls a USDC fee; EntryPoint ETH is the gas tank.
- [PHASE2] **Privy JWKS** — Verify Privy access tokens; email AA users are not shipped.
- [SHIPPED] **Emissions** — Scheduled OU transfers from treasury; not FeeVault mint.
- [SHIPPED] **MoonPay / KYC** — Fiat on-ramp plus identity checks before card buys; Coinbase URL stays fallback.
