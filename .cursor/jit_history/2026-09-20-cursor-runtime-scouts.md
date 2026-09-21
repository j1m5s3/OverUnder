# Cursor-runtime oracles and data scouts

Replace Claude/GPT/Gemini + Tavily/Brave/Exa with Python `cursor-sdk` (`CURSOR_API_KEY`, explicit model ids, inline HTTP search MCP). Keep MockSearch for pytest/CI/anvil. Scouts request bet-relevant fields and auto-POST when 3/3 agree. Resolution still requires three agents, evidence URLs from search hits, and no `submitConsensus` from coordinator/scout.

Locked: **auto-POST on 3/3**. Runtime is **split**: local SDK on a laptop/anvil; **Cursor cloud agents** on the Cloud Run oracle job. MCP is never hosted in GCP.

## When deployed: how MCP works

GCP still does **not** run an MCP server. Search MCP is a **remote Streamable HTTP** URL in Secret Manager (`OU_CURSOR_SEARCH_MCP_URL`). We do not bake stdio/`npx` search into the image.

On each Scheduler tick:

1. Thin Python job (`oracles/Dockerfile`) starts with `OU_CURSOR_RUNTIME=cloud`.
2. For each sports primary (capped per run), it calls `Agent.prompt` with `cloud=CloudAgentOptions(repos=[])` (no-repo cloud agent) and `mcp_servers={"search": HttpMcpServerConfig(url=CURSOR_SEARCH_MCP_URL, headers=optional)}`.
3. Cursor hosts the agent VM. **HTTP MCP `headers`/`auth` are proxied by Cursor's backend** and never land in the VM. The model still runs on Cursor-hosted inference.
4. Python requires `search_hits` in the JSON and rejects invented `evidence_urls`. Three model ids must agree, then the job mints the same HS256 operator JWT as [`_issue`](backend/app/auth/router.py) and `POST`s [`/api/v1/markets/{id}/score`](backend/app/markets/router.py) on **overunder-api**.
5. API/web never talk to Cursor. They only persist and render scores/facts.

Laptop/anvil: `OU_CURSOR_RUNTIME=local` (default) uses `LocalAgentOptions(cwd=oracles/)` plus the same HTTP MCP URL. `disallowed_tools=["shell"]` is **local-only** (SDK ignores it on cloud). Tests stay `OU_ORACLE_MOCK=1` and never import `cursor_sdk`.

Prerequisite before a green deploy: Secret Manager versions for `OU_CURSOR_API_KEY` and `OU_CURSOR_SEARCH_MCP_URL`. Preflight fails closed if either is missing. Do not hardcode a paid search vendor; the URL is env/secret only.

## Decisions (do not reopen)

- Dual runtime in [`oracles/agents/cursor_runtime.py`](oracles/agents/cursor_runtime.py): `OU_CURSOR_RUNTIME=cloud` (or `CLOUD_RUN_JOB` set) → `CloudAgentOptions(repos=[])`; else `LocalAgentOptions(cwd=oracles/)`. Always pass inline HTTP MCP. Lazy-import `cursor_sdk`.
- Models: `OU_CURSOR_MODEL_ALPHA/BETA/GAMMA` (defaults `composer-2.5`, `grok-4.6`, `gpt-5.1`; override to catalog ids if list differs). Live path hard-fails if `CURSOR_API_KEY` or `CURSOR_SEARCH_MCP_URL` missing.
- Scout publish: mint HS256 JWT matching `_issue` (`sub`, `op=True`, `exp`). No new auth route. 401 if operator `User` row missing (fail closed).
- Facts: JSON column on [`LiveScore`](backend/app/models.py). Box-score POST stays primary-only. Wildcards GET `score=null`; parent `facts` on detail. Scheduled 0-0 still 400. Missing requested numbers fail closed.
- Rails unchanged: [`Coordinator.run`](oracles/consensus/coordinator.py) does not `submitConsensus`. [`scripts/e2e_local.py`](scripts/e2e_local.py) stays MockSearch. Do not delete Exchange. T001–T003, T008–T010 stay open. T004–T007 stay done. T005 retarget (Cursor, not vendor LLMs). T011 done this slice.
- **This slice deploys an oracle Cloud Run Job** (`overunder-oracle`) plus a 15-minute Cloud Scheduler tick. API/web still from existing Dockerfiles. Exclude `.secrets/` and `mobile/assets/deployments/84532.json`.
- Job loop: `GET /api/v1/markets`, sports primaries only, skip recently updated rows (e.g. `updated_at` within 10 minutes unless `in_progress`), **cap N markets per tick** (default 5 via `OU_SCOUT_MAX_MARKETS`) so three cloud agents per market cannot blow the 30-minute task timeout. Resolution coordinator is **not** auto-submitted from the job.

## Pointers

