"""Research cooldown shared by the sports and general resolvers.

A research attempt that does not submit is persisted through /oracle/attest
(persist_research). Before researching again, the resolver reads
/oracle/{cid}/status and skips the market while the newest attestation is
younger than OU_RESEARCH_RETRY_SECONDS, so three Cursor runs are not burnt on
the same market every tick and newer markets are not starved under the caps.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable

from redact import log_error
from scores.job import parse_updated_at

DEFAULT_RETRY_SECONDS = 21600


def retry_seconds() -> int:
    raw = os.getenv("OU_RESEARCH_RETRY_SECONDS", str(DEFAULT_RETRY_SECONDS)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("OU_RESEARCH_RETRY_SECONDS must be an integer") from exc
    return max(0, value)


def last_research_at(status: Any) -> float | None:
    """Newest attestation createdAt as unix seconds; rows without createdAt (legacy) are ignored."""
    if not isinstance(status, dict):
        return None
    rows = status.get("attestations")
    if not isinstance(rows, list):
        return None
    now = datetime.now(timezone.utc)
    newest: float | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = parse_updated_at(row.get("createdAt"), now)
        if ts is None:
            continue
        stamp = ts.timestamp()
        newest = stamp if newest is None else max(newest, stamp)
    return newest


def retry_at(getter: Callable[[str], Any], base: str, condition_id: str, clock: float, window: int) -> int | None:
    """Unix time research may run again, or None when the market is not cooling down.

    A failed status read never blocks research (fail open on quota, not on safety).
    """
    if window <= 0:
        return None
    try:
        status = getter(f"{base}/api/v1/oracle/{condition_id}/status")
    except Exception as exc:
        log_error(f"research cooldown status failed {condition_id}: {exc}")
        return None
    last = last_research_at(status)
    if last is None or clock >= last + window:
        return None
    return int(last + window)


def record_research(persister, condition_id: str, reports: list[dict], reason: str) -> str | None:
    """Persist a non-submitting research attempt; returns an error string instead of raising."""
    if not reports:
        return None
    try:
        persist = getattr(persister, "persist_research", None)
        if persist is not None:
            persist(condition_id, reports, reason)
        else:
            persister.persist_attestations(condition_id, reports)
    except Exception as exc:
        log_error(f"research persist failed {condition_id}: {exc}")
        return str(exc)
    return None
