"""Oracle tick: single-flight lease, auth preflight, then scores, resolve, resolve_general, schedule, listing.

A tick that finds an older execution still running exits 0 with {"skipped": "running"}.

OU_TICK_BUDGET_SECONDS bounds one tick, and every stage runs under budget.stage():
- a share of the total budget, OU_STAGE_SHARE_<STAGE> in (0, 1] (scores defaults to
  0.4, the others to 1.0), so the agent-heavy score scout cannot use the whole tick;
- every stage before listing also stops OU_LISTING_RESERVE_SECONDS (default 120,
  at most a quarter of the budget) before the tick deadline, so the cheap,
  time-critical listing always gets to run.
Work a stage leaves for the next tick because its budget ran out is "deferred":
- a whole stage skipped for budget is {"ok": False, "skipped": "budget"} for the
  critical stages (resolve, listing), so the tick exits 1, and
  {"ok": True, "skipped": "budget", "deferred": True} for the others;
- summary["deferred"] counts, per stage, whole-stage skips and markets or games a
  stage deferred for budget (results with reason/skipped "budget" or a "deferred" key).
"""

from __future__ import annotations

import importlib
import json
import os
from typing import Any, Callable

import budget
from redact import SECRET_ENV, log_error, redact  # noqa: F401  (SECRET_ENV re-exported for callers)

STAGES = ("scores", "resolve", "resolve_general", "schedule", "listing")
# Stages that only write through the operator JWT; skipped when the preflight fails.
AUTH_STAGES = frozenset({"scores", "schedule", "listing"})
# A budget skip of these stages fails the tick (exit 1) instead of being a quiet deferral.
CRITICAL_STAGES = frozenset({"resolve", "listing"})
DEFAULT_SHARES = {"scores": 0.4, "resolve": 1.0, "resolve_general": 1.0, "schedule": 1.0, "listing": 1.0}
DEFAULT_LISTING_RESERVE_SECONDS = 120
MAX_RESERVE_FRACTION = 0.25


def stage_share(name: str) -> float:
    env = f"OU_STAGE_SHARE_{name.upper()}"
    raw = os.getenv(env, "").strip()
    if not raw:
        return DEFAULT_SHARES[name]
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{env} must be a number") from exc
    if not 0.0 < value <= 1.0:
        raise RuntimeError(f"{env} must be in (0, 1]")
    return value