- [`oracles/agents/alpha.py`](oracles/agents/alpha.py) / [`beta.py`](oracles/agents/beta.py) / [`gamma.py`](oracles/agents/gamma.py) — delete vendor search+infer
- [`oracles/agents/base.py`](oracles/agents/base.py) — Attestation, MockSearch, mock gate stay
- [`oracles/scores/scout.py`](oracles/scores/scout.py) — ScoreReport extract, no auto-POST yet
- [`oracles/wildcard/generator.py`](oracles/wildcard/generator.py) — fumble / first-score / total templates
- [`backend/app/auth/router.py`](backend/app/auth/router.py) L31–61 — `_issue` / `require_operator`
- [`backend/app/markets/router.py`](backend/app/markets/router.py) L84, L42–58, L138–188 — list, LiveScore, POST score
- [`backend/app/models.py`](backend/app/models.py) L106–115 — LiveScore columns
- [`web/src/features/markets/MarketInfo.tsx`](web/src/features/markets/MarketInfo.tsx) — one facts line
- [`web/src/features/markets/MatchupHero.tsx`](web/src/features/markets/MatchupHero.tsx) — box score only
- [`.github/workflows/deploy-gcp.yml`](.github/workflows/deploy-gcp.yml) L84–137 — extend with oracle image/job
- [`docs/TODOS.md`](docs/TODOS.md) T005 / T011
- [`oracles/requirements.txt`](oracles/requirements.txt) — drop vendor SDKs; add `cursor-sdk`, `pyjwt`

## Micro-steps (execute after approval)

1. **Write** `.cursor/JIT_PLAN.md`, then add [`oracles/agents/cursor_runtime.py`](oracles/agents/cursor_runtime.py): `require_live_env`, `model_id`, `prompt_json` with lazy import; local vs cloud `AgentOptions`; HTTP MCP; local `disallowed_tools=["shell"]` only. Update [`oracles/requirements.txt`](oracles/requirements.txt).
2. **Retarget** alpha/beta/gamma: injectable `search=` for tests; mock heuristic infer unchanged; live path `prompt_json` + evidence URLs subset of `search_hits`.
3. **Scout fields**: extend `ScoreReport` with `facts`; `requested_facts(question)` from vs-box / fumble / first-score / total; fail closed on missing numbers; never invent scheduled 0-0.
4. **Auto-POST**: new [`oracles/scores/publish.py`](oracles/scores/publish.py) mints operator JWT and POSTs. Coordinator publishes only on 3/3.
5. **Job entry**: new [`oracles/scores/job.py`](oracles/scores/job.py) lists markets from `OU_API_URL`, filters sports primaries, applies freshness + max-markets cap, runs `ScoreCoordinator`, publishes. CMD for Docker: `python -m scores.job`.
6. **Persist facts**: `LiveScore.facts` JSON; idempotent `ADD COLUMN` after `create_all`; POST upsert replace; GET primary `score.facts`; GET wildcard parent facts on `MarketDetail.facts`. Tests in [`backend/tests/test_live_score.py`](backend/tests/test_live_score.py).
7. **Web**: extend `LiveScore` type; MatchupHero unchanged; MarketInfo one muted facts line; MarketDetail passes facts.
8. **Oracle image + deploy**: add [`oracles/Dockerfile`](oracles/Dockerfile) (python 3.11-slim, copy `oracles/`). Extend [`deploy-gcp.yml`](.github/workflows/deploy-gcp.yml): preflight `OU_CURSOR_API_KEY` and `OU_CURSOR_SEARCH_MCP_URL`; build/push oracle image; `gcloud run jobs deploy overunder-oracle` after API URL is known (`OU_API_URL`, `OU_CURSOR_RUNTIME=cloud`, secrets for Cursor key, MCP URL, `JWT_SECRET`, `OPERATOR_PRIVATE_KEY`); task timeout 30m; create/update Cloud Scheduler `*/15 * * * *` to run the job. Document secret names in a short runbook section (create secrets if missing — deploy fails closed, does not invent keys).
9. **Tests**: mock never imports `cursor_sdk`; hard-fail on missing Cursor env (not Tavily); live smoke `skipif` no key; e2e still MockSearch; job unit-tests with fake HTTP list + fake publish (no live Cursor).
10. **Docs**: ADR-0008 Cursor-runtime oracles (local laptop vs cloud job; MCP is remote HTTP proxied on cloud). Retarget T005; T011 done. Update [`docs/architecture/oracles.md`](docs/architecture/oracles.md), [`.env.example`](.env.example), JIT_INDEX. No ERC-4337/JWKS/uniform-LVR shipped claims.
11. **Ship**: branch from `feat/mvp-docs`, commit this slice only, PR to `feat/mvp-docs`, dispatch `deploy-gcp.yml`. Parent executes this step after review.

## Acceptance

- No vendor SDK/search keys required in `oracles/**/*.py`.
- Mock pytest green; `cursor_sdk` not in `sys.modules` on mock path.
- 3/3 scout → POST with minted JWT; disagree → no POST; no new auth route.
- Evidence URLs ⊆ hits; coordinator still no `submitConsensus`.
- Cloud Run Job uses `cloud=CloudAgentOptions(repos=[])` + HTTP MCP; API/web images still do not contain Cursor.
- Deploy workflow preflights Cursor secrets; Scheduler ticks the job; MCP is not a GCP service.
- T005 retargeted done; T011 done; T001–T003 and T008–T010 open; Exchange remains.
- PR against `feat/mvp-docs`; deploy workflow dispatched (parent after review).

## Stage 4 review (2026-09-20)

Implementation acceptance PASS. PR/deploy deferred to parent (review must not ship).

- Oracles: `contracts\.venv\Scripts\python.exe -m pytest tests -q` → 32 passed, 1 skipped (`test_live_alpha_agent_smoke`).
- Backend: `contracts\.venv\Scripts\python.exe -m pytest tests/test_live_score.py -q` → 12 passed.
- Mock path does not import `cursor_sdk` (`find_spec` is None in the venv).
- Level-1: `Coordinator` module docstring no longer claims `submitConsensus`.
