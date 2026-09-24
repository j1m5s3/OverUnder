import json
import sys
import types
from datetime import datetime, timezone

import httpx
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from scores.job import run_job, select_targets, skip_fresh, sports_primaries

ANVIL_0 = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
ANVIL_0_ADDR = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"
JWT_SECRET = "test-secret-please-use-32b-min!!"
NONCE = "3f2a9c1e5b7d4a608e1f2c3b4a5d6e7f"
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def test_sports_primaries_only():
    cards = [
        {"primary": {"conditionId": "0xa", "question": "Chiefs vs Broncos: win?", "marketType": 0}},
        {"primary": {"conditionId": "0xb", "question": "Will the bill pass the Senate?", "marketType": 0}},
        {"primary": {"conditionId": "0xc", "question": "Kelce to fumble?", "marketType": 1}},
        {"primary": {"conditionId": "0xd", "question": "Lakers vs. Celtics", "marketType": 0}},
    ]
    ids = [p["conditionId"] for p in sports_primaries(cards)]
    assert ids == ["0xa", "0xd"]


def test_skip_fresh_unless_in_progress():
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    fresh = {"status": "final", "updatedAt": "2026-09-20T11:55:00+00:00"}
    live = {"status": "in_progress", "updatedAt": "2026-09-20T11:55:00+00:00"}
    assert skip_fresh(fresh, now, 600) is True
    assert skip_fresh(live, now, 600) is False
    assert skip_fresh(None, now, 600) is False


def test_select_targets_caps():
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    primaries = [
        {"conditionId": "0xa", "question": "Chiefs vs Broncos"},
        {"conditionId": "0xb", "question": "Lakers vs Celtics"},
        {"conditionId": "0xc", "question": "Packers vs Bears"},
    ]
    details = {
        "0xa": {"score": {"status": "final", "updatedAt": "2026-09-20T11:55:00+00:00"}},
        "0xb": {"score": {"status": "in_progress", "updatedAt": "2026-09-20T11:55:00+00:00"}},
        "0xc": {"score": None},
    }
    selected = select_targets(primaries, lambda cid: details[cid], now, cap=2, window=600)
    assert [p["conditionId"] for p in selected] == ["0xb", "0xc"]


def test_run_job_fake_http_no_cursor(monkeypatch):
    monkeypatch.setenv("OU_API_URL", "http://api.test")
    monkeypatch.setenv("OU_SCOUT_MAX_MARKETS", "5")
    seen = []

    payloads = {
        "http://api.test/api/v1/markets": [
            {"primary": {"conditionId": "0xa", "question": "Chiefs vs Broncos: win?", "marketType": 0}, "children": []},
            {"primary": {"conditionId": "0xb", "question": "Will the bill pass?", "marketType": 0}, "children": []},
        ],
        "http://api.test/api/v1/markets/0xa": {"score": None, "question": "Chiefs vs Broncos: win?"},
    }

    def http_get(url: str):
        return payloads[url]

    class FakeCoord:
        def run(self, question, condition_id=None, kickoff=None):
            seen.append((question, condition_id))
            return {"unanimous": True}

    summary = run_job(http_get=http_get, coordinator_factory=FakeCoord, now=datetime(2026, 9, 20, tzinfo=timezone.utc))
    assert summary["attempted"] == 1
    assert summary["ok"] is True
    assert seen == [("Chiefs vs Broncos: win?", "0xa")]


def _recording(details):
    calls = []

    def fetch(cid):
        calls.append(cid)
        value = details[cid]
        if isinstance(value, Exception):
            raise value
        return value

    return fetch, calls


def test_select_targets_skips_resolved_future_final_cancelled():
    past = int(NOW.timestamp()) - 3600
    primaries = [
        {"conditionId": "0xres", "closeTime": past, "resolved": True},
        {"conditionId": "0xfut", "closeTime": int(NOW.timestamp()) + 3600},
        {"conditionId": "0xfin", "closeTime": past},
        {"conditionId": "0xcan", "closeTime": past},
        {"conditionId": "0xok", "closeTime": past},
    ]
    details = {
        "0xres": {"score": None},
        "0xfut": {"score": None},
        "0xfin": {"score": {"status": "final", "updatedAt": "2026-09-19T00:00:00+00:00"}},
        "0xcan": {"score": {"status": "cancelled", "updatedAt": "2026-09-19T00:00:00+00:00"}},
        "0xok": {"score": {"status": "in_progress", "updatedAt": "2026-09-20T11:59:00+00:00"}},
    }
    fetch, calls = _recording(details)
    selected = select_targets(primaries, fetch, NOW, cap=5, window=600)
    assert [p["conditionId"] for p in selected] == ["0xok"]
    assert "0xres" not in calls
    assert "0xfut" not in calls


