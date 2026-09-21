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
