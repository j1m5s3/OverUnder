# OverUnder

Prediction markets on Base: Polymarket-style CLOB primaries, AMM wildcards, and AI-agent oracles.

MVP settlement is **USDC**. Oracles must reach **unanimous (3/3)** consensus to resolve; otherwise **agent majority + participant votes** after 24 hours. Protocol fees accrue to a vault; **OU** redeems for USDC at NAV.

## Stack

| Layer | Tech |
| --- | --- |
| Contracts | Vyper, Moccasin, Anvil |
| API | FastAPI |
| Oracles | Python agents (Claude/GPT/Gemini + search) |
| Web | Next.js, Tailwind, wagmi, Privy |
| Mobile | Flutter later — see `mobile/README.md` |

## Quickstart (local)

```bash
cp .env.example .env
cd infra && docker compose up -d
cd ../contracts && uv pip install -e ".[dev]" || pip install -r requirements.txt
moccasin test
python script/deploy.py
cd ../backend && pip install -e .
uvicorn app.main:app --reload
cd ../web && npm install && npm run dev
```

## Local stack (Windows)

```bat
scripts\run_stack.cmd
```

Opens Anvil (Docker or Foundry), deploys contracts, then starts the API (`:8000`) and web app (`:3000`). Stop with `scripts\stop_stack.cmd`.


Python 3.12 (Vyper wheels; not 3.14):

```bash
cd contracts
uv venv .venv --python 3.12
uv pip install --python .venv -r requirements.txt
uv pip install --python .venv -r ../backend/requirements.txt -r ../oracles/requirements.txt
.\.venv\Scripts\python.exe -m pytest tests -q
cd ../backend && ..\contracts\.venv\Scripts\python.exe -m pytest tests -q
cd ../oracles && ..\contracts\.venv\Scripts\python.exe -m pytest tests -q
cd .. && .\contracts\.venv\Scripts\python.exe scripts\e2e_local.py
```


## Architecture

Primary markets trade on an off-chain CLOB settled by `Exchange.vy`. Wildcard child markets use `MarketAMM.vy` (CPMM). Both share `ConditionalTokens.vy`, `ConsensusOracle.vy`, and `FeeVault.vy`.
