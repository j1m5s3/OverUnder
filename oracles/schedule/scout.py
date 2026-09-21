"""NFL week schedule extract. Does not resolve or list markets."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from agents.base import MockSearch, SearchHit, hits_from_search, should_use_mock
from agents.cursor_runtime import model_id, prompt_json
from schedule.teams import canonicalize_team

_STATUSES = {"scheduled", "in_progress", "final", "postponed", "cancelled"}
_ROW = re.compile(
    r"(?P<season>20\d{2})\s*W(?P<week>\d{1,2})\s+"
    r"(?P<away>.+?)\s+vs\.?\s+(?P<home>.+?)\s+"
    r"kickoff\s+(?P<kickoff>\d+)\s+(?P<status>scheduled|in_progress|final|postponed|cancelled)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ScheduleGame:
    away: str
    home: str
    kickoff_unix: int
    week: int
    season: int
    status: str

    def key(self) -> tuple:
        return (self.season, self.week, self.away, self.home, self.kickoff_unix, self.status)

    def as_api_body(self) -> dict:
        return {
            "away": self.away,
            "home": self.home,
            "kickoff_unix": self.kickoff_unix,
            "week": self.week,
            "season": self.season,
            "status": self.status,
        }


def _canonicalize_game(raw: dict) -> ScheduleGame | None:
    away = canonicalize_team(str(raw.get("away") or ""))
    home = canonicalize_team(str(raw.get("home") or ""))
    if not away or not home:
        return None
    status = str(raw.get("status") or "scheduled").lower()
    if status not in _STATUSES:
        return None
    try:
        kickoff = int(raw.get("kickoff_unix"))
        week = int(raw.get("week"))
        season = int(raw.get("season"))
    except (TypeError, ValueError):
        return None
    return ScheduleGame(away=away, home=home, kickoff_unix=kickoff, week=week, season=season, status=status)


def _heuristic_extract(hits: list[SearchHit]) -> list[ScheduleGame]:
    blob = " ".join(h.content for h in hits)
    games = []
    seen = set()
    for match in _ROW.finditer(blob):
        game = _canonicalize_game(
            {
                "away": match.group("away"),
                "home": match.group("home"),
                "kickoff_unix": match.group("kickoff"),
                "week": match.group("week"),
                "season": match.group("season"),
                "status": match.group("status"),
            }
        )
        if game is None or game.key() in seen:
            continue
        seen.add(game.key())
        games.append(game)
    if not games:
        raise RuntimeError("no canonical NFL games")
    return games


def _cursor_extract(slot: str) -> list[ScheduleGame]:
    prompt = """Use the search MCP to extract the current NFL week and the next NFL week.

Respond with JSON only:
- games: list of {away, home, kickoff_unix, week, season, status}
- search_hits: list of {url, content} actually returned by search tools
- evidence_urls: subset of search_hits urls

Use only NFL nicknames (Bills, Chiefs, 49ers, ...). status must be scheduled | in_progress | final | postponed | cancelled.
Omit bye weeks. Never invent URLs. kickoff_unix is UNIX seconds UTC.
"""
    data = prompt_json(prompt, model_id(slot))
    raw_hits = data.get("search_hits")
    if not isinstance(raw_hits, list) or not raw_hits:
        raise RuntimeError("search_hits required from cursor agent")
    hit_urls = {str(h.get("url") or "") for h in raw_hits if isinstance(h, dict)}
    for url in data.get("evidence_urls") or []:
        if url not in hit_urls:
            raise RuntimeError(f"Evidence URL {url} not found in search hits")
    raw_games = data.get("games") if isinstance(data.get("games"), list) else []
    games = []
    seen = set()
    for item in raw_games:
        if not isinstance(item, dict):
            continue
        game = _canonicalize_game(item)
        if game is None or game.key() in seen:
            continue
        seen.add(game.key())
        games.append(game)
    if not games:
        raise RuntimeError("no canonical NFL games")
    return games


def scout(search=None, slot: str = "alpha") -> list[ScheduleGame]:
    if search is not None or should_use_mock():
        hits = hits_from_search(search or MockSearch([]), "NFL schedule this week and next week")
        return _heuristic_extract(hits)
    return _cursor_extract(slot)


class ScheduleCoordinator:
    def __init__(self, searches=None, publisher=None, slots=None):
        self.publisher = publisher
        if searches is None:
            self.searches = None
            self.slots = slots or ("alpha", "beta", "gamma")
        else:
            self.searches = searches
            self.slots = None

    def run(self) -> dict:
        if self.searches is not None:
            reports = [scout(search=s) for s in self.searches]
        else:
            reports = [scout(slot=slot) for slot in self.slots]
        keys = {frozenset(g.key() for g in games) for games in reports}
        unanimous = len(keys) == 1
        chosen = reports[0] if unanimous else None
        if unanimous and chosen is not None:
            publisher = self.publisher
            if publisher is None:
                from schedule.publish import publish_schedule

                publisher = publish_schedule
            publisher([g.as_api_body() for g in chosen])
        return {
            "unanimous": unanimous,
            "games": [asdict(g) for g in chosen] if chosen else None,
            "reports": [[asdict(g) for g in games] for games in reports],
        }