def test_select_targets_oldest_close_first():
    primaries = [
        {"conditionId": "0xc", "closeTime": 300},
        {"conditionId": "0xa", "closeTime": 100},
        {"conditionId": "0xb", "closeTime": 200},
    ]
    fetch, calls = _recording({"0xa": {"score": None}, "0xb": {"score": None}, "0xc": {"score": None}})
    selected = select_targets(primaries, fetch, NOW, cap=2, window=600, recent=10**10)
    assert [p["closeTime"] for p in selected] == [100, 200]
    assert calls == ["0xa", "0xb"]


def test_select_targets_no_starvation():
    final = {"score": {"status": "final", "updatedAt": "2026-09-01T00:00:00+00:00"}}
    primaries = [{"conditionId": f"0xf{i}", "closeTime": 100 + i} for i in range(5)]
    primaries.append({"conditionId": "0xnew", "closeTime": 1000})
    details = {p["conditionId"]: final for p in primaries[:5]}
    details["0xnew"] = {"score": None}
    fetch, _ = _recording(details)
    selected = select_targets(primaries, fetch, NOW, cap=1, window=600)
    assert [p["conditionId"] for p in selected] == ["0xnew"]


def test_select_targets_detail_error_skips(capsys):
    primaries = [
        {"conditionId": "0xa", "closeTime": 100},
        {"conditionId": "0xbad", "closeTime": 150},
        {"conditionId": "0xc", "closeTime": 200},
    ]
    details = {"0xa": {"score": None}, "0xbad": RuntimeError("404 not found"), "0xc": {"score": None}}
    fetch, calls = _recording(details)
    selected = select_targets(primaries, fetch, NOW, cap=5, window=600)
    assert [p["conditionId"] for p in selected] == ["0xa", "0xc"]
    assert calls == ["0xa", "0xbad", "0xc"]
    assert "scout detail failed 0xbad" in capsys.readouterr().err


def test_select_targets_max_age_drops_stale_unscored():
    clock = int(NOW.timestamp())
    primaries = [
        {"conditionId": "0xold", "closeTime": clock - 10 * 86400},
        {"conditionId": "0xnew", "closeTime": clock - 3600},
    ]
    fetch, calls = _recording({"0xold": {"score": None}, "0xnew": {"score": None}})
    selected = select_targets(primaries, fetch, NOW, cap=1, window=600, max_age=7 * 86400)
    assert [p["conditionId"] for p in selected] == ["0xnew"]
    assert calls == ["0xnew"]
    # Without max_age the old game is still scouted, but only after the recent kickoff.
    fetch, _ = _recording({"0xold": {"score": None}, "0xnew": {"score": None}})
    assert [p["conditionId"] for p in select_targets(primaries, fetch, NOW, cap=1, window=600)] == ["0xnew"]
    fetch, _ = _recording({"0xold": {"score": None}, "0xnew": {"score": None}})
    assert [p["conditionId"] for p in select_targets(primaries, fetch, NOW, cap=2, window=600)] == ["0xnew", "0xold"]


def test_select_targets_stuck_backlog_never_starves_new_game():
    clock = int(NOW.timestamp())
    stale = "2026-09-20T11:45:00+00:00"  # 15 minutes ago: outside the 600s freshness window
    primaries = [{"conditionId": f"0xold{i}", "closeTime": clock - (20 + i) * 86400} for i in range(5)]
    primaries.append({"conditionId": "0xnew", "closeTime": clock - 3 * 3600})
    details = {p["conditionId"]: {"score": {"status": "postponed", "updatedAt": stale}} for p in primaries[:3]}
    details.update({p["conditionId"]: {"score": None} for p in primaries[3:5]})
    details["0xnew"] = {"score": None}
    fetch, calls = _recording(details)
    selected = select_targets(primaries, fetch, NOW, cap=5, window=600, max_age=0)
    assert selected[0]["conditionId"] == "0xnew"
    assert calls[0] == "0xnew"
    assert len(selected) == 5


def test_select_targets_backlog_fills_leftover_capacity_and_rotates():
    clock = int(NOW.timestamp())
    backlog = [{"conditionId": f"0xb{i}", "closeTime": clock - (10 + i) * 86400} for i in range(4)]
    recent = [{"conditionId": "0xr", "closeTime": clock - 600}]
    details = {p["conditionId"]: {"score": None} for p in backlog + recent}
    fetch, _ = _recording(details)
    selected = [p["conditionId"] for p in select_targets(backlog + recent, fetch, NOW, cap=3, window=600)]
    assert selected[0] == "0xr" and len(selected) == 3
    assert set(selected[1:]) <= {p["conditionId"] for p in backlog}
    seen = set()
    for tick in range(4):
        later = datetime.fromtimestamp(clock + tick * 900, tz=timezone.utc)
        fetch, _ = _recording(details)
        seen.update(p["conditionId"] for p in select_targets(backlog, fetch, later, cap=1, window=600))
    assert seen == {p["conditionId"] for p in backlog}


