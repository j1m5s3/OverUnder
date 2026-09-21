---
title: Cursor-runtime oracles
status: MIXED
area: oracles
summary: Live oracles use Python cursor-sdk with explicit models and remote HTTP search MCP; Cloud Run Job uses cloud agents; MockSearch stays for pytest/CI/anvil.
last_verified: 2026-09-20
pointers:
  - "[oracles/agents/cursor_runtime.py : L26-77]"
  - "[oracles/agents/cursor_runtime.py : L145-156]"
  - "[oracles/agents/alpha.py : L9-27]"
  - "[oracles/scores/scout.py : L268-302]"
  - "[oracles/scores/publish.py : L15-52]"
  - "[oracles/scores/job.py : L116-150]"
  - "[.github/workflows/deploy-gcp.yml : L85-96]"
  - "[.github/workflows/deploy-gcp.yml : L146-185]"
---

## Status

Accepted 2026-09-20.

## Context

Vendor LLM and search SDKs (Claude/Tavily, GPT/Brave, Gemini/Exa) coupled the oracles to six keys and blocked a thin Cloud Run scout. Search MCP must stay off GCP. Resolution still needs three agents and must not `submitConsensus` from the coordinator.

## Decision

- [SHIPPED] Dual runtime in `cursor_runtime.py`: `OU_CURSOR_RUNTIME=cloud` or `CLOUD_RUN_JOB` set → `CloudAgentOptions(repos=[])`; else `LocalAgentOptions(cwd=oracles/)`. MCP is always `HttpMcpServerConfig(url=CURSOR_SEARCH_MCP_URL)`. `disallowed_tools=["shell"]` is local-only. `cursor_sdk` is lazy-imported. [oracles/agents/cursor_runtime.py : L26-77]
- [SHIPPED] Models: `OU_CURSOR_MODEL_ALPHA/BETA/GAMMA` default `composer-2.5` / `grok-4.6` / `gpt-5.1`. Live path hard-fails if `CURSOR_API_KEY` or `CURSOR_SEARCH_MCP_URL` is missing.
- [SHIPPED] `overunder-oracle` Cloud Run Job plus `*/15` Scheduler. Cursor proxies HTTP MCP; GCP does not host MCP. [oracles/scores/job.py : L116-150]
- [SHIPPED] Score scout auto-POSTs on 3/3 with an HS256 JWT matching `_issue`. No new auth route. [oracles/scores/publish.py : L15-52]
- [SHIPPED] MockSearch and `OU_ORACLE_MOCK=1` remain the pytest/CI/anvil path. `Coordinator.run` still does not `submitConsensus`.

## Consequences

- One Cursor key plus a remote search MCP URL replace six vendor keys. Cloud scouts do not need a GCP MCP service.
- Negative: live research depends on Cursor cloud availability and a reachable MCP URL; Secret Manager must already hold `OU_CURSOR_API_KEY` and `OU_CURSOR_SEARCH_MCP_URL` or deploy fails closed.
