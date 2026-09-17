---
title: Local development runbook
status: SHIPPED
area: cross
summary: Commands to install, run, test, and stop the Anvil + API + web stack on Windows.
last_verified: 2026-09-16
pointers:
  - "[scripts/run_stack.cmd : L1-56]"
  - "[scripts/stop_stack.cmd : L1-25]"
  - "[scripts/stop_stack.py : L13-14]"
  - "[scripts/e2e_local.py : L1-24]"
  - "[infra/docker-compose.yml : L1-25]"
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