def test_recent_seconds_env(monkeypatch):
    from scores.job import recent_seconds

    monkeypatch.delenv("OU_SCOUT_RECENT_SECONDS", raising=False)
    assert recent_seconds() == 36 * 3600
    monkeypatch.setenv("OU_SCOUT_RECENT_SECONDS", "7200")
    assert recent_seconds() == 7200
    monkeypatch.setenv("OU_SCOUT_RECENT_SECONDS", "soon")
    with pytest.raises(RuntimeError, match="OU_SCOUT_RECENT_SECONDS"):
        recent_seconds()


def test_run_job_budget_exhausted_skips(monkeypatch):
    import budget

    monkeypatch.setenv("OU_API_URL", "http://api.test")
    payloads = {
        "http://api.test/api/v1/markets": [
            {"primary": {"conditionId": "0xa", "question": "Chiefs vs Broncos: win?", "marketType": 0}, "children": []},
        ],
        "http://api.test/api/v1/markets/0xa": {"score": None},
    }
    runs = []

    class FakeCoord:
        def run(self, question, condition_id=None, kickoff=None):
            runs.append(condition_id)
            return {"unanimous": True}

    budget.start(1)
    budget._deadline[0] = 0
    try:
        summary = run_job(http_get=payloads.__getitem__, coordinator_factory=FakeCoord, now=NOW)
    finally:
        budget.clear()
    assert runs == []
    assert summary["results"] == [{"conditionId": "0xa", "ok": True, "skipped": "budget"}]


def test_orchestrator_runs_later_stages_after_score_error():
    from job import run_tick

    order = []

    def boom(**kwargs):
        order.append("scores")
        raise RuntimeError("score fail")

    def ok(name):
        def inner(**kwargs):
            order.append(name)
            return {name: True}

        return inner

    class Sched:
        def run(self):
            order.append("schedule")
            return {"unanimous": True}

    summary = run_tick(
        score_job=boom,
        resolve_job=ok("resolve"),
        resolve_general_job=ok("resolve_general"),
        schedule_factory=Sched,
        listing_job=ok("listing"),
    )
    assert order == ["scores", "resolve", "resolve_general", "schedule", "listing"]
    assert summary["scores"]["ok"] is False
    assert summary["resolve"]["resolve"] is True
    assert summary["resolve_general"]["resolve_general"] is True
    assert "preflight" not in summary


def test_orchestrator_resolve_sees_listed_chiefs(monkeypatch):
    from job import run_tick

    seen = []
    monkeypatch.setenv("OU_API_URL", "http://api.test")
    payloads = {
        "http://api.test/api/v1/markets": [
            {"primary": {"conditionId": "0xa", "question": "Chiefs vs Broncos: win?", "marketType": 0}, "children": []},
        ]
    }

    def http_get(url: str):
        return payloads[url]

    def scores(**kwargs):
        return {"attempted": 0}

    def resolve(*, http_get=None, **kwargs):
        cards = http_get("http://api.test/api/v1/markets")
        seen.extend(sports_primaries(cards))
        return {"attempted": len(seen)}

    general_seen = []

    def resolve_general(*, http_get=None, **kwargs):
        general_seen.append(http_get)
        return {"ok": True, "attempted": 0}

    class Sched:
        def run(self):
            return {"unanimous": False}

    def listing(**kwargs):
        return {"created": []}

    summary = run_tick(
        http_get=http_get,
        score_job=scores,
        resolve_job=resolve,
        resolve_general_job=resolve_general,
        schedule_factory=Sched,
        listing_job=listing,
    )
    assert seen[0]["question"] == "Chiefs vs Broncos: win?"
    assert summary["resolve"]["attempted"] == 1
    assert general_seen == [http_get]


# --- auth preflight (step 6) ---


@pytest.fixture
def operator_env(monkeypatch):
    monkeypatch.setenv("OU_API_URL", "http://api.test")
    monkeypatch.setenv("CHAIN_ID", "84532")
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("OPERATOR_PRIVATE_KEY", ANVIL_0)


class FakeApi:
    """httpx.MockTransport backend: scripted probe responses, nonce and SIWE."""

    def __init__(self, probes, siwe=(200, {"token": "TOKEN_SENTINEL", "address": ANVIL_0_ADDR})):
        self.probes = list(probes)
        self.siwe = siwe
        self.calls = []
        self.nonce_paths = []
        self.siwe_bodies = []
        self.auth_headers = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/markets/schedule":
            self.calls.append("probe")
            assert json.loads(request.content) == []
            self.auth_headers.append(request.headers.get("authorization") or "")
            status, body = self.probes.pop(0) if len(self.probes) > 1 else self.probes[0]
            if isinstance(body, str):
                return httpx.Response(status, text=body)
            return httpx.Response(status, json=body)
        if request.method == "GET" and path.startswith("/api/v1/auth/nonce/"):
            self.calls.append("nonce")
            self.nonce_paths.append(path)
            return httpx.Response(200, json={"nonce": NONCE})
        if request.method == "POST" and path == "/api/v1/auth/siwe":
            self.calls.append("siwe")
            self.siwe_bodies.append(json.loads(request.content))
            status, body = self.siwe
            return httpx.Response(status, json=body)
        raise AssertionError(f"unexpected request {request.method} {path}")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def test_preflight_ok_no_bootstrap(operator_env):
    from operator_auth import auth_preflight

    api = FakeApi([(200, [])])
    with api.client() as client:
        result = auth_preflight(client=client, now=NOW)
    assert result["ok"] is True
    assert result["bootstrapped"] is False
    assert result["status"] == 200
    assert result["operator"] == ANVIL_0_ADDR
    assert api.calls == ["probe"]
    assert api.auth_headers[0].startswith("Bearer ")


