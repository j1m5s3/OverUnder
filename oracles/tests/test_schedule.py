import os

from agents.base import MockSearch
from listing.questions import question_id, winner_question
from schedule.scout import ScheduleCoordinator, scout

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


def test_coordinator_disagree_does_not_publish():
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        posted = []
        a = MockSearch([SNIP])
        b = MockSearch(["2026 W3 Bills vs Dolphins kickoff 1800000000 scheduled 2026 W4 Chiefs vs Broncos kickoff 1800864000 scheduled"])
        coord = ScheduleCoordinator(searches=[a, a, b], publisher=lambda games: posted.append(games))
        result = coord.run()
        assert result["unanimous"] is False
        assert posted == []
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def test_question_id_deterministic():
    first = question_id(2026, 4, "Chiefs", "Broncos", 1800864000)
    second = question_id(2026, 4, "Chiefs", "Broncos", 1800864000)
    assert first == second
    assert first.startswith("0x")
    assert len(first) == 66
    assert winner_question("Broncos", "Chiefs") == "Broncos vs Chiefs: Broncos win?"
