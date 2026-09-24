# GCP Deployment — OverUnder

This directory documents the GCP Cloud Run deployment for OverUnder prediction markets (Base Sepolia, chain id 84532).

| File | What it is |
|---|---|
| `.github/workflows/deploy-gcp.yml` | App deploy: API, web, oracle Cloud Run Job and its scheduler (manual dispatch) |
| `.github/workflows/deploy-contracts.yml` | Contract checks and redeploys on Base Sepolia (manual dispatch) |
| `.github/workflows/ci.yml` | Tests on every pull request and push to `main` (no secrets, no cloud) |
| `infra/gcp/deploy_wait.sh` | `gcloud run deploy` wrapper that survives "Resource readiness deadline exceeded" |
| `infra/gcp/cloudrun.env.example` | Every env var the API and the oracle job read, with defaults |
| `contracts/script/deploy_ci.py` | Python behind `deploy-contracts.yml` |
| `docs/runbooks/operations.md` | Ops procedures: deploy order, market audit, overdue and orphaned markets, relayer enablement, CDP Portal allowlist |

## Pre-provisioned Infrastructure

The following resources are already created in GCP — **do not recreate via terraform**:

- **Project**: `overunder-509107`
- **Region**: `us-central1`
- **Artifact Registry**: `us-central1-docker.pkg.dev/overunder-509107/overunder`
- **Deploy Service Account**: `github-deploy@overunder-509107.iam.gserviceaccount.com`
- **Workload Identity Federation**:
  - Pool: `github-pool`
  - Provider: `github`
  - Bound repository: `j1m5s3/OverUnder`
- **Cloud Run Services**:
  - `overunder-api` (FastAPI backend)
  - `overunder-web` (Next.js frontend)
- **Cloud Run Job**: `overunder-oracle` (oracle tick), run every 15 minutes by the Cloud Scheduler job `overunder-oracle-tick`
- **Cloud SQL**: PostgreSQL instance `overunder-pg`

### Secret Manager Secrets

Every secret must have a `latest` version whose payload passes the preflight rules below. The deploy workflow checks them before it builds anything (step `Preflight Secret Manager secrets`).

| Secret | Env name | Consumer | Required | Format rule |
|---|---|---|---|---|
| `OU_JWT_SECRET` | `JWT_SECRET` | API, oracle job | yes | one line, no surrounding whitespace (32+ bytes recommended) |
| `OU_DATABASE_URL` | `DATABASE_URL` | API | yes | starts with `postgresql+asyncpg://`, never `sqlite` |
| `OU_ANVIL_RPC_URL` | `ANVIL_RPC_URL` | API, oracle job, deploy-contracts | yes | starts with `http://` or `https://` (Base Sepolia) |
| `OU_CDP_API_KEY_ID` | `CDP_API_KEY_ID` | API | yes | one line |
| `OU_CDP_API_KEY_SECRET` | `CDP_API_KEY_SECRET` | API | yes | non-empty; may be a multi-line PEM |
| `OU_OPERATOR_PRIVATE_KEY` | `OPERATOR_PRIVATE_KEY` | API, oracle job, deploy-contracts | yes | 32-byte hex, optional `0x` |
| `OU_CURSOR_API_KEY` | `CURSOR_API_KEY` | oracle job | yes | one line |
| `OU_CURSOR_SEARCH_MCP_URL` | `CURSOR_SEARCH_MCP_URL` | oracle job | yes | starts with `http://` or `https://` |
| `OU_AGENT_ALPHA_KEY` / `_BETA_KEY` / `_GAMMA_KEY` | `AGENT_*_KEY` | oracle job, deploy-contracts (verify, core) | yes | 32-byte hex, optional `0x` |
| `OU_MOONPAY_SECRET` | `MOONPAY_SECRET` | API | optional | one line; skipped (not mapped) when missing or invalid |
| `OU_RELAYER_PRIVATE_KEY` | `RELAYER_PRIVATE_KEY` | API (CLOB relayer, emissions distribution) | required when `RELAYER_ENABLED=true`, otherwise optional | 32-byte hex; with the relayer off it is skipped when missing or invalid |
| `OU_QUESTION_ID_KEY` | `OU_QUESTION_ID_KEY` | oracle job (listing) | optional | one line, any value (32+ random bytes recommended); skipped when missing or invalid |

"One line" means no newline, no leading or trailing whitespace and no CR. Required secrets that fail stop the deploy; optional ones are left out of `--set-secrets`, so the service still starts. An optional secret that exists but fails a rule also prints a `::warning::` with the fix; a missing one is skipped quietly. `RELAYER_ENABLED=true` (also `1`, `yes`, `on`) makes `OU_RELAYER_PRIVATE_KEY` required: without it every `POST /api/v1/orders` would return 503 while the deploy stayed green.

`OU_QUESTION_ID_KEY` makes the operator's listing question ids an HMAC-SHA256 under this key (`oracles/listing/questions.py`), so nobody can precompute a condition id from the public schedule and `prepareCondition` it first (a squatted listing fails with HTTP 409). Without it the ids stay the legacy public sha256. Set it once and keep it: changing or removing it changes the ids of games not listed yet (games already listed are matched by condition id or question and kickoff, so they are not listed twice). The oracles redact it from logs; never print it.

## Deployment

### deploy-gcp.yml (app)

Manual dispatch only, from `main`:

```bash
gh workflow run deploy-gcp.yml --ref main -R j1m5s3/OverUnder                       # default: wait up to 45 min on a slow revision
gh workflow run deploy-gcp.yml --ref main -R j1m5s3/OverUnder -f ready_timeout_minutes=90
```

Or GitHub → Actions → "Deploy to GCP Cloud Run" → Run workflow. Only one deploy runs at a time (`concurrency: deploy-gcp`); the job times out after 150 minutes.

