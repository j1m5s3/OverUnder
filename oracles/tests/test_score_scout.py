import importlib
import os

import pytest

from agents.base import MockSearch
from scores.scout import ScoreCoordinator, ScoreReport, canonicalize, extract_score, parse_teams, requested_facts, scout


def test_parse_teams():
    home, away = parse_teams("Chiefs vs Broncos: Chiefs win?")
    assert home == "Chiefs"
    assert away == "Broncos"


def test_scout_extracts_named_score():
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        search = MockSearch(["Chiefs 27, Broncos 24. Final."])
        report = scout("Chiefs vs Broncos: Chiefs win?", search=search)
        assert report.home_label == "Chiefs"
        assert report.away_label == "Broncos"
        assert report.home_score == 27
        assert report.away_score == 24
        assert report.status == "final"
        hits = search.search("x")
        hit_urls = {h.url for h in hits}
        for url in report.evidence_urls:
            assert url in hit_urls
        body = report.as_api_body()
        assert body["homeScore"] == 27
        assert body["status"] == "final"
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def test_scout_scheduled_has_null_scores():
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        search = MockSearch(["Preview: kickoff Sunday. No score yet."])
        report = scout("Chiefs vs Broncos: Chiefs win?", search=search)
        assert report.home_score is None
        assert report.away_score is None
        assert report.status == "scheduled"
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def test_coordinator_unanimous_mock():
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        search = MockSearch(["Chiefs defeated Broncos 27-24."])
        coord = ScoreCoordinator(searches=[search, search, search])
        result = coord.run("Chiefs vs Broncos: Chiefs win?")
        assert result["unanimous"] is True
        assert result["apiBody"]["homeScore"] == 27
        assert result["apiBody"]["awayScore"] == 24
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def test_coordinator_disagrees_does_not_publish():
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        a = MockSearch(["Chiefs 27, Broncos 24. Final."])
        b = MockSearch(["Chiefs 21, Broncos 24. Final."])
        coord = ScoreCoordinator(searches=[a, a, b])
        result = coord.run("Chiefs vs Broncos: Chiefs win?")
        assert result["unanimous"] is False
        assert result["apiBody"] is None
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def test_extract_rejects_invented_evidence_via_hits_only():
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        search = MockSearch(["Chiefs 10, Broncos 3. Q2."])
        hits = search.search("score")
        report = extract_score("Chiefs vs Broncos: Chiefs win?", hits)
        assert report.status == "in_progress"
        assert report.period_label == "Q2"
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def test_requested_facts_from_templates():
    assert requested_facts("Chiefs vs Broncos: Chiefs win?") == ("home_score", "away_score")
    assert requested_facts("Travis Kelce to fumble at least once?") == ("fumbles",)
    assert requested_facts("Broncos to score first?") == ("first_scorer",)
    assert requested_facts("Total points over 45.5?") == ("total_points",)


def test_fail_closed_missing_fumble_number():
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        search = MockSearch(["Chiefs defeated Broncos 27-24."])
        with pytest.raises(RuntimeError, match="missing requested fact fumbles"):
            scout("Travis Kelce to fumble at least once?", search=search)
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def test_canonicalize_ignores_team_aliases():
    question = "Chiefs vs Broncos: Chiefs win?"
    a = ScoreReport("Kansas City Chiefs", "Denver Broncos", 27, 24, "final", None, "", [], {"home_score": 27, "away_score": 24})
    b = ScoreReport("Chiefs", "Broncos", 27, 24, "final", None, "", [], {"home_score": 27, "away_score": 24})
    assert canonicalize(a, question).key() == canonicalize(b, question).key()


