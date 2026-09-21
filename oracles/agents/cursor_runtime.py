"""Lazy cursor-sdk runtime: local laptop vs cloud job, HTTP search MCP."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

_ORACLES_ROOT = Path(__file__).resolve().parents[1]
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

_DEFAULT_MODELS = {
    "alpha": "composer-2.5",
    "beta": "grok-4.6",
    "gamma": "gpt-5.1",
}

_MODEL_ENV = {
    "alpha": "OU_CURSOR_MODEL_ALPHA",
    "beta": "OU_CURSOR_MODEL_BETA",
    "gamma": "OU_CURSOR_MODEL_GAMMA",
}


def require_live_env() -> tuple[str, str]:
    api_key = os.getenv("CURSOR_API_KEY", "").strip()
    mcp_url = os.getenv("CURSOR_SEARCH_MCP_URL", "").strip()
    if not api_key:
        raise RuntimeError("CURSOR_API_KEY required for live cursor agents")
    if not mcp_url:
        raise RuntimeError("CURSOR_SEARCH_MCP_URL required for live cursor agents")
    return api_key, mcp_url


def model_id(slot: str) -> str:
    key = slot.lower()
    if key not in _DEFAULT_MODELS:
        raise ValueError(f"unknown cursor slot {slot}")
    override = os.getenv(_MODEL_ENV[key], "").strip()
    return override or _DEFAULT_MODELS[key]


def use_cloud_runtime() -> bool:
    if os.getenv("OU_CURSOR_RUNTIME", "").strip().lower() == "cloud":
        return True
    return bool(os.getenv("CLOUD_RUN_JOB", "").strip())


def _mcp_servers(mcp_url: str):
    from cursor_sdk import HttpMcpServerConfig

    headers = None
    raw = os.getenv("CURSOR_SEARCH_MCP_HEADERS", "").strip()
    if raw:
        headers = json.loads(raw)
    return {"search": HttpMcpServerConfig(url=mcp_url, headers=headers)}


def agent_options(api_key: str, model: str, mcp_url: str):
    from cursor_sdk import AgentOptions, CloudAgentOptions, LocalAgentOptions

    mcp = _mcp_servers(mcp_url)
    if use_cloud_runtime():
        return AgentOptions(
            api_key=api_key,
            model=model,
            cloud=CloudAgentOptions(repos=[]),
            mcp_servers=mcp,
        )
    return AgentOptions(
        api_key=api_key,
        model=model,
        local=LocalAgentOptions(cwd=str(_ORACLES_ROOT)),
        mcp_servers=mcp,
        disallowed_tools=["shell"],
    )


def _result_text(result) -> str:
    raw = getattr(result, "result", result)
    if callable(raw):
        raw = raw()
    if raw is None:
        return ""
    if isinstance(raw, dict):
        return json.dumps(raw)
    return str(raw)


def parse_json_object(text: str) -> dict:
    blob = (text or "").strip()
    fenced = _JSON_FENCE.search(blob)
    if fenced:
        blob = fenced.group(1)
    start, end = blob.find("{"), blob.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("cursor agent returned no JSON object")
    data = json.loads(blob[start : end + 1])
    if not isinstance(data, dict):
        raise RuntimeError("cursor agent JSON must be an object")
    return data


def _research_prompt(question: str) -> str:
    return f"""Research this prediction-market question using the search MCP tools.

Question: "{question}"

Respond with JSON only:
- outcome: 0 for yes/affirmative or 1 for no/negative
- confidence: a float between 0 and 1
- summary: brief explanation (max 280 chars)
- search_hits: list of {{url, content}} actually returned by search tools
- evidence_urls: subset of search_hits urls

Never invent URLs. evidence_urls must be taken from search_hits.
"""


def live_research(question: str, slot: str) -> tuple[list, int, float, str, list[str]]:
    from agents.base import SearchHit

    data = prompt_json(_research_prompt(question), model_id(slot))
    raw_hits = data.get("search_hits")
    if not isinstance(raw_hits, list) or not raw_hits:
        raise RuntimeError("search_hits required from cursor agent")
    hits = []
    for item in raw_hits:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if url:
            hits.append(SearchHit(url=url, content=str(item.get("content") or "")))
    if not hits:
        raise RuntimeError("search_hits required from cursor agent")
    hit_urls = {h.url for h in hits}
    evidence_urls = [url for url in (data.get("evidence_urls") or []) if url in hit_urls]
    outcome = int(data["outcome"])
    confidence = float(data.get("confidence") or 0)
    summary = str(data.get("summary") or "")[:280]
    return hits, outcome, confidence, summary, evidence_urls


def prompt_json(prompt: str, model: str) -> dict:
    api_key, mcp_url = require_live_env()
    from cursor_sdk import Agent

    result = Agent.prompt(prompt, agent_options(api_key, model, mcp_url))
    status = getattr(result, "status", "finished")
    if status == "error":
        raise RuntimeError(f"cursor agent run failed: {getattr(result, 'id', '')}")
    parsed = getattr(result, "result", None)
    if isinstance(parsed, dict):
        return parsed
    return parse_json_object(_result_text(result))
