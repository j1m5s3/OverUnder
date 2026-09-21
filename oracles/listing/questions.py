"""Winner-primary question and deterministic question_id."""

from __future__ import annotations

import hashlib


def winner_question(home: str, away: str) -> str:
    return f"{home} vs {away}: {home} win?"


def question_id(season: int, week: int, away: str, home: str, kickoff_unix: int) -> str:
    payload = f"ou:nfl:{season}:{week}:{away}@{home}:{kickoff_unix}"
    return "0x" + hashlib.sha256(payload.encode()).hexdigest()
