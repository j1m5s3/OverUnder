---
title: Glossary
status: MIXED
area: intent
summary: Domain terms for OverUnder markets, the pm-AMM, listing, trading halt, oracles, relayer and OU.
last_verified: 2026-09-23
pointers: []
---

# Glossary

- [SHIPPED] **Primary** — Parent market that heads an event card: an operator primary (`marketType=0`) or a user-listed market (`marketType=2`). Trades on the seeded `MarketAMM`.
- [SHIPPED] **Wildcard** — Child market (`marketType=1`) that must close ≤ parent and trades on the same AMM.
- [SHIPPED] **User-listed market** — `marketType=2`, created by `createPermissionlessMarket` with a seed and a criteria hash; public in the API only once its listing is confirmed.
- [SHIPPED] **Listing** — The prepare → sponsored create → confirm flow for user markets. States: prepared, indexed, confirmed (public), rejected (hidden).
- [SHIPPED] **Loosely gated listing** — Anyone allowlisted, or anyone once the operator turns `permissionless` on, may list, subject to seed, lead time, horizon, question, criteria and cooldown gates. The operator can still pause.
- [SHIPPED] **Condition** — Binary CTF condition identified by `conditionId = keccak256(oracle, questionId)`.
- [SHIPPED] **YES / NO** — Outcomes 0 and 1. ERC-1155 ids are `keccak256(conditionId, outcome)`.
- [SHIPPED] **CLOB** — Leftover overlay: off-chain order book plus on-chain `Exchange.matchOrders` with EIP-712 orders from EOA makers. Not the product path.
- [SHIPPED] **Taker fee** — 75 bps of USDC volume on leftover Exchange fills, paid to FeeVault.
- [SHIPPED] **CPMM** — Constant-product AMM on YES/NO reserves: the Phase 1 MarketAMM, still live on Base Sepolia until the v2 redeploy. Replaced in code by the pm-AMM.
- [SHIPPED] **Uniform-LVR / pm-AMM** — Prediction-native AMM (Moallemi–Robinson). MarketAMM v2 is the static form: NO = L·g(u), YES = L·g(−u), YES price Φ(u).
- [PHASE2] **Dynamic L_t** — pm-AMM liquidity that shrinks toward closeTime so LP loss stays uniform over time (OU-T015).
- [SHIPPED] **Liquidity (L)** — pm-AMM depth parameter, S/φ(0) ≈ 2.5·S at seed; grows and shrinks only with LP adds and removes.
- [SHIPPED] **AMM fee** — 100 bps: 50 to FeeVault, 50 to the pool's LP fee accumulator.
- [SHIPPED] **Seed lock** — The seed provider's LP shares cannot be withdrawn before closeTime or resolution, and `MIN_LP` of them never.
- [SHIPPED] **Trading halt** — Trading stops at closeTime: MarketAMM v2 reverts buys and sells (`closeGate`, default on) and the API refuses quotes and trades with 409 (`TRADING_HALT_AT_CLOSE`, default true). Quotes on chain and LP exits stay open.
- [SHIPPED] **Attestation** — Agent EIP-712 statement of outcome + evidenceHash after closeTime.
- [SHIPPED] **Research record** — A research attempt that did not resolve, stored as an attestation row with `kind: research`; it drives the research cooldown and never counts toward unanimity.
- [SHIPPED] **Unanimous consensus** — Three distinct agents, same outcome, `submitConsensus`.
- [SHIPPED] **Dual-gate sports resolve** — LiveScore `final` plus unanimous research matching the score-derived winner, then `submitConsensus`.
- [SHIPPED] **General resolver** — Research-only resolve for wildcards, user markets and non-sports primaries: 3/3 with every confidence ≥ 0.8.
- [SHIPPED] **Fallback window** — 86400 seconds after closeTime before `resolveFallback`.
- [SHIPPED] **Fallback policy** — `OU_FALLBACK_POLICY`: `attest` (default; matching agents attest, then `resolveFallback`), `arbitrate` (also operator `resolveArbitrated`) or `manual`.
- [SHIPPED] **Research cooldown** — After research that does not resolve, the resolvers skip that market for `OU_RESEARCH_RETRY_SECONDS` (6 h) so quota is not burnt every tick.
- [SHIPPED] **Not registered** — A market whose condition has closeTime 0 on the configured ConsensusOracle (created on another deployment). It can never resolve there; resolvers skip it and the operator archives the API row.
- [SHIPPED] **Overdue market** — Past close and unresolved beyond the expected path (for example final but awaiting research); `scripts/audit_markets.py` reports these.
- [STUB] **Invalid outcome** — A refund settlement for cancelled or unanswerable markets. Detected, but ConsensusOracle only pays [1,0] or [0,1] (OU-T014).
- [SHIPPED] **Participant vote** — Token-weighted `castVote` using YES+NO balances; the API vote is authenticated and weighted from chain.
- [SHIPPED] **NAV** — USDC on FeeVault per 1e18 OU (`balance * 1e18 / supply`).
- [SHIPPED] **OU** — Fixed-supply revenue token (100M). Redeem burns OU for USDC.
- [SHIPPED] **CDP login** — Email OTP; backend `validateAccessToken`; HS256 `sub` is the smart-account address.
- [SHIPPED] **Relayer** — Backend service for the leftover CLOB, off by default: verifies EIP-712 orders at the API, queues matches as relay jobs and submits `matchOrders` with `RELAYER_PRIVATE_KEY` under a nonce, gas and receipt policy.
- [SHIPPED] **CDP Paymaster** — Coinbase-sponsored user ops via `useCdpPaymaster: true`. Never a paymaster URL in client code.
- [SHIPPED] **OverUnderPaymaster** — Leftover ERC-4337 contract; app does not call it.
- [SHIPPED] **Emissions** — Scheduled OU transfers from treasury; not FeeVault mint.
- [SHIPPED] **MoonPay / KYC** — Fiat on-ramp plus identity checks before card buys; Coinbase URL stays fallback.
