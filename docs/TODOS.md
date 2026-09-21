---
title: TODO registry
status: PHASE2
area: cross
summary: Machine-parseable gaps for paymaster, JWKS, leftover CLOB relayer, uniform-LVR, and permissionless listing.
last_verified: 2026-09-21
pointers:
  - "[backend/app/auth/router.py : L90-102]"
  - "[backend/app/orderbook/matcher.py : L88-136]"
  - "[contracts/src/MarketAMM.vy : L40]"
  - "[contracts/src/MarketFactory.vy : L82-89]"
  - "[docs/adr/0007-amm-first-uniform-lvr.md : L29-36]"
  - "[docs/adr/0008-cursor-runtime-oracles.md : L18-32]"
  - "[docs/adr/0009-dual-gate-sports-resolve-and-week-listing.md : L18-32]"
  - "[oracles/resolve/run.py : L33-119]"
  - "[oracles/listing/run.py : L58-137]"
---

# TODOs

Machine registry below. Human index:

- [PHASE2] OU-T001 ERC-4337 paymaster
- [PHASE2] OU-T002 Privy JWKS + SIWE ecrecover
- [PHASE2] OU-T003 Centralized production relayer
- [SHIPPED] OU-T004 OU emissions from treasury (no FeeVault mint)
- [SHIPPED] OU-T005 Cursor-runtime agents; MockSearch tests-only
- [SHIPPED] OU-T006 Flutter from `mobile/README.md`
- [SHIPPED] OU-T007 MoonPay + KYC
- [PHASE2] OU-T008 Uniform-LVR Vyper spike
- [PHASE2] OU-T009 Migrate MarketAMM CPMM to uniform-LVR
- [PHASE2] OU-T010 Permissionless or loosely gated listing
- [SHIPPED] OU-T011 Agent score scout (search extract, auto-POST on 3/3)
- [SHIPPED] OU-T012 Dual-gate sports auto-resolve
- [SHIPPED] OU-T013 NFL week-roll winner listing

YAML status values: `open` | `blocked` | `done`.

