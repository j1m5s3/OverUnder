---
title: Glossary
status: MIXED
area: intent
summary: Domain terms for OverUnder markets, books, oracles, and OU.
last_verified: 2026-09-16
pointers: []
---

# Glossary

- [SHIPPED] **Primary** — Operator-created parent market (`marketType=0`) that trades on the CLOB.
- [SHIPPED] **Wildcard** — Child market (`marketType=1`) that must close ≤ parent and trades on the AMM.
- [SHIPPED] **Condition** — Binary CTF condition identified by `conditionId = keccak256(oracle, questionId)`.
- [SHIPPED] **YES / NO** — Outcomes 0 and 1. ERC-1155 ids are `keccak256(conditionId, outcome)`.
- [SHIPPED] **CLOB** — Off-chain order book; on-chain `Exchange.matchOrders` with EIP-712 orders.
- [SHIPPED] **Taker fee** — 75 bps of USDC volume, paid to FeeVault by the taker’s counterparty USDC flow.
- [SHIPPED] **CPMM** — Constant-product AMM on YES/NO reserves (`MarketAMM`).
- [SHIPPED] **AMM fee** — 100 bps split 50 vault / 50 LP.
- [SHIPPED] **Attestation** — Agent EIP-712 statement of outcome + evidenceHash after closeTime.
- [SHIPPED] **Unanimous consensus** — Three distinct agents, same outcome, `submitConsensus`.
- [SHIPPED] **Fallback window** — 86400 seconds after closeTime before `resolveFallback`.
- [SHIPPED] **Participant vote** — Token-weighted `castVote` using YES+NO balances.
- [SHIPPED] **NAV** — USDC on FeeVault per 1e18 OU (`balance * 1e18 / supply`).
- [SHIPPED] **OU** — Fixed-supply revenue token (100M). Redeem burns OU for USDC.
- [STUB] **Privy login** — API accepts a token without JWKS; web synthesizes a hex address from email.
- [STUB] **Relayer** — Optional `relayer_private_key` that submits `matchOrders`; fills may stay off-chain.
- [PHASE2] **Paymaster** — ERC-4337 contract that sponsors UserOps (approvals, swaps, orders) in USDC or protocol credits.
- [PHASE2] **Emissions** — Scheduled OU distribution from treasury or a dedicated minter; not FeeVault mint.
- [PHASE2] **MoonPay / KYC** — Fiat on-ramp plus identity checks before card buys.
