# OverUnder JIT index — living graph after MVP execute cycle

## Subsystems

- [contracts/src/ConditionalTokens.vy] - binary ERC-1155 CTF split/merge/redeem
- [contracts/src/MarketFactory.vy] - permissioned primaries + wildcard children
- [contracts/src/Exchange.vy] - EIP-712 CLOB settlement, 75 bps taker
- [contracts/src/MarketAMM.vy] - CPMM wildcards, 50/50 fee split
- [contracts/src/ConsensusOracle.vy] - 3-agent unanimity + 24h fallback
- [contracts/src/FeeVault.vy] - OU NAV redeem + 24h cooldown
- [backend/app/main.py] - FastAPI routers + health
- [backend/app/orderbook/matcher.py] - off-chain crossing + relayer
- [oracles/consensus/coordinator.py] - collect attestations
- [oracles/wildcard/generator.py] - child market proposals
- [web/src/features/markets] - Polymarket-style market UI
- [shared/design-tokens/tokens.json] - Flutter theme contract
- [shared/openapi.json] - Flutter API contract
- [scripts/e2e_local.py] - happy path + fallback

## Decisions

- Hybrid CLOB primary / AMM wildcard
- OU is fixed-supply NAV token, not a public savings vault
- Trusted operator/relayer/oracle keys in MVP

## Docs

- [AGENTS.md] — agent entrypoint (read first)
- [docs/README.md] — map and reading order
- [docs/architecture/overview.md] — layers and trust boundaries
- [docs/architecture/contracts.md] — Vyper contract map
- [docs/architecture/backend.md] — FastAPI MIXED map
- [docs/architecture/oracles.md] — agent consensus MIXED map
- [docs/architecture/web.md] — Next.js MIXED map
- [docs/architecture/data-flow.md] — CLOB / AMM / resolve traces
- [docs/intent/vision.md] — product intent
- [docs/intent/glossary.md] — domain terms
- [docs/adr/0001-hybrid-clob-amm.md] … [docs/adr/0006-docs-system-conventions.md]
- [docs/roadmap/phase-1-mvp.md] — shipped checklist
- [docs/roadmap/phase-2.md] — Flutter, parity, paymaster, JWKS, KYC, emissions
- [docs/TODOS.md] — YAML registry OU-T001+
- [docs/runbooks/local-dev.md] — run/test/stop
- [.cursor/rules/ai-docs.mdc] — always-on pointer hygiene
