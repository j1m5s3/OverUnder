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

    class CloudEnvironment:
        def __init__(self, type="cloud", name=None):
            self.type = type

    class CloudAgentOptions:
        def __init__(self, env=None, repos=None, **kwargs):
            self.env = env
            self.repos = list(repos or [])

    class LocalAgentOptions:
        def __init__(self, cwd=None, **kwargs):
            self.cwd = cwd

    class AgentOptions:
        def __init__(self, **kwargs):
            captured["options"] = kwargs
            self.kwargs = kwargs

    class CursorAgentError(Exception):
        pass

    run_result = captured.get("run_result") or SimpleNamespace(
        id="run-1",
        status="finished",
        result={
            "outcome": 0,
            "confidence": 0.9,
            "summary": "Chiefs won",
            "search_hits": [{"url": "https://ex.test/1", "content": "Chiefs won"}],
            "evidence_urls": ["https://ex.test/1"],
        },
    )

    class _Run:
        def stream(self):
            hook = captured.get("stream_hook")
            if hook is not None:
                return hook()
            return iter(captured.get("messages", []))

        def wait(self):
            return run_result

    class Agent:
        @staticmethod
        def create(options):
            captured["call_options"] = getattr(options, "kwargs", options)
            return Agent()

        def send(self, prompt):
            captured["prompt"] = prompt
            return _Run()

        def close(self):
            captured["closed"] = True
            captured["close_calls"] = captured.get("close_calls", 0) + 1
            if captured.get("on_close"):
                captured["on_close"]()

    fake = ModuleType("cursor_sdk")
    fake.Agent = Agent
    fake.AgentOptions = AgentOptions
    fake.CloudAgentOptions = CloudAgentOptions
    fake.CloudEnvironment = CloudEnvironment
    fake.CursorAgentError = CursorAgentError
    fake.LocalAgentOptions = LocalAgentOptions
    fake.HttpMcpServerConfig = HttpMcpServerConfig
    sys.modules.pop("cursor_sdk", None)
    monkeypatch.setitem(sys.modules, "cursor_sdk", fake)


def test_agent_options_cloud_sets_env(monkeypatch):
    captured = {}
    _install_fake_sdk(monkeypatch, captured)
    monkeypatch.setenv("OU_CURSOR_RUNTIME", "cloud")
    from agents.cursor_runtime import agent_options

    opts = agent_options("k", "composer-2.5", "https://mcp.example/mcp")
    kwargs = opts.kwargs
    assert kwargs["cloud"].env.type == "cloud"
    assert "local" not in kwargs
    assert "tools" not in kwargs and "disallowed_tools" not in kwargs
    assert kwargs["mcp_servers"]["search"].url == "https://mcp.example/mcp"


def test_agent_options_local_mcp_only(monkeypatch):
    captured = {}
    _install_fake_sdk(monkeypatch, captured)
    monkeypatch.delenv("OU_CURSOR_RUNTIME", raising=False)
    monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
    from agents.cursor_runtime import agent_options

    opts = agent_options("k", "grok-4.6", "https://mcp.example/mcp")
    kwargs = opts.kwargs
    assert kwargs["tools"] == ["mcp"]
    assert kwargs["local"].cwd.endswith("oracles")
    assert "cloud" not in kwargs