```yaml
todos:
  - id: OU-T001
    title: ERC-4337 paymaster for AA users
    status: open
    area: contracts
    phase: 2
    summary: Sponsor approve/split/AMM/vote/cancel UserOps; keep matchOrders on the relayer.
    pointers:
      - "[docs/roadmap/phase-2.md : L1-199]"
      - "[web/src/app/providers.tsx : L10-17]"
  - id: OU-T002
    title: Verify Privy JWKS instead of trusting body.address
    status: open
    area: backend
    phase: 2
    summary: RS256 JWKS, audience check, SIWE ecrecover; delete demo hex email login.
    pointers:
      - "[backend/app/auth/router.py : L90-102]"
      - "[web/src/features/wallet/ConnectBar.tsx : L32-46]"
  - id: OU-T003
    title: Centralized production relayer
    status: open
    area: backend
    phase: 2
    summary: Queue matchOrders with nonce/gas policy and preflight allowance checks; fail closed on bad EIP-712 sigs.
    pointers:
      - "[backend/app/orderbook/matcher.py : L88-136]"
      - "[contracts/src/Exchange.vy : L128-159]"
  - id: OU-T004
    title: OU emissions from treasury without minting into FeeVault
    status: done
    area: contracts
    phase: 2
    summary: LP/maker/agent/quest transfers from treasury; NAV stays USDC backing / supply.
    pointers:
      - "[contracts/src/EmissionsDistributor.vy : L22-50]"
      - "[contracts/src/RevenueToken.vy : L13-32]"
      - "[contracts/src/FeeVault.vy : L44-50]"
      - "[docs/emissions/schedule.yaml : L1-59]"
      - "[backend/app/emissions/router.py : L16-79]"
  - id: OU-T005
    title: Replace heuristic agents and MockSearch as the default path
    status: done
    area: oracles
    phase: 2
    summary: Bind Cursor agents (composer-2.5 / grok-4.6 / gpt-5.1) plus HTTP search MCP; keep MockSearch only for unit tests and keyless CI.
    pointers:
      - "[oracles/agents/cursor_runtime.py : L26-77]"
      - "[oracles/agents/alpha.py : L9-27]"
      - "[oracles/consensus/coordinator.py : L36-52]"
      - "[docs/adr/0008-cursor-runtime-oracles.md : L18-32]"
  - id: OU-T006
    title: Flutter app from mobile carryover contract
    status: done
    area: web
    phase: 2
    summary: Implement lib/features/* mirroring web; consume tokens.json and openapi.json.
    pointers:
      - "[mobile/README.md : L1-70]"
      - "[mobile/lib/main.dart : L1-72]"
      - "[mobile/lib/theme/app_theme.dart : L1-92]"
      - "[mobile/lib/services/api_client.dart : L1-137]"
      - "[mobile/lib/features/markets/market_list_screen.dart : L1-154]"
      - "[mobile/lib/features/trade/amm_swap_widget.dart : L1-186]"
  - id: OU-T007
    title: MoonPay widget plus KYC gating
    status: done
    area: backend
    phase: 2
    summary: Signed MoonPay sessions, webhook, jurisdiction policy; Coinbase URL stays fallback.
    pointers:
      - "[backend/app/ramps/router.py : L28-129]"
      - "[backend/app/kyc/router.py : L29-168]"
      - "[backend/app/models.py : L143-148]"
  - id: OU-T008
    title: Uniform-LVR AMM spike in Vyper
    status: open
    area: contracts
    phase: 2
    summary: Spike pm-AMM / Moallemi–Robinson–Zhu formula, gas, and Gaussian vs jump-event fit. Shipped MarketAMM stays CPMM until this lands.
    pointers:
      - "[docs/adr/0007-amm-first-uniform-lvr.md : L29-36]"
      - "[contracts/src/MarketAMM.vy : L40]"
      - "[contracts/src/MarketAMM.vy : L86-119]"
  - id: OU-T009
    title: Migrate MarketAMM CPMM to uniform-LVR default pool
    status: open
    area: contracts
    phase: 2
    summary: Replace CPMM buy/sell with the uniform-LVR default pool after T008. Keep 100 bps 50/50 vault/LP unless a new ADR changes fees.
    pointers:
      - "[docs/adr/0007-amm-first-uniform-lvr.md : L29-36]"
      - "[contracts/src/MarketAMM.vy : L122-201]"
  - id: OU-T010
    title: Permissionless or loosely gated market listing
    status: open
    area: contracts
    phase: 2
    summary: Let AMM seed—not operator-recruited makers—bootstrap a market. Factory stays permissioned until this lands.
    pointers:
      - "[docs/adr/0007-amm-first-uniform-lvr.md : L35]"
      - "[contracts/src/MarketFactory.vy : L82-98]"
  - id: OU-T011
    title: AI agent score scout for sports cards
    status: done
    area: oracles
    phase: 2
    summary: Three Cursor agents extract box scores and bet-relevant facts; auto-POST operator JWT on 3/3. Does not resolve markets. MockSearch in tests.
    pointers:
      - "[oracles/scores/scout.py : L68-77]"
      - "[oracles/scores/scout.py : L268-302]"
      - "[oracles/scores/publish.py : L15-52]"
      - "[oracles/scores/job.py : L116-150]"
      - "[backend/app/markets/router.py : L232-273]"
      - "[web/src/features/markets/MatchupHero.tsx : L1-75]"
  - id: OU-T012
    title: Dual-gate sports auto-resolve
    status: done
    area: oracles
    phase: 2
    summary: After LiveScore final, submitConsensus only when score-derived winner matches unanimous Coordinator research. Coordinator.run still does not submit.
    pointers:
      - "[oracles/resolve/run.py : L33-119]"
      - "[oracles/resolve/winner.py : L26-48]"
      - "[oracles/consensus/coordinator.py : L27-53]"
      - "[backend/app/oracle/router.py : L51-73]"
      - "[docs/adr/0009-dual-gate-sports-resolve-and-week-listing.md : L18-32]"
  - id: OU-T013
    title: NFL week-roll winner listing
    status: done
    area: oracles
    phase: 2
    summary: Schedule scout 3/3 current and next NFL week; after every week-W game is final, operator-POST week W+1 winner primaries. Factory stays permissioned.
    pointers:
      - "[oracles/schedule/scout.py : L128-157]"
      - "[oracles/listing/run.py : L58-137]"
      - "[backend/app/markets/router.py : L134-197]"
      - "[oracles/job.py : L14-50]"
```
