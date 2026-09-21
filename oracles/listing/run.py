"""Create week N+1 winner primaries after week N is fully final."""

from __future__ import annotations

import os
import sys
from typing import Any, Callable

import httpx

from listing.questions import question_id, winner_question
from scores.job import _api_url, _http_get_json, sports_primaries
from scores.publish import mint_operator_jwt

DEFAULT_SEED = 200_000_000


def seed_usdc() -> int:
    raw = os.getenv("OU_SEED_USDC", str(DEFAULT_SEED)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("OU_SEED_USDC must be an integer") from exc
    if value <= 0:
        raise RuntimeError("OU_SEED_USDC must be > 0")
    return value


def week_complete(games: list[dict], live_status: dict[str, str | None]) -> bool:
    if not games:
        return False
    for game in games:
        question = winner_question(game["home"], game["away"])
        if question in live_status:
            if live_status[question] != "final":
                return False
        elif game.get("status") != "final":
            return False
    return True


def _post_json(url: str, body: Any, headers: dict[str, str] | None = None, client: httpx.Client | None = None) -> Any:
    own = client is None
    http = client or httpx.Client(timeout=60)
    try:
        response = http.post(url, json=body, headers=headers or {})
        if response.status_code == 401:
            raise RuntimeError("operator User missing or JWT rejected")
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()
    finally:
        if own:
            http.close()


def run(
    *,
    http_get: Callable[[str], Any] | None = None,
    http_post: Callable[..., Any] | None = None,
) -> dict:
    base = _api_url()
    getter = http_get or _http_get_json
    poster = http_post or _post_json
    cards = getter(f"{base}/api/v1/markets")
    if not isinstance(cards, list):
        raise RuntimeError("markets list must be an array")
    schedule = getter(f"{base}/api/v1/markets/schedule")
    if not isinstance(schedule, list):
        raise RuntimeError("schedule must be an array")
    primaries = sports_primaries(cards)
    listed_questions = {p.get("question") for p in primaries}
    live_status: dict[str, str | None] = {}
    for primary in primaries:
        cid = primary.get("conditionId") or ""
        question = primary.get("question") or ""
        if not cid or not question:
            continue
        detail = getter(f"{base}/api/v1/markets/{cid}")
        score = detail.get("score") if isinstance(detail, dict) and isinstance(detail.get("score"), dict) else None
        live_status[question] = None if not score else score.get("status")
    grouped: dict[tuple[int, int], list[dict]] = {}
    for game in schedule:
        grouped.setdefault((int(game["season"]), int(game["week"])), []).append(game)
    created = []
    skipped = []
    token = None
    for (season, week), games in sorted(grouped.items()):
        nxt = grouped.get((season, week + 1))
        if not nxt:
            continue
        if not week_complete(games, live_status):
            continue
        for game in nxt:
            q = winner_question(game["home"], game["away"])
            if q in listed_questions:
                skipped.append(q)
                continue
            if token is None:
                token = mint_operator_jwt()
            body = {
                "question": q,
                "resolution_criteria": "NFL winner; YES if named team wins",
                "close_time": int(game["kickoff_unix"]),
                "question_id": question_id(game["season"], game["week"], game["away"], game["home"], game["kickoff_unix"]),
                "seed_usdc": seed_usdc(),
                "market_type": 0,
            }
            created_row = poster(
                f"{base}/api/v1/markets",
                body,
                {"Authorization": f"Bearer {token}"},
            )
            cid = created_row.get("conditionId") if isinstance(created_row, dict) else None
            listed_questions.add(q)
            created.append({"question": q, "conditionId": cid, "close_time": body["close_time"]})
            if cid:
                try:
                    poster(
                        f"{base}/api/v1/markets/schedule",
                        [
                            {
                                "away": game["away"],
                                "home": game["home"],
                                "kickoff_unix": game["kickoff_unix"],
                                "week": game["week"],
                                "season": game["season"],
                                "status": game.get("status") or "scheduled",
                                "listedConditionId": cid,
                            }
                        ],
                        {"Authorization": f"Bearer {token}"},
                    )
                except Exception as exc:
                    print(f"listing schedule upsert failed {q}: {exc}", file=sys.stderr)
    return {"created": created, "skipped": skipped}