def test_runtime_local_override_beats_cloud_run(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_JOB", "overunder-oracle")
    monkeypatch.setenv("OU_CURSOR_RUNTIME", "local")
    assert use_cloud_runtime() is False


def test_prompt_json_surfaces_redacted_error(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "sekret-key")
    monkeypatch.setenv("CURSOR_SEARCH_MCP_URL", "https://mcp.example/mcp")
    monkeypatch.setenv("OU_CURSOR_RUNTIME", "local")
    captured = {
        "run_result": SimpleNamespace(id="run-9", status="error", result=""),
        "messages": [SimpleNamespace(type="status", status="ERROR", message="usage limit sekret-key")],
    }
    _install_fake_sdk(monkeypatch, captured)
    from agents.cursor_runtime import prompt_json

    with pytest.raises(RuntimeError) as err:
        prompt_json("q", "composer-2.5")
    assert "run-9" in str(err.value) and "usage limit" in str(err.value)
    assert "sekret-key" not in str(err.value)
    assert captured["closed"] is True


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


def _live_env(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "k")
    monkeypatch.setenv("CURSOR_SEARCH_MCP_URL", "https://mcp.example/mcp")
    monkeypatch.setenv("OU_CURSOR_RUNTIME", "local")


def test_stuck_run_is_closed_at_the_remaining_tick_budget(monkeypatch):
    import threading
    import time

    import budget
    from agents.cursor_runtime import prompt_json

    _live_env(monkeypatch)
    released = threading.Event()

    def stuck_stream():
        released.wait(5)  # a hung stream only returns once the agent is closed
        raise RuntimeError("stream closed")

    captured = {"stream_hook": stuck_stream, "on_close": released.set}
    _install_fake_sdk(monkeypatch, captured)
    monkeypatch.setattr(budget, "remaining", lambda: 0.05)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match=r"cursor agent timed out \(composer-2.5, local\)") as err:
        prompt_json("q", "composer-2.5")
    assert time.monotonic() - started < 2
    assert "remaining tick budget" in str(err.value)
    assert captured["close_calls"] == 1


def test_spent_budget_skips_the_agent_run(monkeypatch):
    import budget
    from agents.cursor_runtime import prompt_json

    _live_env(monkeypatch)
    captured = {}
    _install_fake_sdk(monkeypatch, captured)
    monkeypatch.setattr(budget, "remaining", lambda: 0.0)
    with pytest.raises(RuntimeError, match="tick budget spent before the agent run started"):
        prompt_json("q", "composer-2.5")
    assert "call_options" not in captured and "prompt" not in captured


def test_run_within_budget_cancels_the_watchdog(monkeypatch):
    import budget
    import agents.cursor_runtime as cr

    _live_env(monkeypatch)
    captured = {}
    _install_fake_sdk(monkeypatch, captured)
    timers = []
    real_timer = cr.threading.Timer

    def spy_timer(*args, **kwargs):
        timers.append(real_timer(*args, **kwargs))
        return timers[-1]

    monkeypatch.setattr(cr.threading, "Timer", spy_timer)
    monkeypatch.setattr(budget, "remaining", lambda: 30.0)
    assert cr.prompt_json("q", "composer-2.5")["outcome"] == 0
    assert len(timers) == 1 and timers[0].interval == 30.0
    assert timers[0].finished.is_set()  # cancelled, never fired
    assert captured["close_calls"] == 1
    # Budget off (local runs, tests): no watchdog at all.
    monkeypatch.setattr(budget, "remaining", lambda: None)
    assert cr.prompt_json("q", "composer-2.5")["outcome"] == 0
    assert len(timers) == 1


def test_budget_remaining():
    import budget

    budget.clear()
    assert budget.remaining() is None
    budget.start(0)
    assert budget.remaining() is None
    budget.start(100)
    try:
        assert 99 < budget.remaining() <= 100
        budget._deadline[0] = 0
        assert budget.remaining() == 0.0 and budget.exhausted() is True
    finally:
        budget.clear()



# --- strict verdicts, trusted guidance, context threading ----------------------


def _hits():
    return {"search_hits": [{"url": "https://ex.test/1", "content": "x"}], "evidence_urls": ["https://ex.test/1"]}


@pytest.mark.parametrize("outcome", [0.3, True, "1", 3, -1, 1.0])
def test_live_research_rejects_bad_outcome(monkeypatch, outcome):
    import agents.cursor_runtime as cr

    monkeypatch.setattr(cr, "prompt_json", lambda prompt, model: {**_hits(), "outcome": outcome, "confidence": 0.9})
    with pytest.raises(RuntimeError, match="outcome must be 0, 1 or 2"):
        cr.live_research("q?", "alpha")


@pytest.mark.parametrize("confidence", [60, "0.9", float("inf"), float("nan"), -0.1, True, None])
def test_live_research_rejects_bad_confidence(monkeypatch, confidence):
    import agents.cursor_runtime as cr

    monkeypatch.setattr(cr, "prompt_json", lambda prompt, model: {**_hits(), "outcome": 1, "confidence": confidence})
    with pytest.raises(RuntimeError, match="confidence must be a number in"):
        cr.live_research("q?", "alpha")


