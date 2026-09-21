import os
import sys
from types import ModuleType, SimpleNamespace

import pytest

from agents.cursor_runtime import (
    model_id,
    parse_json_object,
    require_live_env,
    use_cloud_runtime,
)


def test_require_live_env_fails_closed(monkeypatch):
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("CURSOR_SEARCH_MCP_URL", raising=False)
    with pytest.raises(RuntimeError, match="CURSOR_API_KEY required"):
        require_live_env()
    monkeypatch.setenv("CURSOR_API_KEY", "k")
    with pytest.raises(RuntimeError, match="CURSOR_SEARCH_MCP_URL required"):
        require_live_env()
    monkeypatch.setenv("CURSOR_SEARCH_MCP_URL", "https://mcp.example/mcp")
    assert require_live_env() == ("k", "https://mcp.example/mcp")


def test_model_id_defaults_and_overrides(monkeypatch):
    monkeypatch.delenv("OU_CURSOR_MODEL_ALPHA", raising=False)
    monkeypatch.delenv("OU_CURSOR_MODEL_BETA", raising=False)
    monkeypatch.delenv("OU_CURSOR_MODEL_GAMMA", raising=False)
    assert model_id("alpha") == "composer-2.5"
    assert model_id("beta") == "grok-4.6"
    assert model_id("gamma") == "gpt-5.1"
    monkeypatch.setenv("OU_CURSOR_MODEL_ALPHA", "composer-2")
    assert model_id("alpha") == "composer-2"
    with pytest.raises(ValueError):
        model_id("delta")


def test_use_cloud_runtime(monkeypatch):
    monkeypatch.delenv("OU_CURSOR_RUNTIME", raising=False)
    monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
    assert use_cloud_runtime() is False
    monkeypatch.setenv("OU_CURSOR_RUNTIME", "cloud")
    assert use_cloud_runtime() is True
    monkeypatch.delenv("OU_CURSOR_RUNTIME", raising=False)
    monkeypatch.setenv("CLOUD_RUN_JOB", "overunder-oracle")
    assert use_cloud_runtime() is True


def test_parse_json_object_from_fence():
    data = parse_json_object('noise\n```json\n{"outcome": 0, "summary": "ok"}\n```\n')
    assert data["outcome"] == 0


def _install_fake_sdk(monkeypatch, captured):
    class HttpMcpServerConfig:
        def __init__(self, url, headers=None, **kwargs):
            self.url = url
            self.headers = headers

    class CloudAgentOptions:
        def __init__(self, repos=None, **kwargs):
            self.repos = list(repos or [])

    class LocalAgentOptions:
        def __init__(self, cwd=None, **kwargs):
            self.cwd = cwd

    class AgentOptions:
        def __init__(self, **kwargs):
            captured["options"] = kwargs
            self.kwargs = kwargs

    class Agent:
        @staticmethod
        def prompt(prompt, options):
            captured["prompt"] = prompt
            captured["call_options"] = getattr(options, "kwargs", options)
            return SimpleNamespace(
                status="finished",
                result={
                    "outcome": 0,
                    "confidence": 0.9,
                    "summary": "Chiefs won",
                    "search_hits": [{"url": "https://ex.test/1", "content": "Chiefs won"}],
                    "evidence_urls": ["https://ex.test/1"],
                },
            )

    fake = ModuleType("cursor_sdk")
    fake.Agent = Agent
    fake.AgentOptions = AgentOptions
    fake.CloudAgentOptions = CloudAgentOptions
    fake.LocalAgentOptions = LocalAgentOptions
    fake.HttpMcpServerConfig = HttpMcpServerConfig
    sys.modules.pop("cursor_sdk", None)
    monkeypatch.setitem(sys.modules, "cursor_sdk", fake)


def test_agent_options_cloud_empty_repos(monkeypatch):
    captured = {}
    _install_fake_sdk(monkeypatch, captured)
    monkeypatch.setenv("OU_CURSOR_RUNTIME", "cloud")
    from agents.cursor_runtime import agent_options

    opts = agent_options("k", "composer-2.5", "https://mcp.example/mcp")
    kwargs = opts.kwargs
    assert kwargs["cloud"].repos == []
    assert "local" not in kwargs
    assert "disallowed_tools" not in kwargs
    assert kwargs["mcp_servers"]["search"].url == "https://mcp.example/mcp"


def test_agent_options_local_disallows_shell(monkeypatch):
    captured = {}
    _install_fake_sdk(monkeypatch, captured)
    monkeypatch.delenv("OU_CURSOR_RUNTIME", raising=False)
    monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
    from agents.cursor_runtime import agent_options

    opts = agent_options("k", "grok-4.6", "https://mcp.example/mcp")
    kwargs = opts.kwargs
    assert kwargs["disallowed_tools"] == ["shell"]
    assert kwargs["local"].cwd.endswith("oracles")
    assert "cloud" not in kwargs


def test_prompt_json_does_not_load_until_called(monkeypatch):
    sys.modules.pop("cursor_sdk", None)
    monkeypatch.setenv("CURSOR_API_KEY", "k")
    monkeypatch.setenv("CURSOR_SEARCH_MCP_URL", "https://mcp.example/mcp")
    captured = {}
    _install_fake_sdk(monkeypatch, captured)
    from agents.cursor_runtime import prompt_json

    data = prompt_json("q", "composer-2.5")
    assert data["outcome"] == 0
    assert "search_hits" in data
