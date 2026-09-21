---
title: Local development runbook
status: SHIPPED
area: cross
summary: Commands to install, run, test, and stop the Anvil + API + web stack on Windows.
last_verified: 2026-09-20
pointers:
  - "[scripts/run_stack.cmd : L1-56]"
  - "[scripts/stop_stack.cmd : L1-25]"
  - "[scripts/stop_stack.py : L13-14]"
  - "[scripts/e2e_local.py : L1-24]"
  - "[infra/docker-compose.yml : L1-25]"
  - "[.github/workflows/deploy-gcp.yml : L83-96]"
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

The `overunder-oracle` job is Cursor cloud agents plus remote HTTP search MCP. GCP does not host MCP. Deploy preflight fails closed if either secret is missing; the workflow does not invent keys.

| Env in job | Secret Manager name |
| --- | --- |
| `CURSOR_API_KEY` | `OU_CURSOR_API_KEY` |
| `CURSOR_SEARCH_MCP_URL` | `OU_CURSOR_SEARCH_MCP_URL` |
| `JWT_SECRET` | `OU_JWT_SECRET` |
| `OPERATOR_PRIVATE_KEY` | `OU_OPERATOR_PRIVATE_KEY` |

Create empty secrets if needed, then add versions from a local file (never commit the values):

```bat
gcloud secrets create OU_CURSOR_API_KEY --project=overunder-509107
gcloud secrets versions add OU_CURSOR_API_KEY --data-file=cursor-api-key.txt --project=overunder-509107
gcloud secrets create OU_CURSOR_SEARCH_MCP_URL --project=overunder-509107
gcloud secrets versions add OU_CURSOR_SEARCH_MCP_URL --data-file=cursor-search-mcp-url.txt --project=overunder-509107
```

Local/anvil: set `CURSOR_API_KEY` and `CURSOR_SEARCH_MCP_URL` in `.env`, leave `OU_CURSOR_RUNTIME` unset (local SDK). Pytest/CI/anvil keep `OU_ORACLE_MOCK=1`.
