# OverUnder JIT index — living graph after dual-gate resolve

last_verified: 2026-09-21

## Subsystems

- [contracts/src/ConditionalTokens.vy] - binary ERC-1155 CTF split/merge/redeem
- [contracts/src/MarketFactory.vy] - permissioned primaries (required seed) + wildcard children
- [contracts/src/MarketAMM.vy] - seeded CPMM on all types, 50/50 fee split, quoteSell
- [contracts/src/Exchange.vy] - leftover EIP-712 CLOB overlay, unused by happy path
- [contracts/src/ConsensusOracle.vy] - 3-agent unanimity + 24h fallback
- [contracts/src/FeeVault.vy] - OU NAV redeem + 24h cooldown
- [backend/app/main.py : L22-39] - FastAPI lifespan; LiveScore facts migrate after create_all
- [backend/app/markets/router.py : L100-131] - EventCard list
- [backend/app/markets/router.py : L134-197] - GET/POST NFL schedule
- [backend/app/markets/router.py : L232-273] - LiveScore.facts JSON upsert
- [backend/app/markets/router.py : L277-419] - operator createPrimaryMarket
- [backend/app/oracle/router.py : L51-73] - operator POST /oracle/resolved
- [backend/app/models.py : L119-129] - NflScheduleGame unique week/home/away
- [backend/app/amm/router.py] - quoteBuy / quoteSell proxy
- [backend/app/orderbook/matcher.py] - leftover CLOB crossing + relayer
- [oracles/agents/cursor_runtime.py : L26-77] - lazy cursor-sdk; local vs cloud; HTTP MCP
- [oracles/agents/cursor_runtime.py : L145-156] - prompt_json lazy Agent.prompt
- [oracles/agents/alpha.py : L9-27] - Cursor alpha (composer-2.5); mock heuristic for tests
- [oracles/consensus/coordinator.py : L27-43] - collect attestations; no submitConsensus
- [oracles/consensus/coordinator.py : L45-53] - sign_unanimous returns deadline plus sigs
- [oracles/resolve/run.py : L33-119] - dual-gate submitConsensus
- [oracles/resolve/winner.py : L26-48] - score-derived YES/NO
- [oracles/schedule/scout.py : L128-157] - 3/3 NFL week extract
- [oracles/listing/run.py : L58-137] - week-complete then POST W+1 winners
- [oracles/job.py : L14-50] - scores then resolve then schedule then listing
- [oracles/scores/scout.py : L268-302] - ScoreReport.facts; 3/3 auto-POST
- [oracles/scores/publish.py : L15-52] - mint HS256 operator JWT matching _issue
- [oracles/scores/job.py : L116-150] - score scout loop; cap OU_SCOUT_MAX_MARKETS
- [oracles/wildcard/generator.py] - child market proposals
- [web/src/features/trade/AmmSwap.tsx] - wallet buyWithUSDC / sellToUSDC
- [web/src/features/markets/MarketInfo.tsx : L15-60] - event hub + one muted facts line
- [shared/design-tokens/tokens.json] - Flutter theme contract
- [shared/openapi.json] - Flutter API contract
- [scripts/e2e_local.py] - AMM buy+sell happy path + fallback; MockSearch
- [oracles/Dockerfile] - python -m job
- [docs/adr/0008-cursor-runtime-oracles.md : L18-32] - Cursor-runtime oracles; remote HTTP MCP
- [docs/adr/0009-dual-gate-sports-resolve-and-week-listing.md : L18-32] - dual-gate resolve + week-roll listing
- [.github/workflows/deploy-gcp.yml : L85-96] - preflight Cursor + AGENT secrets
- [.github/workflows/deploy-gcp.yml : L152-162] - overunder-oracle Job secrets including AGENT keys

## Decisions

- ADR-0007: AMM-first; uniform-LVR is PHASE2 target; CLOB is leftover overlay
- ADR-0008: Cursor-runtime oracles; Coordinator.run does not submitConsensus
- ADR-0009: dual-gate sports auto-resolve; week N complete then list week N+1 winner primaries
- ADR-0001 superseded (historical hybrid CLOB-primary / AMM-wildcard)
- OU is fixed-supply NAV token, not a public savings vault
- Trusted operator/relayer/oracle keys in MVP
- Do not invent ERC-4337, Privy JWKS, or uniform-LVR as shipped; T004–T007 stay done
- T005 retargeted to Cursor agents; T011–T013 done; T001–T003 and T008–T010 stay open
- Exchange remains deployed leftover overlay
- 2026-09-20 Stage 4: mock pytest does not need cursor_sdk; live smoke skipif no key
- 2026-09-21: resolve module submits; score scout still data-only; factory permissioned

## Docs

- [AGENTS.md] — agent entrypoint (read first)
- [docs/README.md] — map and reading order
- [docs/architecture/overview.md] — layers and trust boundaries
- [docs/architecture/contracts.md] — Vyper contract map
- [docs/architecture/backend.md] — FastAPI MIXED map
- [docs/architecture/oracles.md] — Cursor-runtime consensus MIXED map
- [docs/architecture/web.md] — Next.js MIXED map
- [docs/architecture/data-flow.md] — AMM traces + leftover CLOB
- [docs/intent/vision.md] — product intent
- [docs/intent/glossary.md] — domain terms
- [docs/adr/0001-hybrid-clob-amm.md] … [docs/adr/0009-dual-gate-sports-resolve-and-week-listing.md]
- [docs/explore/amm.md] [docs/explore/edge_opportunities.md] — literature, not spec
- [docs/roadmap/phase-1-mvp.md] — seeded AMM checklist
- [docs/roadmap/phase-2.md] — remaining PHASE2 + shipped T004–T007/T011–T013
- [docs/TODOS.md] — YAML registry OU-T001–T013
- [docs/runbooks/local-dev.md] — run/test/stop + Cursor oracle secrets
- [.cursor/rules/ai-docs.mdc] — always-on pointer hygiene
- [.cursor/jit_history/2026-09-20-cursor-runtime-scouts.md] — archived Cursor-runtime scouts plan
