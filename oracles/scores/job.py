"""Cloud Run Job / local poller: scout sports primaries and POST on 3/3."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

import budget
from redact import log_error, redact
from scores.scout import ScoreCoordinator

DEFAULT_MAX_MARKETS = 5
DEFAULT_STALE_SECONDS = 600
DEFAULT_MAX_AGE_SECONDS = 0
# Games that kicked off within this window are scouted before the backlog.
DEFAULT_RECENT_SECONDS = 36 * 3600
# Backlog rotation step: the scheduled tick interval.
BACKLOG_ROTATE_SECONDS = 900
DONE_SCORE_STATUSES = frozenset({"final", "cancelled"})


def _api_url() -> str:
    url = os.getenv("OU_API_URL", "").rstrip("/")
    if not url:
        raise RuntimeError("OU_API_URL required for score job")
    return url


def max_markets() -> int:
    raw = os.getenv("OU_SCOUT_MAX_MARKETS", str(DEFAULT_MAX_MARKETS)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("OU_SCOUT_MAX_MARKETS must be an integer") from exc
    return max(1, value)


def stale_seconds() -> int:
    raw = os.getenv("OU_SCOUT_STALE_SECONDS", str(DEFAULT_STALE_SECONDS)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("OU_SCOUT_STALE_SECONDS must be an integer") from exc
    return max(0, value)


def max_age_seconds() -> int:
    raw = os.getenv("OU_SCOUT_MAX_AGE_SECONDS", str(DEFAULT_MAX_AGE_SECONDS)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("OU_SCOUT_MAX_AGE_SECONDS must be an integer") from exc
    return max(0, value)


def recent_seconds() -> int:
    raw = os.getenv("OU_SCOUT_RECENT_SECONDS", str(DEFAULT_RECENT_SECONDS)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("OU_SCOUT_RECENT_SECONDS must be an integer") from exc
    return max(0, value)


def is_sports_primary(card: dict) -> bool:
    primary = card.get("primary") if isinstance(card.get("primary"), dict) else card
    if int(primary.get("marketType") or 0) != 0:
        return False
    question = (primary.get("question") or "").lower()
    return " vs " in question or " vs. " in question


def parse_updated_at(value: Any, now: datetime) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        ts = value
    else:
        text = str(value).replace("Z", "+00:00")
        try:
            ts = datetime.fromisoformat(text)
        except ValueError:
            return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def skip_fresh(score: dict | None, now: datetime, window: int) -> bool:
    if not score:
        return False
    if score.get("status") == "in_progress":
        return False
    ts = parse_updated_at(score.get("updatedAt"), now)
    if ts is None:
        return False
    return (now - ts).total_seconds() < window


def sports_primaries(cards: list[dict]) -> list[dict]:
    out = []
    for card in cards:
        if not is_sports_primary(card):
            continue
        primary = card.get("primary") if isinstance(card.get("primary"), dict) else card
        out.append(primary)
    return out


def _close_time(primary: dict) -> int:
    try:
        return int(primary.get("closeTime") or 0)
    except (TypeError, ValueError):
        return 0


def select_targets(
    primaries: list[dict],
    fetch_detail: Callable[[str], dict],
    now: datetime,
    cap: int,
    window: int,
    max_age: int = 0,
    recent: int = DEFAULT_RECENT_SECONDS,
) -> list[dict]:
    """Recent kickoffs first, then the backlog; card-level filters run before any detail GET.

    Tier 1: games that kicked off within `recent` seconds (or have no closeTime),
    oldest first. Tier 2: older games, oldest first, rotated each tick so a few
    games that never reach a done status (postponed, never 3/3, legacy) cannot hold
    the leftover slots either. A stuck backlog therefore never starves new games,
    and old games still catch up in quiet periods. `max_age` > 0 is an opt-in hard
    cutoff that drops games closed longer ago than that.
    """
    clock = now.timestamp()
    eligible: list[dict] = []
    for primary in sorted(primaries, key=lambda p: (_close_time(p), p.get("conditionId") or "")):
        if not (primary.get("conditionId") or "") or primary.get("resolved"):
            continue
        close = _close_time(primary)
        if close > clock:
            continue
        if max_age and close and clock - close > max_age:
            continue
        eligible.append(primary)
    fresh_tier = [p for p in eligible if not _close_time(p) or clock - _close_time(p) <= recent]
    backlog = [p for p in eligible if _close_time(p) and clock - _close_time(p) > recent]
    if backlog:
        shift = int(clock // BACKLOG_ROTATE_SECONDS) % len(backlog)
        backlog = backlog[shift:] + backlog[:shift]
    selected: list[dict] = []
    for primary in fresh_tier + backlog:
        if len(selected) >= cap:
            break
        condition_id = primary["conditionId"]
        try:
            detail = fetch_detail(condition_id) or {}
        except Exception as exc:
            log_error(f"scout detail failed {condition_id}: {exc}")
            continue
        if detail.get("resolved"):
            continue
        score = detail.get("score") if isinstance(detail.get("score"), dict) else None
        if score and score.get("status") in DONE_SCORE_STATUSES:
            continue
        if skip_fresh(score, now, window):
            continue
        selected.append(primary)
    return selected


def kickoff_iso(primary: dict) -> str | None:
    """The game's kickoff (a primary's closeTime) as an ISO UTC stamp, or None when unknown."""
    close = _close_time(primary)
    if close <= 0:
        return None
    return datetime.fromtimestamp(close, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _http_get_json(url: str) -> Any:
    with httpx.Client(timeout=30) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()


def run_job(
    *,
    http_get: Callable[[str], Any] | None = None,
    coordinator_factory: Callable[..., ScoreCoordinator] | None = None,
    publisher=None,
    now: datetime | None = None,
) -> dict:
    base = _api_url()
    getter = http_get or _http_get_json
    clock = now or datetime.now(timezone.utc)
    cap = max_markets()
    window = stale_seconds()
    cards = getter(f"{base}/api/v1/markets")
    if not isinstance(cards, list):
        raise RuntimeError("markets list must be an array")
    primaries = sports_primaries(cards)

    def fetch_detail(condition_id: str) -> dict:
        payload = getter(f"{base}/api/v1/markets/{condition_id}")
        return payload if isinstance(payload, dict) else {}

    targets = select_targets(primaries, fetch_detail, clock, cap, window, max_age_seconds(), recent_seconds())
    factory = coordinator_factory or (lambda: ScoreCoordinator(publisher=publisher))
    min_seconds = budget.research_min_seconds()
    results = []
    for primary in targets:
        cid = primary["conditionId"]
        question = primary.get("question") or ""
        # Three sequential agent runs: do not start one the stage budget cannot finish.
        if budget.exhausted() or not budget.can_start(min_seconds):
            results.append({"conditionId": cid, "ok": True, "skipped": "budget"})
            continue
        try:
            coord = factory()
            # The kickoff pins the scout to this meeting of the two teams (not an earlier one).
            outcome = coord.run(question, condition_id=cid, kickoff=kickoff_iso(primary))
            compact = [
                {
                    "home": r.get("home_label"),
                    "away": r.get("away_label"),
                    "homeScore": r.get("home_score"),
                    "awayScore": r.get("away_score"),
                    "status": r.get("status"),
                    "gameDate": r.get("game_date"),
                    "facts": r.get("facts"),
                }
                for r in (outcome.get("reports") or [])
            ]
            log_error(f"scout {cid} unanimous={outcome.get('unanimous')} reports={compact}")
            results.append(
                {
                    "conditionId": cid,
                    "ok": True,
                    "unanimous": outcome.get("unanimous"),
                    "reports": compact,
                }
            )
        except Exception as exc:
            log_error(f"scout failed {cid}: {exc}")
            results.append({"conditionId": cid, "ok": False, "error": str(exc)})
    return {"ok": True, "attempted": len(targets), "results": results}


def main() -> int:
    summary = run_job()
    print(redact(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
