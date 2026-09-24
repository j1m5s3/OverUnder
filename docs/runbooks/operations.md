---
title: Production operations runbook
status: SHIPPED
area: cross
summary: Base Sepolia / GCP operations — deploy order for the AMM + Factory v2 redeploy, CI, secrets, repo vars and oracle tunables, market audit and overdue-market handling, archiving orphans, fallback and research cooldown knobs, Cursor quota, relayer enablement, CDP Portal paymaster allowlist, readiness-deadline handling.
last_verified: 2026-09-23
pointers:
  - "[.github/workflows/deploy-contracts.yml : L1-48]"
  - "[.github/workflows/deploy-contracts.yml : L141-246]"
  - "[.github/workflows/deploy-gcp.yml : L26-31]"
  - "[.github/workflows/deploy-gcp.yml : L165-183]"
  - "[.github/workflows/deploy-gcp.yml : L221-317]"
  - "[.github/workflows/deploy-gcp.yml : L409-420]"
  - "[.github/workflows/deploy-gcp.yml : L492-510]"
  - "[.github/workflows/deploy-gcp.yml : L571-605]"
  - "[.github/workflows/ci.yml : L17-128]"
  - "[infra/gcp/deploy_wait.sh : L1-35]"
  - "[contracts/script/deploy_ci.py : L62-81]"
  - "[contracts/script/deploy_ci.py : L307-334]"
  - "[contracts/script/deploy_ci.py : L436-449]"
  - "[contracts/script/deploy_v2.py : L166-256]"
  - "[scripts/audit_markets.py : L72-138]"
  - "[scripts/audit_markets.py : L1372-1409]"
  - "[scripts/sync_mobile_deployments.py : L1-20]"
  - "[backend/app/markets/router.py : L515-531]"
  - "[backend/app/markets/router.py : L552-582]"
  - "[backend/app/relayer/router.py : L55-106]"
  - "[backend/app/relayer/queue.py : L30-38]"
  - "[backend/app/aa/router.py : L18-24]"
  - "[backend/app/aa/router.py : L75-104]"
  - "[oracles/resolve/fallback.py : L1-39]"
  - "[oracles/resolve/cooldown.py : L1-28]"
  - "[oracles/agents/cursor_runtime.py : L48-86]"
  - "[oracles/budget.py : L1-29]"
  - "[oracles/tick_lease.py : L1-33]"
  - "[oracles/listing/questions.py : L1-26]"
  - "[oracles/requirements.txt : L4]"
---

# Production operations

Scope: Base Sepolia (chain 84532), GCP project `overunder-509107`, region `us-central1`, repo `j1m5s3/OverUnder`. The full reference for secrets, IAM, error lines and env vars is [infra/gcp/README.md](../../infra/gcp/README.md). Local work is in [local-dev.md](local-dev.md). This file gives the order of operations and the knobs.

Never print, echo or commit a secret value. Secret payloads go only into pipes (`printf %s … | gcloud … --data-file=-`). No contract addresses are recorded here: take them from the workflow run summary.

## Deploy order (AMM + Factory v2 redeploy, then the app)

This is an ops procedure that runs after the branch merges. It is not done until the run summaries say so.

1. Merge the PR into `main` once CI is green (`ci.yml`, below).
2. Run `deploy-contracts.yml` with `target=verify`. It is read-only and every row must say "yes" [.github/workflows/deploy-contracts.yml : L8-48].
3. Run `target=v2` without `broadcast`. This simulates on a `boa.fork` and sends nothing.
4. Run `target=v2` with `broadcast=true`. The steps in order:
   - The contracts test suite runs first as a gate.
   - The workflow pauses `overunder-oracle-tick` and waits up to 20 min for running `overunder-oracle` executions [.github/workflows/deploy-contracts.yml : L141-180].
   - `deploy_ci.py` refuses to send while the operator has pending transactions. It also refuses if the repo vars already point at a v2 pair or `oracle.factory() != FACTORY_ADDRESS`. The operator needs at least 0.005 ETH [contracts/script/deploy_ci.py : L307-334].
   - `migrate_v2` deploys AMM v2, then Factory v2. It imports the legacy cids the old factory knows and cuts the shared oracle over [contracts/script/deploy_v2.py : L166-256].
   - After success the scheduler stays paused. It is resumed automatically only if the run fails.
