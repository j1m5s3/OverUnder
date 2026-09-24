# Lifecycle hardening + Phase 2 completion (T003, T008–T010)

status: ACTIVE · slice: lifecycle-phase2-completion · base: 24fc095 · branch: feat/lifecycle-phase2-completion · created: 2026-09-23 · updated: 2026-09-23

Supersedes the 2026-09-23 00:09 DRAFT "Deploy recovery and market lifecycle hardening" (its steps 1–16 are folded in below). Get `deploy-gcp.yml` green and self-diagnosing, make the `overunder-oracle` job list/resolve reliably (including the ADR-0002 24 h fallback), halt trading at closeTime, ship the uniform-LVR MarketAMM (OU-T008/T009), loosely gated user listing (OU-T010) and the production CLOB relayer (OU-T003), then redeploy AMM + Factory to Base Sepolia through GitHub Actions and deploy the app.

## User decisions (2026-09-23, do not reopen)

- Scope: complete every open TODO; ship via own branch → PR → reviewer agent → merge → deploy. Agents MAY push, open PRs, dispatch the repo's GitHub Actions workflows and change GCP resources with gcloud (project `overunder-509107`). This overrides the DRAFT's "agents never push/dispatch/run gcloud/send tx".
- Still never print, log, echo or commit a secret value; Secret Manager payloads flow only into pipes or process memory.
- Fallback policy (old Q3): `OU_FALLBACK_POLICY=attest` is the default. After closeTime + 24 h without unanimous matching research, agents whose research matches the 3/3 final score `submitAttestation` (operator relays), then permissionless `resolveFallback` (2/3). `arbitrate` (operator `resolveArbitrated` when <2 match or votes force arbitration) and `manual` stay selectable.
- Trading halt (old Q4): on chain in MarketAMM v2 (closeTime gate, default ON, operator toggle) **and** in UI/API (quotes 409, web/mobile disable) behind `TRADING_HALT_AT_CLOSE` (deploy sets true).
- Contracts: targeted redeploy of MarketAMM v2 + MarketFactory v2 on 84532 via a new `deploy-contracts.yml` workflow; CTF, ConsensusOracle, FeeVault, USDC, Exchange, paymaster are reused (existing condition ids keep resolving).

## Live findings that shaped this slice (2026-09-23)

- Last deploy (run 35679662541) failed at API rollout: `Resource readiness deadline exceeded`; instance started ~27 min after revision create, container boots in 1 s → GCP-side scheduling delay, not app code. Fix: deploy_wait orchestration + smoke + diagnostics.
- Every Cursor agent run fails: the Cursor account hit its Pro+ usage limit (resets 2026-10-01). Needs a spend limit (user action in Cursor dashboard). Also `CloudAgentOptions(repos=[])` silently ran LOCAL agents with the full toolset next to key env vars — fixed to `tools=["mcp"]` local mode + error surfacing; `cursor-sdk==1.0.32` pinned.
- Live markets: 3 of 4 listed cids were created on the previous oracle (closeTime 0 on the configured oracle → never resolvable); `0x24c901…39c2` is registered, final 31–10, unresolved ~36 h. Week-2 schedule row 16 (Rams–Giants) is stuck `scheduled`, blocking week-3 listing under the old all-final gate.
- Backend: KYC/MoonPay routes 500 for every authenticated user (`user["address"]` on an ORM `User`); 5 failed + 3 errored tests from stale tests and a missing `conftest.py`.

## Tracks, file ownership and interfaces

Coders edit only files in their track. Shared files (`backend/app/config.py`, `backend/app/models.py`, `backend/app/main.py`) take additive, localized edits; re-read before editing.

