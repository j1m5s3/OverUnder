# OverUnder

Prediction markets on Base. Every market trades on a seeded static pm-AMM (`MarketAMM`), and trading halts at `closeTime`. There are three kinds of market:

- operator primaries
- wildcard children
- user-listed markets, which are loosely gated: seed, lead time, horizon, cooldown and question rules

A leftover EIP-712 CLOB overlay and AI-agent oracles complete the system.

MVP settlement is **USDC**. Oracles must reach **unanimous (3/3)** consensus to resolve. Otherwise, after 24 hours, **agent majority + participant votes** decide. Protocol fees accrue to a vault; **OU** redeems for USDC at NAV.

## Stack

| Layer | Tech |
| --- | --- |
| Contracts | Vyper, titanoboa (Moccasin project), Anvil |
| API | FastAPI (SQLite locally, Postgres on Cloud Run) |
| Oracles | Python Cloud Run Job: three Cursor-runtime agents (cursor-sdk) with a remote search MCP |
| Web | Next.js, Tailwind, wagmi, Coinbase CDP hooks |
| Mobile | Flutter — see `mobile/README.md` |

## Quickstart (local)

Set up the env and containers, then the contracts (run each block from the repo root):

```bash
cp .env.example .env
cd infra && docker compose up -d
```

```bash
cd contracts && pip install -r requirements.txt
python -m pytest tests -q
python script/deploy.py
```

Then the API and the web app, each in its own shell from the repo root:

```bash
cd backend && pip install -e . && uvicorn app.main:app --reload
cd web && npm install && npm run dev
```

## Local stack (Windows)

```bat
scripts\run_stack.cmd
```

This starts Anvil (Docker or Foundry), deploys the contracts, then starts the API (`:8000`) and the web app (`:3000`). Stop it with `scripts\stop_stack.cmd`.

Use Python 3.12, because Vyper ships wheels for it; 3.14 does not work. Create one venv for every Python package, from `contracts/`:

```bash
uv venv .venv --python 3.12
uv pip install --python .venv -r requirements.txt
uv pip install --python .venv -r ../backend/requirements.txt -r ../oracles/requirements.txt
```

Run the Python suites from the repo root:

```bash
cd contracts && .\.venv\Scripts\python.exe -m pytest tests -q
cd ../backend && ..\contracts\.venv\Scripts\python.exe -m pytest tests -q
cd ../oracles && ..\contracts\.venv\Scripts\python.exe -m pytest tests -q
```

Then the scripts suite, the in-process e2e and the web checks, also from the repo root:

```bash
.\contracts\.venv\Scripts\python.exe -m unittest discover -s scripts/tests -v
.\contracts\.venv\Scripts\python.exe scripts\e2e_local.py
cd web && npm ci && npm run build && npm test
```

See [docs/runbooks/local-dev.md](docs/runbooks/local-dev.md) for details, and [docs/runbooks/operations.md](docs/runbooks/operations.md) for production deploys and market operations.

## Architecture

- **MarketAMM** (`MarketAMM.vy`): static pm-AMM. The YES price is Φ((no − yes)/L). It charges a 100 bps fee, split 50/50 between the vault and LPs, and has an on-chain `closeGate`. See [docs/adr/0011-pm-amm-v2-close-gate.md](docs/adr/0011-pm-amm-v2-close-gate.md).
- **MarketFactory** (`MarketFactory.vy`): creates operator primaries and wildcards, and exposes `createPermissionlessMarket` for user listings. Seed LP goes to whoever provides the seed.
- **Exchange** (`Exchange.vy`): the leftover CLOB overlay. The production relayer (OU-T003) is off by default.
- **Shared**: all of the above use `ConditionalTokens.vy`, `ConsensusOracle.vy` and `FeeVault.vy`.

Book of record: [docs/adr/0007-amm-first-uniform-lvr.md](docs/adr/0007-amm-first-uniform-lvr.md). Base Sepolia runs MarketAMM v2 and MarketFactory v2 since 2026-09-24; the live addresses are in the operations runbook.
