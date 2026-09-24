"""Score scout: search agents extract box scores and bet-relevant facts, not resolution.

Does not call ConsensusOracle or write resolution. ScoreCoordinator auto-POSTs
only on 3/3 via publish.py. Fail closed: missing numbers stay null, never 0-0.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from agents.base import MockSearch, SearchHit, hits_from_search, should_use_mock
from agents.cursor_runtime import model_id, prompt_json

_VS = re.compile(r"^([^:?]+?)\s+vs\.?\s+([^:?]+?)(?::|$)", re.IGNORECASE)
_PAIR = re.compile(r"(\d{1,3})\s*[-–]\s*(\d{1,3})")
_NAMED = re.compile(
    r"\b([A-Za-z][A-Za-z .]{1,24})\s+(\d{1,3})\s*[,/]\s*([A-Za-z][A-Za-z .]{1,24})\s+(\d{1,3})\b"
)
_FUMBLE = re.compile(r"\b(zero|one|two|three|\d+)\s+fumble", re.IGNORECASE)
_TOTAL = re.compile(r"\btotal(?: points)?\s*[:=]?\s*(\d{1,3})\b", re.IGNORECASE)
_FIRST = re.compile(r"\b([A-Za-z][A-Za-z .]{1,24})\s+(?:scored first|to score first|first score)\b", re.IGNORECASE)
_WORD_NUM = {"zero": 0, "one": 1, "two": 2, "three": 3}


@dataclass
class ScoreReport:
    home_label: str
    away_label: str
    home_score: int | None
    away_score: int | None
    status: str
    period_label: str | None
    summary: str
    evidence_urls: list[str]
    facts: dict = field(default_factory=dict)

    def key(self) -> tuple:
        return (
            self.home_label.lower(),
            self.away_label.lower(),
            self.home_score,
            self.away_score,
            self.status,
            json.dumps(self.facts or {}, sort_keys=True),
        )

    def as_api_body(self) -> dict:
        return {
            "homeLabel": self.home_label,
            "awayLabel": self.away_label,
            "homeScore": self.home_score,
            "awayScore": self.away_score,
            "status": self.status,
            "periodLabel": self.period_label,
            "facts": self.facts or {},
        }


_STATUSES = {"scheduled", "in_progress", "final", "postponed", "cancelled"}
# Statuses with no meaningful score: numbers and facts stay null.
_SCORELESS = frozenset({"scheduled", "postponed", "cancelled"})


def canonicalize(report: ScoreReport, question: str) -> ScoreReport:
    """Pin labels to the market question so 3/3 is not blocked by team-name aliases."""
    home, away = parse_teams(question)
    keys = requested_facts(question)
    status = report.status if report.status in _STATUSES else "scheduled"
    home_score, away_score = report.home_score, report.away_score
    src = report.facts or {}
    facts = {}
    for key in keys:
        value = src.get(key)
        if value is None and key == "home_score":
            value = home_score
        elif value is None and key == "away_score":
            value = away_score
        facts[key] = value
    if status in _SCORELESS:
        home_score, away_score = None, None
        facts = {key: None for key in keys}
    return ScoreReport(
        home_label=home,
        away_label=away,
        home_score=home_score,
        away_score=away_score,
        status=status,
        period_label=report.period_label,
        summary=report.summary,
        evidence_urls=report.evidence_urls,
        facts=facts,
    )


def parse_teams(question: str) -> tuple[str, str]:
    m = _VS.search(question.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "Home", "Away"


def requested_facts(question: str) -> tuple[str, ...]:
    q = question.lower()
    if "fumble" in q:
        return ("fumbles",)
    if "score first" in q or "first score" in q:
        return ("first_scorer",)
    if "total" in q or re.search(r"\bover\b", q):
        return ("total_points",)
    return ("home_score", "away_score")


def _status_from_text(text: str, has_score: bool) -> tuple[str, str | None]:
    low = text.lower()
    if any(w in low for w in ("final", "defeated", "won", "beats", "beat")):
        return "final", None
    if any(w in low for w in ("q1", "q2", "q3", "q4", "quarter", "halftime", "ot", "in progress", "live")):
        period = None
        for token in ("Q1", "Q2", "Q3", "Q4", "OT", "Halftime"):
            if token.lower() in low:
                period = token
                break
        return "in_progress", period
    if has_score:
        return "in_progress", None
    return "scheduled", None


def _int_or_none(value) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _facts_from_blob(question: str, blob: str, home: str, away: str, home_score: int | None, away_score: int | None) -> dict:
    facts: dict = {}
    for key in requested_facts(question):
        if key == "home_score":
            facts[key] = home_score
        elif key == "away_score":
            facts[key] = away_score
        elif key == "total_points":
            if home_score is not None and away_score is not None:
                facts[key] = home_score + away_score
            else:
                m = _TOTAL.search(blob)
                facts[key] = int(m.group(1)) if m else None
        elif key == "fumbles":
            m = _FUMBLE.search(blob)
            if not m:
                facts[key] = None
            else:
                token = m.group(1).lower()
                facts[key] = _WORD_NUM.get(token, int(token) if token.isdigit() else None)
        elif key == "first_scorer":
            m = _FIRST.search(blob)
            if m:
                facts[key] = m.group(1).strip()
            elif home.lower() in blob.lower():
                facts[key] = home
            elif away.lower() in blob.lower():
                facts[key] = away
            else:
                facts[key] = None
    return facts


def _require_facts(question: str, status: str, facts: dict) -> None:
    if status in _SCORELESS:
        return
    for key in requested_facts(question):
        if facts.get(key) is None:
            raise RuntimeError(f"missing requested fact {key}")


def _heuristic_extract(question: str, hits: list[SearchHit]) -> ScoreReport:
    home, away = parse_teams(question)
    blob = " ".join(h.content for h in hits)
    text = blob if blob else question
    home_score = None
    away_score = None

    named = _NAMED.search(blob)
    if named:
        a_name, a_pts, b_name, b_pts = named.group(1).strip(), int(named.group(2)), named.group(3).strip(), int(named.group(4))
        if a_name.lower() in home.lower() or home.lower() in a_name.lower():
            home_score, away_score = a_pts, b_pts
        elif a_name.lower() in away.lower() or away.lower() in a_name.lower():
            home_score, away_score = b_pts, a_pts
        else:
            home_score, away_score = a_pts, b_pts

    if home_score is None:
        pair = _PAIR.search(blob)
        if pair:
            home_score, away_score = int(pair.group(1)), int(pair.group(2))

    status, period = _status_from_text(text, home_score is not None)
    if status == "scheduled":
        home_score, away_score = None, None

    facts = _facts_from_blob(question, blob, home, away, home_score, away_score)
    if status == "scheduled":
        facts = {k: None for k in requested_facts(question)}
    _require_facts(question, status, facts)

    urls = [h.url for h in hits[:3] if h.url]
    summary = (hits[0].content[:280] if hits else "no evidence")
    return ScoreReport(
        home_label=home,
        away_label=away,
        home_score=home_score,
        away_score=away_score,
        status=status,
        period_label=period,
        summary=summary,
        evidence_urls=urls,
        facts=facts,
    )


def _cursor_extract(question: str, slot: str) -> ScoreReport:
    home, away = parse_teams(question)
    keys = list(requested_facts(question))
    prompt = f"""Use the search MCP to extract the current or final sports facts for this market.