- **C Contracts** (C1 AMM then C2 Factory, sequential). `contracts/src/MarketAMM.vy`, `contracts/src/lib/NormalMath.vy`, `contracts/src/MarketFactory.vy`, `contracts/tests/**` (C1 owns `conftest.py`), `contracts/script/deploy.py`, `contracts/script/deploy_v2.py`, `backend/app/abi/MarketAMM.json`, `backend/app/abi/MarketFactory.json`.
  - MarketAMM v2 keeps every existing selector and event; adds `seedPoolFor(bytes32 conditionId, uint256 usdcAmount, address provider)` (factory/operator only; LP credited to `provider`), `removeLiquidity(bytes32,uint256)`, `priceYes(bytes32) -> uint256` (1e18 WAD), `closeGate() -> bool` (default True) + operator `setCloseGate(bool)`, event `LiquidityRemoved`, and appends Pool fields `liquidity, lpFees, closeTime` (indexes 0–3 unchanged). Buys/sells revert `"market closed"` at/after closeTime when the gate is on; quotes stay ungated.
  - MarketFactory v2 keeps the v1 constructor/primary/wildcard/pause ABI and `Market` struct; adds `createPermissionlessMarket(bytes32 salt, uint256 closeTime, string question, bytes32 criteriaHash, uint256 seedUsdc) -> bytes32` (selector `0x4e7d1a32`, marketType 2, qid = keccak(abi.encode(sender, salt))), `userQuestionId`, `userConditionId`, listing config/permissionless/lister admin, `creatorOf/seedOf/criteriaHashOf`, `importLegacyMarkets`. All seeding goes through `amm.seedPoolFor(cid, seed, msg.sender)`.
  - `contracts/script/deploy_v2.py` exposes `migrate_v2(*, deployments: dict, operator: str, legacy_cids: list[str], listing: dict, permissionless: bool, close_gate: bool, progress: dict | None = None, allow_v2_source: bool = False) -> dict` (runs inside an already-configured boa env; returns `{"MarketAMM", "MarketFactory", "MarketAMMLegacy", "MarketFactoryLegacy", "imported", "deployBlock"}`; `progress` records completed steps for partial-failure reports; `allow_v2_source` is for a fresh local 31337 stack only).
- **B1 Backend core.** `backend/app/markets/**`, `backend/app/amm/**`, `backend/app/aa/router.py`, `backend/app/indexer/**`, `backend/app/kyc/router.py`, `backend/app/ramps/router.py`, `backend/tests/conftest.py` (+ the tests it touches), `shared/openapi.json`. Step 12 idempotent create, trading halt (`TRADING_HALT_AT_CLOSE`, `MarketPublic.tradingHaltsAt/tradingOpen/yesPriceMicros`), listing endpoints (`/api/v1/markets/listing/{config,eligibility,prepare,confirm}`, `MarketListing` table), type 2 lists as a primary card, cdp-send allowlist for listing, indexer chunked ranges + `INDEXER_START_BLOCK` + pm-AMM price (`Φ((no-yes)/L)` from `pools(cid)[4]`, CPMM fallback), KYC/ramps fix, test isolation.
- **B2 Relayer (OU-T003).** `backend/app/orderbook/**`, `backend/app/relayer/**`, `backend/app/emissions/router.py`, `backend/app/db.py`; `Order/Trade/RelayJob` models; `RELAYER_*` settings block. EIP-712 verify at the edge, DB queue, worker, nonce manager, EIP-1559 gas policy, preflight, receipts/bumps, operator endpoints. Off by default (`RELAYER_ENABLED=false`).
- **O1 Oracle job.** `oracles/job.py`, `oracles/operator_auth.py`, `oracles/scores/**`, `oracles/listing/**`, `oracles/schedule/**`, `oracles/tests/test_job.py|test_listing.py|test_schedule.py|test_score_scout.py`. Steps 6, 7, 10, 11; tick order scores → resolve → resolve_general → schedule → listing; exit 1 on any stage `ok: false`.
- **O2 Oracle resolve.** `oracles/resolve/**`, `oracles/consensus/**`, `oracles/abi/**`, `oracles/tests/test_resolve*.py|test_consensus.py|test_winner.py`. Steps 8, 9, 13 + `oracles/resolve/general.py` `run(*, http_get=None, coordinator_factory=None, chain=None, publisher=None, now=None, fallback_policy=None) -> dict` for wildcards (type 1), user markets (type 2) and non-sports type 0.
- **I Infra.** `.github/workflows/**`, `infra/**`, `.gitattributes`, `contracts/script/deploy_ci.py`, `contracts/tests/test_deploy_ci.py`, `oracles/Dockerfile`, `web/Dockerfile`, `backend/Dockerfile`. Steps 1, 2, 4 + readiness handling + `deploy-contracts.yml` (targets verify | v2) + new env vars.
- **W Web.** `web/src/**`, `web/tests/**`, `web/package.json`. Trading-closed UI, List-a-market page (factory mode via backend listing endpoints + CDP sponsored batch), live YES price, type-2 criteria/creator, node test script.
- **M Mobile.** `mobile/**` except `mobile/assets/deployments/84532.json`. camelCase parsing, EventCard list, quote params, trading halt, USDC units.
- **S Scripts.** `scripts/audit_markets.py`, `scripts/tests/**`. Read-only audit (step 5).
- **D Docs** (after code). ADR-0011 (pm-AMM v2 + on-chain gate), ADR-0012 (user listing), dated notes on ADR-0002/0004/0007/0008/0009/0010, TODOS, architecture, runbooks (`operations.md`), AGENTS.md, JIT_INDEX. The orchestrator archives this plan after deploy (step 16), not in the docs step.

