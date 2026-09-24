"""Wall-clock budget for one oracle tick (OU_TICK_BUDGET_SECONDS).

The Cloud Run schedule is */15 and deploy-gcp.yml sets the task timeout to
OU_TICK_BUDGET_SECONDS + 60s (840s at the default 780s budget, capped so the task
ends 60s before the next tick). run_tick sets a deadline; stages and research loops
call exhausted() before starting more work and record the rest as skipped, and a live
Cursor agent run is bounded by remaining() (agents/cursor_runtime.py _run_agent), so
the tick wraps up inside the margin instead of being killed at the task timeout.
"""

from __future__ import annotations

import os
import time

DEFAULT_BUDGET_SECONDS = 780

_deadline: list[float] = []


def budget_seconds() -> int:
    raw = os.getenv("OU_TICK_BUDGET_SECONDS", str(DEFAULT_BUDGET_SECONDS)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("OU_TICK_BUDGET_SECONDS must be an integer") from exc
    if value < 0:
        raise RuntimeError("OU_TICK_BUDGET_SECONDS must be >= 0")
    return value


def start(seconds: int | None = None) -> None:
    """Arm the budget; 0 disables it."""
    total = budget_seconds() if seconds is None else seconds
    _deadline.clear()
    if total > 0:
        _deadline.append(time.monotonic() + total)


def clear() -> None:
    _deadline.clear()


def exhausted() -> bool:
    return bool(_deadline) and time.monotonic() >= _deadline[0]


def remaining() -> float | None:
    """Seconds left before the deadline (0.0 once past it), or None when the budget is off."""
    if not _deadline:
        return None
    return max(0.0, _deadline[0] - time.monotonic())