def test_preflight_unknown_user_bootstraps_via_siwe(operator_env, capsys):
    from operator_auth import auth_preflight

    api = FakeApi([(401, {"detail": "unknown user"}), (200, [])])
    with api.client() as client:
        result = auth_preflight(client=client, now=NOW)
    assert api.calls == ["probe", "nonce", "siwe", "probe"]
    assert api.nonce_paths == [f"/api/v1/auth/nonce/{ANVIL_0_ADDR}"]
    body = api.siwe_bodies[0]
    message = body["message"]
    # Mirror backend/app/auth/router.py siwe(): recover, address in message, nonce in message.
    recovered = Account.recover_message(encode_defunct(text=message), signature=body["signature"])
    assert recovered.lower() == ANVIL_0_ADDR
    assert body["address"].lower() == ANVIL_0_ADDR
    assert NONCE in message
    assert ANVIL_0_ADDR in message.lower()
    assert message.splitlines()[0].endswith("wants you to sign in with your Ethereum account:")
    assert message.splitlines()[0].startswith("api.test ")
    assert "Chain ID: 84532" in message
    assert "Issued At: 2026-09-20T12:00:00Z" in message
    assert result["ok"] is True
    assert result["bootstrapped"] is True
    out = capsys.readouterr()
    assert "TOKEN_SENTINEL" not in json.dumps(result)
    assert "TOKEN_SENTINEL" not in out.out + out.err
    assert ANVIL_0[2:] not in json.dumps(result) + out.out + out.err


def test_preflight_forbidden_bootstraps_once_and_recovers(operator_env):
    from operator_auth import auth_preflight

    api = FakeApi([(403, {"detail": "operator only"}), (200, [])])
    with api.client() as client:
        result = auth_preflight(client=client, now=NOW)
    assert api.calls == ["probe", "nonce", "siwe", "probe"]
    assert result["ok"] is True
    assert result["bootstrapped"] is True


def test_preflight_invalid_token_names_jwt_secret(operator_env):
    from operator_auth import auth_preflight

    api = FakeApi([(401, {"detail": "invalid token"})])
    with api.client() as client:
        result = auth_preflight(client=client, now=NOW)
    assert result["ok"] is False
    assert "JWT_SECRET" in result["cause"]
    assert api.calls == ["probe"]
    assert JWT_SECRET not in json.dumps(result)


def test_preflight_forbidden_names_operator_key(operator_env):
    from operator_auth import auth_preflight

    api = FakeApi([(403, {"detail": "operator only"})])
    with api.client() as client:
        result = auth_preflight(client=client, now=NOW)
    assert result["ok"] is False
    assert "OPERATOR_PRIVATE_KEY" in result["cause"]
    assert ANVIL_0_ADDR in result["cause"]
    assert api.calls.count("siwe") == 1
    assert api.calls == ["probe", "nonce", "siwe", "probe"]
    assert ANVIL_0[2:] not in json.dumps(result)


def test_preflight_bootstrap_only_once(operator_env):
    from operator_auth import auth_preflight

    api = FakeApi([(401, {"detail": "unknown user"})])
    with api.client() as client:
        result = auth_preflight(client=client, now=NOW)
    assert api.calls.count("nonce") == 1
    assert api.calls.count("siwe") == 1
    assert result["ok"] is False
    assert "after SIWE" in result["cause"]


def test_preflight_siwe_rejection_reported(operator_env):
    from operator_auth import auth_preflight

    api = FakeApi([(401, {"detail": "unknown user"})], siwe=(401, {"detail": "nonce mismatch"}))
    with api.client() as client:
        result = auth_preflight(client=client, now=NOW)
    assert api.calls == ["probe", "nonce", "siwe"]
    assert result["ok"] is False
    assert result["bootstrapped"] is False
    assert result["cause"] == "siwe failed: 401 nonce mismatch"


def test_preflight_missing_operator_key(operator_env, monkeypatch):
    from operator_auth import auth_preflight

    monkeypatch.delenv("OPERATOR_PRIVATE_KEY")
    api = FakeApi([(200, [])])
    with api.client() as client:
        result = auth_preflight(client=client, now=NOW)
    assert result["ok"] is False
    assert result["cause"].startswith("config:")
    assert "OPERATOR_PRIVATE_KEY" in result["cause"]
    assert api.calls == []


