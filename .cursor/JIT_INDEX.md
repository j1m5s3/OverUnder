# OverUnder JIT index — living graph after Phase 1 AMM closeout

## Subsystems

- [contracts/src/ConditionalTokens.vy] - binary ERC-1155 CTF split/merge/redeem
- [contracts/src/MarketFactory.vy] - permissioned primaries (required seed) + wildcard children
- [contracts/src/MarketAMM.vy] - seeded CPMM on all types, 50/50 fee split, quoteSell
- [contracts/src/Exchange.vy] - leftover EIP-712 CLOB overlay, unused by happy path
- [contracts/src/ConsensusOracle.vy] - 3-agent unanimity + 24h fallback
- [contracts/src/FeeVault.vy] - OU NAV redeem + 24h cooldown
- [backend/app/main.py] - FastAPI routers + indexer lifespan
- [backend/app/markets/router.py] - factory-backed POST /markets
- [backend/app/amm/router.py] - quoteBuy / quoteSell proxy
- [backend/app/orderbook/matcher.py] - leftover CLOB crossing + relayer
- [oracles/consensus/coordinator.py] - collect attestations
- [oracles/wildcard/generator.py] - child market proposals
- [web/src/features/trade/AmmSwap.tsx] - wallet buyWithUSDC / sellToUSDC
- [web/src/features/markets] - event hub + AmmSwap ticket
- [shared/design-tokens/tokens.json] - Flutter theme contract
- [shared/openapi.json] - Flutter API contract
- [scripts/e2e_local.py] - AMM buy+sell happy path + fallback

## Decisions

- ADR-0007: AMM-first; uniform-LVR is PHASE2 target; CLOB is leftover overlay
- ADR-0001 superseded (historical hybrid CLOB-primary / AMM-wildcard)
- OU is fixed-supply NAV token, not a public savings vault
- Trusted operator/relayer/oracle keys in MVP
- Do not invent ERC-4337, Privy JWKS, or uniform-LVR as shipped; T004–T007 stay done

## Docs

- [AGENTS.md] — agent entrypoint (read first)
- [docs/README.md] — map and reading order
- [docs/architecture/overview.md] — layers and trust boundaries
- [docs/architecture/contracts.md] — Vyper contract map
- [docs/architecture/backend.md] — FastAPI MIXED map
- [docs/architecture/oracles.md] — agent consensus MIXED map
- [docs/architecture/web.md] — Next.js MIXED map
- [docs/architecture/data-flow.md] — AMM traces + leftover CLOB
- [docs/intent/vision.md] — product intent
- [docs/intent/glossary.md] — domain terms
- [docs/adr/0001-hybrid-clob-amm.md] … [docs/adr/0007-amm-first-uniform-lvr.md]
- [docs/explore/amm.md] [docs/explore/edge_opportunities.md] — literature, not spec
- [docs/roadmap/phase-1-mvp.md] — seeded AMM checklist
- [docs/roadmap/phase-2.md] — remaining PHASE2 + shipped T004–T007
- [docs/TODOS.md] — YAML registry OU-T001–T010
- [docs/runbooks/local-dev.md] — run/test/stop
- [.cursor/rules/ai-docs.mdc] — always-on pointer hygiene
