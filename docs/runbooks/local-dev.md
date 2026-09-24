---
title: Local development runbook
status: SHIPPED
area: cross
summary: Commands to install, run, test, and stop the Anvil + API + web stack on Windows, plus test isolation, the local relayer, the mock oracle tick, fork-cache and Postgres notes, and the local env vars.
last_verified: 2026-09-23
pointers:
  - "[scripts/run_stack.cmd : L1-57]"
  - "[scripts/stop_stack.cmd : L1-25]"
  - "[scripts/stop_stack.py : L1-14]"
  - "[scripts/e2e_local.py : L1-24]"
  - "[infra/docker-compose.yml : L1-28]"
  - "[backend/tests/conftest.py : L1-39]"
  - "[backend/tests/test_db_migrations.py : L1-5]"
  - "[backend/app/contract_addresses.py : L10-29]"
  - "[backend/app/config.py : L12-32]"
  - "[backend/app/config.py : L107-130]"
  - "[backend/app/relayer/queue.py : L30-38]"
  - "[backend/app/relayer/router.py : L92-106]"
  - "[contracts/script/deploy.py : L74-89]"
  - "[contracts/script/deploy.py : L233-234]"
  - "[oracles/schedule/scout.py : L123-126]"
  - "[.env.example : L39-52]"
  - "[.env.example : L66-79]"
  - "[.github/workflows/ci.yml : L17-128]"
  - "[.github/workflows/deploy-gcp.yml : L26-31]"
  - "[.github/workflows/deploy-gcp.yml : L472-486]"
---

# Local development

Use Python **3.12**, because Vyper ships wheels for it; Python 3.14 fails the contract installs. You also need Node 22 for the web app and Flutter 3.27 or newer for mobile. Production operations (deploys, audit, relayer, CDP Portal) are covered in [operations.md](operations.md).

## One-shot stack (Windows)

From the repo root:

```bat
scripts\run_stack.cmd
```

The script:

- creates `.env` from `.env.example` if it is missing
- ensures `contracts\.venv`
- starts Anvil, with Docker compose `infra/docker-compose.yml` first and a local Foundry `anvil` as the fallback
- deploys the contracts and syncs their addresses into `.env`
- starts the API on `:8000` and the web app on `:3000`

On chain 31337 `deploy.py` opens permissionless user listing (10 USDC minimum seed, 1 h lead, 90 d horizon, no cooldown). Public chains keep the closed allowlist.

To stop, run the command below. It kills the listeners on 8000, 3000 and 8545, then runs `docker compose stop`. It does not rely on console window titles [scripts/stop_stack.py : L1-14].

```bat
scripts\stop_stack.cmd
```