The workflow:
1. Authenticates with Workload Identity Federation (no JSON keys).
2. Preflights every Secret Manager secret against the rules above and builds the `--set-secrets` maps (payloads only ever flow into `wc`/`grep -q`; nothing is echoed). Then `Resolve oracle job settings` validates the oracle repo variables and builds the job's env list and task timeout, so a bad variable fails before anything is built.
3. Probes access to Cloud Run Jobs and Cloud Scheduler, and warns if the runtime service account cannot read the mapped secrets or list the oracle job's executions (tick single-flight guard).
4. Builds and pushes `backend/Dockerfile`, deploys `overunder-api` through `infra/gcp/deploy_wait.sh`, captures the URL and smoke-tests `GET /health` (must return `{"ok":true}`) and `GET /api/v1/markets` (warn only).
5. Builds `web/Dockerfile` with the **real API URL** and the `NEXT_PUBLIC_*` build args, deploys `overunder-web` through `deploy_wait.sh` and requires HTTP 200 on `/`.
6. Builds `oracles/Dockerfile`, deploys the `overunder-oracle` job (task timeout = `OU_TICK_BUDGET_SECONDS` + 60 s, 840 s by default, so a tick always ends before the next one starts) and creates or updates the `overunder-oracle-tick` scheduler (`*/15 * * * *`).
7. Writes the URLs and the effective settings to the run summary.
8. On any failure, `Diagnose failed deploy (conditions only)` prints service, revision, job, execution and scheduler conditions plus hints. It never reads logs or env values.

### Required GitHub configuration

**Repository secret** (`/settings/secrets/actions`):
- `GCP_PROJECT_NUMBER` — GCP project number for the WIF provider path (not the project id) **[REQUIRED]**

**Repository variables** (`/settings/variables/actions`). Unset variables fall back to the default shown.

