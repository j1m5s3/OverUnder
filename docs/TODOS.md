---
title: TODO registry
status: MIXED
area: cross
summary: Machine-parseable gaps. OU-T001–T013 are done. Open lifecycle gaps are OU-T014 (invalid/refund outcome), OU-T015 (dynamic pm-AMM L_t) and OU-T016 (smart-account makers and on-chain cancel for the leftover CLOB).
last_verified: 2026-09-24
pointers:
  - "[backend/app/orderbook/router.py : L94-164]"
  - "[backend/app/relayer/worker.py : L1-21]"
  - "[contracts/src/MarketAMM.vy : L1-12]"
  - "[contracts/src/lib/NormalMath.vy : L1-8]"
  - "[contracts/src/MarketFactory.vy : L177-199]"
  - "[backend/app/markets/listing.py : L1-9]"
  - "[contracts/src/ConsensusOracle.vy : L120-127]"
  - "[contracts/src/Exchange.vy : L94-100]"
  - "[oracles/resolve/run.py : L147-348]"
  - "[oracles/listing/run.py : L172-379]"
---

# TODOs

Machine registry below. Human index:

- [SHIPPED] OU-T001 ERC-4337 paymaster (leftover; app uses CDP)
- [SHIPPED] OU-T002 CDP validateAccessToken
- [SHIPPED] OU-T003 Centralized production relayer for the leftover CLOB (off by default)
- [SHIPPED] OU-T004 OU emissions from treasury (no FeeVault mint)
- [SHIPPED] OU-T005 Cursor-runtime agents; MockSearch tests-only
- [SHIPPED] OU-T006 Flutter from `mobile/README.md`
- [SHIPPED] OU-T007 MoonPay + KYC
- [SHIPPED] OU-T008 Uniform-LVR Vyper spike (static pm-AMM chosen)
- [SHIPPED] OU-T009 MarketAMM v2: static pm-AMM with closeTime gate (Base Sepolia redeploy is an ops step)
- [SHIPPED] OU-T010 Loosely gated user listing (MarketFactory v2, listing API, web `/list`)
- [SHIPPED] OU-T011 Agent score scout (search extract, auto-POST on 3/3)
- [SHIPPED] OU-T012 Dual-gate sports auto-resolve
- [SHIPPED] OU-T013 NFL week-roll winner listing
- [STUB] OU-T014 Invalid/refund outcome for cancelled or ambiguous markets (detection wired, no payout path)
- [PHASE2] OU-T015 Dynamic pm-AMM liquidity schedule L_t (static L plus close gate shipped)
- [STUB] OU-T016 Smart-account (EIP-1271) CLOB makers and an on-chain cancel path (cancel args wired, no relayed cancel)

YAML status values: `open` | `blocked` | `done`.

