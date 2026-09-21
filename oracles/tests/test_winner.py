from resolve.winner import score_outcome, yes_team


def test_yes_team_from_question():
    assert yes_team("Chiefs vs Broncos: Chiefs win?") == "Chiefs"
    assert yes_team("Bills vs Dolphins: Bills win?") == "Bills"


def test_chiefs_win_and_loss_and_tie():
    q = "Chiefs vs Broncos: Chiefs win?"
    assert score_outcome(q, "Chiefs", "Broncos", 27, 24) == 0
    assert score_outcome(q, "Chiefs", "Broncos", 24, 27) == 1
    assert score_outcome(q, "Chiefs", "Broncos", 24, 24) is None
    assert score_outcome(q, "Chiefs", "Broncos", None, 24) is None


def test_yes_team_as_away_label():
    q = "Bills vs Dolphins: Bills win?"
    assert score_outcome(q, "Dolphins", "Bills", 10, 21) == 0
    assert score_outcome(q, "Dolphins", "Bills", 21, 10) == 1