5. Copy the `gh variable set` lines from the run summary. The `GITHUB_TOKEN` cannot write repo vars. The keys come from [contracts/script/deploy_ci.py : L62-81].

```bash
gh variable set AMM_ADDRESS --body <MarketAMM from summary> -R j1m5s3/OverUnder
gh variable set FACTORY_ADDRESS --body <MarketFactory from summary> -R j1m5s3/OverUnder
gh variable set INDEXER_START_BLOCK --body <deployBlock from summary> -R j1m5s3/OverUnder
```

6. Sync the deployment files from the uploaded artifact `contracts-84532-v2-broadcast-<run>`. The summary prints these commands with the run id filled in [contracts/script/deploy_ci.py : L436-449].
   - `mobile/assets/deployments/84532.json` is generated. Never edit it by hand.
   - `contracts/deployments/84532.json` is gitignored. Merge `MarketAMM`, `MarketFactory`, `MarketAMMLegacy`, `MarketFactoryLegacy` and `deployBlock` into it by hand, because the upload omits `SimpleAccount`, `treasury` and `canonicalUSDC`.

```bash
gh run download <run> -n contracts-84532-v2-broadcast-<run> -D /tmp/ou-84532-v2 -R j1m5s3/OverUnder
python scripts/sync_mobile_deployments.py 84532 --source /tmp/ou-84532-v2/84532.json
python scripts/sync_mobile_deployments.py 84532 --check
```