def test_preflight_invalid_operator_key_never_echoes_key(operator_env, monkeypatch):
    from operator_auth import auth_preflight

    bad = "0x" + "zz" * 32
    monkeypatch.setenv("OPERATOR_PRIVATE_KEY", bad)
    api = FakeApi([(200, [])])
    with api.client() as client:
        result = auth_preflight(client=client, now=NOW)
    assert result["cause"] == "config: OPERATOR_PRIVATE_KEY invalid"
    assert "zz" not in json.dumps(result)
    assert api.calls == []


def test_preflight_missing_api_url_and_jwt_secret(operator_env, monkeypatch):
    from operator_auth import auth_preflight

    monkeypatch.delenv("JWT_SECRET")
    api = FakeApi([(200, [])])
    with api.client() as client:
        assert auth_preflight(client=client, now=NOW)["cause"] == "config: JWT_SECRET missing"
        monkeypatch.delenv("OU_API_URL")
        assert auth_preflight(client=client, now=NOW)["cause"] == "config: OU_API_URL missing"
    assert api.calls == []


def test_preflight_route_missing_and_html_detail(operator_env):
    from operator_auth import auth_preflight

    api = FakeApi([(404, "<html>not found</html>")])
    with api.client() as client:
        result = auth_preflight(client=client, now=NOW)
    assert result["ok"] is False
    assert result["detail"] == ""
    assert "OU_API_URL" in result["cause"]


def test_preflight_transport_error_is_unreachable(operator_env):
    from operator_auth import auth_preflight

    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = auth_preflight(client=client, now=NOW)
    assert result["ok"] is False
    assert result["cause"] == "api unreachable: ConnectError"


def test_preflight_server_error_is_unreachable(operator_env):
    from operator_auth import auth_preflight

    api = FakeApi([(503, "unavailable")])
    with api.client() as client:
        result = auth_preflight(client=client, now=NOW)
    assert result["cause"] == "api unreachable: 503"


def test_build_siwe_message_eip4361_shape():
    from operator_auth import build_siwe_message

    msg = build_siwe_message(
        api_url="https://api.example.run.app",
        address="0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        nonce=NONCE,
        chain_id=84532,
        issued_at=datetime(2026, 9, 20, 12, 0),
    )
    lines = msg.split("\n")
    assert lines[0] == "api.example.run.app wants you to sign in with your Ethereum account:"
    assert lines[1] == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    assert lines[2] == ""
    assert lines[4] == ""
    assert lines[5:] == [
        "URI: https://api.example.run.app",
        "Version: 1",
        "Chain ID: 84532",
        f"Nonce: {NONCE}",
        "Issued At: 2026-09-20T12:00:00Z",
    ]


# --- tick orchestration and exit status (step 6) ---


def _stage_fakes(order):
    def fn(name):
        def inner(**kwargs):
            order.append(name)
            return {"ok": True}

        return inner

    class Sched:
        def run(self):
            order.append("schedule")
            return {"ok": True}

    return {
        "score_job": fn("scores"),
        "resolve_job": fn("resolve"),
        "resolve_general_job": fn("resolve_general"),
        "schedule_factory": Sched,
        "listing_job": fn("listing"),
    }


def test_run_tick_preflight_failure_skips_api_stages():
    from job import exit_code, run_tick

    order = []
    summary = run_tick(preflight=lambda: {"ok": False, "cause": "x"}, **_stage_fakes(order))
    assert order == ["resolve", "resolve_general"]
    for stage in ("scores", "schedule", "listing"):
        assert summary[stage] == {"ok": False, "skipped": "preflight"}
    assert summary["preflight"] == {"ok": False, "cause": "x"}
    assert exit_code(summary) == 1


def test_run_tick_preflight_ok_runs_all_stages_in_order():
    from job import STAGES, exit_code, run_tick

    order = []
    summary = run_tick(preflight=lambda: {"ok": True}, **_stage_fakes(order))
    assert order == list(STAGES) == ["scores", "resolve", "resolve_general", "schedule", "listing"]
    assert exit_code(summary) == 0


def test_run_tick_preflight_exception_is_failure():
    from job import run_tick

    def boom():
        raise RuntimeError("preflight exploded")

    order = []
    summary = run_tick(preflight=boom, **_stage_fakes(order))
    assert summary["preflight"] == {"ok": False, "cause": "preflight exploded"}
    assert order == ["resolve", "resolve_general"]


def test_run_tick_default_resolve_general_import_failure(monkeypatch):
    from job import exit_code, run_tick

    monkeypatch.setitem(sys.modules, "resolve.general", None)
    order = []
    fakes = _stage_fakes(order)
    fakes.pop("resolve_general_job")
    summary = run_tick(**fakes)
    assert summary["resolve_general"]["ok"] is False
    assert "resolve.general" in summary["resolve_general"]["error"]
    assert order == ["scores", "resolve", "schedule", "listing"]
    assert exit_code(summary) == 1