| Variable | Used by | Default | Meaning |
|---|---|---|---|
| `USDC_ADDRESS`, `CTF_ADDRESS`, `FACTORY_ADDRESS`, `EXCHANGE_ADDRESS`, `AMM_ADDRESS`, `ORACLE_ADDRESS`, `FEE_VAULT_ADDRESS`, `OU_TOKEN_ADDRESS` | deploy-gcp (API env, web build args), deploy-contracts | empty | Contract addresses; must match the live Base Sepolia deployment |
| `PAYMASTER_ADDRESS`, `ENTRYPOINT_ADDRESS`, `ACCOUNT_FACTORY_ADDRESS` | deploy-gcp, deploy-contracts | empty | Leftover ERC-4337 contracts (user wallets are CDP, ADR-0010) |
| `CDP_PROJECT_ID` | deploy-gcp (API env, web build arg) | empty | Coinbase CDP project id |
| `COINBASE_ONRAMP_APP_ID` | deploy-gcp | empty | Coinbase Pay app id |
| `INDEXER_START_BLOCK` | deploy-gcp (API) | `0` | Where a new `(MarketFactory, MarketAMM)` address pair's indexer checkpoint starts; set it to the `deployBlock` printed by deploy-contracts before the cutover deploy. An existing checkpoint only fast-forwards to it, never rewinds |
| `TRADING_HALT_AT_CLOSE` | deploy-gcp (API env + web build arg `NEXT_PUBLIC_TRADING_HALT_AT_CLOSE`) | `true` | At `closeTime`: AMM quotes return 409, the UI disables trading, and the CLOB stops too (`POST /orders` 409, no matching, the relayer worker rolls back fills matched after close). MarketAMM v2 also rejects trades on chain (`closeGate`) |
| `RELAYER_ENABLED` | deploy-gcp (API, secret preflight) | `false` | Accept CLOB orders (OU-T003); when true the preflight requires `OU_RELAYER_PRIVATE_KEY` |
| `RELAYER_WORKER_ENABLED` | deploy-gcp (API) | `false` | Run the relayer settlement worker in the API process; has no effect (the preflight warns) unless `RELAYER_ENABLED` is also true. With both true the API deploys with `--no-cpu-throttling --min-instances=1`; turning it off later does not remove them (`gcloud run services update overunder-api --cpu-throttling --min-instances=0 …`) |
| `OU_KEEP_SCHEDULER_PAUSED` | deploy-gcp (scheduler step) | unset | `true` = leave a PAUSED `overunder-oracle-tick` paused; otherwise deploy-gcp resumes it at the end (skipped with a warning while a deploy-contracts run is in progress) |
| `OU_CURSOR_RUNTIME` | deploy-gcp (oracle job, always passed) | `local` | `local` = cursor-sdk agents in the job container restricted to MCP tools; `cloud` = Cursor cloud agents |
| `OU_FALLBACK_POLICY` | deploy-gcp (oracle job, always passed) | `attest` | ADR-0002 24 h fallback: `attest` (matching agents attest, then permissionless `resolveFallback`), `arbitrate` or `manual` |
| `OU_TICK_BUDGET_SECONDS` | deploy-gcp (oracle job, always passed; sets the task timeout) | `780` | Wall-clock budget per tick, 1 to 780; the job's task timeout is this + 60 s |
| `OU_SEED_USDC`, `OU_SCOUT_*`, `OU_RESOLVE_MAX_MARKETS`, `OU_RESEARCH_RETRY_SECONDS`, `OU_RESEARCH_ERROR_RETRY_SECONDS`, `OU_RESEARCH_MIN_SECONDS`, `OU_GENERAL_RESOLVE_*`, `OU_LISTING_STALE_GRACE_SECONDS`, `OU_LISTING_RESERVE_SECONDS`, `OU_STAGE_SHARE_*`, `OU_TICK_SINGLE_FLIGHT`, `OU_CURSOR_MODEL_*` | deploy-gcp (oracle job, passed only when set) | oracles code default | See [Oracle job tunables](#oracle-job-tunables); leave unset unless you need to change the default |
| `RUNTIME_SERVICE_ACCOUNT` | deploy-gcp (warn-only check) | `<project-number>-compute@developer.gserviceaccount.com` | Only tells the check which account to inspect; the deploys do not pass `--service-account` |
| `TREASURY_ADDRESS` | deploy-contracts (`core` only) | none | Receives the OU supply on a full redeploy |
| `PRIVY_APP_ID` | nothing | — | Stale (Privy was replaced by CDP embedded wallets); safe to delete |

**Note**: `NEXT_PUBLIC_API_URL` is **not** a GitHub variable. The workflow captures the deployed API URL and rebuilds the web image with it.

### ci.yml (tests)

Runs on every pull request and every push to `main` (and on manual dispatch), with read-only `contents` permission and no secrets:

| Job | Command |
|---|---|
| contracts | `pip install -r contracts/requirements.txt`, then `python -m pytest -q` in `contracts/` |
| backend | `pip install -r backend/requirements.txt -r contracts/requirements.txt` (titanoboa for the EIP-712 order-digest parity test against `Exchange.vy`), then `python -m pytest -q -rs` in `backend/` |
| oracles | `pip install -r oracles/requirements.txt`, then `python -m pytest -q` in `oracles/` |
| scripts | `pip install -r oracles/requirements.txt` (the parity tests import `oracles/` and `eth_utils`), then `python -m unittest discover -s scripts/tests -v` |
| web | Node 22: `npm ci`, `npm run build`, `npm run test --if-present` in `web/` |

The backend and scripts jobs fail when a test is skipped for a missing dependency (`could not import`, `No module named`, `not installed`), so a parity check cannot pass by being skipped. Python is 3.12 (the local toolchain); the Docker images still run Python 3.11 and Node 22.

## Preflight and deploy errors

Every line the deploy prints that needs action, and its fix. `<S>` is a secret name, `<svc>` a Cloud Run service.

| Log line | Meaning | Fix |
|---|---|---|
| `ERROR: <S>: access denied` | github-deploy cannot read the secret | `gcloud secrets add-iam-policy-binding <S> --member=serviceAccount:github-deploy@overunder-509107.iam.gserviceaccount.com --role=roles/secretmanager.secretAccessor --project=overunder-509107` |
| `ERROR: <S>: secret missing or has no versions` | secret or its first version does not exist | `gcloud secrets create <S> --project=overunder-509107`, then add a version (see Filling secrets) |
| `ERROR: <S>: latest version is disabled or destroyed` | `latest` points at an unusable version | add a new version |
| `ERROR: <S>: access failed` | any other gcloud error (shown indented as `  gcloud: …`) | read the indented gcloud lines |
| `ERROR: <S>: empty payload` | the version is empty | add a non-empty version |
| `ERROR: <S>: contains a newline` | the payload has a line break | re-add with `printf %s` (not `echo`, not a PowerShell pipe) |
| `ERROR: <S>: leading or trailing whitespace or CR` | stray space or `\r` | same as above |
| `ERROR: <S>: not a 32-byte hex private key` | key secrets must be 64 hex chars, optional `0x` | re-add the key |
| `ERROR: OU_DATABASE_URL: must start with postgresql+asyncpg://` | wrong driver or scheme | use the Cloud SQL URL format under Database |
| `ERROR: OU_DATABASE_URL: must not be sqlite` | SQLite is ephemeral on Cloud Run | point it at `overunder-pg` |
| `ERROR: <S>: must start with http:// or https://` | RPC / MCP URL secrets | re-add the full URL |
| `ERROR: workflow env map has a malformed entry` | a `*_SECRETS_*` map in the workflow is broken | fix the `ENV=OU_SECRET` pairs at the top of `deploy-gcp.yml` |
| `Skipping optional <S>: <rule>` | optional secret not mapped this deploy | fine unless you need MoonPay, the relayer key (emissions) or `OU_QUESTION_ID_KEY`; fix the rule to map it. With `RELAYER_ENABLED=true` the relayer key is required instead and fails as `ERROR: OU_RELAYER_PRIVATE_KEY: …` |
| `warning: Optional secret <S> exists but failed preflight (<rule>)` | the secret is there but unusable, so it is not mapped | add a valid version (the warning prints the command) |
| `warning: RELAYER_WORKER_ENABLED=… has no effect while RELAYER_ENABLED=…` | the worker only runs with both flags on | set both, or unset `RELAYER_WORKER_ENABLED` |
| `RELAYER_WORKER_ENABLED=…: the API deploys with --no-cpu-throttling --min-instances=1` | worker on: the API keeps CPU between requests and one warm instance (billed while idle) | expected; see the variable table to undo it |
| `warning: overunder-oracle-tick is PAUSED (<reason>)` | the scheduler step did not resume it: `OU_KEEP_SCHEDULER_PAUSED`, a running deploy-contracts, or a failed check or resume | resume it with the printed command once nothing else signs with the operator key |
| `Broadcast refused` (deploy-contracts) | broadcast dispatched from a ref other than `main`, or `confirm` is not exactly `BROADCAST <target>` | merge, then dispatch from `main` with the confirm phrase, or untick broadcast to simulate |
| `ERROR: repo variable <V>='<value>' …` | an oracle repo variable fails validation (for example `OU_TICK_BUDGET_SECONDS` above 780) | `gh variable set <V> --body <value> -R j1m5s3/OverUnder`, or `gh variable delete <V>` to use the default |
| `warning: ORACLE_ADDRESS is not set` | the oracle job gets an empty `ORACLE_ADDRESS` and every resolve stage fails | set the `ORACLE_ADDRESS` repo variable |
| `ERROR: Cloud Run Jobs (overunder-oracle): no access` | github-deploy lacks Cloud Run rights | grant `roles/run.admin` (IAM matrix) |
| `ERROR: Cloud Scheduler (overunder-oracle-tick): no access` | Scheduler API disabled or role missing | `gcloud services enable cloudscheduler.googleapis.com --project=overunder-509107` and grant `roles/cloudscheduler.admin` |
| `warning: Could not run testIamPermissions` | Cloud Resource Manager API off | ignore, or `gcloud services enable cloudresourcemanager.googleapis.com` |
| `warning: github-deploy@… lacks project-level <permission>` | not granted at project level | fine if granted on the resource; otherwise see the IAM matrix |
| `warning: Cannot read IAM policy of <S>` | runtime-SA check could not read the secret's policy | optional: grant github-deploy `roles/secretmanager.viewer` |
| `warning: Runtime SA may lack access to <S>` | the runtime service account may not read the secret | run the printed `add-iam-policy-binding` (IAM matrix) |
| `warning: Runtime SA may lack run.executions.get/list on overunder-oracle` | the tick single-flight guard cannot list executions, so it fails open (`summary.lease.error`) and a slow tick can overlap the next | run the printed `gcloud run jobs add-iam-policy-binding … --role=roles/run.viewer` |
| `warning: Cannot read the project IAM policy` | github-deploy cannot read project IAM, so runtime SA roles were not checked | optional: grant github-deploy `roles/iam.securityReviewer`, or check the roles by hand |
| `ERROR: <svc>: gcloud run deploy failed (exit N)` | any deploy error except the readiness deadline | read gcloud's message above it and the Diagnose step |
| `warning: <svc>: Cloud Run reported 'Resource readiness deadline exceeded'` | instance start was delayed; the script keeps waiting | none; see the next section |
| `warning: <svc>: still not Ready after 15 min; redeploying once` | one async retry revision was created | none |
| `ERROR: <svc>: revision failed with a non-deadline error` | e.g. container crashed on boot | Diagnose step hints; check `DATABASE_URL` and the runtime SA roles; read logs (below) |
| `ERROR: <svc>: no revision became Ready within N min` | still delayed after `ready_timeout_minutes` | re-run with a larger `ready_timeout_minutes`; check status.cloud.google.com |
| `ERROR: GET <url>/health did not return {"ok":true}` | API is up but unhealthy or unreachable | read API logs; check DB and RPC secrets |
| `warning: GET /api/v1/markets returned <code>` | markets endpoint failing (DB or RPC) | read API logs |
| `ERROR: GET <url>/ returned <code>` | web root not serving 200 | read web logs; check the build args |

## IAM matrix

| Principal | Roles | Why |
|---|---|---|
| `github-deploy@overunder-509107.iam.gserviceaccount.com` | `roles/run.admin` | deploy services and the job, `update-traffic`, describe revisions |
| | `roles/artifactregistry.writer` | push images |
| | `roles/iam.serviceAccountUser` | act as the runtime SA and as itself for the scheduler's OAuth token |
| | `roles/secretmanager.secretAccessor` | preflight payload checks and deploy-contracts key reads |
| | **`roles/cloudscheduler.admin`** (new) | create/update `overunder-oracle-tick` |
| | `roles/secretmanager.viewer` (optional) | lets the warn-only runtime-SA check read secret IAM policies |
| Runtime SA (default `<project-number>-compute@developer.gserviceaccount.com`) | `roles/secretmanager.secretAccessor` | API and oracle job read their mapped secrets |
| | **`roles/cloudsql.client`** | API connects to `overunder-pg` over the Cloud SQL socket |
| | `run.executions.get` + `run.executions.list` on `overunder-oracle` (`roles/run.viewer`) | the oracle tick's single-flight guard (`oracles/tick_lease.py`) skips a tick while an older execution still runs. The default compute SA has `roles/editor`, which covers it; grant `roles/run.viewer` on the job only if you removed Editor or use a custom `RUNTIME_SERVICE_ACCOUNT`. Without it the guard fails open (`summary.lease.error`) |

Grant commands (project-level):

```bash
P=overunder-509107
DEPLOYER=serviceAccount:github-deploy@$P.iam.gserviceaccount.com
RUNTIME=serviceAccount:$(gcloud projects describe $P --format='value(projectNumber)')-compute@developer.gserviceaccount.com
for role in run.admin artifactregistry.writer iam.serviceAccountUser secretmanager.secretAccessor cloudscheduler.admin; do
  gcloud projects add-iam-policy-binding $P --member="$DEPLOYER" --role="roles/$role" --condition=None
done
gcloud projects add-iam-policy-binding $P --member="$DEPLOYER" --role=roles/secretmanager.viewer --condition=None   # optional
for role in secretmanager.secretAccessor cloudsql.client; do
  gcloud projects add-iam-policy-binding $P --member="$RUNTIME" --role="roles/$role" --condition=None
done
# Only if the runtime SA lacks roles/editor (the default compute SA has it): tick single-flight guard.
gcloud run jobs add-iam-policy-binding overunder-oracle --member="$RUNTIME" --role=roles/run.viewer --region=us-central1 --project=$P
gcloud services enable cloudscheduler.googleapis.com sqladmin.googleapis.com --project=$P
```

## "Resource readiness deadline exceeded"

Cloud Run gives a new revision about 18 minutes to start its first instance; gcloud cannot extend that. On 2026-09-22 the API instance started ~27 minutes after the revision was created (the container itself boots in ~1 s), so the deploy failed although the image was fine.

`infra/gcp/deploy_wait.sh` handles this:
- The happy path is one synchronous `gcloud run deploy`, as before.
- Only when gcloud fails with "readiness deadline exceeded" does it keep polling that revision's `Ready` condition (instances can still start late).
- If the revision is still not Ready after `RETRY_AFTER_MINUTES` (15), it redeploys once with `--async --revision-suffix=retry-<run>-<attempt>` and accepts whichever revision becomes Ready first.
- Any other `Ready=False` reason is a real failure and exits at once. It gives up after `ready_timeout_minutes` (dispatch input, default 45).
- On Ready it runs `update-traffic --to-latest`. It prints revision conditions only.

Flags that do **not** help: `--timeout` (request timeout), `--cpu-boost` (boot is already fast), `--min-instances` (warms serving capacity only), `--no-traffic` (still waits for readiness).

Diagnostics (none print env values):

```bash
gcloud run revisions list --service=overunder-api --region=us-central1 --project=overunder-509107 --limit=10 \
  --format='table(metadata.name,metadata.creationTimestamp,status.conditions[0].status,status.conditions[0].lastTransitionTime)'
gcloud run revisions describe <REVISION> --region=us-central1 --project=overunder-509107 --format='yaml(status.conditions)'
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="overunder-api" AND logName="projects/overunder-509107/logs/run.googleapis.com%2Fvarlog%2Fsystem"' \
  --project=overunder-509107 --limit=200 --freshness=2h --format='table(timestamp,resource.labels.revision_name,textPayload)'
gcloud sql instances describe overunder-pg --project=overunder-509107 --format='value(state,settings.activationPolicy)'
gcloud run jobs executions list --job=overunder-oracle --region=us-central1 --project=overunder-509107 --limit=30 \
  --format='table(metadata.name,status.startTime,status.completionTime,status.succeededCount,status.failedCount)'
```

Also check https://status.cloud.google.com (Cloud Run, us-central1) and the Cloud Run CPU/memory quotas for us-central1.

## Contract redeploys and repo variables

Contracts are deployed by `.github/workflows/deploy-contracts.yml`, never by `deploy-gcp.yml`. It uses the same WIF auth, reads `OU_OPERATOR_PRIVATE_KEY` and `OU_ANVIL_RPC_URL` (plus the three agent keys for `verify`/`core`) from Secret Manager as masked step outputs, and never prints them or the RPC URL. Addresses come from the repo variables above.

| Input | Default | Meaning |
|---|---|---|
| `target` | `verify` | `verify` = read-only wiring check; `v2` = MarketAMM v2 + MarketFactory v2 (reuses CTF, ConsensusOracle, FeeVault, USDC, Exchange, paymaster); `core` = full new stack |
| `broadcast` | `false` | unchecked = simulate on `boa.fork` (nothing sent, any branch); checked = real transactions, gated by the contracts test suite. Refused unless dispatched from `main` with `confirm` set |
| `confirm` | empty | broadcast only: must be exactly `BROADCAST <target>` (`BROADCAST v2`, `BROADCAST core`); checked in the first step, before checkout |
| `permissionless` | `true` | v2: anyone may list a market (`createPermissionlessMarket`), rate-limited by `listing_cooldown`. On for the Sepolia migration per ADR-0012; untick to keep listing allowlist-only |
| `close_gate` | `true` | v2: MarketAMM rejects buys/sells at or after `closeTime` |
| `min_seed_usdc` | `10000000` | v2: minimum seed for user listings (6-decimal units, 10 USDC) |
| `listing_cooldown` | `3600` | v2: seconds between user listings per creator, the only per-address rate limit on listing (`0` disables it) |
| `api_url` | overunder-api URL | v2: legacy markets to import come from `GET {api_url}/api/v1/markets` (primaries and children) |
| `extra_cids` | empty | v2: more legacy cids, comma separated (paused markets are hidden from the API list) |
| `rpc_source` | `secret` | `public` uses `https://sepolia.base.org` |

v2 refuses to send anything unless the repo variables describe the live, unmigrated stack: every address has code, `FACTORY_ADDRESS`/`AMM_ADDRESS` are still v1 (a v2 -> v2 re-run would strand user-listed pools), the operator key owns the factory and oracle, `oracle.factory() == FACTORY_ADDRESS`, and the factory/AMM wiring matches. An RPC error during those probes fails the run (nothing sent) instead of reading as "v1". A broadcast also refuses while the operator has pending transactions. Cids the legacy factory does not know (for example markets from an older oracle) are skipped and listed in the summary. It needs ≥ 0.005 ETH on the operator (`core`: 0.07 ETH).

AMM + Factory redeploy, in order:

```bash
R=j1m5s3/OverUnder
gh workflow run deploy-contracts.yml --ref main -R $R -f target=verify                   # 1. read-only: must be all "yes"
gh workflow run deploy-contracts.yml --ref main -R $R -f target=v2                       # 2. simulate on a fork
gcloud scheduler jobs pause overunder-oracle-tick --location=us-central1 --project=overunder-509107   # 3. required before any broadcast
gcloud run jobs executions list --job=overunder-oracle --region=us-central1 --project=overunder-509107 --filter='NOT status.completionTime:*' --format='value(metadata.name)'   # 3b. wait until this prints nothing
gh workflow run deploy-contracts.yml --ref main -R $R -f target=v2 -f broadcast=true -f confirm="BROADCAST v2"   # 4. deploy + migrate (main only)
# 5. copy the `gh variable set` lines from the run summary (GITHUB_TOKEN cannot write repo variables):
gh variable set AMM_ADDRESS --body <new MarketAMM> -R $R
gh variable set FACTORY_ADDRESS --body <new MarketFactory> -R $R
gh variable set INDEXER_START_BLOCK --body <deployBlock> -R $R
# 5b. regenerate the mobile asset with scripts/sync_mobile_deployments.py (commands in the run summary; see below)
gh workflow run deploy-gcp.yml --ref main -R $R                                          # 6. redeploy API, web and job; resumes the scheduler
# 7. check the "oracle scheduler state" row of the deploy-gcp summary; only if it is not ENABLED:
gcloud scheduler jobs resume overunder-oracle-tick --location=us-central1 --project=overunder-509107
gh workflow run deploy-contracts.yml --ref main -R $R -f target=verify                   # 8. verify the new wiring
```

Step 3 is required whenever `broadcast=true`: the migration signs with `OU_OPERATOR_PRIVATE_KEY`, the same key the oracle job and the API use. titanoboa takes each nonce from `latest` and predicts contract addresses locally, so a concurrent operator transaction can replace a pending one, fail the migration part-way, or leave a contract mined at an unexpected address (the summary lists it under "Deployed but never wired"). The workflow also pauses the scheduler and waits up to 20 min for running executions itself, refuses to broadcast while the operator has pending transactions, and resumes the scheduler if the run fails; after a successful broadcast it stays paused until deploy-gcp (step 6) resumes it. deploy-gcp leaves it paused, with a warning and the resume command in its summary, when `OU_KEEP_SCHEDULER_PAUSED=true`, while a deploy-contracts run is in progress, or when that check or the resume fails. The API cannot be paused, so avoid operator actions in it (listing confirms) during step 4. Before step 4, resolve or wind down closed legacy markets: v1 pools check only `isResolved`, so a closed, unresolved legacy pool stays tradable on chain (checklist in [docs/runbooks/operations.md](../../docs/runbooks/operations.md#pre-broadcast-checklist)).

If a migration fails part-way, the summary and the uploaded JSON (`migrationFailed`, `completedSteps`, `orphans`) show what landed. Do not re-run v2 blindly once `oracle.setFactory` is listed; finish the remaining steps by hand as the operator. The local CLI (`contracts/script/deploy_v2.py`) writes the same record to `deployments/<chain>.v2-partial.json` and leaves `deployments/<chain>.json` unchanged; it refuses a JSON that already records a migration (`MarketFactoryLegacy`) and a pair that is already v2 (`--allow-v2-source` exists for chain 31337 only).

Between step 4 and the end of step 6 the running API still points at the old factory, so operator listings fail (and retry harmlessly on the next tick); pausing the scheduler avoids noisy failed ticks. After the broadcast, also allowlist the new MarketAMM / MarketFactory (and `createPermissionlessMarket`) in the CDP Portal paymaster policy (selectors and the 800k gas cap: [docs/runbooks/operations.md](../../docs/runbooks/operations.md#cdp-portal-paymaster-allowlist)), then update the local deployment files from the uploaded `contracts-84532-v2-broadcast-<run>` artifact (the run summary prints these commands with the run id filled in):

```bash
gh run download <run> -n contracts-84532-v2-broadcast-<run> -D /tmp/ou-84532-v2 -R j1m5s3/OverUnder
python scripts/sync_mobile_deployments.py 84532 --source /tmp/ou-84532-v2/84532.json   # rewrites mobile/assets/deployments/84532.json
# merge MarketAMM, MarketFactory, MarketAMMLegacy, MarketFactoryLegacy and deployBlock into contracts/deployments/84532.json
python scripts/sync_mobile_deployments.py 84532 --check                               # exit 0 = mobile asset matches contracts JSON
```

The sync script takes every contract address (and `operator`/`agents`) from the upload and carries `treasury` over from the current mobile asset, so the mobile asset is regenerated, never hand-edited. It exits 1 and writes nothing for a simulation upload (`simulated: true`), a failed or partial migration (`migrationFailed`, `orphans`, `completedSteps`), or a v2 payload missing a v2 key; `--force` overrides that after you check the addresses on chain. Merge by hand only into `contracts/deployments/84532.json` (gitignored; read by `scripts/sync_deploy_env.py 84532`): the upload omits `SimpleAccount`, `treasury` and `canonicalUSDC`, so do not overwrite that file wholesale. A `core` broadcast prints the same download and sync commands with `-core-` in the names.

## Operations

### Filling secrets

Use `printf %s` so no newline is stored (do **not** change your local default project):

```bash
printf %s '<value>' | gcloud secrets versions add OU_JWT_SECRET --data-file=- --project=overunder-509107

# Cloud SQL unix socket for overunder-pg
printf %s 'postgresql+asyncpg://USER:PASSWORD@/overunder?host=/cloudsql/overunder-509107:us-central1:overunder-pg' \
  | gcloud secrets versions add OU_DATABASE_URL --data-file=- --project=overunder-509107

printf %s 'https://sepolia.base.org' | gcloud secrets versions add OU_ANVIL_RPC_URL --data-file=- --project=overunder-509107

# Multi-line PEM: pass the file directly
gcloud secrets versions add OU_CDP_API_KEY_SECRET --data-file=cdp_key.pem --project=overunder-509107
```

**PowerShell warning:** piping into gcloud from PowerShell appends `\r\n`, which the preflight rejects. Run these from Git Bash, or write the value to a file without a trailing newline and use `--data-file=<file>`. Never paste secret values into shell history on shared machines.

### Viewing service status

```bash
gcloud run services list --platform=managed --region=us-central1 --project=overunder-509107
gcloud run services describe overunder-api --platform=managed --region=us-central1 --format='value(status.url)' --project=overunder-509107
gcloud run services describe overunder-web --platform=managed --region=us-central1 --format='value(status.url)' --project=overunder-509107
gcloud scheduler jobs describe overunder-oracle-tick --location=us-central1 --project=overunder-509107 --format='value(state,lastAttemptTime)'
```

### Viewing logs

```bash
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="overunder-api"' \
  --project=overunder-509107 --limit=100 --freshness=1h --format='table(timestamp,severity,textPayload)'
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="overunder-oracle"' \
  --project=overunder-509107 --limit=100 --freshness=1h --format='table(timestamp,severity,textPayload)'
gcloud beta logging tail 'resource.labels.service_name="overunder-web"' --project=overunder-509107
```

### Rollback to a previous revision

```bash
gcloud run revisions list --service=overunder-api --region=us-central1 --project=overunder-509107
gcloud run services update-traffic overunder-api --to-revisions=overunder-api-00042-xyz=100 --region=us-central1 --project=overunder-509107
gcloud run services update-traffic overunder-web --to-revisions=overunder-web-00023-abc=100 --region=us-central1 --project=overunder-509107
```

Or in the console: Cloud Run → service → "Manage Traffic" → set the revision to 100%. A pinned split can keep new revisions from serving; after the fix run `gcloud run services update-traffic <service> --to-latest --region=us-central1 --project=overunder-509107`.

### Manual deployment

Prefer the workflow: it maps secrets, env vars and build args consistently. For a one-off image:

```bash
docker build -t us-central1-docker.pkg.dev/overunder-509107/overunder/api:manual ./backend
docker push us-central1-docker.pkg.dev/overunder-509107/overunder/api:manual
bash infra/gcp/deploy_wait.sh overunder-api --image=us-central1-docker.pkg.dev/overunder-509107/overunder/api:manual \
  --region=us-central1 --project=overunder-509107   # needs GCP_REGION and GCP_PROJECT_ID exported
```

Deploying without `--set-secrets`/`--set-env-vars` keeps the revision's existing settings.

## Environment variables

See `infra/gcp/cloudrun.env.example` for the full list.

### Backend (overunder-api)

- **From Secret Manager**: `JWT_SECRET`, `DATABASE_URL`, `ANVIL_RPC_URL`, `CDP_API_KEY_ID`, `CDP_API_KEY_SECRET`, `OPERATOR_PRIVATE_KEY`, `RELAYER_PRIVATE_KEY` (required when `RELAYER_ENABLED=true`, otherwise mapped when valid) and, when valid, `MOONPAY_SECRET`.
- **Set by the workflow**: `CHAIN_ID=84532`, `CDP_PROJECT_ID`, every contract address, `COINBASE_ONRAMP_APP_ID`, `INDEXER_START_BLOCK`, `TRADING_HALT_AT_CLOSE`, `RELAYER_ENABLED`, `RELAYER_WORKER_ENABLED`.
- **Code defaults** (`backend/app/config.py`, not passed by the workflow): the RPC timeouts (`AMM_QUOTE_RPC_TIMEOUT_SECONDS=5`, `OPERATOR_RPC_TIMEOUT_SECONDS=15`, `OPERATOR_TX_TIMEOUT_SECONDS=60`), the indexer (`INDEXER_ENABLED`, `INDEXER_*`) and the relayer (`RELAYER_*`, including `RELAYER_LOG_LOOKBACK_BLOCKS=5000` and `RELAYER_MAX_OPEN_ORDERS_PER_MAKER=100`). `infra/gcp/cloudrun.env.example` lists each one with its default. `--set-env-vars` replaces the service's whole env list, so an override made in the console is dropped by the next deploy; add it to the API step of `deploy-gcp.yml` instead.

### Frontend (overunder-web)

- **Build-time**: `NEXT_PUBLIC_API_URL` (captured from the deployed API), `NEXT_PUBLIC_AMM_ADDRESS`, `NEXT_PUBLIC_USDC_ADDRESS`, `NEXT_PUBLIC_CTF_ADDRESS`, `NEXT_PUBLIC_RPC_URL`, `NEXT_PUBLIC_CDP_PROJECT_ID`, `NEXT_PUBLIC_TRADING_HALT_AT_CLOSE`.
- **Runtime**: `PORT` (Cloud Run sets it). No secrets.

### Oracle job (overunder-oracle)

- **From Secret Manager**: `CURSOR_API_KEY`, `CURSOR_SEARCH_MCP_URL`, `JWT_SECRET`, `OPERATOR_PRIVATE_KEY`, `AGENT_ALPHA_KEY`, `AGENT_BETA_KEY`, `AGENT_GAMMA_KEY`, `ANVIL_RPC_URL` and, when valid, `OU_QUESTION_ID_KEY`.
- **Always set by the workflow**: `OU_API_URL` (the deployed API), `CHAIN_ID=84532`, `ORACLE_ADDRESS`, `OU_CURSOR_RUNTIME` (default `local`), `OU_FALLBACK_POLICY` (default `attest`), `OU_TICK_BUDGET_SECONDS` (default `780`).
- **Set only when the repo variable exists**: every other tunable below. `--set-env-vars` replaces the job's whole env list, so an unset repo variable means the oracles code default, and a console edit is dropped by the next deploy.
- **Image defaults** (`oracles/Dockerfile`): `OU_CURSOR_RUNTIME=local`, `OU_FALLBACK_POLICY=attest`, `OU_SCOUT_MAX_MARKETS=5` (the same values as the code and workflow defaults).
- **Set by Cloud Run**: `CLOUD_RUN_JOB`, `CLOUD_RUN_EXECUTION` (turn on the tick single-flight guard).
- **Task timeout**: `OU_TICK_BUDGET_SECONDS` + 60 s (840 s by default), with `--max-retries=0`. Once the budget is spent, stages start no new work and record the rest as skipped; the extra 60 s lets in-flight work (a research run, a receipt wait) finish. Work still running at the timeout is killed and picked up by the next tick, which starts at least 60 s later. Budgets above 780 are refused, because the timeout must end before the next `*/15` tick.

#### Oracle job tunables

The workflow validates each repo variable the way the oracles code parses it and fails before any build on a bad value. Defaults are the code defaults.

| Variable | Default | Meaning |
|---|---|---|
| `OU_TICK_BUDGET_SECONDS` | `780` | Always passed. Wall-clock budget per tick (`oracles/budget.py`), 1 to 780 here; sets the task timeout |
| `OU_TICK_SINGLE_FLIGHT` | on under Cloud Run | `0` turns off the guard that exits a tick with `{"skipped": "running"}` while an older execution still runs (`oracles/tick_lease.py`; needs the IAM row above) |
| `OU_SEED_USDC` | `200000000` | Seed per operator-listed market (6-decimal units, 200 USDC) |
| `OU_SCOUT_MAX_MARKETS` | `5` | Sports primaries whose score is scouted per tick |
| `OU_SCOUT_STALE_SECONDS` | `600` | Skip a game whose score row was updated within this window |
| `OU_SCOUT_RECENT_SECONDS` | `129600` (36 h) | Games that closed within this window are scouted before the rotating backlog |
| `OU_SCOUT_MAX_AGE_SECONDS` | `0` (off) | Opt-in cutoff: stop scouting games that closed longer ago than this |
| `OU_RESOLVE_MAX_MARKETS` | `3` | Sports primaries resolved per tick |
| `OU_RESEARCH_RETRY_SECONDS` | `21600` (6 h) | Skip re-researching a market while its newest research record is younger than this (reads `attestations[].createdAt` from `GET /api/v1/oracle/{cid}/status`) |
| `OU_RESEARCH_ERROR_RETRY_SECONDS` | `3600` (1 h) | Shorter cooldown when the newest research record is an `oracle-job` failure marker (the research run raised) (`oracles/resolve/cooldown.py`) |
| `OU_RESEARCH_MIN_SECONDS` | `90` | Do not start a 3-agent research run (resolvers, score scout) with less than this left in the stage budget; the market is deferred to the next tick (`oracles/budget.py`) |
| `OU_GENERAL_RESOLVE_MAX_MARKETS` | `2` | Wildcard, user-listed and non-sports markets researched per tick |
| `OU_GENERAL_RESOLVE_DELAY_SECONDS` | `86400` (24 h) | Wait after `closeTime` before researching an ungated general market (no score feed or parent to wait for) |
| `OU_GENERAL_RESOLVE_GATED_DELAY_SECONDS` | `3600` | Wait after `closeTime` for event-gated markets, which also need a final score or a resolved parent |
| `OU_GENERAL_RESOLVE_MIN_CONFIDENCE` | `0.8` | Every agent must reach this confidence (0 to 1) for unanimous consensus; the 24 h fallback counts only agents at or above it |
| `OU_LISTING_STALE_GRACE_SECONDS` | `28800` (8 h) | Schedule rows still `scheduled`/`in_progress` this long after kickoff count as done for week-roll listing (`scripts/audit_markets.py` mirrors it) |
| `OU_LISTING_RESERVE_SECONDS` | `120` | Every stage before listing stops this long before the tick deadline, capped at a quarter of the budget (`oracles/job.py`) |
| `OU_STAGE_SHARE_<STAGE>` | `0.4` for `SCORES`, `1.0` for `RESOLVE`, `RESOLVE_GENERAL`, `SCHEDULE`, `LISTING` | Largest share of the tick budget one stage may use, a number in (0, 1] (`oracles/job.py`) |
| `OU_CURSOR_MODEL_ALPHA` / `_BETA` / `_GAMMA` | `composer-2.5` / `grok-4.6` / `gpt-5.1` | Cursor model per agent |

## Security Notes

### Sepolia smoke configuration only

- `--allow-unauthenticated` on both API and web (any client can call).
- CORS allows all origins in `backend/app/main.py`.
- `OPERATOR_PRIVATE_KEY` (and `RELAYER_PRIVATE_KEY` when mapped) are mounted directly in the Cloud Run process; the oracle job also holds the three agent keys.
- The oracle job runs cursor-sdk agents locally with MCP tools only (`OU_CURSOR_RUNTIME=local`), so injected web content cannot reach a shell or the job's env.
- Contract addresses and RPC are Base Sepolia (chain id 84532).

### Before mainnet (Base)

1. Remove `--allow-unauthenticated` from the API (`gcloud run services update overunder-api --no-allow-unauthenticated --region=us-central1 --project=overunder-509107`) and grant the web identity `roles/run.invoker`.
2. Restrict CORS origins in `backend/app/main.py`; add Cloud Armor WAF rules and rate limiting.
3. Move operator/relayer keys to Cloud KMS or a multisig; add a timelock for market operations.
4. Use a VPC connector, logging alerts, Secret Manager rotation, and review IAM bindings for least privilege.

### Private keys (current)

- Stored in Secret Manager and mounted as env vars; acceptable for Sepolia only.
- Agent keys are immutable in ConsensusOracle, so rotating them means an oracle redeploy (`deploy-contracts` `core`).

### Web rebuild after API URL changes

The web image bakes `NEXT_PUBLIC_API_URL` in at build time. The workflow deploys the API first, captures its URL and rebuilds web. If you deploy the API separately, re-run the whole workflow. Changing the service name or region means updating `API_SERVICE_NAME` / `GCP_REGION` in the workflow.

## Database

The API reads `DATABASE_URL` from `OU_DATABASE_URL`. SQLite works locally but is ephemeral on Cloud Run, and the preflight rejects it.

### Cloud SQL connection (overunder-pg)

```
postgresql+asyncpg://USER:PASSWORD@/overunder?host=/cloudsql/overunder-509107:us-central1:overunder-pg
```

- Never commit real credentials.
- The unix-socket form **requires** `--add-cloudsql-instances=overunder-509107:us-central1:overunder-pg` on the deploy (the workflow sets it) and `roles/cloudsql.client` on the runtime SA.

### Creating a new Cloud SQL instance (if needed)

```bash
gcloud sql instances create overunder-pg --database-version=POSTGRES_15 --tier=db-f1-micro --region=us-central1 --project=overunder-509107
gcloud sql databases create overunder --instance=overunder-pg --project=overunder-509107
gcloud sql users set-password postgres --instance=overunder-pg --password='<SECURE_PASSWORD>' --project=overunder-509107
gcloud sql instances describe overunder-pg --format='value(connectionName)' --project=overunder-509107
```

Then add a new `OU_DATABASE_URL` version with the connection string above.

## Project number vs project id

- **Project ID**: `overunder-509107` (most commands)
- **Project Number**: numeric id used in the WIF provider path — `gcloud projects describe overunder-509107 --format='value(projectNumber)'`; store it as the `GCP_PROJECT_NUMBER` repo secret.

## Troubleshooting

- **Authentication error in Actions**: check the WIF pool/provider, the `GCP_PROJECT_NUMBER` secret, and the IAM matrix.
- **Deploy fails**: read the Diagnose step first, then the Preflight and deploy errors table.
- **Service returns 500**: read the logs; confirm `CHAIN_ID=84532`, the contract address variables and the RPC secret.
- **Database connection errors**: `overunder-pg` must be RUNNABLE, the runtime SA needs `roles/cloudsql.client`, and `DATABASE_URL` must use the socket format.
- **CORS errors in the browser**: make sure `NEXT_PUBLIC_API_URL` points at the deployed API; the real error is often auth, not CORS.
- **Oracle ticks fail**: `gcloud run jobs executions list --job=overunder-oracle --region=us-central1 --project=overunder-509107`, then the job logs command above. The job exits 1 when any stage reports `ok: false`. A tick that finds an older execution still running exits 0 with `{"skipped": "running"}`; `summary.lease.error` means the single-flight guard could not list executions (IAM matrix) and ran anyway. An execution that ends with a task-timeout failure ran past `OU_TICK_BUDGET_SECONDS` + 60 s; the next tick picks up what it left.

## Next steps

1. Fill the Secret Manager secrets and apply the IAM matrix.
2. Set the repository variables; run `deploy-contracts` with `target=verify`.
3. Dispatch `deploy-gcp.yml` from `main`.
4. Check `/health`, the web root and the first oracle executions.
5. Review security before mainnet (auth, rate limits, key custody).