## Micro-steps

<!-- [ ] pending · [>] active · [x] done · [-] dropped (reason) · [!] blocked -->

1. [x] Cursor runtime: surface run errors, real cloud env or MCP-only local tools, `OU_CURSOR_RUNTIME=local` override, pin `cursor-sdk==1.0.32` (orchestrator) — local mode is `tools=["mcp"]`; cloud needs a non-empty `CloudEnvironment`; SDK status errors raised and redacted.
2. [x] C1 MarketAMM v2 (pm-AMM static, NormalMath module, gate, LP add/remove, fee accumulator) + tests + ABI — v1 ABI kept, additions only; `closeGate` defaults on; seed LP locked until close, `MIN_LP` locked forever (after review); 17,390 B runtime; all measured buys < 150k gas in boa.
3. [x] C2 MarketFactory v2 (user listing, legacy import, seedPoolFor) + `deploy.py` + `deploy_v2.py` + tests + ABI — `createPermissionlessMarket` (`0x4e7d1a32`) with 7 ordered gates; `importLegacyMarkets`; every seed goes through `seedPoolFor(cid, seed, msg.sender)`; 31337 opens listing; about 697k gas per listing incl. seed.
4. [x] B1 backend core (step 12, halt, listing API, indexer, KYC fix, isolation) — `conftest.py` isolation; KYC/ramps 500 fixed; idempotent create; halt (quote 409, cdp-send 409, `MarketPublic.tradingHaltsAt/tradingOpen/yesPriceMicros`); listing endpoints + `MarketListing`; indexer start block, chunks, leader lock, pm-AMM price.
5. [x] B2 relayer (OU-T003) — EIP-712 verify at the edge, DB queue, nonce manager, EIP-1559 bumps, preflight, receipts, rollback, Postgres leader lock, operator status/tick; off by default; emissions route fixed.
6. [x] O1 job steps 6, 7, 10, 11 + resolve_general stage — auth preflight + one SIWE bootstrap; lazy stage imports; exit 1 on any `ok: false`; score-scout tiers; per-game listing isolation with postponed/cancelled/stale-grace as done; per-game 3/3 schedule.
7. [x] O2 resolve steps 8, 9, 13 + general resolver — config guard, not-registered skip, chain mirror, oldest first, `OU_FALLBACK_POLICY=attest` fallback, `resolve/general.py` (3/3 + confidence floor, gated delays).
8. [x] I deploy-gcp hardening, readiness wait, smoke, diagnostics, deploy-contracts, env vars, README — `deploy_wait.sh`, secret preflight, probes, smoke, diagnose; `deploy-contracts.yml` (verify | v2 | core, scheduler pause); `ci.yml`; oracle env only-when-set; task timeout = budget + 60 s.
9. [x] W web halt + listing + price — trading-closed UI and halt timer; `/list` page (CDP sponsored batch, confirm with bounded retries, send guard); live YES price; type-2 criteria/creator; `OrderTicket.tsx` removed; `npm test` 127/127.
10. [x] M mobile parsing + halt — camelCase models, event cards, quote params, 6-decimal units, halt banner/timer, API chain addresses with asset fallback; two pre-existing compile errors fixed; `flutter test` green.
11. [x] S audit script — `scripts/audit_markets.py` (read-only, stdlib) + `scripts/tests`; live run: 3 `not_registered`, `0x24c901…` `overdue_final_unresolved`, exit 1.
12. [x] Integration: all suites, e2e_local, ABI parity, openapi, `npm run build` — anvil rehearsal (migration, listing, relayer), oracle lifecycle rehearsal (8 scenarios), Postgres rehearsal (migrations, leader locks); `shared/openapi.json` regenerated; see the Stage 4 review log.
13. [x] Adversarial review + fixes — 45 findings, 44 confirmed and fixed, plus a cross-area fix round; see the Stage 4 review log.
14. [x] D docs + ADRs + TODOS + JIT archive — ADR-0011/0012, dated ADR notes, TODOS (T003/T008–T010 done; T014–T016 open), architecture, `docs/runbooks/operations.md`, AGENTS.md, JIT_INDEX. The plan archive is deferred until after deploy.
15. [ ] Ship: commit, push, PR, reviewer agent, merge
16. [ ] Ops: deploy-contracts (verify → v2), repo vars, IAM, deploy-gcp, oracle job, audit (procedure: `docs/runbooks/operations.md`)