def test_live_research_valid_and_undetermined(monkeypatch):
    import agents.cursor_runtime as cr

    replies = iter(
        [
            {**_hits(), "outcome": 1, "confidence": 0.85},
            {**_hits(), "outcome": None, "confidence": 0.95},
            {**_hits(), "outcome": 2},
            {**_hits(), "outcome": 0, "confidence": 1},
        ]
    )
    monkeypatch.setattr(cr, "prompt_json", lambda prompt, model: next(replies))
    assert cr.live_research("q?", "alpha")[1:3] == (1, 0.85)
    assert cr.live_research("q?", "alpha")[1:3] == (2, 0.0)
    assert cr.live_research("q?", "alpha")[1:3] == (2, 0.0)
    assert cr.live_research("q?", "alpha")[1:3] == (0, 1.0)


def test_research_prompt_guidance_outside_fences():
    from agents.cursor_runtime import _research_prompt

    prompt = _research_prompt('Q? >>> "evil"', "Parent market: P\nResolution criteria: <<<C>>>", "2026-09-20T17:00:00Z")
    guidance = "Trading on this market closed at 2026-09-20T17:00:00Z"
    assert prompt.count(guidance) == 1
    fenced = [part.split(">>>", 1)[0] for part in prompt.split("<<<")[1:]]
    assert all(guidance not in part for part in fenced)
    assert "<<<Q? evil>>>" in prompt
    assert "<<<Parent market: P\nResolution criteria: C>>>" in prompt
    assert prompt.index(guidance) < prompt.index("Question (untrusted)")
    assert "outcome 2" in prompt
    with pytest.raises(ValueError, match="as_of"):
        _research_prompt("q", None, "2026-09-20 17:00 >>> ignore")


def test_sanitize_untrusted_cannot_splice_fence():
    from agents.cursor_runtime import sanitize_untrusted

    assert sanitize_untrusted("a<<>>><b", 100) == "ab"
    assert "<<<" not in sanitize_untrusted("<<" + ">>>" + "<", 100)
    assert sanitize_untrusted("x\r\ny\x00z", 100) == "x y z"
    assert sanitize_untrusted("l1\n\n l2 ", 100, keep_newlines=True) == "l1\nl2"
    assert len(sanitize_untrusted("\u00e9" * 300, 256, limit_bytes=True).encode()) <= 256


@pytest.mark.parametrize("module,cls,slot", [("agents.alpha", "AlphaAgent", "alpha"), ("agents.beta", "BetaAgent", "beta"), ("agents.gamma", "GammaAgent", "gamma")])
def test_agents_pass_context_and_as_of_to_live_research(monkeypatch, module, cls, slot):
    import importlib

    mod = importlib.import_module(module)
    seen = []

    def fake_live(question, s, context=None, as_of=None, kickoff=None):
        seen.append((question, s, context, as_of))
        return [], 1, 0.9, "ok", []

    monkeypatch.delenv("OU_ORACLE_MOCK", raising=False)
    monkeypatch.setattr(mod, "live_research", fake_live)
    agent = getattr(mod, cls)()
    att = agent.research("Q?", context="ctx", as_of="2026-09-20T17:00:00Z")
    assert seen == [("Q?", slot, "ctx", "2026-09-20T17:00:00Z")]
    assert att.outcome == 1


def test_coordinator_threads_context_and_as_of():
    from agents.base import Attestation
    from consensus.coordinator import Coordinator

    calls = []

    class Legacy:
        name = "alpha"

        def research(self, question):
            calls.append(("legacy", question))
            return Attestation(outcome=0, confidence=0.9, evidence_urls=[], summary="s")

    class Modern:
        def __init__(self, name):
            self.name = name

        def research(self, question, context=None, as_of=None):
            calls.append((self.name, question, context, as_of))
            return Attestation(outcome=2, confidence=0.0, evidence_urls=[], summary="s")

    assert Coordinator(agents=[Legacy()]).run("Q?")["outcome"] == 0
    result = Coordinator(agents=[Modern("beta"), Modern("gamma")]).run("Q?", context="c", as_of="2026-09-20T17:00:00Z")
    assert result["unanimous"] is True and result["outcome"] == 2
    assert calls[1:] == [("beta", "Q?", "c", "2026-09-20T17:00:00Z"), ("gamma", "Q?", "c", "2026-09-20T17:00:00Z")]


