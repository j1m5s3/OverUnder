---
title: Cursor-runtime oracles
status: MIXED
area: oracles
summary: Live oracles use Python cursor-sdk (pinned 1.0.32) with explicit models and a remote HTTP search MCP. The Cloud Run Job defaults to local MCP-only agents bounded by the tick budget, with surfaced, redacted errors. MockSearch stays for pytest/CI/anvil.
last_verified: 2026-09-23
pointers:
  - "[oracles/agents/cursor_runtime.py : L30-86]"
  - "[oracles/agents/cursor_runtime.py : L122-142]"
  - "[oracles/agents/cursor_runtime.py : L239-332]"
  - "[oracles/budget.py : L1-9]"
  - "[oracles/requirements.txt : L4]"
  - "[oracles/agents/alpha.py : L9-27]"
  - "[oracles/scores/scout.py : L299-347]"
  - "[oracles/scores/publish.py : L15-52]"
  - "[oracles/scores/job.py : L182-238]"
  - "[.github/workflows/deploy-gcp.yml : L26-31]"
  - "[.github/workflows/deploy-gcp.yml : L223-318]"
  - "[.github/workflows/deploy-gcp.yml : L486-538]"
---

## Status

Accepted 2026-09-20.

Amended 2026-09-23 after live findings:
- The old cloud mode passed `CloudAgentOptions(repos=[])`, which serializes to `{}`. The SDK dropped it and silently created local agents with the full toolset, running next to the job's key env vars. No abuse was found. [oracles/agents/cursor_runtime.py : L65-86]
- The Cursor account had hit its usage limit (it resets 2026-10-01), so every live research run failed. A spend limit in the Cursor dashboard is a user action. Until then, research-dependent resolution (dual gate, fallback, general resolver) cannot run.

Changes:
- Cloud mode now sends `CloudAgentOptions(env=CloudEnvironment(type="cloud"))`. [oracles/agents/cursor_runtime.py : L65-86]
- Local mode is restricted to `tools=["mcp"]`, so no shell, read or edit tools.
- deploy-gcp sets `OU_CURSOR_RUNTIME=local` unless the repo var says `cloud`. [.github/workflows/deploy-gcp.yml : L272-273]
- Failed runs raise with the run id, model and mode, plus the streamed error text, redacted of the API key and MCP URL and capped at 500 characters. [oracles/agents/cursor_runtime.py : L239-243] [oracles/agents/cursor_runtime.py : L312-332]
- A watchdog closes an agent when the tick budget runs out (`OU_TICK_BUDGET_SECONDS`, default 780 s; the task timeout is the budget + 60 s). [oracles/agents/cursor_runtime.py : L250-309] [oracles/budget.py : L1-9]
- `cursor-sdk==1.0.32` is pinned. [oracles/requirements.txt : L4]

## Context

Vendor LLM and search SDKs (Claude/Tavily, GPT/Brave, Gemini/Exa) coupled the oracles to six keys and blocked a thin Cloud Run scout. Search MCP must stay off GCP. Resolution still needs three agents and must not `submitConsensus` from the coordinator.

## Decision

- [SHIPPED] Dual runtime in `cursor_runtime.py`. `OU_CURSOR_RUNTIME=cloud|local` wins; unset, `CLOUD_RUN_JOB` selects cloud. Cloud uses `CloudAgentOptions(env=CloudEnvironment(type="cloud"))`. Local uses `LocalAgentOptions(cwd=oracles/)` with `tools=["mcp"]`. MCP is always `HttpMcpServerConfig(url=CURSOR_SEARCH_MCP_URL)`. `cursor_sdk` is lazy-imported. [oracles/agents/cursor_runtime.py : L48-86]
- [SHIPPED] Models: `OU_CURSOR_MODEL_ALPHA/BETA/GAMMA` default `composer-2.5` / `grok-4.6` / `gpt-5.1`. Live path hard-fails if `CURSOR_API_KEY` or `CURSOR_SEARCH_MCP_URL` is missing. [oracles/agents/cursor_runtime.py : L17-45]
- [SHIPPED] Creator and market text is sanitized and fenced as untrusted, and verdict JSON is parsed strictly. Outcome 2 means undetermined. [oracles/agents/cursor_runtime.py : L122-142] [oracles/agents/cursor_runtime.py : L187-208]
- [SHIPPED] `overunder-oracle` Cloud Run Job plus `*/15` Scheduler. Cursor proxies HTTP MCP; GCP does not host MCP. [oracles/scores/job.py : L182-238] [.github/workflows/deploy-gcp.yml : L486-538]
- [SHIPPED] Score scout auto-POSTs on 3/3 with an HS256 JWT matching `_issue`. No new auth route. [oracles/scores/scout.py : L308-347] [oracles/scores/publish.py : L15-52]
- [SHIPPED] MockSearch and `OU_ORACLE_MOCK=1` remain the pytest/CI/anvil path. `Coordinator.run` still does not `submitConsensus`.

## Consequences

- One Cursor key plus a remote search MCP URL replace six vendor keys. Cloud scouts do not need a GCP MCP service.
- Sports auto-submit lives in `oracles/resolve/` under [ADR-0009](0009-dual-gate-sports-resolve-and-week-listing.md); the score scout still never submits.
- Negative: live research depends on Cursor availability, account quota and a reachable MCP URL. Secret Manager must already hold `OU_CURSOR_API_KEY` and `OU_CURSOR_SEARCH_MCP_URL`, or deploy fails closed. [.github/workflows/deploy-gcp.yml : L26-31]
- Negative: local mode runs the agent inside the job container. The MCP-only tool allowlist is the only barrier between injected web content and the job's env secrets.
- Negative: the SDK exposes no run timeout. If the watchdog's `close()` does not unblock the stream, the Cloud Run task timeout ends the tick, and resolvers must tolerate a partial multi-transaction send.