7. Update the CDP Portal paymaster allowlist (see [below](#cdp-portal-paymaster-allowlist)).
8. Run `deploy-gcp.yml` (`gh workflow run deploy-gcp.yml --ref main -R j1m5s3/OverUnder`). It deploys the API, web, the oracle job and the scheduler.
9. Resume the scheduler:

```bash
gcloud scheduler jobs resume overunder-oracle-tick --location=us-central1 --project=overunder-509107
```

10. Re-run `target=verify`, then [audit](#audit-and-close-overdue-markets).

Migration caveats:

- Between step 4 and the end of step 8 the running API still points at the old factory, so operator listings fail and retry harmlessly on the next tick. Avoid operator actions in the API (listing confirms, creates) during step 4.
- Imported legacy markets keep their pools on `MarketAMMLegacy`. The API, indexer, `/aa/cdp-send`, web and mobile only know `AMM_ADDRESS`, so a legacy market that is still open cannot be quoted or traded from the app after step 8. It still resolves and redeems normally, because the CTF and ConsensusOracle are shared. Check the audit for open legacy markets before broadcasting.
- If a migration fails part-way, read the summary and the uploaded JSON (`migrationFailed`, `completedSteps`, `orphans`). Do not re-run v2 blindly once `oracle.setFactory` is listed; see [infra/gcp/README.md](../../infra/gcp/README.md) (Contract redeploys).
- If v2 went out with `listing_cooldown=0`, the operator can call `factory.setListingConfig(minSeed, fee, FeeVault, 3600, 7776000, 3600)`.

## CI

`ci.yml` runs on every pull request and every push to `main`, with no secrets and no cloud access [.github/workflows/ci.yml : L17-128]:

- contracts: pytest
- backend: pytest; fails if a test is skipped for a missing dependency
- oracles: pytest
- scripts: `unittest discover -s scripts/tests`; same missing-dependency rule
- web: `npm ci`, `npm run build`, `npm test`

There is no Flutter job; run `flutter test` locally ([local-dev.md](local-dev.md)).

## Secrets

The maps at [.github/workflows/deploy-gcp.yml : L26-31] feed both the preflight and `--set-secrets`. Required secrets that fail a rule stop the deploy. Optional secrets are left out of `--set-secrets` when they are missing or invalid.

- Required, API: `OU_JWT_SECRET`, `OU_DATABASE_URL`, `OU_ANVIL_RPC_URL`, `OU_CDP_API_KEY_ID`, `OU_CDP_API_KEY_SECRET`, `OU_OPERATOR_PRIVATE_KEY`.
- Required, oracle job: `OU_CURSOR_API_KEY`, `OU_CURSOR_SEARCH_MCP_URL`, `OU_JWT_SECRET`, `OU_OPERATOR_PRIVATE_KEY`, `OU_AGENT_ALPHA_KEY` / `_BETA_KEY` / `_GAMMA_KEY`, `OU_ANVIL_RPC_URL`.
- Optional: `OU_MOONPAY_SECRET` and `OU_RELAYER_PRIVATE_KEY`. The relayer key becomes required when `RELAYER_ENABLED=true` [.github/workflows/deploy-gcp.yml : L165-183].
- Optional: `OU_QUESTION_ID_KEY` (oracle job only). It makes listing question ids an HMAC under this key, so nobody can precompute a condition id from the public schedule and squat it [oracles/listing/questions.py : L1-26].
  - Set it once and keep it. Changing or removing it changes the ids of games that are not listed yet.
  - Create it with one version:

```bash
gcloud secrets create OU_QUESTION_ID_KEY --project=overunder-509107
printf %s '<32+ random bytes, one line>' | gcloud secrets versions add OU_QUESTION_ID_KEY --data-file=- --project=overunder-509107
```

Run secret commands from Git Bash. Piping from PowerShell appends `\r\n`, which the preflight rejects.

## Repo variables and oracle tunables

- Addresses: `USDC_ADDRESS`, `CTF_ADDRESS`, `FACTORY_ADDRESS`, `EXCHANGE_ADDRESS`, `AMM_ADDRESS`, `ORACLE_ADDRESS`, `FEE_VAULT_ADDRESS`, `OU_TOKEN_ADDRESS`, plus the leftover `PAYMASTER_ADDRESS` / `ENTRYPOINT_ADDRESS` / `ACCOUNT_FACTORY_ADDRESS`.
- `INDEXER_START_BLOCK` (default `0`): a floor for the indexer; a checkpoint behind it fast-forwards.
- `TRADING_HALT_AT_CLOSE` (default `true`) goes to the API and to the web build arg. At `closeTime`:
  - AMM quotes return 409, and `/aa/cdp-send` rejects AMM trades.
  - The web and mobile UIs disable trading.
  - The CLOB stops: `POST /orders` returns 409 and nothing new is matched.
  - MarketAMM v2 also enforces this on chain (`closeGate`, default on).
- `RELAYER_ENABLED` / `RELAYER_WORKER_ENABLED` (default `false`); see [Relayer enablement](#relayer-enablement-checklist).

Oracle job env is built by `Resolve oracle job settings`, which runs before any build and fails on a bad value [.github/workflows/deploy-gcp.yml : L221-317]:

- Always passed: `CHAIN_ID=84532`, `ORACLE_ADDRESS`, `OU_CURSOR_RUNTIME` (default `local`), `OU_FALLBACK_POLICY` (default `attest`) and `OU_TICK_BUDGET_SECONDS` (default `780`).
- Passed only when the repo var is set, otherwise the code default applies:
  - `OU_SEED_USDC`
  - `OU_SCOUT_*`
  - `OU_RESOLVE_MAX_MARKETS`
  - `OU_RESEARCH_RETRY_SECONDS`
  - `OU_GENERAL_RESOLVE_*`
  - `OU_LISTING_STALE_GRACE_SECONDS`
  - `OU_TICK_SINGLE_FLIGHT`
  - `OU_CURSOR_MODEL_*`

  Defaults are in the tunables table in [infra/gcp/README.md](../../infra/gcp/README.md).
- Task timeout is `OU_TICK_BUDGET_SECONDS` + 60 s, which is 840 s by default. Budgets above 780 are refused, so a tick ends before the next `*/15` run [.github/workflows/deploy-gcp.yml : L492-510], [oracles/budget.py : L1-29].
- `--set-env-vars` replaces the whole env list on the API and the job, so a console edit is dropped by the next deploy. Change the repo var or the workflow instead.
- Single-flight: a tick that finds an older running execution exits 0 with `{"skipped": "running"}`. `summary.lease.error` means the guard could not list executions (IAM) and ran anyway [oracles/tick_lease.py : L1-33].

## Audit and close overdue markets

`scripts/audit_markets.py` is read-only and uses only the standard library: HTTP GETs plus `eth_chainId` / `eth_call`. It prints the RPC as `scheme://host` only. Exit codes: 0 = no findings, 1 = findings, 2 = usage error or API/RPC unreachable.

```bash
python scripts/audit_markets.py --api "$OU_API_URL" --rpc "$OU_RPC_URL" --oracle "$ORACLE_ADDRESS"
python scripts/audit_markets.py --api "$OU_API_URL" --rpc "$OU_RPC_URL" --oracle "$ORACLE_ADDRESS" --json
```

Flags [scripts/audit_markets.py : L1372-1409]:

- `--ctf` / `--factory` (defaults read from the oracle)
- `--now`
- `--live-grace-hours` (6)
- `--general-delay-seconds` (86400) and `--general-gated-delay-seconds` (3600); they mirror `OU_GENERAL_RESOLVE_*`
- `--no-general`: audit a job without the resolve_general stage
- `--ignore-manual`
- `--strict`: warnings count as findings
- `--timeout`

Buckets, most urgent first, with the script's hint [scripts/audit_markets.py : L72-138]:

| Bucket | Meaning | Action |
|---|---|---|
| `not_registered` | Created on a different ConsensusOracle (closeTime 0 here); can never resolve on the configured oracle | [Archive](#archive-orphaned-markets) |
| `mismatch` | DB and chain disagree (resolved, payouts, closeTime, marketExists) | Follow the hint. Chain-resolved markets are mirrored by the next tick; DB-only resolution goes through the repair path |
| `overdue_final_unresolved` | Final score posted, not resolved on chain | Execute the job (below). A tie or team-label mismatch gives no outcome: operator `resolveArbitrated` after closeTime + 24 h |
| `overdue_general_unresolved` | General resolver past its delay | Read the `resolve_general` stage log (research split, low confidence, cap, Cursor quota), then execute the job |
| `overdue_no_final` | No final score | Check the scores stage (Cursor quota) or post the score through the operator API, then execute the job. `cancelled` has no payout path (OU-T014) |
| `manual` | Only with `--no-general` | Operator `resolveArbitrated` after closeTime + 24 h |
| `live`, `awaiting_general`, `open`, `resolved` | On a path | none |

Other flags:

- `fallback_window`: more than 24 h past close; shows the on-chain attestation count.
- `cancelled`: see OU-T014.
- `chain_error`: counts as a finding.
- Warnings (findings only under `--strict`): `early_final`, `unscheduled`, `detail_missing`, `detail_error`.

Execute one tick and wait:

```bash
gcloud run jobs execute overunder-oracle --region=us-central1 --project=overunder-509107 --wait
```

Per-execution overrides raise the caps for one run only; the job's saved env does not change:

```bash
gcloud run jobs execute overunder-oracle --region=us-central1 --project=overunder-509107 --wait \
  --update-env-vars=OU_RESOLVE_MAX_MARKETS=6,OU_GENERAL_RESOLVE_MAX_MARKETS=4,OU_SCOUT_MAX_MARKETS=8
```

- Start it right after a scheduled tick finishes. If an older execution is still running, the single-flight guard skips the newer one.
- Add `OU_RESEARCH_RETRY_SECONDS=0` to bypass the research cooldown for that run. Every cap spends Cursor runs and operator gas.
- Re-audit afterwards. The job exits 1 when any stage reports `ok: false`.

Known state on 2026-09-23 (before the v2 redeploy):

- 3 legacy markets from an older deployment are `not_registered` on the configured oracle: `0xaff65685…` (smoke), `0x5daf4afb…` (wildcard child) and `0x18bbc46a…`. Archive them.
- `0x24c901…39c2` is `overdue_final_unresolved` (final 31–10) and is awaiting research. It needs working Cursor agents (see [Cursor quota](#cursor-quota-and-runtime-mode)). The ADR-0002 attest fallback also needs agent research that matches the score.

## Archive orphaned markets

`POST /api/v1/markets/{condition_id}/archive` is operator-only and DB-only (no transaction) [backend/app/markets/router.py : L552-582]:

- It hides the row: `paused=true`, and `GET /api/v1/markets` leaves out paused rows.
- It only works when the configured ConsensusOracle has no closeTime for the cid.
- It is idempotent.

```bash
curl -fsS -X POST -H "Authorization: Bearer $OPERATOR_JWT" "$OU_API_URL/api/v1/markets/<condition_id>/archive"
```

- Get `OPERATOR_JWT` by signing in with the operator wallet (`GET /api/v1/auth/nonce/{address}` then `POST /api/v1/auth/siwe`). Keep it out of shell history.
- Responses:
  - 200: archived
  - 404: unknown cid
  - 409: the market is registered here; pause it on the factory instead with `POST /api/v1/markets/{condition_id}/pause`, which sends an operator tx [backend/app/markets/router.py : L515-531]
  - 503: chain unavailable
- User listings whose `listing` is not `confirmed` are hidden from `GET /markets` but stay tradable on chain. Review them with the operator-only `GET /api/v1/markets/listing/review`, then pause or arbitrate them.

## Fallback policy (ADR-0002 24 h window)

`OU_FALLBACK_POLICY` [oracles/resolve/fallback.py : L1-39]:

- `attest` (default): after closeTime + 24 h without a unanimous `submitConsensus`, each agent whose research matches the outcome basis calls `submitAttestation` (the operator relays it). The basis is the 3/3 final score for sports, or the confident research majority for general markets. Then the permissionless `resolveFallback` runs once its preflight passes.
- `arbitrate`: as `attest`, plus the operator `resolveArbitrated` when there is no on-chain agent majority or votes force arbitration.
- `manual`: never sends.

Related:

- `OU_GENERAL_RESOLVE_MIN_CONFIDENCE` (0.8): every agent must reach it for general-market consensus, and the fallback counts only agents at or above it.
- The 24 h window is `ConsensusOracle.WINDOW` on chain; it is not configurable.
- Gas: each overdue market can cost up to 3 `submitAttestation` plus 1 `resolveFallback` or `resolveArbitrated`, all paid by the operator EOA. Keep it funded.

## Research cooldown

When a research attempt does not submit, it is stored through the operator-only `POST /api/v1/oracle/attest` as a `kind: "research"` row. Before researching a market again, both resolvers read `attestations[].createdAt` from `GET /api/v1/oracle/{cid}/status`. They skip the market while its newest row is younger than `OU_RESEARCH_RETRY_SECONDS` (default 21600, 6 h) [oracles/resolve/cooldown.py : L1-28]. Research rows are not resolution evidence: `unanimous` counts only `resolution` rows. To retry sooner, use a per-execution override (above).

## Cursor quota and runtime mode

- On 2026-09-23 every Cursor agent run failed because the account hit its usage limit, which resets on 2026-10-01.
  - Fix: a user sets a spend limit in the Cursor dashboard. There is no repo change.
  - Until then, every research-dependent step fails per market: score scout, dual-gate research, fallback attestations and the general resolver.
  - Job logs show `cursor agent failed …` or `cursor agent run failed: …`, redacted.
- `OU_CURSOR_RUNTIME=local` is the default in the workflow and `oracles/Dockerfile`. It runs cursor-sdk agents inside the job container with `tools=["mcp"]` (search MCP only), so injected web content cannot reach a shell or the job's env [oracles/agents/cursor_runtime.py : L48-86].
- `cloud` uses Cursor cloud agents with a non-empty `CloudEnvironment`.
- History: the old `cloud` mode passed `CloudAgentOptions(repos=[])`. The SDK dropped it and silently ran local agents with the full toolset next to the key env vars.
  - Fixed: MCP-only local tools, run errors surfaced from the streamed status, and `cursor-sdk==1.0.32` pinned [oracles/requirements.txt : L4].
  - There is no evidence of abuse. Agent keys are immutable in ConsensusOracle, so rotating them means an oracle redeploy, which is the user's call.
- Unverified: whether local mode runs in `python:3.11-slim` without extra packages. Check the first execution's logs after deploy; the fallback is the repo var `OU_CURSOR_RUNTIME=cloud`.

## Relayer enablement checklist

The OU-T003 CLOB relayer is off by default. The key alone never enables it: `relayer_ready` needs `RELAYER_ENABLED`, a parseable key and a configured Exchange [backend/app/relayer/queue.py : L30-38].

1. Create a dedicated relayer EOA, not the operator. Add `OU_RELAYER_PRIVATE_KEY` (32-byte hex, `printf %s`). Emissions distribution also signs with this key.
2. Fund the relayer address with Base Sepolia ETH.
3. Confirm `EXCHANGE_ADDRESS` is set.
4. Choose how jobs get sent:
   - Worker: `RELAYER_WORKER_ENABLED=true` runs a background worker in the API process. A Postgres advisory lock elects one sender across instances.
   - Manual tick: operator `POST /api/v1/relayer/tick` drains one pass. Call it by hand or from a scheduler.
5. `gh variable set RELAYER_ENABLED --body true -R j1m5s3/OverUnder` (plus `RELAYER_WORKER_ENABLED` if you chose the worker), then run `deploy-gcp.yml`. The preflight now requires the key and warns if only the worker flag is set.
6. Verify with operator `GET /api/v1/relayer/status`: `ready: true`, `relayerAddress`, `ethBalanceWei`, job counts, `lastTickAt` [backend/app/relayer/router.py : L55-106].

Limits:

- Makers must be EOAs, because Exchange is `ecrecover`-only (OU-T016).
- `DELETE /orders` cancels off-chain only. Once the signed order has been exposed in `matchOrders` calldata, the response sets `onchainCancelRequired` and returns `cancelOrderArgs` for an on-chain `Exchange.cancelOrder` (maker or operator). There is no automated operator cancel job yet (OU-T016).
- Custom gas caps or other `RELAYER_*` values need an edit to the API step of `deploy-gcp.yml`.

## CDP Portal paymaster allowlist

This is set only in the CDP Portal; there is nothing in the repo. It must match the `/aa/cdp-send` allowlist [backend/app/aa/router.py : L18-24], [backend/app/aa/router.py : L75-104]. After the v2 redeploy, allow:

| Contract | Function (selector) | Constraint |
|---|---|---|
| new MarketAMM | `buyWithUSDC` (`0xa9c98025`), `sellToUSDC` (`0xd4bd65f0`) | — |
| USDC | `approve` (`0x095ea7b3`) | spender = new MarketAMM or new MarketFactory |
| ConditionalTokens | `setApprovalForAll` (`0xa22cb465`) | operator = new MarketAMM |
| new MarketFactory | `createPermissionlessMarket` (`0x4e7d1a32`) | — |

Set the per-operation gas cap to at least 800k: a listing batch measured about 697k execution gas in boa, including the pm-AMM seed.

## Readiness deadline in deploy-gcp

- Cloud Run gives a new revision about 18 min to start its first instance. On 2026-09-22 an instance started about 27 min after revision creation, so the deploy failed although the image was fine.
- `infra/gcp/deploy_wait.sh` wraps `gcloud run deploy` [infra/gcp/deploy_wait.sh : L1-35], [.github/workflows/deploy-gcp.yml : L409-420]:
  - Only on "Resource readiness deadline exceeded" does it keep polling the revision.
  - It redeploys once (`--async`, unique revision suffix) after 15 min.
  - It gives up after the `ready_timeout_minutes` dispatch input (default 45).
- A slow region: re-run with a larger timeout.

```bash
gh workflow run deploy-gcp.yml --ref main -R j1m5s3/OverUnder -f ready_timeout_minutes=90
```

- On any failure, `Diagnose failed deploy (conditions only)` prints service, revision, job, execution and scheduler conditions plus hints. It never prints env values [.github/workflows/deploy-gcp.yml : L571-605].
- The error-line table is in [infra/gcp/README.md](../../infra/gcp/README.md).

## Local Docker Desktop note

Docker Desktop 4.88.1 on this Windows machine crashed at startup on stale AF_UNIX socket files in `%LOCALAPPDATA%\Docker\run`, for example `sailor-ingest.sock`.

- Fix: quit Docker Desktop, delete those files (a reboot may be needed first), then restart it.
- Meanwhile, `scripts\run_stack.cmd` falls back to a Foundry `anvil`. The 2026-09-23 Postgres rehearsal used a native PostgreSQL 16 server instead of the compose `postgres` service.