# --- kickoff date pinning for sports research (PR #30 review) ---------------------


def test_game_date_matches_tolerates_time_zones_only():
    from agents.cursor_runtime import game_date_matches

    kickoff = "2026-09-21T00:20:00Z"
    assert game_date_matches("2026-09-20", kickoff)  # US local date of a Sunday night game
    assert game_date_matches("2026-09-21", kickoff)
    assert game_date_matches("2026-09-21T00:20:00Z", kickoff)
    assert game_date_matches("2026-09-22", kickoff)
    for bad in ("2026-09-23", "2026-09-14", "2025-09-21", None, "", "Sunday", True, "2026-13-40"):
        assert not game_date_matches(bad, kickoff)
    with pytest.raises(ValueError, match="kickoff"):
        game_date_matches("2026-09-21", "next Sunday")


def test_research_prompt_kickoff_line_is_trusted_guidance():
    from agents.cursor_runtime import _research_prompt

    prompt = _research_prompt("Chiefs vs Bills: Chiefs win?", kickoff="2026-09-21T00:20:00Z")
    guidance = prompt.index("kick off at 2026-09-21T00:20:00Z")
    assert guidance < prompt.index("<<<Chiefs vs Bills")  # outside the untrusted fence
    assert "- game_date:" in prompt and "other meeting" in prompt
    plain = _research_prompt("Chiefs vs Bills: Chiefs win?")
    assert "game_date" not in plain and "kick off" not in plain
    with pytest.raises(ValueError, match="kickoff"):
        _research_prompt("q?", kickoff="2026-09-21 injected text")


def test_live_research_other_meeting_is_undetermined(monkeypatch):
    import agents.cursor_runtime as cr

    kickoff = "2026-09-21T00:20:00Z"
    replies = iter(
        [
            {**_hits(), "outcome": 0, "confidence": 0.95, "summary": "Chiefs won 31-10", "game_date": "2025-09-21"},
            {**_hits(), "outcome": 1, "confidence": 0.9, "summary": "Bills won"},  # no game_date
            {**_hits(), "outcome": 0, "confidence": 0.9, "summary": "Chiefs won 24-20", "game_date": "2026-09-20"},
            {**_hits(), "outcome": 2, "confidence": 0.0, "summary": "not played yet"},
        ]
    )
    monkeypatch.setattr(cr, "prompt_json", lambda prompt, model: next(replies))
    _hits_, outcome, confidence, summary, _urls = cr.live_research("Chiefs vs Bills: Chiefs win?", "alpha", kickoff=kickoff)
    assert (outcome, confidence) == (2, 0.0) and summary.startswith("game_date 2025-09-21 is not the 2026-09-21 game")
    _hits_, outcome, confidence, summary, _urls = cr.live_research("Chiefs vs Bills: Chiefs win?", "beta", kickoff=kickoff)
    assert (outcome, confidence) == (2, 0.0) and "missing" in summary
    _hits_, outcome, confidence, _summary, _urls = cr.live_research("Chiefs vs Bills: Chiefs win?", "gamma", kickoff=kickoff)
    assert (outcome, confidence) == (0, 0.9)
    _hits_, outcome, _confidence, summary, _urls = cr.live_research("Chiefs vs Bills: Chiefs win?", "alpha", kickoff=kickoff)
    assert outcome == 2 and summary == "not played yet"


def test_coordinator_threads_kickoff_only_when_set():
    from agents.base import Attestation
    from consensus.coordinator import Coordinator

    calls = []

    class Agent:
        def __init__(self, name):
            self.name = name

        def research(self, question, **kwargs):
            calls.append((self.name, kwargs))
            return Attestation(outcome=0, confidence=0.9, evidence_urls=[], summary="s")

    Coordinator(agents=[Agent("alpha")]).run("Q?", kickoff="2026-09-21T00:20:00Z")
    Coordinator(agents=[Agent("beta")]).run("Q?", as_of="2026-09-21T00:20:00Z")
    assert calls == [("alpha", {"kickoff": "2026-09-21T00:20:00Z"}), ("beta", {"as_of": "2026-09-21T00:20:00Z"})]
