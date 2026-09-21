"""Cloud Run Job / local poller: scout sports primaries and POST on 3/3."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from scores.scout import ScoreCoordinator

DEFAULT_MAX_MARKETS = 5
DEFAULT_STALE_SECONDS = 600


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


def select_targets(
    primaries: list[dict],
    fetch_detail: Callable[[str], dict],
    now: datetime,
    cap: int,
    window: int,
) -> list[dict]:
    selected: list[dict] = []
    for primary in primaries:
        if len(selected) >= cap:
            break
        condition_id = primary.get("conditionId") or ""
        if not condition_id:
            continue
        detail = fetch_detail(condition_id) or {}
        if skip_fresh(detail.get("score"), now, window):
            continue
        selected.append(primary)
    return selected


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

    targets = select_targets(primaries, fetch_detail, clock, cap, window)
    factory = coordinator_factory or (lambda: ScoreCoordinator(publisher=publisher))
    results = []
    for primary in targets:
        cid = primary["conditionId"]
        question = primary.get("question") or ""
        try:
            coord = factory()
            outcome = coord.run(question, condition_id=cid)
            results.append({"conditionId": cid, "ok": True, "unanimous": outcome.get("unanimous")})
        except Exception as exc:
            print(f"scout failed {cid}: {exc}", file=sys.stderr)
            results.append({"conditionId": cid, "ok": False, "error": str(exc)})
    return {"attempted": len(targets), "results": results}


def main() -> int:
    summary = run_job()
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
