"""Oracle tick: single-flight lease, auth preflight, then scores, resolve, resolve_general, schedule, listing.

A tick that finds an older execution still running exits 0 with {"skipped": "running"}.
OU_TICK_BUDGET_SECONDS bounds one tick: once spent, remaining stages and markets are
recorded as skipped (ok) and the next tick picks them up.
"""

from __future__ import annotations

import importlib
import json
from typing import Any, Callable

import budget
from redact import SECRET_ENV, log_error, redact  # noqa: F401  (SECRET_ENV re-exported for callers)

STAGES = ("scores", "resolve", "resolve_general", "schedule", "listing")
# Stages that only write through the operator JWT; skipped when the preflight fails.
AUTH_STAGES = frozenset({"scores", "schedule", "listing"})


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
        summary[name] = call()
    except Exception as exc:
        log_error(f"tick {name} failed: {exc}")
        summary[name] = {"ok": False, "error": str(exc)}


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
        if budget.exhausted():
            summary[name] = {"ok": True, "skipped": "budget"}
            continue
        _run_stage(summary, name, runners[name])


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
