---
title: TODO registry
status: PHASE2
area: cross
summary: Machine-parseable gaps for paymaster, JWKS, relayer, emissions, oracle mocks, Flutter, and KYC.
last_verified: 2026-09-16
pointers:
  - "[backend/app/auth/router.py : L90-102]"
  - "[backend/app/orderbook/matcher.py : L88-136]"
  - "[oracles/agents/base.py : L30-35]"
  - "[mobile/README.md : L1-25]"
---

# TODOs

Machine registry below. Human index:

- [PHASE2] OU-T001 ERC-4337 paymaster
- [PHASE2] OU-T002 Privy JWKS + SIWE ecrecover
- [PHASE2] OU-T003 Centralized production relayer
- [PHASE2] OU-T004 OU emissions from treasury (no FeeVault mint)
- [PHASE2] OU-T005 Live LLM agents; MockSearch tests-only
- [PHASE2] OU-T006 Flutter from `mobile/README.md`
- [PHASE2] OU-T007 MoonPay + KYC

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
    status: open
    area: contracts
    phase: 2
    summary: LP/maker/agent/quest transfers from treasury; NAV stays USDC backing / supply.
    pointers:
      - "[contracts/src/RevenueToken.vy : L13-32]"
      - "[contracts/src/FeeVault.vy : L44-50]"
  - id: OU-T005
    title: Replace heuristic agents and MockSearch as the default path
    status: open
    area: oracles
    phase: 2
    summary: Bind Claude/GPT/Gemini with tool-use; keep MockSearch only for unit tests and keyless CI.
    pointers:
      - "[oracles/agents/base.py : L30-35]"
      - "[oracles/agents/alpha.py : L10-47]"
      - "[oracles/consensus/coordinator.py : L36-52]"
  - id: OU-T006
    title: Flutter app from mobile carryover contract
    status: open
    area: web
    phase: 2
    summary: Implement lib/features/* mirroring web; consume tokens.json and openapi.json.
    pointers:
      - "[mobile/README.md : L1-25]"
  - id: OU-T007
    title: MoonPay widget plus KYC gating
    status: open
    area: backend
    phase: 2
    summary: Signed MoonPay sessions, webhook, jurisdiction policy; Coinbase URL stays fallback.
    pointers:
      - "[backend/app/ramps/router.py : L9-32]"
```