If Docker Desktop crashes at startup on stale socket files, see the note in [operations.md](operations.md#local-docker-desktop-note). The stack still starts on Foundry `anvil`.

## Manual

Create `.env` and start the containers:

```bat
copy .env.example .env
cd infra && docker compose up -d
```

Create the venv and install the dependencies, from `contracts\`:

```bat
uv venv .venv --python 3.12
uv pip install --python .venv -r requirements.txt
uv pip install --python .venv -r ..\backend\requirements.txt -r ..\oracles\requirements.txt
```

Deploy to anvil and write the addresses into `.env`, from `contracts\`:

```bat
.\.venv\Scripts\python.exe script\deploy.py
cd ..\scripts && ..\contracts\.venv\Scripts\python.exe sync_deploy_env.py 31337
```

Run the API, then the web app, each in its own window:

```bat
cd backend && ..\contracts\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
cd web && npm install && npm run dev
```

When `contracts/deployments/31337.json` exists, the backend reads contract addresses from it instead of from `.env` [backend/app/contract_addresses.py : L10-29]. A concurrent `deploy.py` rewrites that file, so a running API can end up talking to other contracts.

## Tests

CI runs the same suites on every PR [.github/workflows/ci.yml : L17-128]. Run them from the repo root.

Python suites (contracts take about 160 s because of the pm-AMM fuzz and gas tests):

```bat
cd contracts && .\.venv\Scripts\python.exe -m pytest tests -q
cd ..\backend && ..\contracts\.venv\Scripts\python.exe -m pytest tests -q
cd ..\oracles && ..\contracts\.venv\Scripts\python.exe -m pytest tests -q
```

The scripts suite (audit and mobile-sync parity) and the in-process e2e:

```bat
.\contracts\.venv\Scripts\python.exe -m unittest discover -s scripts/tests -v
.\contracts\.venv\Scripts\python.exe scripts\e2e_local.py
```

Web (build plus `node --test` over `web/tests/**/*.test.ts`) and mobile (no CI job; run it locally):

```bat
cd web && npm ci && npm run build && npm test
cd ..\mobile && flutter pub get && flutter test
```

Notes:

- `e2e_local.py` uses in-process boa, so it does not need Anvil.
- The scripts parity tests import `oracles/` and `eth_utils`. The venv above has both. In CI, a skip caused by a missing module fails the job.
- **Backend isolation.** `backend/tests/conftest.py` pins every setting before `app` is imported [backend/tests/conftest.py : L1-39]:
  - a throwaway SQLite file
  - `CHAIN_ID=999999`
  - empty keys and addresses
  - a fixed JWT secret
  - `AUTH_ANVIL_BYPASS=false`

  So the repo `.env`, `.secrets/cb_keys.json`, `contracts/deployments/*.json` and `backend/overunder.db` never leak into a run.
- **Postgres.** The committed conftest always uses SQLite. On 2026-09-23 the Postgres-only paths were rehearsed against native PostgreSQL 16 using a scratch copy of the tests whose conftest pointed `DATABASE_URL` at a throwaway `postgresql+asyncpg://` database. Those paths are the int4 to BIGINT widening, the advisory-locked `run_migrations` under concurrent startups, and the relayer leader lock. The `_on_postgres` tests only run in that setup [backend/tests/test_db_migrations.py : L1-5]. To repeat it, point a copy at the compose `postgres` service on `:5432`.

## Mock oracle tick

In `.env`, keep `OU_ORACLE_MOCK=1` for pytest, CI and anvil. Mock agents never call Cursor.

- The schedule stage returns no rows under mock unless `OU_MOCK_SCHEDULE` supplies them [oracles/schedule/scout.py : L123-126].
- Rows look like `2026 W3 Bills vs Dolphins kickoff 1800000000 scheduled`, separated by `;` or newlines.
- Run one tick against the local API:

```bat
cd oracles && ..\contracts\.venv\Scripts\python.exe -m job
```

It prints one JSON summary, redacted, and exits 1 if any stage reports `ok: false`.

## Relayer on Anvil (OU-T003)

The CLOB relayer is off unless `RELAYER_ENABLED=true`. It becomes ready only when all three are present [backend/app/relayer/queue.py : L30-38]:

- `RELAYER_ENABLED`
- a parseable `RELAYER_PRIVATE_KEY`
- an `Exchange` address

`.env.example` already sets `RELAYER_PRIVATE_KEY` to Anvil account 1, which is prefunded, and `sync_deploy_env.py` writes `EXCHANGE_ADDRESS`. To try it:

1. Set `RELAYER_ENABLED=true` in `.env`, then restart the API.
2. Either set `RELAYER_WORKER_ENABLED=true` for the background worker, or drain by hand with operator `POST /api/v1/relayer/tick` [backend/app/relayer/router.py : L92-106].
3. Check operator `GET /api/v1/relayer/status`.

The other `RELAYER_*` gas, nonce and poll settings keep their code defaults [backend/app/config.py : L107-130]. Makers must be EOAs that sign EIP-712 orders: Exchange is `ecrecover`-only (OU-T016).

## titanoboa fork cache

titanoboa caches fork reads on disk by chain id and block. Every anvil is chain 31337 and starts at block 0, so a second or restarted anvil could be served the previous chain's state. The symptom is `RuntimeError: uh oh! <addr> != <addr>` on deploy.

`deploy.py` now re-forks 31337 with an in-memory cache [contracts/script/deploy.py : L74-89], [contracts/script/deploy.py : L233-234]. Other scripts that fork anvil can still hit the disk cache. Clear `~/.cache/titanoboa/fork`, or run them with `USERPROFILE`/`HOME` pointed at a scratch directory.

## Env vars

`.env.example` lists every local setting. Tunables that are commented out show the code default.

- Oracle tick [.env.example : L39-52]:
  - `OU_FALLBACK_POLICY` (`attest`)
  - `OU_TICK_BUDGET_SECONDS` (780)
  - `OU_TICK_SINGLE_FLIGHT`, active only on Cloud Run
  - `OU_SCOUT_*`
  - `OU_RESEARCH_RETRY_SECONDS` (6 h)
  - `OU_GENERAL_RESOLVE_*` (24 h ungated delay, 1 h gated delay, 0.8 confidence)
  - `OU_LISTING_STALE_GRACE_SECONDS` (8 h)
  - optional `OU_QUESTION_ID_KEY`
- Lifecycle, indexer and relayer [.env.example : L66-79]:
  - `TRADING_HALT_AT_CLOSE` (`true`)
  - `INDEXER_*` (`INDEXER_START_BLOCK=0` indexes anvil from block 1)
  - the RPC timeouts
  - `RELAYER_ENABLED` / `RELAYER_WORKER_ENABLED` (`false`)
  - `RELAYER_LOG_LOOKBACK_BLOCKS`
  - `RELAYER_MAX_OPEN_ORDERS_PER_MAKER`
- Web: `NEXT_PUBLIC_TRADING_HALT_AT_CLOSE` is used only when the API omits `tradingHaltsAt`.

For the Cloud Run equivalents, see `infra/gcp/cloudrun.env.example` and [infra/gcp/README.md](../../infra/gcp/README.md).

## URLs

- Web `http://127.0.0.1:3000` (user listing at `/list`)
- API health `http://127.0.0.1:8000/health`
- RPC `http://127.0.0.1:8545`

## Secrets

`.env`, `.venv`, `node_modules`, `*.db`, `*.tsbuildinfo` and `contracts/deployments/*.json` are gitignored. Never commit keys. The Anvil demo keys in `deploy.py` and `.env.example` are public test accounts only. `deploy.py` refuses them on any chain other than 31337.

## Cursor oracle secrets (Cloud Run Job)

By default the `overunder-oracle` job runs cursor-sdk agents inside the container with MCP tools only (`OU_CURSOR_RUNTIME=local`), plus a remote HTTP search MCP. GCP does not host the MCP. The deploy preflight fails closed if a required Cursor or agent-key secret is missing or malformed, and the workflow does not invent keys [.github/workflows/deploy-gcp.yml : L26-31].

| Env in job | Secret Manager name |
| --- | --- |
| `CURSOR_API_KEY` | `OU_CURSOR_API_KEY` |
| `CURSOR_SEARCH_MCP_URL` | `OU_CURSOR_SEARCH_MCP_URL` |
| `JWT_SECRET` | `OU_JWT_SECRET` |
| `OPERATOR_PRIVATE_KEY` | `OU_OPERATOR_PRIVATE_KEY` |
| `AGENT_ALPHA_KEY` | `OU_AGENT_ALPHA_KEY` |
| `AGENT_BETA_KEY` | `OU_AGENT_BETA_KEY` |
| `AGENT_GAMMA_KEY` | `OU_AGENT_GAMMA_KEY` |
| `ANVIL_RPC_URL` | `OU_ANVIL_RPC_URL` |
| `OU_QUESTION_ID_KEY` (optional) | `OU_QUESTION_ID_KEY` |

Payloads must be a single line. A file with a trailing newline fails the preflight. Add versions from Git Bash with `printf %s`, and never commit the values:

```bash
gcloud secrets create OU_CURSOR_API_KEY --project=overunder-509107
printf %s '<value>' | gcloud secrets versions add OU_CURSOR_API_KEY --data-file=- --project=overunder-509107
```

For local runs and anvil, set `CURSOR_API_KEY` and `CURSOR_SEARCH_MCP_URL` in `.env` and leave `OU_CURSOR_RUNTIME` unset (local SDK). The Cursor quota and runtime notes are in [operations.md](operations.md#cursor-quota-and-runtime-mode).

## Coinbase CDP (app wallets)

User-facing login and gasless swaps use Coinbase CDP embedded wallets, not OverUnderPaymaster. See [ADR-0010](../adr/0010-cdp-embedded-wallets.md).

1. In the [CDP Portal](https://portal.cdp.coinbase.com/), create a project and a secret API key.
2. Enable embedded wallets with smart accounts on login, and CDP Paymaster for Base Sepolia. Do not put a paymaster URL in client code. For the contracts and selectors the paymaster policy must allow, see [operations.md](operations.md#cdp-portal-paymaster-allowlist).
3. Set these env vars (empty placeholders are in `.env.example`): `CDP_PROJECT_ID`, `CDP_API_KEY_ID`, `CDP_API_KEY_SECRET` and `NEXT_PUBLIC_CDP_PROJECT_ID` (the same project id, for the web build).
4. If those env vars are unset, local config may read the keys `PROJECT_ID`, `API_KEY_ID` and `API_SECRET` from `.secrets/cb_keys.json`, and nothing else [backend/app/config.py : L12-32]. Never log the values.
5. CDP routes return 503 if `CDP_PROJECT_ID` or `CDP_API_KEY_SECRET` is missing. Backend tests mock the CDP client and must not call Coinbase. With no project id, the web app renders signed out and shows that sign-in isn't configured.

On Cloud Run:

- `CDP_PROJECT_ID` comes from the GitHub variable `vars.CDP_PROJECT_ID`.
- `CDP_API_KEY_ID` and `CDP_API_KEY_SECRET` come from the Secret Manager secrets `OU_CDP_API_KEY_ID` and `OU_CDP_API_KEY_SECRET`.
- The web image gets the build arg `NEXT_PUBLIC_CDP_PROJECT_ID` [.github/workflows/deploy-gcp.yml : L472-486].

## Leftover OverUnderPaymaster tank

`OverUnderPaymaster.vy` stays in the repo but is not the app path. After an Anvil deploy, `deploy.py` may still deposit EntryPoint ETH; refilling that tank does not sponsor CDP user ops.

Run `scripts/list_chiefs_primary.py` only after the API `.env` points at the **new** factory and AMM addresses. The script skips the create if the question `Chiefs vs Broncos: Chiefs win?` already exists, and it never scores a different condition id.