def test_vs_box_facts_populated():
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        search = MockSearch(["Chiefs 27, Broncos 24. Final."])
        report = scout("Chiefs vs Broncos: Chiefs win?", search=search)
        assert report.facts["home_score"] == 27
        assert report.facts["away_score"] == 24
        assert report.as_api_body()["facts"]["home_score"] == 27
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def test_cursor_extract_postponed_and_cancelled_are_scoreless(monkeypatch):
    import importlib

    scout_mod = importlib.import_module("scores.scout")

    prompts = []

    def fake_prompt(prompt, model):
        prompts.append(prompt)
        return {
            "home_label": "Chiefs",
            "away_label": "Broncos",
            "home_score": 7,
            "away_score": 3,
            "status": status_box[0],
            "facts": {},
            "search_hits": [{"url": "https://ex.test/1", "content": "postponed"}],
            "evidence_urls": ["https://ex.test/1"],
        }

    monkeypatch.setattr(scout_mod, "prompt_json", fake_prompt)
    for status in ("postponed", "cancelled"):
        status_box = [status]
        report = scout_mod._cursor_extract("Chiefs vs Broncos: Kelce to fumble?", "alpha")
        assert report.status == status
        assert report.home_score is None and report.away_score is None
        assert report.facts == {"fumbles": None}
        assert scout_mod.canonicalize(report, "Chiefs vs Broncos: Kelce to fumble?").status == status
    assert "scheduled | in_progress | final | postponed | cancelled" in prompts[0]
    status_box = ["halftime-ish"]
    assert scout_mod._cursor_extract("Chiefs vs Broncos: Chiefs win?", "alpha").status == "scheduled"


# --- kickoff date pinning (PR #30 review) ---------------------------------------

KICKOFF = "2026-09-21T00:20:00Z"  # Sunday night game: US local date 2026-09-20


def _live_reply(status="final", game_date="2026-09-20", home=31, away=10):
    reply = {
        "home_label": "Chiefs",
        "away_label": "Broncos",
        "home_score": home,
        "away_score": away,
        "status": status,
        "facts": {},
        "search_hits": [{"url": "https://ex.test/1", "content": "final"}],
        "evidence_urls": ["https://ex.test/1"],
    }
    if game_date is not None:
        reply["game_date"] = game_date
    return reply


def test_cursor_extract_prompt_names_kickoff_and_requires_game_date(monkeypatch):
    scout_mod = importlib.import_module("scores.scout")

    prompts = []

    def fake_prompt(prompt, model):
        prompts.append(prompt)
        return _live_reply()

    monkeypatch.setattr(scout_mod, "prompt_json", fake_prompt)
    report = scout_mod._cursor_extract("Chiefs vs Broncos: Chiefs win?", "alpha", KICKOFF)
    assert report.status == "final" and report.game_date == "2026-09-20"
    assert KICKOFF in prompts[0] and "game_date" in prompts[0] and "other meeting" in prompts[0]
    # Without a kickoff (legacy callers) the prompt carries no date guidance.
    scout_mod._cursor_extract("Chiefs vs Broncos: Chiefs win?", "alpha")
    assert "game_date" not in prompts[1] and "kick off at" not in prompts[1]


@pytest.mark.parametrize("game_date", ["2025-09-21", "2026-09-14", None, "last Sunday"])
def test_cursor_extract_rejects_other_meeting(monkeypatch, game_date):
    scout_mod = importlib.import_module("scores.scout")

    monkeypatch.setattr(scout_mod, "prompt_json", lambda prompt, model: _live_reply(game_date=game_date))
    with pytest.raises(RuntimeError, match="does not match kickoff"):
        scout_mod._cursor_extract("Chiefs vs Broncos: Chiefs win?", "alpha", KICKOFF)


