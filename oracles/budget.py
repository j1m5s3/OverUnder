"""Wall-clock budget for one oracle tick (OU_TICK_BUDGET_SECONDS).

The Cloud Run schedule is */15 and deploy-gcp.yml sets the task timeout to
OU_TICK_BUDGET_SECONDS + 60s (840s at the default 780s budget, capped so the task
ends 60s before the next tick). run_tick sets a deadline; stages and research loops
call exhausted() / can_start() before starting more work and record the rest as
deferred, and a live Cursor agent run is bounded by remaining()
(agents/cursor_runtime.py _run_agent).

job.py also runs each stage under stage(): a stage deadline that is the earlier of
its share of the total budget and the tick deadline minus a reserve kept for later
stages. exhausted() and remaining() honour that stage deadline, so one agent-heavy
stage (scores) cannot starve resolve and listing. Transaction sends use
total_remaining() instead: they bound each receipt wait by the tick deadline minus
SEND_MARGIN_SECONDS and raise SendDeferred (before broadcasting) when that leaves
less than MIN_RECEIPT_SECONDS, so the tick wraps up inside the 60s margin.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Iterator

DEFAULT_BUDGET_SECONDS = 780
DEFAULT_RESEARCH_MIN_SECONDS = 90
# Receipt waits end this long before the tick deadline.
SEND_MARGIN_SECONDS = 10
# A send whose receipt wait would be shorter than this is deferred to the next tick.
MIN_RECEIPT_SECONDS = 20

_deadline: list[float] = []
_total: list[int] = []
_stage: list[float] = []


class SendDeferred(RuntimeError):
    """A transaction was not broadcast because the tick budget cannot cover its receipt wait."""


def budget_seconds() -> int:
    raw = os.getenv("OU_TICK_BUDGET_SECONDS", str(DEFAULT_BUDGET_SECONDS)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("OU_TICK_BUDGET_SECONDS must be an integer") from exc
    if value < 0:
        raise RuntimeError("OU_TICK_BUDGET_SECONDS must be >= 0")
    return value


def research_min_seconds() -> int:
    """OU_RESEARCH_MIN_SECONDS: do not start a 3-agent run with less budget than this left."""
    raw = os.getenv("OU_RESEARCH_MIN_SECONDS", str(DEFAULT_RESEARCH_MIN_SECONDS)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("OU_RESEARCH_MIN_SECONDS must be an integer") from exc
    return max(0, value)


def start(seconds: int | None = None) -> None:
    """Arm the budget; 0 disables it."""
    total = budget_seconds() if seconds is None else seconds
    clear()
    if total > 0:
        _deadline.append(time.monotonic() + total)
        _total.append(total)


def clear() -> None:
    _deadline.clear()
    _total.clear()
    _stage.clear()


def total_seconds() -> int | None:
    return _total[0] if _total else None


def _effective() -> float | None:
    if not _deadline:
        return None
    if _stage:
        return min(_deadline[0], _stage[0])
    return _deadline[0]


def exhausted() -> bool:
    """True once the stage deadline (or the tick deadline) has passed."""
    deadline = _effective()
    return deadline is not None and time.monotonic() >= deadline


def remaining() -> float | None:
    """Seconds left before the stage (or tick) deadline, 0.0 once past it; None when off."""
    deadline = _effective()
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def total_exhausted() -> bool:
    return bool(_deadline) and time.monotonic() >= _deadline[0]


def total_remaining() -> float | None:
    """Seconds left before the tick deadline, ignoring any stage share; None when off."""
    if not _deadline:
        return None
    return max(0.0, _deadline[0] - time.monotonic())


def can_start(min_seconds: float) -> bool:
    """True when the budget is off or at least `min_seconds` remain in this stage."""
    left = remaining()
    return left is None or (left > 0 and left >= min_seconds)


def receipt_timeout(cap: float) -> float:
    """Receipt wait for a send: min(cap, tick time left - margin). Raises SendDeferred when too short."""
    left = total_remaining()
    if left is None:
        return cap
    wait = min(cap, left - SEND_MARGIN_SECONDS)
    if wait < MIN_RECEIPT_SECONDS:
        raise SendDeferred(f"tick budget too short for a send ({left:.0f}s left)")
    return wait


@contextmanager
def stage(share: float = 1.0, reserve: float = 0.0) -> Iterator[None]:
    """Run a stage under min(start + share * total, tick deadline - reserve). No-op when off."""
    if not _deadline:
        yield
        return
    now = time.monotonic()
    cap = min(now + share * _total[0], _deadline[0] - max(0.0, reserve))
    _stage.clear()
    _stage.append(cap)
    try:
        yield
    finally:
        _stage.clear()
