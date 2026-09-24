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
