"""Derive YES/NO from a winner-primary question and posted scores."""

from __future__ import annotations

import re

_YES = re.compile(r":\s*(.+?)\s+win\??\s*$", re.IGNORECASE)


def yes_team(question: str) -> str | None:
    match = _YES.search((question or "").strip())
    if not match:
        return None
    name = match.group(1).strip()
    return name or None


def _same(left: str, right: str) -> bool:
    a = (left or "").casefold().strip()
    b = (right or "").casefold().strip()
    if not a or not b:
        return False
    return a == b or a in b or b in a


def score_outcome(
    question: str,
    home_label: str,
    away_label: str,
    home_score: int | None,
    away_score: int | None,
) -> int | None:
    yes = yes_team(question)
    if yes is None or home_score is None or away_score is None:
        return None
    if home_score == away_score:
        return None
    if _same(yes, home_label):
        yes_score, no_score = home_score, away_score
    elif _same(yes, away_label):
        yes_score, no_score = away_score, home_score
    else:
        return None
    if yes_score > no_score:
        return 0
    if yes_score < no_score:
        return 1
    return None