def test_run_tick_default_resolve_general_calls_module_run(monkeypatch):
    from job import run_tick

    seen = []
    fake = types.ModuleType("resolve.general")

    def fake_run(**kwargs):
        seen.append(kwargs)
        return {"ok": True, "attempted": 0}

    fake.run = fake_run
    monkeypatch.setitem(sys.modules, "resolve.general", fake)
    fakes = _stage_fakes([])
    fakes.pop("resolve_general_job")

    def getter(url):
        return []

    summary = run_tick(http_get=getter, **fakes)
    assert summary["resolve_general"] == {"ok": True, "attempted": 0}
    assert seen == [{"http_get": getter}]


def test_exit_code_rules():
    from job import exit_code

    clean = {
        "preflight": {"ok": True},
        "scores": {"ok": True},
        "resolve": {"attempted": 0, "results": []},
        "resolve_general": {"ok": True},
        "schedule": {"ok": True},
        "listing": {"ok": True},
    }
    assert exit_code(clean) == 0
    assert exit_code({}) == 0
    assert exit_code(dict(clean, listing={"ok": False, "errors": [1]})) == 1
    assert exit_code(dict(clean, resolve_general={"ok": False, "error": "x"})) == 1
    assert exit_code(dict(clean, preflight={"ok": False})) == 1
    assert exit_code(dict(clean, resolve={"ok": None})) == 0


def test_main_prints_json_and_returns_exit_code(monkeypatch, capsys):
    import job

    calls = []

    def fake_run_tick(**kwargs):
        calls.append(kwargs)
        return {"preflight": {"ok": True}, "scores": {"ok": True}, "listing": {"ok": False, "error": "x"}}

    monkeypatch.setattr(job, "run_tick", fake_run_tick)
    assert job.main() == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["exitCode"] == 1
    assert printed["listing"]["ok"] is False
    assert callable(calls[0]["preflight"])


def test_main_redacts_secret_values(monkeypatch, capsys):
    import job

    rpc = "https://base-sepolia.g.alchemy.com/v2/abcdEFGH1234ijklMNOP"
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("OPERATOR_PRIVATE_KEY", ANVIL_0)
    monkeypatch.setenv("ANVIL_RPC_URL", rpc)
    monkeypatch.setenv("CURSOR_SEARCH_MCP_HEADERS", json.dumps({"Authorization": "Bearer mcp-token-0123456789"}))

    def fake_run_tick(**kwargs):
        return {
            "resolve": {"ok": False, "error": f"Max retries exceeded with url: /v2/abcdEFGH1234ijklMNOP ({rpc})"},
            "scores": {"ok": False, "error": f"secret {JWT_SECRET} key {ANVIL_0[2:]}"},
            "schedule": {"ok": False, "error": "mcp said mcp-token-0123456789"},
        }

    monkeypatch.setattr(job, "run_tick", fake_run_tick)
    assert job.main() == 1
    out = capsys.readouterr().out
    for secret in (JWT_SECRET, ANVIL_0[2:], "abcdEFGH1234ijklMNOP", "mcp-token-0123456789"):
        assert secret not in out
    assert json.loads(out)["exitCode"] == 1
    assert "[redacted]" in out


def test_stage_error_stderr_redacted(monkeypatch, capsys):
    from job import run_tick

    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    fakes = _stage_fakes([])

    def leaky(**kwargs):
        raise RuntimeError(f"bad secret {JWT_SECRET}")

    fakes["resolve_job"] = leaky
    summary = run_tick(**fakes)
    err = capsys.readouterr().err
    assert "tick resolve failed: bad secret [redacted]" in err
    assert JWT_SECRET not in err
    assert summary["resolve"]["ok"] is False



# --- single flight and tick budget ----------------------------------------------


def test_run_tick_lease_held_skips_everything_exit_zero():
    from job import exit_code, run_tick

    order = []
    preflights = []
    summary = run_tick(
        preflight=lambda: preflights.append(1) or {"ok": True},
        lease=lambda: {"acquired": False, "holder": ["exec-older"]},
        **_stage_fakes(order),
    )
    assert order == [] and preflights == []
    assert summary["skipped"] == "running"
    assert summary["lease"]["holder"] == ["exec-older"]
    assert exit_code(summary) == 0


def test_run_tick_lease_error_fails_open(capsys):
    from job import run_tick

    def broken():
        raise RuntimeError("403 run.executions.list denied")

    order = []
    summary = run_tick(lease=broken, **_stage_fakes(order))
    assert order == ["scores", "resolve", "resolve_general", "schedule", "listing"]
    assert summary["lease"]["acquired"] is True and "403" in summary["lease"]["error"]
    assert "tick lease check failed" in capsys.readouterr().err


