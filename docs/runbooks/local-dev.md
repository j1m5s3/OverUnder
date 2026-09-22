---
title: Local development runbook
status: SHIPPED
area: cross
summary: Commands to install, run, test, and stop the Anvil + API + web stack on Windows.
last_verified: 2026-09-21
pointers:
  - "[scripts/run_stack.cmd : L1-56]"
  - "[scripts/stop_stack.cmd : L1-25]"
  - "[scripts/stop_stack.py : L13-14]"
  - "[scripts/e2e_local.py : L1-24]"
  - "[infra/docker-compose.yml : L1-25]"
  - "[.github/workflows/deploy-gcp.yml : L85-96]"
  - "[backend/app/config.py : L11-31]"
  - "[docs/adr/0010-cdp-embedded-wallets.md : L28-39]"
---

# Local development

Python **3.12** (Vyper wheels). Python 3.14 will fail contract installs.

## One-shot stack (Windows)

From repo root:

```bat
scripts\run_stack.cmd
```

Creates `.env` from `.env.example` if missing, ensures `contracts\.venv`, starts Anvil (Docker compose `infra/docker-compose.yml` or Foundry `anvil`), deploys, syncs addresses into `.env`, then API `:8000` and web `:3000`.

Stop (kills listeners on 8000/3000/8545, then `docker compose stop`):

```bat
scripts\stop_stack.cmd
```

Do not rely on console window titles. [scripts/stop_stack.py : L13-14]

## Manual

```bat
copy .env.example .env
cd infra && docker compose up -d
cd ..\contracts
uv venv .venv --python 3.12
uv pip install --python .venv -r requirements.txt
uv pip install --python .venv -r ..\backend\requirements.txt -r ..\oracles\requirements.txt
.\.venv\Scripts\python.exe script\deploy.py
cd ..\scripts && ..\contracts\.venv\Scripts\python.exe sync_deploy_env.py 31337
cd ..\backend && ..\contracts\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
cd ..\web && npm install && npm run dev
```

## Tests

```bat
cd contracts && .\.venv\Scripts\python.exe -m pytest tests -q
cd ..\backend && ..\contracts\.venv\Scripts\python.exe -m pytest tests -q
cd ..\oracles && ..\contracts\.venv\Scripts\python.exe -m pytest tests -q
cd .. && .\contracts\.venv\Scripts\python.exe scripts\e2e_local.py
```

`e2e_local.py` uses in-process boa; Anvil is not required for that script.

## URLs

- Web `http://127.0.0.1:3000`
- API health `http://127.0.0.1:8000/health`
- RPC `http://127.0.0.1:8545`

## Secrets

`.env`, `.venv`, `node_modules`, `*.db`, and `contracts/deployments/*.json` are gitignored. Never commit keys. Anvil demo keys in `deploy.py` are public test accounts only.

## Cursor oracle secrets (Cloud Run Job)

The `overunder-oracle` job is Cursor cloud agents plus remote HTTP search MCP. GCP does not host MCP. Deploy preflight fails closed if Cursor or agent-key secrets are missing; the workflow does not invent keys.

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

Create empty secrets if needed, then add versions from a local file (never commit the values):

```bat
gcloud secrets create OU_CURSOR_API_KEY --project=overunder-509107
gcloud secrets versions add OU_CURSOR_API_KEY --data-file=cursor-api-key.txt --project=overunder-509107
gcloud secrets create OU_CURSOR_SEARCH_MCP_URL --project=overunder-509107
gcloud secrets versions add OU_CURSOR_SEARCH_MCP_URL --data-file=cursor-search-mcp-url.txt --project=overunder-509107
```

Local/anvil: set `CURSOR_API_KEY` and `CURSOR_SEARCH_MCP_URL` in `.env`, leave `OU_CURSOR_RUNTIME` unset (local SDK). Pytest/CI/anvil keep `OU_ORACLE_MOCK=1`.

## Coinbase CDP (app wallets)

User-facing login and gasless swaps use Coinbase CDP embedded wallets, not OverUnderPaymaster. See [ADR-0010](../adr/0010-cdp-embedded-wallets.md).

1. In [CDP Portal](https://portal.cdp.coinbase.com/) create a project and a secret API key.
2. Enable embedded wallets with smart accounts on login, and CDP Paymaster for Base Sepolia. Do not put a paymaster URL in client code.
3. Set env (empty placeholders live in `.env.example`): `CDP_PROJECT_ID`, `CDP_API_KEY_ID`, `CDP_API_KEY_SECRET`, `NEXT_PUBLIC_CDP_PROJECT_ID` (same project id on the web build).
4. If those env vars are unset, local config may read `.secrets/cb_keys.json` keys `PROJECT_ID`, `API_KEY_ID`, `API_SECRET` only. Never log the values. There is no wallet secret unless an SDK call fails without it. [backend/app/config.py : L11-31]
5. CDP routes return 503 if `CDP_PROJECT_ID` or `CDP_API_KEY_SECRET` is missing. Backend tests mock the CDP client and must not call Coinbase.

Cloud Run: `CDP_PROJECT_ID` from GitHub `vars.CDP_PROJECT_ID`. Secrets `CDP_API_KEY_ID`/`CDP_API_KEY_SECRET` from Secret Manager `OU_CDP_API_KEY_ID` / `OU_CDP_API_KEY_SECRET`. Web image build-arg `NEXT_PUBLIC_CDP_PROJECT_ID`. [`.github/workflows/deploy-gcp.yml : L85-96`]

## Leftover OverUnderPaymaster tank

`OverUnderPaymaster.vy` stays in the repo and is not the app path. After Anvil deploy, `deploy.py` may still deposit EntryPoint ETH. Refilling that tank does not sponsor CDP user ops. Run `scripts/list_chiefs_primary.py` only after the API `.env` points at the **new** factory/AMM addresses. The script skips create if the question `Chiefs vs Broncos: Chiefs win?` already exists and never scores a different condition id.

