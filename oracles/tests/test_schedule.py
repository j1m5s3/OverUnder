import os

from agents.base import MockSearch
from listing.questions import question_id, winner_question
from schedule.scout import ScheduleCoordinator, ScheduleGame, agreed_games, scout

SNIP = "2026 W3 Bills vs Dolphins kickoff 1800000000 final 2026 W4 Chiefs vs Broncos kickoff 1800864000 scheduled"


def test_scout_parses_week_rows():
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        games = scout(search=MockSearch([SNIP]))
        assert [(g.away, g.home, g.week, g.status) for g in games] == [
            ("Bills", "Dolphins", 3, "final"),
            ("Chiefs", "Broncos", 4, "scheduled"),
        ]
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def test_unknown_team_dropped_known_kept():
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        blob = "2026 W3 Bills vs Dolphins kickoff 1800000000 final 2026 W3 Foo vs Bar kickoff 1800000001 scheduled"
        games = scout(search=MockSearch([blob]))
        assert len(games) == 1
        assert games[0].away == "Bills"
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def test_all_unknown_fails_closed():
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        import pytest

        with pytest.raises(RuntimeError, match="no canonical NFL games"):
            scout(search=MockSearch(["2026 W3 Foo vs Bar kickoff 1 scheduled"]))
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def test_coordinator_unanimous_publishes():
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        posted = []
        search = MockSearch([SNIP])
        coord = ScheduleCoordinator(searches=[search, search, search], publisher=lambda games: posted.append(games))
        result = coord.run()
        assert result["unanimous"] is True
        assert len(posted) == 1
        assert posted[0][0]["home"] == "Dolphins"
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def test_coordinator_order_independent():
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        posted = []
        a = MockSearch([SNIP])
        b = MockSearch(
            [
                "2026 W4 Chiefs vs Broncos kickoff 1800864000 scheduled 2026 W3 Bills vs Dolphins kickoff 1800000000 final"
            ]
        )
        coord = ScheduleCoordinator(searches=[a, b, a], publisher=lambda games: posted.append(games))
        result = coord.run()
        assert result["unanimous"] is True
        assert len(posted) == 1
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def test_coordinator_publishes_only_agreed_games():
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        posted = []
        a = MockSearch([SNIP])
        b = MockSearch(["2026 W3 Bills vs Dolphins kickoff 1800000000 scheduled 2026 W4 Chiefs vs Broncos kickoff 1800864000 scheduled"])
        coord = ScheduleCoordinator(searches=[a, a, b], publisher=lambda games: posted.append(games))
        result = coord.run()
        assert result["ok"] is True
        assert result["unanimous"] is False
        assert posted == [
            [{"away": "Chiefs", "home": "Broncos", "kickoff_unix": 1800864000, "week": 4, "season": 2026, "status": "scheduled"}]
        ]
        assert result["published"] == 1
        assert len(result["disputed"]) == 2
        assert {(g["away"], g["status"]) for g in result["disputed"]} == {("Bills", "final"), ("Bills", "scheduled")}
        assert [g["away"] for g in result["games"]] == ["Chiefs"]
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def test_coordinator_no_common_game_does_not_publish():
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        posted = []
        a = MockSearch(["2026 W3 Bills vs Dolphins kickoff 1800000000 final"])
        b = MockSearch(["2026 W4 Chiefs vs Broncos kickoff 1800864000 scheduled"])
        coord = ScheduleCoordinator(searches=[a, a, b], publisher=lambda games: posted.append(games))
        result = coord.run()
        assert posted == []
        assert result["published"] == 0
        assert result["games"] is None
        assert result["unanimous"] is False
        assert len(result["disputed"]) == 2
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def test_coordinator_scout_error_fails_stage():
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        import pytest

        posted = []
        good = MockSearch([SNIP])
        bad = MockSearch(["no schedule rows here"])
        coord = ScheduleCoordinator(searches=[good, good, bad], publisher=lambda games: posted.append(games))
        with pytest.raises(RuntimeError, match="no canonical NFL games"):
            coord.run()
        assert posted == []
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def _game(**overrides):
    base = {"away": "Chiefs", "home": "Broncos", "kickoff_unix": 1800864000, "week": 4, "season": 2026, "status": "scheduled"}
    base.update(overrides)
    return ScheduleGame(**base)


def test_agreed_games_kickoff_conflict_excluded():
    bills = _game(away="Bills", home="Dolphins", kickoff_unix=1800000000, week=3, status="final")
    chiefs = _game()
    flexed = _game(kickoff_unix=1800875000)
    agreed, disputed = agreed_games([[bills, chiefs], [chiefs, bills], [bills, flexed]])
    assert agreed == [bills]
    assert disputed == [chiefs, flexed]


def test_agreed_games_ambiguous_matchup_disputed():
    chiefs = _game()
    flexed = _game(kickoff_unix=1800875000)
    agreed, disputed = agreed_games([[chiefs, flexed], [flexed, chiefs], [chiefs, flexed]])
    assert agreed == []
    assert disputed == [chiefs, flexed]
    assert agreed_games([]) == ([], [])


def test_question_id_deterministic():
    first = question_id(2026, 4, "Chiefs", "Broncos", 1800864000)
    second = question_id(2026, 4, "Chiefs", "Broncos", 1800864000)
    assert first == second
    assert first.startswith("0x")
    assert len(first) == 66
    assert winner_question("Broncos", "Chiefs") == "Broncos vs Chiefs: Broncos win?"


def test_mock_without_rows_publishes_nothing(monkeypatch):
    from schedule.scout import ScheduleCoordinator

    monkeypatch.setenv("OU_ORACLE_MOCK", "1")
    monkeypatch.delenv("OU_MOCK_SCHEDULE", raising=False)
    published = []
    summary = ScheduleCoordinator(publisher=published.append).run()
    assert summary["ok"] is True
    assert summary["published"] == 0
    assert published == []


def test_mock_rows_from_env(monkeypatch):
    from schedule.scout import ScheduleCoordinator

    monkeypatch.setenv("OU_ORACLE_MOCK", "1")
    monkeypatch.setenv(
        "OU_MOCK_SCHEDULE",
        "2026 W3 Broncos vs Chiefs kickoff 1800000000 final;2026 W4 Dolphins vs Bills kickoff 1800864000 scheduled",
    )
    published = []
    summary = ScheduleCoordinator(publisher=published.append).run()
    assert summary["published"] == 2
    assert [(g["week"], g["home"], g["status"]) for g in published[0]] == [(3, "Chiefs", "final"), (4, "Bills", "scheduled")]


def test_mock_rows_without_canonical_games_fail_closed(monkeypatch):
    import pytest

    monkeypatch.setenv("OU_ORACLE_MOCK", "1")
    monkeypatch.setenv("OU_MOCK_SCHEDULE", "2026 W3 Foo vs Bar kickoff 1 scheduled")
    with pytest.raises(RuntimeError, match="no canonical NFL games"):
        scout()
