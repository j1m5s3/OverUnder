"""Create week N+1 winner primaries once every week N game is final, postponed or cancelled.

Schedule rows are matched to markets by the row's listedConditionId, else by
(question, closeTime == kickoff): question text alone repeats across seasons
and rematches. A stale row whose kickoff is older than
OU_LISTING_STALE_GRACE_SECONDS counts as done, because the schedule scout only
refreshes the current and next week. A 409 from create (condition prepared
outside the factory) is reported under `squatted`, not as a stage error.

A linked market is trusted only while it is registered on the configured oracle
(ConsensusOracle.closeTime != 0). A link to a market created on an older oracle
(an orphan, possibly archived) can never resolve, so the game is treated as
unlisted: it is relisted, and when it cannot be relisted this tick the link is
cleared (schedule upsert with listedConditionId ""). A paused market that is still
registered keeps its link (an operator decision) and is reported under
`pausedLinks`. When the chain read fails, or the job has no RPC/oracle config,
links are trusted as before (`linkCheck` says which). Creates stop once the tick
budget is spent; the rest are reported under `deferred` for the next tick.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable

import httpx

import budget
from listing.questions import question_id, winner_question
from redact import log_error
from scores.job import _api_url, _http_get_json, sports_primaries
from scores.publish import mint_operator_jwt

DEFAULT_SEED = 200_000_000
# MarketFactory asserts close > block.timestamp; leave room for the tx to land.
MIN_LEAD_SECONDS = 600
DONE_STATUSES = frozenset({"final", "postponed", "cancelled"})
STALE_STATUSES = (None, "scheduled", "in_progress")
DEFAULT_STALE_GRACE_SECONDS = 8 * 3600


def seed_usdc() -> int:
    raw = os.getenv("OU_SEED_USDC", str(DEFAULT_SEED)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("OU_SEED_USDC must be an integer") from exc
    if value <= 0:
        raise RuntimeError("OU_SEED_USDC must be > 0")
    return value


def stale_grace_seconds() -> int:
    raw = os.getenv("OU_LISTING_STALE_GRACE_SECONDS", str(DEFAULT_STALE_GRACE_SECONDS)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("OU_LISTING_STALE_GRACE_SECONDS must be an integer") from exc
    if value < 0:
        raise RuntimeError("OU_LISTING_STALE_GRACE_SECONDS must be >= 0")
    return value


def _listed_cid(game: dict, cards_by_key: dict[tuple[str, int], str]) -> str | None:
    """The market linked to this schedule row: listedConditionId, else a card with the
    same question and closeTime == kickoff (legacy rows, or a failed schedule upsert)."""
    cid = game.get("listedConditionId")
    if cid:
        return cid
    return cards_by_key.get((winner_question(game["home"], game["away"]), int(game["kickoff_unix"])))


def _game_status(game: dict, status_by_cid: dict[str, str | None]) -> str | None:
    """Live score status only when the row is linked to that specific market."""
    cid = game.get("listedConditionId")
    live = status_by_cid.get(cid) if cid else None
    return live if live is not None else game.get("status")


def _stale(game: dict, status: str | None, now: float | None, grace: int) -> bool:
    return now is not None and status in STALE_STATUSES and int(game["kickoff_unix"]) + grace < now


def week_complete(
    games: list[dict],
    live_status: dict[str, str | None],
    now: float | None = None,
    grace: int = DEFAULT_STALE_GRACE_SECONDS,
) -> bool:
    """Every game is final/postponed/cancelled, or stale past the grace period.

    `live_status` is keyed by conditionId and applies to a game only through its
    listedConditionId; otherwise the schedule row's status decides. With
    `now=None` there is no stale rule.
    """
    return bool(games) and not _blocking_games(games, live_status, now, grace)


def _blocking_games(games: list[dict], live_status: dict[str, str | None], now: float | None, grace: int) -> list[dict]:
    out = []
    for game in games:
        status = _game_status(game, live_status)
        if status in DONE_STATUSES or _stale(game, status, now, grace):
            continue
        out.append(game)
    return out


def _brief(game: dict, status: str | None) -> dict:
    return {
        "id": game.get("id"),
        "away": game.get("away"),
        "home": game.get("home"),
        "status": status,
        "kickoff_unix": game.get("kickoff_unix"),
    }


def _default_chain():
    """resolve.chain when the job has RPC + oracle config, else None (link checks off)."""
    rpc = (os.getenv("ANVIL_RPC_URL") or os.getenv("OU_RPC_URL") or "").strip()
    if not rpc or not (os.getenv("ORACLE_ADDRESS") or "").strip():
        return None
    from resolve import chain as chain_mod

    return chain_mod


def _schedule_body(game: dict, listed_cid: str) -> dict:
    return {
        "away": game["away"],
        "home": game["home"],
        "kickoff_unix": game["kickoff_unix"],
        "week": game["week"],
        "season": game["season"],
        "status": game.get("status") or "scheduled",
        "listedConditionId": listed_cid,
    }


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


def _conflict_detail(exc: Exception) -> str | None:
    """Detail text when create failed with HTTP 409 (condition prepared outside the factory)."""
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) != 409:
        return None
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
        return payload["detail"]
    return "conflict"


def run(
    *,
    http_get: Callable[[str], Any] | None = None,
    http_post: Callable[..., Any] | None = None,
    now: float | None = None,
    chain=None,
) -> dict:
    """`chain` needs onchain_close_time(cid); defaults to resolve.chain when configured."""
    base = _api_url()
    getter = http_get or _http_get_json
    poster = http_post or _post_json
    clock = now if now is not None else time.time()
    grace = stale_grace_seconds()
    cards = getter(f"{base}/api/v1/markets")
    if not isinstance(cards, list):
        raise RuntimeError("markets list must be an array")
    schedule = getter(f"{base}/api/v1/markets/schedule")
    if not isinstance(schedule, list):
        raise RuntimeError("schedule must be an array")
    primaries = sports_primaries(cards)
    chain_api = chain if chain is not None else _default_chain()
    cards_by_key: dict[tuple[str, int], str] = {}
    questions_listed: set[str] = set()
    visible_cids = {str(p.get("conditionId") or "").lower() for p in primaries if p.get("conditionId")}
    for primary in primaries:
        cid = primary.get("conditionId") or ""
        question = primary.get("question") or ""
        if not cid or not question:
            continue
        questions_listed.add(question)
        try:
            close = int(primary.get("closeTime") or 0)
        except (TypeError, ValueError):
            close = 0
        if close:
            cards_by_key.setdefault((question, close), cid)
    # Link every schedule row to its own market before any status lookup.
    games_linked = [
        {**game, "listedConditionId": _listed_cid(game, cards_by_key), "_rowLink": game.get("listedConditionId") or ""}
        for game in schedule
    ]
    status_by_cid: dict[str, str | None] = {}
    for cid in sorted({g["listedConditionId"] for g in games_linked if g["listedConditionId"]}):
        try:
            detail = getter(f"{base}/api/v1/markets/{cid}")
        except Exception as exc:
            log_error(f"listing detail failed {cid}: {exc}")
            detail = None
        score = detail.get("score") if isinstance(detail, dict) and isinstance(detail.get("score"), dict) else None
        status_by_cid[cid] = None if not score else score.get("status")
    grouped: dict[tuple[int, int], list[dict]] = {}
    for game in games_linked:
        grouped.setdefault((int(game["season"]), int(game["week"])), []).append(game)
    created = []
    skipped = []
    too_late = []
    inactive = []
    errors = []
    squatted = []
    blocked = []
    assumed_done = []
    collisions = []
    orphaned = []
    paused_links = []
    link_errors = []
    deferred = []
    link_state: dict[str, str] = {}
    seen_ids: set[str] = set()
    token = None

    def check_link(cid: str) -> str:
        """"ok" | "orphan" (not registered on the configured oracle) | "paused" | "unknown"."""
        if cid in link_state:
            return link_state[cid]
        if chain_api is None:
            state = "unknown"
        else:
            try:
                registered = int(chain_api.onchain_close_time(cid) or 0) != 0
            except Exception as exc:
                log_error(f"listing link check failed {cid}: {exc}")
                link_errors.append({"conditionId": cid, "error": str(exc)})
                registered = None
            if registered is None:
                state = "unknown"
            elif not registered:
                state = "orphan"
            elif cid.lower() not in visible_cids:
                state = "paused"
            else:
                state = "ok"
        link_state[cid] = state
        return state

    def operator_token() -> str:
        nonlocal token
        if token is None:
            token = mint_operator_jwt()
        return token

    def clear_link(game: dict, q: str) -> None:
        try:
            poster(f"{base}/api/v1/markets/schedule", [_schedule_body(game, "")], {"Authorization": f"Bearer {operator_token()}"})
        except Exception as exc:
            log_error(f"listing unlink failed {q}: {exc}")
            errors.append({"question": q, "error": f"unlink orphan: {exc}"})

    def list_game(game: dict, q: str) -> bool:
        """Create the game's market and link it; True when a market was created."""
        qid = question_id(game["season"], game["week"], game["away"], game["home"], game["kickoff_unix"])
        if qid in seen_ids:
            skipped.append(q)
            return False
        if q in questions_listed:
            # Same text, different game (earlier season or a rematch): list it, but say so.
            collisions.append({"question": q, "season": game["season"], "week": game["week"]})
        if (game.get("status") or "scheduled") != "scheduled":
            inactive.append(q)
            return False
        kickoff = int(game["kickoff_unix"])
        if kickoff <= clock + MIN_LEAD_SECONDS:
            too_late.append(q)
            return False
        if budget.exhausted():
            deferred.append(q)
            return False
        # Config errors (JWT_SECRET, OU_SEED_USDC) still fail the whole stage.
        auth = {"Authorization": f"Bearer {operator_token()}"}
        body = {
            "question": q,
            "resolution_criteria": "NFL winner; YES if named team wins",
            "close_time": kickoff,
            "question_id": qid,
            "seed_usdc": seed_usdc(),
            "market_type": 0,
        }
        try:
            created_row = poster(f"{base}/api/v1/markets", body, auth)
        except Exception as exc:
            conflict = _conflict_detail(exc)
            if conflict is not None:
                # Someone prepared this condition first; retrying every tick cannot fix it.
                log_error(f"listing create squatted {q}: {conflict}")
                squatted.append({"question": q, "questionId": qid, "detail": conflict})
                seen_ids.add(qid)
                return False
            log_error(f"listing create failed {q}: {exc}")
            errors.append({"question": q, "error": str(exc)})
            return False
        seen_ids.add(qid)
        cid = created_row.get("conditionId") if isinstance(created_row, dict) else None
        created.append({"question": q, "conditionId": cid, "close_time": body["close_time"]})
        if cid:
            try:
                poster(f"{base}/api/v1/markets/schedule", [_schedule_body(game, cid)], auth)
            except Exception as exc:
                log_error(f"listing schedule upsert failed {q}: {exc}")
        return True

    for (season, week), games in sorted(grouped.items()):
        nxt = grouped.get((season, week + 1))
        if not nxt:
            continue
        for game in games:
            status = _game_status(game, status_by_cid)
            if status not in DONE_STATUSES and _stale(game, status, clock, grace):
                assumed_done.append({"season": season, "week": week, **_brief(game, status)})
        blocking = _blocking_games(games, status_by_cid, clock, grace)
        if not games or blocking:
            blocked.append(
                {
                    "season": season,
                    "week": week,
                    "blockedBy": [_brief(game, _game_status(game, status_by_cid)) for game in blocking],
                }
            )
            continue
        for game in nxt:
            q = winner_question(game["home"], game["away"])
            linked = game["listedConditionId"]
            if linked:
                state = check_link(linked)
                if state == "paused":
                    paused_links.append({"question": q, "conditionId": linked})
                if state != "orphan":
                    skipped.append(q)
                    continue
                # Not registered on the configured oracle: it can never resolve, so list the game again.
                orphaned.append({"question": q, "conditionId": linked, "season": game["season"], "week": game["week"]})
            if not list_game(game, q) and linked and game["_rowLink"]:
                clear_link(game, q)
    return {
        "ok": not errors,
        "created": created,
        "skipped": skipped,
        "tooLate": too_late,
        "inactive": inactive,
        "errors": errors,
        "squatted": squatted,
        "blocked": blocked,
        "assumedDone": assumed_done,
        "collisions": collisions,
        "orphaned": orphaned,
        "pausedLinks": paused_links,
        "linkCheck": "off" if chain_api is None else ("errors" if link_errors else "on"),
        "linkCheckErrors": link_errors,
        "deferred": deferred,
    }