Question: "{question}"
Expected home team: {home}
Expected away team: {away}
Requested fact keys: {keys}

Respond with JSON only:
- home_label, away_label (strings)
- home_score, away_score (integers or null if the game has not started / score unknown)
- status: scheduled | in_progress | final | postponed | cancelled
- period_label: short string or null
- facts: object containing every requested key (number, string, or null)
- summary: max 280 chars
- search_hits: list of {{url, content}} actually returned by search tools
- evidence_urls: subset of search_hits urls

Never invent a 0-0 score for a scheduled, postponed or cancelled game. Use nulls instead. Never invent URLs.
"""
    data = prompt_json(prompt, model_id(slot))
    raw_hits = data.get("search_hits")
    if not isinstance(raw_hits, list) or not raw_hits:
        raise RuntimeError("search_hits required from cursor agent")
    hits = [SearchHit(url=str(h.get("url") or ""), content=str(h.get("content") or "")) for h in raw_hits if isinstance(h, dict) and h.get("url")]
    if not hits:
        raise RuntimeError("search_hits required from cursor agent")
    hit_urls = {h.url for h in hits}
    evidence_urls = [url for url in (data.get("evidence_urls") or []) if url in hit_urls]
    home_score = _int_or_none(data.get("home_score"))
    away_score = _int_or_none(data.get("away_score"))
    status = data.get("status") or "scheduled"
    if status not in _STATUSES:
        status = "scheduled"
    if status in _SCORELESS:
        home_score, away_score = None, None
    facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
    for key in keys:
        facts.setdefault(key, None)
        if key == "home_score":
            facts[key] = home_score if facts.get(key) is None else facts[key]
        elif key == "away_score":
            facts[key] = away_score if facts.get(key) is None else facts[key]
    if status in _SCORELESS:
        facts = {k: None for k in keys}
        home_score, away_score = None, None
    _require_facts(question, status, facts)
    report = ScoreReport(
        home_label=str(data.get("home_label") or home),
        away_label=str(data.get("away_label") or away),
        home_score=home_score,
        away_score=away_score,
        status=status,
        period_label=data.get("period_label"),
        summary=str(data.get("summary") or "")[:280],
        evidence_urls=evidence_urls,
        facts=facts,
    )
    for url in report.evidence_urls:
        if url not in hit_urls:
            raise RuntimeError(f"Evidence URL {url} not found in search hits")
    return report


def extract_score(question: str, hits: list[SearchHit]) -> ScoreReport:
    report = _heuristic_extract(question, hits)
    hit_urls = {h.url for h in hits}
    for url in report.evidence_urls:
        if url not in hit_urls:
            raise RuntimeError(f"Evidence URL {url} not found in search hits")
    return report


def scout(question: str, search=None, slot: str = "alpha") -> ScoreReport:
    if search is not None or should_use_mock():
        hits = hits_from_search(search, f"{question} score box score")
        report = extract_score(question, hits)
    else:
        report = _cursor_extract(question, slot)
    return canonicalize(report, question)


class ScoreCoordinator:
    """Three independent searches must agree on the numeric score before publish."""

    def __init__(self, searches=None, publisher=None, slots=None):
        self.publisher = publisher
        if searches is None:
            self.searches = None
            self.slots = slots or ("alpha", "beta", "gamma")
        else:
            self.searches = searches
            self.slots = None

    def run(self, question: str, condition_id: str | None = None) -> dict:
        if self.searches is not None:
            reports = [scout(question, search=s) for s in self.searches]
        else:
            reports = [scout(question, slot=slot) for slot in self.slots]
        keys = {r.key() for r in reports}
        unanimous = len(keys) == 1
        chosen = reports[0] if unanimous else None
        if unanimous and chosen is not None and condition_id:
            publisher = self.publisher
            if publisher is None:
                from scores.publish import publish_score

                publisher = publish_score
            publisher(condition_id, chosen)
        return {
            "unanimous": unanimous,
            "report": asdict(chosen) if chosen else None,
            "apiBody": chosen.as_api_body() if chosen else None,
            "reports": [asdict(r) for r in reports],
        }


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) or "Chiefs vs Broncos: Chiefs win?"
    print(json.dumps(ScoreCoordinator().run(q), indent=2))