## Acceptance

- contracts, backend, oracles pytest green; `scripts/e2e_local.py` green; `python -m unittest discover -s scripts/tests` green; `web` `npm run build` + `npm test` green.
- Workflow harness (fake gcloud) passes; a real `deploy-gcp.yml` run is green with smoke checks.
- New AMM + Factory live on 84532, repo vars updated, API/web/job redeployed, audit run and reported.

## Stage 4 review log

- **Review:** 45 findings, 44 confirmed and fixed, plus seeded items carried over from implementation open issues. By area:
  - contracts:
    - dust-pool LP theft
    - dead pool after a full exit
    - free seed withdrawal (fix: seed lock and `MIN_LP`)
  - backend:
    - listing gates re-run on confirm and in the indexer
    - KYC `nan`/negative bypass
    - blocking web3 calls moved off the event loop
    - indexer leader lock and unique price points
    - int64/int4 bounds
    - duplicate-question rule
    - operator-only attest, server-side vote weight, `archive`
  - relayer:
    - nonce-too-low double send
    - dead-job reuse
    - stale `filled` writes
    - CLOB halt at close
    - RPC URL leak in `lastError`
    - cancel and claim races
    - emissions double distribution
    - shared-balance preflight
    - on-chain cancel flag
  - oracles:
    - mid-game wildcard settlement
    - post-close research wording
    - cross-season listing dedupe
    - stale schedule row blocking listing
    - scout starvation
    - overlapping ticks (single-flight lease)
    - question-id squatting (`OU_QUESTION_ID_KEY`)
    - untrusted prompt slot, outcome/confidence range checks
    - research cooldown
  - infra:
    - `deploy_v2` v2 -> v2 re-run guard
    - RPC URL leak and lost progress
    - listing cooldown input
    - operator-key broadcast vs scheduler (pause and wait)
    - fail-closed v1/v2 probes
    - CI cross-package deps
    - relayer key required when enabled
    - fork cache
  - web:
    - status-poll misread as a failed trade
    - fallback copy
    - `OrderTicket.tsx` deleted
  - mobile/scripts:
    - stale 84532 asset (fix: `sync_mobile_deployments.py` + `GET /chain/addresses`)
    - `attestations` parsing
    - web3dart ABI outputs
    - audit archive hint and week-complete parity
