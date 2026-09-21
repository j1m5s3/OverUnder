from datetime import datetime, timezone

from scores.job import run_job, select_targets, skip_fresh, sports_primaries


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
        def run(self, question, condition_id=None):
            seen.append((question, condition_id))
            return {"unanimous": True}

    summary = run_job(http_get=http_get, coordinator_factory=FakeCoord, now=datetime(2026, 9, 20, tzinfo=timezone.utc))
    assert summary["attempted"] == 1
    assert seen == [("Chiefs vs Broncos: win?", "0xa")]


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

    summary = run_tick(score_job=boom, resolve_job=ok("resolve"), schedule_factory=Sched, listing_job=ok("listing"))
    assert order == ["scores", "resolve", "schedule", "listing"]
    assert summary["scores"]["ok"] is False
    assert summary["resolve"]["resolve"] is True


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

    class Sched:
        def run(self):
            return {"unanimous": False}

    def listing(**kwargs):
        return {"created": []}

    summary = run_tick(http_get=http_get, score_job=scores, resolve_job=resolve, schedule_factory=Sched, listing_job=listing)
    assert seen[0]["question"] == "Chiefs vs Broncos: win?"
    assert summary["resolve"]["attempted"] == 1
