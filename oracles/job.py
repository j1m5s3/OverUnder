"""Four-stage oracle tick: scores, resolve, schedule, listing."""

from __future__ import annotations

import sys
from typing import Any, Callable

from listing import run as listing_run
from resolve import run as resolve_run
from schedule.scout import ScheduleCoordinator
from scores import job as scores_job


def run_tick(
    *,
    http_get: Callable[[str], Any] | None = None,
    score_job=None,
    resolve_job=None,
    schedule_factory: Callable[..., ScheduleCoordinator] | None = None,
    listing_job=None,
) -> dict:
    summary: dict[str, Any] = {}
    try:
        runner = score_job or scores_job.run_job
        kwargs = {"http_get": http_get} if http_get is not None else {}
        summary["scores"] = runner(**kwargs)
    except Exception as exc:
        print(f"tick scores failed: {exc}", file=sys.stderr)
        summary["scores"] = {"ok": False, "error": str(exc)}
    try:
        runner = resolve_job or resolve_run.run
        kwargs = {"http_get": http_get} if http_get is not None else {}
        summary["resolve"] = runner(**kwargs)
    except Exception as exc:
        print(f"tick resolve failed: {exc}", file=sys.stderr)
        summary["resolve"] = {"ok": False, "error": str(exc)}
    try:
        factory = schedule_factory or ScheduleCoordinator
        summary["schedule"] = factory().run()
    except Exception as exc:
        print(f"tick schedule failed: {exc}", file=sys.stderr)
        summary["schedule"] = {"ok": False, "error": str(exc)}
    try:
        runner = listing_job or listing_run.run
        kwargs = {"http_get": http_get} if http_get is not None else {}
        summary["listing"] = runner(**kwargs)
    except Exception as exc:
        print(f"tick listing failed: {exc}", file=sys.stderr)
        summary["listing"] = {"ok": False, "error": str(exc)}
    return summary


def main() -> int:
    summary = run_tick()
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