- **Rehearsal bugs fixed:**
  - Anvil:
    - fork paths read "safe" instead of "latest"
    - v2 re-run after the repo vars changed deployed a third pair (now refused)
    - partial-migration record written
    - relayer liveness
    - test pollution
  - Oracle lifecycle:
    - chain clock = max(wall, latest block) in resolve, general and `sign_unanimous`
    - `OU_MOCK_SCHEDULE` for mock ticks
    - stage stderr redaction (`oracles/redact.py`)
    - `scores.job` summary redaction
  - Postgres: concurrent-startup `create_all`/ALTER race, fixed by `run_migrations` under an advisory xact lock.
- **Cross-area fix round:**
  - web confirm classification and retries
  - backend: single locked `run_migrations`, `GET /api/v1/chain/addresses`, seed >= `MIN_LP` (422), oracle status `kind` with resolution-only `unanimous`
  - infra: oracle env only when set, task timeout = budget + 60 s (840 s), optional `OU_QUESTION_ID_KEY`, v2 summary points at the mobile sync
- **Last reported results:**
  - backend: 382 passed, 2 skipped (SQLite); 383 passed on Postgres
  - web: 127/127 tests plus `tsc` and build
  - contracts deploy tests: 47 passed
  - workflow harness: 75/75
  - contracts, oracles and scripts suites green in their last runs

## Open risks

- The Cursor account is out of quota until 2026-10-01 unless the user sets a spend limit. Until then research-dependent work cannot run: score scout, dual gate, fallback attestations and the general resolver. `0x24c901…39c2` stays overdue until research runs.
- Static pm-AMM LP value: without the gate, a static pm-AMM keeps less LP value than a CPMM under Gaussian drift. The on-chain gate (default on) mitigates this. Dynamic L_t is OU-T015.
- Legacy markets after v2: imported legacy markets keep their pools on `MarketAMMLegacy`. The API, indexer, cdp-send, web and mobile know only `AMM_ADDRESS`, so a legacy market that is still open cannot be traded from the app after the migration. It still resolves and redeems. Check the audit for open legacy markets before broadcasting.
- Security: production agents previously ran with shell/file tools beside the key env vars. That is fixed, and there is no evidence of abuse. Agent keys are immutable in ConsensusOracle, so rotation implies an oracle redeploy (the user's call).
- Manual ops that are not done yet:
  - CDP Portal paymaster allowlist (new AMM/Factory, `createPermissionlessMarket` `0x4e7d1a32`, USDC approve to the factory, gas cap >= 800k)
  - repo vars after v2
  - `roles/cloudscheduler.admin` for github-deploy
  - `OU_QUESTION_ID_KEY` (optional; set once)
- Unverified until the first execution: whether cursor-sdk local mode runs in `python:3.11-slim`. The fallback is the repo var `OU_CURSOR_RUNTIME=cloud`.
- Registered gaps: OU-T014 has no invalid/refund outcome for cancelled or ambiguous markets (the audit flags them), and OU-T016 means the CLOB takes only EOA makers and has no automated on-chain cancel job for exposed orders.
- The relayer is off in production. Its Postgres race variants are covered by SQLite-interleaved tests plus one Postgres rehearsal.

## Changelog

- 2026-09-23 CREATED (ACTIVE) — replaces the 00:09 DRAFT after user decisions on fallback, halt and redeploy; 8 read-only mapping reports under the session scratchpad `maps/`.
- 2026-09-23 CODE-COMPLETE — steps 1–14 done (implementation, integration rehearsals, adversarial review 44/45 confirmed and fixed, cross-area fixes, docs); steps 15 (ship) and 16 (ops) pending; archive after deploy.