```yaml
todos:
  - id: OU-T001
    title: ERC-4337 paymaster for AA users
    status: done
    area: contracts
    phase: 2
    summary: OverUnderPaymaster and SimpleAccount remain in-tree leftover. The app path is Coinbase CDP Paymaster (ADR-0010); POST /aa/userop returns 410.
    pointers:
      - "[contracts/src/OverUnderPaymaster.vy : L322-355]"
      - "[contracts/src/OverUnderPaymaster.vy : L358-368]"
      - "[contracts/src/SimpleAccount.vy : L40-49]"
      - "[backend/app/aa/router.py : L163-165]"
  - id: OU-T002
    title: Verify CDP access tokens instead of trusting body.address
    status: done
    area: backend
    phase: 2
    summary: validateAccessToken; HS256 session JWT sub is the smart-account address; ignore client-supplied address.
    pointers:
      - "[backend/app/cdp.py : L116-134]"
      - "[backend/app/auth/router.py : L144-173]"
      - "[web/src/features/wallet/ConnectBar.tsx : L36-59]"
  - id: OU-T003
    title: Centralized production relayer
    status: done
    area: backend
    phase: 2
    summary: >-
      Leftover CLOB only, off by default (RELAYER_ENABLED, RELAYER_WORKER_ENABLED). POST /orders recomputes the
      EIP-712 digest and recovers the maker at the API edge (unsigned or bad signatures 400, nothing stored),
      returns 409 once the market halts, and can preflight balances and approvals on chain. Each match enqueues a
      RelayJob; the worker signs matchOrders with RELAYER_PRIVATE_KEY using a write-ahead nonce, EIP-1559 fees with
      bumps, receipt reconciliation and rollback of optimistic fills, under a Postgres advisory-lock leader.
      Operator /relayer/status, /jobs and /tick. Rehearsed on anvil and Postgres. EOA makers only (see OU-T016).
    pointers:
      - "[backend/app/orderbook/router.py : L94-164]"
      - "[backend/app/orderbook/eip712.py : L174-196]"
      - "[backend/app/relayer/queue.py : L30-62]"
      - "[backend/app/relayer/worker.py : L1-21]"
      - "[backend/app/relayer/worker.py : L693-857]"
      - "[backend/app/relayer/gas.py : L23-63]"
      - "[backend/app/relayer/router.py : L24-106]"
      - "[backend/app/config.py : L107-130]"
      - "[backend/tests/test_relayer_worker.py : L144-174]"
  - id: OU-T004
    title: OU emissions from treasury without minting into FeeVault
    status: done
    area: contracts
    phase: 2
    summary: LP/maker/agent/quest transfers from treasury; NAV stays USDC backing / supply. The API route now keeps a write-ahead EmissionDistribution row (202 on an ambiguous broadcast, idempotencyKey replays).
    pointers:
      - "[contracts/src/EmissionsDistributor.vy : L23-49]"
      - "[contracts/src/RevenueToken.vy : L13-32]"
      - "[contracts/src/FeeVault.vy : L44-50]"
      - "[docs/emissions/schedule.yaml : L1-61]"
      - "[backend/app/emissions/router.py : L203-386]"
  - id: OU-T005
    title: Replace heuristic agents and MockSearch as the default path
    status: done
    area: oracles
    phase: 2
    summary: Bind Cursor agents (composer-2.5 / grok-4.6 / gpt-5.1) plus HTTP search MCP; keep MockSearch only for unit tests and keyless CI. Local runs are MCP-only (tools=["mcp"]), run errors are surfaced, cursor-sdk is pinned to 1.0.32.
    pointers:
      - "[oracles/agents/cursor_runtime.py : L31-87]"
      - "[oracles/agents/cursor_runtime.py : L364-384]"
      - "[oracles/agents/alpha.py : L9-31]"
      - "[oracles/consensus/coordinator.py : L31-57]"
      - "[oracles/requirements.txt : L4]"
  - id: OU-T006
    title: Flutter app from mobile carryover contract
    status: done
    area: web
    phase: 2
    summary: Implement lib/features/* mirroring web; consume tokens.json and openapi.json.
    pointers:
      - "[mobile/lib/config/app_config.dart : L1-24]"
      - "[mobile/lib/main.dart : L1-81]"
      - "[mobile/lib/theme/app_theme.dart : L1-85]"
      - "[mobile/lib/services/api_client.dart : L61-119]"
      - "[mobile/lib/features/markets/market_list_screen.dart : L1-216]"
      - "[mobile/lib/features/trade/amm_swap_widget.dart : L81-109]"
  - id: OU-T007
    title: MoonPay widget plus KYC gating
    status: done
    area: backend
    phase: 2
    summary: Signed MoonPay sessions, webhook, jurisdiction policy; Coinbase URL stays fallback. Routes read user.address (the ORM User), and amounts must be finite, positive and at most 1,000,000 USDC.
    pointers:
      - "[backend/app/ramps/router.py : L24-88]"
      - "[backend/app/kyc/router.py : L18-85]"
      - "[backend/app/models.py : L163-179]"
  - id: OU-T008
    title: Uniform-LVR AMM spike in Vyper
    status: done
    area: contracts
    phase: 2
    summary: >-
      Chose the static pm-AMM (Moallemi–Robinson): NO = L·g(u), YES = L·g(−u), YES price Φ(u). NormalMath.vy
      ports Solady expWad/lnWad, uses the Hart 5666 Φ tail and a verified Newton solver; errors and gas are
      recorded in docs/explore/uniform-lvr-spike.md. LP-value sims favour a close gate now and dynamic L_t later
      (OU-T015).
    pointers:
      - "[contracts/src/lib/NormalMath.vy : L1-8]"
      - "[contracts/src/lib/NormalMath.vy : L140-159]"
      - "[contracts/src/lib/NormalMath.vy : L179-229]"
      - "[contracts/tests/test_normal_math.py : L106-126]"
  - id: OU-T009
    title: Migrate MarketAMM CPMM to uniform-LVR default pool
    status: done
    area: contracts
    phase: 2
    summary: >-
      MarketAMM v2 keeps every v1 selector and event and adds seedPoolFor, removeLiquidity, priceYes, closeGate
      (default on, operator setCloseGate) and lpLocked. Buys, sells and adds revert "market closed" at or after the
      cached closeTime; quotes, priceYes and removeLiquidity stay open. Fees stay 100 bps split 50/50 (vault / LP
      accumulator). Seed LP is locked until close or resolution, MIN_LP forever. Code-complete on this branch; the
      Base Sepolia AMM + Factory redeploy runs through deploy-contracts.yml after merge. Decision in ADR-0011.
    pointers:
      - "[contracts/src/MarketAMM.vy : L60-78]"
      - "[contracts/src/MarketAMM.vy : L115-119]"
      - "[contracts/src/MarketAMM.vy : L126-149]"
      - "[contracts/src/MarketAMM.vy : L159-238]"
      - "[contracts/src/MarketAMM.vy : L370-414]"
      - "[contracts/tests/test_amm_pm.py : L680-719]"
  - id: OU-T010
    title: Permissionless or loosely gated market listing
    status: done
    area: contracts
    phase: 2
    summary: >-
      MarketFactory v2 createPermissionlessMarket (marketType 2) behind allowlist-or-permissionless, min seed,
      lead/horizon, question length, criteria hash and per-creator cooldown gates; the lister owns the seed LP
      (locked until close). Backend /markets/listing config, eligibility, prepare, confirm and operator review;
      a type-2 market is public only once its listing is confirmed. Web /list sends the approve + create batch
      through CDP Paymaster. Mobile has no listing UI. Non-sports user markets resolve through resolve/general.py. Decision in ADR-0012.
    pointers:
      - "[contracts/src/MarketFactory.vy : L177-199]"
      - "[contracts/src/MarketFactory.vy : L201-227]"
      - "[backend/app/markets/listing.py : L288-362]"
      - "[backend/app/markets/listing.py : L376-424]"
      - "[backend/app/markets/visibility.py : L73-107]"
      - "[web/src/features/listing/ListMarketForm.tsx : L283-344]"
      - "[contracts/tests/test_factory_listing.py : L207-254]"
  - id: OU-T011
    title: AI agent score scout for sports cards
    status: done
    area: oracles
    phase: 2
    summary: Three Cursor agents extract box scores and bet-relevant facts; auto-POST operator JWT on 3/3. Does not resolve markets. MockSearch in tests. Target selection is recent kickoffs first, then a rotating backlog.
    pointers:
      - "[oracles/scores/scout.py : L110-118]"
      - "[oracles/scores/scout.py : L332-371]"
      - "[oracles/scores/publish.py : L15-52]"
      - "[oracles/scores/job.py : L120-172]"
      - "[backend/app/markets/router.py : L346-387]"
      - "[web/src/features/markets/MatchupHero.tsx : L1-75]"
  - id: OU-T012
    title: Dual-gate sports auto-resolve
    status: done
    area: oracles
    phase: 2
    summary: After LiveScore final, submitConsensus only when score-derived winner matches unanimous Coordinator research. Coordinator.run still does not submit. Past closeTime + 24h the OU_FALLBACK_POLICY (default attest) path attests and calls resolveFallback.
    pointers:
      - "[oracles/resolve/run.py : L147-348]"
      - "[oracles/resolve/winner.py : L26-48]"
      - "[oracles/resolve/fallback.py : L48-156]"
      - "[oracles/consensus/coordinator.py : L31-57]"
      - "[backend/app/oracle/router.py : L100-121]"
  - id: OU-T013
    title: NFL week-roll winner listing
    status: done
    area: oracles
    phase: 2
    summary: Schedule scout publishes rows all three agents agree on, per game; once every week-W game is final, postponed, cancelled or stale past the grace period, operator-POST week W+1 winner primaries. Operator creates stay permissioned (user listing is OU-T010).
    pointers:
      - "[oracles/schedule/scout.py : L147-202]"
      - "[oracles/listing/run.py : L85-97]"
      - "[oracles/listing/run.py : L172-379]"
      - "[backend/app/markets/router.py : L237-301]"
      - "[oracles/job.py : L136-177]"
  - id: OU-T014
    title: Invalid/refund outcome for cancelled or ambiguous markets
    status: open
    area: contracts
    phase: 2
    summary: >-
      ConsensusOracle._payouts only reports [1,0] or [0,1], so a cancelled game or a question with no verifiable
      answer can never settle. CTF reportPayouts already accepts any non-zero denominator, so a [1,1] refund is
      possible on chain. Wired: the audit flags cancelled games with "no payout path", the general resolver
      reports "undetermined" when an agent answers outcome 2, and the sports resolver reports "no score outcome".
      Missing: an invalid outcome in ConsensusOracle (consensus, fallback and arbitration), a resolver path to it,
      and UI copy for refunds.
    pointers:
      - "[contracts/src/ConsensusOracle.vy : L120-133]"
      - "[contracts/src/ConditionalTokens.vy : L97-105]"
      - "[oracles/resolve/general.py : L346-349]"
      - "[scripts/audit_markets.py : L760-762]"
  - id: OU-T015
    title: Dynamic pm-AMM liquidity schedule L_t
    status: open
    area: contracts
    phase: 2
    summary: >-
      MarketAMM v2 ships the static pm-AMM: L is fixed at seed and changes only with LP adds and removes, and LPs
      are protected by the closeTime gate. The dynamic pm-AMM (L_t shrinking with sqrt of time to close, released
      tokens credited to LPs) is not implemented. Already in place: Pool.closeTime cached at seed, proportional
      addLiquidity, pro-rata removeLiquidity and the lpFees accumulator. Design notes in
      docs/explore/uniform-lvr-spike.md.
    pointers:
      - "[contracts/src/MarketAMM.vy : L126-149]"
      - "[contracts/src/MarketAMM.vy : L319-352]"
      - "[contracts/src/MarketAMM.vy : L370-414]"
  - id: OU-T016
    title: Smart-account (EIP-1271) CLOB makers and an on-chain cancel path
    status: open
    area: contracts
    phase: 2
    summary: >-
      Exchange verifies maker signatures with ecrecover only, so CDP smart accounts cannot place CLOB orders; only
      EOA (SIWE) wallets can, and /aa/cdp-send still refuses matchOrders. DELETE /orders cancels off-chain; once a
      signature may be public the response sets onchainCancelRequired and returns cancelOrderArgs for the maker to
      call Exchange.cancelOrder. Missing: EIP-1271 verification in a new Exchange and a relayed or operator cancel
      job.
    pointers:
      - "[contracts/src/Exchange.vy : L94-121]"
      - "[backend/app/orderbook/eip712.py : L174-196]"
      - "[backend/app/orderbook/router.py : L167-213]"
      - "[backend/app/aa/router.py : L75-82]"
```