def test_run_tick_budget_spent_skips_later_stages(monkeypatch):
    import budget
    from job import exit_code, run_tick

    order = []
    fakes = _stage_fakes(order)

    def slow_resolve(**kwargs):
        order.append("resolve")
        budget._deadline[0] = 0  # the budget runs out during this stage
        return {"ok": True}

    fakes["resolve_job"] = slow_resolve
    summary = run_tick(budget_seconds=600, **fakes)
    assert order == ["scores", "resolve"]
    for stage in ("resolve_general", "schedule"):
        assert summary[stage] == {"ok": True, "skipped": "budget", "deferred": True}
    # Listing is critical: a budget skip fails the tick instead of passing quietly.
    assert summary["listing"] == {"ok": False, "skipped": "budget"}
    assert summary["deferred"] == {"resolve_general": 1, "schedule": 1, "listing": 1}
    assert exit_code(summary) == 1
    assert budget.exhausted() is False  # cleared after the tick


def test_run_tick_scores_capped_to_its_share_then_resolve_and_listing_run(monkeypatch):
    """A score stage that would use the whole tick stops at its share (default 0.4)."""
    import time

    import budget
    from job import exit_code, run_tick

    monkeypatch.delenv("OU_STAGE_SHARE_SCORES", raising=False)
    monkeypatch.setenv("OU_LISTING_RESERVE_SECONDS", "0")
    order = []
    fakes = _stage_fakes(order)
    seen = {}

    def greedy_scores(**kwargs):
        order.append("scores")
        seen["scores_left"] = budget.remaining()
        while not budget.exhausted():  # scouts until its stage budget is spent
            time.sleep(0.01)
        seen["total_left"] = budget.total_remaining()
        return {"ok": True, "results": [{"conditionId": "0xa", "ok": True, "skipped": "budget"}]}

    def resolve(**kwargs):
        order.append("resolve")
        seen["resolve_left"] = budget.remaining()
        return {"ok": True, "results": [{"conditionId": "0xb", "ok": True, "reason": "budget"}]}

    fakes["score_job"] = greedy_scores
    fakes["resolve_job"] = resolve
    summary = run_tick(budget_seconds=1, **fakes)
    assert order == ["scores", "resolve", "resolve_general", "schedule", "listing"]
    assert seen["scores_left"] <= 0.41
    assert seen["total_left"] > 0.5
    assert seen["resolve_left"] > 0.5
    assert summary["deferred"] == {"scores": 1, "resolve": 1}
    assert exit_code(summary) == 0


def test_run_tick_listing_reserve_stops_earlier_stages(monkeypatch):
    import time

    import budget
    from job import run_tick

    monkeypatch.setenv("OU_LISTING_RESERVE_SECONDS", "100000")  # capped at a quarter: 150 of 600
    order = []
    fakes = _stage_fakes(order)
    seen = {}

    def resolve(**kwargs):
        order.append("resolve")
        seen["resolve_left"] = budget.remaining()
        seen["total_left"] = budget.total_remaining()
        return {"ok": True}

    fakes["resolve_job"] = resolve
    run_tick(budget_seconds=600, **fakes)
    assert 440 < seen["resolve_left"] <= 450 < seen["total_left"]
    assert order[-1] == "listing"

    order.clear()
    fakes = _stage_fakes(order)

    def slow_scores(**kwargs):
        order.append("scores")
        budget._deadline[0] = time.monotonic() + 100  # only part of the 150s reserve is left
        return {"ok": True}

    fakes["score_job"] = slow_scores
    summary = run_tick(budget_seconds=600, **fakes)
    assert order == ["scores", "listing"]
    assert summary["resolve"] == {"ok": False, "skipped": "budget"}
    assert summary["resolve_general"] == {"ok": True, "skipped": "budget", "deferred": True}
    assert summary["listing"] == {"ok": True}


def test_stage_share_env_validation(monkeypatch):
    from job import listing_reserve_seconds, run_tick, stage_share

    monkeypatch.delenv("OU_STAGE_SHARE_SCORES", raising=False)
    assert stage_share("scores") == 0.4 and stage_share("resolve") == 1.0
    monkeypatch.setenv("OU_STAGE_SHARE_RESOLVE", "0.5")
    assert stage_share("resolve") == 0.5
    for bad in ("0", "1.5", "x"):
        monkeypatch.setenv("OU_STAGE_SHARE_SCORES", bad)
        with pytest.raises(RuntimeError, match="OU_STAGE_SHARE_SCORES"):
            stage_share("scores")
    order = []
    summary = run_tick(budget_seconds=600, **_stage_fakes(order))
    assert summary["scores"]["ok"] is False and "OU_STAGE_SHARE_SCORES" in summary["scores"]["error"]
    assert "scores" not in order and "listing" in order
    monkeypatch.setenv("OU_LISTING_RESERVE_SECONDS", "-1")
    with pytest.raises(RuntimeError, match="OU_LISTING_RESERVE_SECONDS"):
        listing_reserve_seconds()