def test_cursor_extract_date_check_scope(monkeypatch):
    scout_mod = importlib.import_module("scores.scout")

    replies = iter(
        [
            _live_reply(game_date="2026-09-21T00:20:00Z"),  # ISO datetime on the kickoff date
            _live_reply(status="postponed", game_date="2026-09-24"),  # a postponed game may move
            _live_reply(status="scheduled", game_date=None),
            _live_reply(status="in_progress", game_date="2025-09-21"),
        ]
    )
    monkeypatch.setattr(scout_mod, "prompt_json", lambda prompt, model: next(replies))
    q = "Chiefs vs Broncos: Chiefs win?"
    assert scout_mod._cursor_extract(q, "alpha", KICKOFF).status == "final"
    assert scout_mod._cursor_extract(q, "alpha", KICKOFF).status == "postponed"
    assert scout_mod._cursor_extract(q, "alpha", KICKOFF).status == "scheduled"
    with pytest.raises(RuntimeError, match="does not match kickoff"):
        scout_mod._cursor_extract(q, "alpha", KICKOFF)


def test_score_coordinator_threads_kickoff_and_never_publishes_other_meeting(monkeypatch):
    scout_mod = importlib.import_module("scores.scout")

    published = []
    monkeypatch.delenv("OU_ORACLE_MOCK", raising=False)
    monkeypatch.setattr(scout_mod, "prompt_json", lambda prompt, model: _live_reply(game_date="2025-09-21"))
    coord = scout_mod.ScoreCoordinator(publisher=lambda cid, report: published.append(cid))
    with pytest.raises(RuntimeError, match="does not match kickoff"):
        coord.run("Chiefs vs Broncos: Chiefs win?", condition_id="0xa", kickoff=KICKOFF)
    assert published == []

    monkeypatch.setattr(scout_mod, "prompt_json", lambda prompt, model: _live_reply())
    out = coord.run("Chiefs vs Broncos: Chiefs win?", condition_id="0xa", kickoff=KICKOFF)
    assert out["unanimous"] is True and published == ["0xa"]
    assert out["reports"][0]["game_date"] == "2026-09-20"
    assert "game_date" not in out["apiBody"]


def test_run_job_passes_kickoff_from_close_time(monkeypatch):
    from datetime import datetime, timezone

    from scores.job import run_job

    monkeypatch.setenv("OU_API_URL", "http://api.test")
    payloads = {
        "http://api.test/api/v1/markets": [
            {"primary": {"conditionId": "0xa", "question": "Chiefs vs Broncos: win?", "marketType": 0, "closeTime": 1_789_950_000}, "children": []},
            {"primary": {"conditionId": "0xb", "question": "Bills vs Jets: win?", "marketType": 0}, "children": []},
        ],
        "http://api.test/api/v1/markets/0xa": {"score": None},
        "http://api.test/api/v1/markets/0xb": {"score": None},
    }
    seen = []

    class FakeCoord:
        def run(self, question, condition_id=None, kickoff=None):
            seen.append((condition_id, kickoff))
            return {"unanimous": False, "reports": []}

    run_job(http_get=payloads.__getitem__, coordinator_factory=FakeCoord, now=datetime(2026, 9, 21, 12, tzinfo=timezone.utc))
    assert sorted(seen) == [("0xa", "2026-09-21T00:20:00Z"), ("0xb", None)]


def test_run_job_does_not_start_scout_without_min_budget(monkeypatch):
    from datetime import datetime, timezone

    import budget
    from scores.job import run_job

    monkeypatch.setenv("OU_API_URL", "http://api.test")
    monkeypatch.setenv("OU_RESEARCH_MIN_SECONDS", "120")
    payloads = {
        "http://api.test/api/v1/markets": [{"primary": {"conditionId": "0xa", "question": "Chiefs vs Broncos: win?", "marketType": 0}, "children": []}],
        "http://api.test/api/v1/markets/0xa": {"score": None},
    }
    runs = []

    class FakeCoord:
        def run(self, question, condition_id=None, kickoff=None):
            runs.append(condition_id)
            return {"unanimous": False}

    budget.start(60)
    try:
        summary = run_job(http_get=payloads.__getitem__, coordinator_factory=FakeCoord, now=datetime(2026, 9, 21, tzinfo=timezone.utc))
    finally:
        budget.clear()
    assert runs == [] and summary["results"] == [{"conditionId": "0xa", "ok": True, "skipped": "budget"}]
