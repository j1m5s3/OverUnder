---
title: Architecture overview
status: MIXED
area: cross
summary: Layered map of contracts, FastAPI, oracles, Next.js and Flutter with trust boundaries, market lifecycle (trading halt, resolution, fallback) and the Base Sepolia AMM + Factory v2 deployment (since 2026-09-24).
last_verified: 2026-09-24
pointers:
  - "[contracts/src/MarketFactory.vy : L143-150]"
  - "[contracts/src/MarketFactory.vy : L152-159]"
  - "[contracts/src/MarketFactory.vy : L177-199]"
  - "[contracts/src/MarketAMM.vy : L1-12]"
  - "[contracts/src/MarketAMM.vy : L115-119]"
  - "[contracts/src/MarketAMM.vy : L69-72]"
  - "[contracts/src/Exchange.vy : L94-100]"
  - "[contracts/src/ConsensusOracle.vy : L120-127]"
  - "[contracts/src/ConsensusOracle.vy : L144-161]"
  - "[contracts/src/ConsensusOracle.vy : L220-226]"
  - "[backend/app/main.py : L27-61]"
  - "[backend/app/markets/trading.py : L47-67]"
  - "[backend/app/orderbook/router.py : L94-164]"
  - "[backend/app/relayer/worker.py : L1-21]"
  - "[oracles/job.py : L1-31]"
  - "[oracles/resolve/fallback.py : L48-156]"
  - "[web/src/app/providers.tsx : L21-45]"
  - "[contracts/src/OverUnderPaymaster.vy : L322-355]"
---

# Architecture overview

OverUnder is a Base-chain prediction market. Collateral is USDC (6 decimals). Outcomes are binary YES/NO ERC-1155 positions. Every market trades on a seeded `MarketAMM`: in code that is MarketAMM v2, a static uniform-LVR pool (pm-AMM) that halts at closeTime; Base Sepolia runs it (with MarketFactory v2) since 2026-09-24. Resolution is a three-agent oracle. Book of record: [ADR-0007](../adr/0007-amm-first-uniform-lvr.md); MarketAMM v2: [ADR-0011](../adr/0011-pm-amm-v2-close-gate.md); user listing: [ADR-0012](../adr/0012-loosely-gated-user-listing.md).

## Layers

- [SHIPPED] Vyper contracts under `contracts/src/` deploy as one graph: MockUSDC, ConditionalTokens, MarketFactory, Exchange, MarketAMM (with `lib/NormalMath.vy`), ConsensusOracle, FeeVault, RevenueToken, OverUnderPaymaster, SimpleAccount + factory. `contracts/script/deploy_v2.py` swaps in MarketAMM v2 + MarketFactory v2 and reuses the rest.
- [SHIPPED] FastAPI under `backend/app/` exposes `/api/v1` plus `/health`; startup runs one locked schema migration, the chain indexer and (when enabled) the CLOB relayer worker. [backend/app/main.py : L27-61]
- [SHIPPED] Python oracles under `oracles/` run as one Cloud Run Job tick: scores → resolve → resolve_general → schedule → listing, after a single-flight lease and an operator auth preflight. [oracles/job.py : L1-31]
- [SHIPPED] Next.js web under `web/` lists markets, executes AMM swaps, lists user markets at `/list`, shows oracle status.
- [SHIPPED] Flutter app under `mobile/` mirrors the web markets, trade, wallet and oracle modules (no listing UI). See [mobile/README.md](../../mobile/README.md).

## Market types

- [SHIPPED] Type 0 primary: operator `createPrimaryMarket` with required seed; the operator owns the seed LP. [contracts/src/MarketFactory.vy : L143-150]
- [SHIPPED] Type 1 wildcard: generator- or operator-created child (closes ≤ parent) with optional seed. [contracts/src/MarketFactory.vy : L152-159]
- [SHIPPED] Type 2 user market: `createPermissionlessMarket` by an allowlisted lister, or anyone once `permissionless` is on; the lister owns the seed LP. The API shows it only after the listing is confirmed. [contracts/src/MarketFactory.vy : L177-199]
- [SHIPPED] Exchange CLOB (`matchOrders`) remains deployed leftover overlay; it is not required to trade. Makers must be EOAs because Exchange uses `ecrecover` (OU-T016). [contracts/src/Exchange.vy : L94-100]
- [PHASE2] Dynamic liquidity L_t (OU-T015).

## Market lifecycle

