---
title: Cursor-runtime oracles
status: MIXED
area: oracles
summary: Live oracles use Python cursor-sdk (pinned 1.0.32) with explicit models and a remote HTTP search MCP. The Cloud Run Job defaults to local MCP-only agents bounded by the tick budget, with surfaced, redacted errors. MockSearch stays for pytest/CI/anvil.
last_verified: 2026-09-23
pointers:
  - "[oracles/agents/cursor_runtime.py : L31-87]"
  - "[oracles/agents/cursor_runtime.py : L123-143]"
  - "[oracles/agents/cursor_runtime.py : L291-384]"
  - "[oracles/budget.py : L1-17]"
  - "[oracles/requirements.txt : L4]"
  - "[oracles/agents/alpha.py : L9-31]"
  - "[oracles/scores/scout.py : L322-371]"
  - "[oracles/scores/publish.py : L15-52]"
  - "[oracles/scores/job.py : L190-250]"
  - "[.github/workflows/deploy-gcp.yml : L26-31]"
  - "[.github/workflows/deploy-gcp.yml : L233-342]"
  - "[.github/workflows/deploy-gcp.yml : L512-594]"
---

## Status

Accepted 2026-09-20.

Amended 2026-09-23 after live findings:
- The old cloud mode passed `CloudAgentOptions(repos=[])`, which serializes to `{}`. The SDK dropped it and silently created local agents with the full toolset, running next to the job's key env vars. No abuse was found. [oracles/agents/cursor_runtime.py : L66-87]
- The Cursor account had hit its usage limit (it resets 2026-10-01), so every live research run failed. A spend limit in the Cursor dashboard is a user action. Until then, research-dependent resolution (dual gate, fallback, general resolver) cannot run.

Changes:
- Cloud mode now sends `CloudAgentOptions(env=CloudEnvironment(type="cloud"))`. [oracles/agents/cursor_runtime.py : L66-87]
- Local mode is restricted to `tools=["mcp"]`, so no shell, read or edit tools.
- deploy-gcp sets `OU_CURSOR_RUNTIME=local` unless the repo var says `cloud`. [.github/workflows/deploy-gcp.yml : L291-292]
- Failed runs raise with the run id, model and mode, plus the streamed error text, redacted of the API key and MCP URL and capped at 500 characters. [oracles/agents/cursor_runtime.py : L291-295] [oracles/agents/cursor_runtime.py : L364-384]
- A watchdog closes an agent when the tick budget runs out (`OU_TICK_BUDGET_SECONDS`, default 780 s; the task timeout is the budget + 60 s). [oracles/agents/cursor_runtime.py : L302-361] [oracles/budget.py : L1-17]
- `cursor-sdk==1.0.32` is pinned. [oracles/requirements.txt : L4]

## Context

Vendor LLM and search SDKs (Claude/Tavily, GPT/Brave, Gemini/Exa) coupled the oracles to six keys and blocked a thin Cloud Run scout. Search MCP must stay off GCP. Resolution still needs three agents and must not `submitConsensus` from the coordinator.

## Decision

- [SHIPPED] Dual runtime in `cursor_runtime.py`. `OU_CURSOR_RUNTIME=cloud|local` wins; unset, `CLOUD_RUN_JOB` selects cloud. Cloud uses `CloudAgentOptions(env=CloudEnvironment(type="cloud"))`. Local uses `LocalAgentOptions(cwd=oracles/)` with `tools=["mcp"]`. MCP is always `HttpMcpServerConfig(url=CURSOR_SEARCH_MCP_URL)`. `cursor_sdk` is lazy-imported. [oracles/agents/cursor_runtime.py : L49-87]
- [SHIPPED] Models: `OU_CURSOR_MODEL_ALPHA/BETA/GAMMA` default `composer-2.5` / `grok-4.6` / `gpt-5.1`. Live path hard-fails if `CURSOR_API_KEY` or `CURSOR_SEARCH_MCP_URL` is missing. [oracles/agents/cursor_runtime.py : L18-46]
- [SHIPPED] Creator and market text is sanitized and fenced as untrusted, and verdict JSON is parsed strictly. Outcome 2 means undetermined. [oracles/agents/cursor_runtime.py : L123-143] [oracles/agents/cursor_runtime.py : L232-253]
- [SHIPPED] `overunder-oracle` Cloud Run Job plus `*/15` Scheduler. Cursor proxies HTTP MCP; GCP does not host MCP. [oracles/scores/job.py : L190-250] [.github/workflows/deploy-gcp.yml : L512-594]
- [SHIPPED] Score scout auto-POSTs on 3/3 with an HS256 JWT matching `_issue`. No new auth route. [oracles/scores/scout.py : L332-371] [oracles/scores/publish.py : L15-52]
- [SHIPPED] MockSearch and `OU_ORACLE_MOCK=1` remain the pytest/CI/anvil path. `Coordinator.run` still does not `submitConsensus`.

## Consequences

- One Cursor key plus a remote search MCP URL replace six vendor keys. Cloud scouts do not need a GCP MCP service.
- Sports auto-submit lives in `oracles/resolve/` under [ADR-0009](0009-dual-gate-sports-resolve-and-week-listing.md); the score scout still never submits.
- Negative: live research depends on Cursor availability, account quota and a reachable MCP URL. Secret Manager must already hold `OU_CURSOR_API_KEY` and `OU_CURSOR_SEARCH_MCP_URL`, or deploy fails closed. [.github/workflows/deploy-gcp.yml : L26-31]
- Negative: local mode runs the agent inside the job container. The MCP-only tool allowlist is the only barrier between injected web content and the job's env secrets.
- Negative: the SDK exposes no run timeout. If the watchdog's `close()` does not unblock the stream, the Cloud Run task timeout ends the tick, and resolvers must tolerate a partial multi-transaction send.
