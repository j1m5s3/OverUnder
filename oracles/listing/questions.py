"""Winner-primary question and deterministic question_id.

question_id is deterministic so a replayed create stays idempotent (the API
returns the existing market for a known question_id). With OU_QUESTION_ID_KEY
set it is an HMAC-SHA256 under that operator secret, so a third party reading
the public schedule cannot precompute it and prepareCondition first (squat).
Without the key it falls back to the legacy public sha256.
"""

from __future__ import annotations

import hashlib
import hmac
import os


def winner_question(home: str, away: str) -> str:
    return f"{home} vs {away}: {home} win?"


def question_id(season: int, week: int, away: str, home: str, kickoff_unix: int, key: str | None = None) -> str:
    payload = f"ou:nfl:{season}:{week}:{away}@{home}:{kickoff_unix}".encode()
    secret = (os.getenv("OU_QUESTION_ID_KEY", "") if key is None else key).strip()
    if secret:
        return "0x" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return "0x" + hashlib.sha256(payload).hexdigest()