def test_deferred_counts_listing_list_and_results():
    from job import deferred_counts

    summary = {
        "scores": {"ok": True, "results": [{"skipped": "budget"}, {"ok": True}]},
        "resolve": {"ok": True, "results": [{"reason": "budget", "deferred": "send"}, {"reason": "capped"}]},
        "listing": {"ok": True, "deferred": ["Bills vs Jets: Bills win?"]},
    }
    assert deferred_counts(summary) == {"scores": 1, "resolve": 1, "listing": 1}


def test_budget_stage_and_send_helpers():
    import budget

    budget.start(0)
    try:
        assert budget.receipt_timeout(180) == 180 and budget.can_start(10**6)
        with budget.stage(0.1):
            assert budget.remaining() is None
    finally:
        budget.clear()
    budget.start(100)
    try:
        assert 170 > budget.receipt_timeout(180) > 85
        with budget.stage(0.1, reserve=0):
            assert budget.remaining() <= 10.0
            assert not budget.can_start(90) and budget.can_start(5)
            assert budget.total_remaining() > 90
        assert budget.remaining() > 90
        budget._deadline[0] = budget.time.monotonic() + 25
        with pytest.raises(budget.SendDeferred):
            budget.receipt_timeout(180)
    finally:
        budget.clear()


def test_budget_env_validation(monkeypatch):
    import budget

    monkeypatch.setenv("OU_TICK_BUDGET_SECONDS", "0")
    budget.start()
    assert budget.exhausted() is False and budget._deadline == []
    monkeypatch.setenv("OU_TICK_BUDGET_SECONDS", "-5")
    with pytest.raises(RuntimeError, match="OU_TICK_BUDGET_SECONDS"):
        budget.budget_seconds()
    monkeypatch.delenv("OU_TICK_BUDGET_SECONDS")
    assert budget.budget_seconds() == 780
    budget.clear()


def test_tick_lease_off_outside_cloud_run(monkeypatch):
    import tick_lease

    monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
    monkeypatch.delenv("CLOUD_RUN_EXECUTION", raising=False)
    assert tick_lease.cloud_run_lease() == {"acquired": True, "mode": "off"}
    monkeypatch.setenv("CLOUD_RUN_JOB", "overunder-oracle")
    monkeypatch.setenv("CLOUD_RUN_EXECUTION", "overunder-oracle-abc")
    monkeypatch.setenv("OU_TICK_SINGLE_FLIGHT", "0")
    assert tick_lease.enabled() is False


def _run_api(executions, mine_name="overunder-oracle-new"):
    base = "https://run.googleapis.com/v2/projects/proj-1/locations/us-central1/jobs/overunder-oracle/executions"
    by_name = {e["name"].rsplit("/", 1)[-1]: e for e in executions}
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if url.startswith("http://metadata.google.internal"):
            assert request.headers.get("metadata-flavor") == "Google"
            if url.endswith("project/project-id"):
                return httpx.Response(200, text="proj-1")
            if url.endswith("instance/region"):
                return httpx.Response(200, text="projects/123/regions/us-central1")
            return httpx.Response(200, json={"access_token": "ya29.token", "expires_in": 3599})
        assert request.headers.get("authorization") == "Bearer ya29.token"
        if request.url.path.endswith("/executions"):
            if "pageToken" not in url:
                return httpx.Response(200, json={"executions": executions[:1], "nextPageToken": "p2"})
            return httpx.Response(200, json={"executions": executions[1:]})
        name = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=by_name[name])

    assert base
    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def _execution(name, created, completed=None):
    row = {"name": f"projects/proj-1/locations/us-central1/jobs/overunder-oracle/executions/{name}", "createTime": created}
    if completed:
        row["completionTime"] = completed
    return row


def test_cloud_run_lease_detects_older_running_execution(monkeypatch):
    import tick_lease

    monkeypatch.setenv("CLOUD_RUN_JOB", "overunder-oracle")
    monkeypatch.setenv("CLOUD_RUN_EXECUTION", "overunder-oracle-new")
    monkeypatch.delenv("OU_TICK_SINGLE_FLIGHT", raising=False)
    executions = [
        _execution("overunder-oracle-done", "2026-09-20T11:30:00.123456789Z", "2026-09-20T11:40:00Z"),
        _execution("overunder-oracle-old", "2026-09-20T11:45:00.5Z"),
        _execution("overunder-oracle-new", "2026-09-20T12:00:00Z"),
        _execution("overunder-oracle-newer", "2026-09-20T12:15:00Z"),
    ]
    client, _ = _run_api(executions)
    with client:
        held = tick_lease.cloud_run_lease(client=client)
    assert held["acquired"] is False
    assert held["holder"] == [executions[1]["name"]]

    client, _ = _run_api([e for e in executions if not e["name"].endswith("-old")])
    with client:
        assert tick_lease.cloud_run_lease(client=client)["acquired"] is True