def listing_reserve_seconds() -> int:
    raw = os.getenv("OU_LISTING_RESERVE_SECONDS", str(DEFAULT_LISTING_RESERVE_SECONDS)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("OU_LISTING_RESERVE_SECONDS must be an integer") from exc
    if value < 0:
        raise RuntimeError("OU_LISTING_RESERVE_SECONDS must be >= 0")
    return value


def _reserve() -> float:
    """Seconds kept free for listing: the env value, capped at a quarter of the tick budget."""
    value = float(listing_reserve_seconds())
    total = budget.total_seconds()
    return min(value, total * MAX_RESERVE_FRACTION) if total else value


# Stage modules load lazily so one broken import fails only its own stage.
def _default_scores(**kwargs):
    from scores import job as scores_job

    return scores_job.run_job(**kwargs)


def _default_resolve(**kwargs):
    from resolve import run as resolve_run

    return resolve_run.run(**kwargs)


def _default_resolve_general(**kwargs):
    general = importlib.import_module("resolve.general")
    return general.run(**kwargs)


def _default_schedule():
    from schedule.scout import ScheduleCoordinator

    return ScheduleCoordinator()


def _default_listing(**kwargs):
    from listing import run as listing_run

    return listing_run.run(**kwargs)


def _run_stage(summary: dict[str, Any], name: str, call: Callable[[], Any]) -> None:
    try:
        reserve = 0.0 if name == "listing" else _reserve()
        with budget.stage(stage_share(name), reserve):
            summary[name] = call()
    except Exception as exc:
        log_error(f"tick {name} failed: {exc}")
        summary[name] = {"ok": False, "error": str(exc)}


def _is_deferred(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    return item.get("reason") == "budget" or item.get("skipped") == "budget" or bool(item.get("deferred"))


def deferred_counts(summary: dict[str, Any]) -> dict[str, int]:
    """Per stage: 1 for a whole-stage budget skip, else the results/entries deferred for budget."""
    out: dict[str, int] = {}
    for name in STAGES:
        stage = summary.get(name)
        if not isinstance(stage, dict):
            continue
        if stage.get("skipped") == "budget":
            out[name] = 1
            continue
        count = sum(1 for item in stage.get("results") or [] if _is_deferred(item))
        extra = stage.get("deferred")
        if isinstance(extra, list):
            count += len(extra)
        if count:
            out[name] = count
    return out


def run_tick(
    *,
    http_get: Callable[[str], Any] | None = None,
    score_job=None,
    resolve_job=None,
    resolve_general_job=None,
    schedule_factory: Callable[..., Any] | None = None,
    listing_job=None,
    preflight: Callable[[], dict] | None = None,
    lease: Callable[[], dict] | None = None,
    budget_seconds: int | None = None,
) -> dict:
    summary: dict[str, Any] = {}
    if lease is not None:
        try:
            held = lease()
        except Exception as exc:
            # Fail open: a broken guard must not stop resolution; the send-race handling still applies.
            log_error(f"tick lease check failed: {exc}; running anyway")
            held = {"acquired": True, "error": str(exc)}
        if not isinstance(held, dict):
            held = {"acquired": True, "error": "lease returned no summary"}
        summary["lease"] = held
        if not held.get("acquired", True):
            log_error(f"tick skipped: older execution still running {held.get('holder')}")
            summary["skipped"] = "running"
            return summary
    budget.start(budget_seconds)
    try:
        _run_stages(
            summary,
            http_get=http_get,
            score_job=score_job,
            resolve_job=resolve_job,
            resolve_general_job=resolve_general_job,
            schedule_factory=schedule_factory,
            listing_job=listing_job,
            preflight=preflight,
        )
    finally:
        budget.clear()
    return summary


def _run_stages(
    summary: dict[str, Any],
    *,
    http_get,
    score_job,
    resolve_job,
    resolve_general_job,
    schedule_factory,
    listing_job,
    preflight,
) -> None:
    auth_ok = True
    if preflight is not None:
        try:
            result = preflight()
        except Exception as exc:
            result = {"ok": False, "cause": str(exc)}
        if not isinstance(result, dict):
            result = {"ok": False, "cause": "preflight returned no summary"}
        summary["preflight"] = result
        auth_ok = bool(result.get("ok"))
        if not auth_ok:
            log_error(f"tick preflight failed: {result.get('cause')}; skipping scores, schedule, listing")
    kwargs = {"http_get": http_get} if http_get is not None else {}
    runners: dict[str, Callable[[], Any]] = {
        "scores": lambda: (score_job or _default_scores)(**kwargs),
        "resolve": lambda: (resolve_job or _default_resolve)(**kwargs),
        "resolve_general": lambda: (resolve_general_job or _default_resolve_general)(**kwargs),
        "schedule": lambda: (schedule_factory or _default_schedule)().run(),
        "listing": lambda: (listing_job or _default_listing)(**kwargs),
    }
    for name in STAGES:
        if name in AUTH_STAGES and not auth_ok:
            summary[name] = {"ok": False, "skipped": "preflight"}
            continue
        if _stage_starved(name):
            log_error(f"tick {name} skipped: tick budget spent")
            if name in CRITICAL_STAGES:
                summary[name] = {"ok": False, "skipped": "budget"}
            else:
                summary[name] = {"ok": True, "skipped": "budget", "deferred": True}
            continue
        _run_stage(summary, name, runners[name])
    deferred = deferred_counts(summary)
    if deferred:
        summary["deferred"] = deferred


def _stage_starved(name: str) -> bool:
    """No time left for this stage: the tick deadline passed, or (before listing) only the reserve is left."""
    if budget.total_exhausted():
        return True
    left = budget.total_remaining()
    if left is None or name == "listing":
        return False
    try:
        reserve = _reserve()
    except RuntimeError:
        return False  # _run_stage reports the bad env as this stage's error
    return left <= reserve


def exit_code(summary: dict) -> int:
    preflight = summary.get("preflight")
    if isinstance(preflight, dict) and preflight.get("ok") is False:
        return 1
    for name in STAGES:
        stage = summary.get(name)
        if isinstance(stage, dict) and stage.get("ok") is False:
            return 1
    return 0


def main() -> int:
    import operator_auth
    import tick_lease

    summary = run_tick(preflight=operator_auth.auth_preflight, lease=tick_lease.cloud_run_lease)
    summary["exitCode"] = exit_code(summary)
    print(redact(json.dumps(summary, default=str, sort_keys=True)))
    return summary["exitCode"]


if __name__ == "__main__":
    raise SystemExit(main())