- [SHIPPED] Trading halts at closeTime twice over: MarketAMM v2 reverts buys, sells and adds (`closeGate`, default on) and the API returns 409 for quotes, sponsored trades and CLOB orders (`TRADING_HALT_AT_CLOSE`, default true). Direct contract calls on a v1 AMM are not gated. [contracts/src/MarketAMM.vy : L115-119] [backend/app/markets/trading.py : L47-67]
- [SHIPPED] Resolution: unanimous `submitConsensus` from the job (dual gate for sports winners, 3/3 plus a confidence floor elsewhere). [contracts/src/ConsensusOracle.vy : L144-161]
- [SHIPPED] Past closeTime + 24 h the job's `OU_FALLBACK_POLICY` (default `attest`) has matching agents `submitAttestation`, then calls `resolveFallback`; `arbitrate` adds operator `resolveArbitrated`. [oracles/resolve/fallback.py : L48-156]
- [STUB] Cancelled or unanswerable markets have no payout path: ConsensusOracle only reports [1,0] or [0,1] (OU-T014). [contracts/src/ConsensusOracle.vy : L120-127]

## Trust boundaries

- [SHIPPED] `operator` creates primaries, pauses markets, sets factory, generator, listing config and the AMM close gate, relays fallback attestations and arbitrates after the window. [contracts/src/ConsensusOracle.vy : L220-226]
- [SHIPPED] The CLOB relayer (off by default) submits leftover `matchOrders` with `RELAYER_PRIVATE_KEY` only after verifying both EIP-712 signatures at the API edge; anyone holding both signatures could settle, so the relayer is a convenience, not an on-chain privilege. [backend/app/orderbook/router.py : L94-164] [backend/app/relayer/worker.py : L1-21]
- [SHIPPED] Three agent EOAs are the only attestors. The job's config guard refuses to send when its keys do not match `oracle.agents(i)` and `operator()`.
- [SHIPPED] User-facing auth is Coinbase CDP `validateAccessToken`; HS256 `sub` is the smart-account address. CDP users are not operators. [backend/app/auth/router.py : L144-173]
- [SHIPPED] SIWE with `ecrecover` remains for operator/dev JWT; the oracle job bootstraps its operator user through it. [backend/app/auth/router.py : L86-141]
- [SHIPPED] Gasless user ops use CDP Paymaster (`useCdpPaymaster: true`). OverUnderPaymaster stays leftover in-tree. [web/src/app/providers.tsx : L21-45] [contracts/src/OverUnderPaymaster.vy : L322-355]
- [SHIPPED] Research agents run locally with MCP-only tools by default (`OU_CURSOR_RUNTIME=local`, `tools=["mcp"]`), treat question and criteria text as untrusted, and job output is redacted.

## Settlement

- [SHIPPED] AMM fee 100 bps: 50 bps to FeeVault, 50 bps to the pool's LP fee accumulator, on all markets. [contracts/src/MarketAMM.vy : L69-72]
- [SHIPPED] Leftover CLOB taker fee 75 bps to FeeVault when Exchange matching is used. [contracts/src/Exchange.vy : L33]
- [SHIPPED] Optional user-listing fee goes to `feeRecipient`; the deploy scripts set FeeVault as recipient and a fee of 0.
- [SHIPPED] OU is 100M fixed supply; FeeVault NAV is `usdc_balance * 1e18 / ou_supply`. [contracts/src/FeeVault.vy : L44-50]
- [SHIPPED] OU emissions transfer from treasury; they do not mint into FeeVault.

## Deployment state

- [SHIPPED] Code: MarketAMM v2 and MarketFactory v2 on branch `feat/lifecycle-phase2-completion`, with the `deploy-contracts.yml` workflow (targets verify, v2, core).
- [SHIPPED] Base Sepolia still runs the v1 AMM + Factory until an operator runs that workflow after merge (verify, then v2). It reuses CTF, ConsensusOracle, FeeVault, USDC, Exchange and the paymaster, so existing condition ids keep resolving; repo vars and `deploy-gcp.yml` follow. Procedure: [runbooks/operations.md](../runbooks/operations.md).

## Read next

- [SHIPPED] [contracts.md](contracts.md)
- [SHIPPED] [backend.md](backend.md)
- [SHIPPED] [oracles.md](oracles.md)
- [SHIPPED] [web.md](web.md) (includes Mobile)
- [SHIPPED] [data-flow.md](data-flow.md)
