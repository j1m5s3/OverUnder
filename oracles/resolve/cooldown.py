"""Research cooldown shared by the sports and general resolvers.

A research attempt that ran but did not resolve (mismatch, split, low
confidence, undetermined) is persisted through /oracle/attest (persist_research).
A research run that raised (agent error, quota, bad JSON) is persisted as one
marker row from ERROR_AGENT (record_failure). Before researching again, the
resolver reads /oracle/{cid}/status and skips the market while the newest row is
younger than OU_RESEARCH_RETRY_SECONDS (default 6h), or
OU_RESEARCH_ERROR_RETRY_SECONDS (default 1h) when that row is a failure marker.
So three Cursor runs are not burnt on the same market every tick and newer
markets are not starved under the caps. A failed transaction send is not
recorded: the next tick researches and sends again.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable

from redact import log_error, redact
from scores.job import parse_updated_at

DEFAULT_RETRY_SECONDS = 21600
DEFAULT_ERROR_RETRY_SECONDS = 3600
# Agent name of the failure marker row (AttestIn.agent allows 42 chars).
ERROR_AGENT = "oracle-job"
ERROR_REASON = "research error"
ZERO_HASH = "0x" + "00" * 32


def retry_seconds() -> int:
    raw = os.getenv("OU_RESEARCH_RETRY_SECONDS", str(DEFAULT_RETRY_SECONDS)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("OU_RESEARCH_RETRY_SECONDS must be an integer") from exc
    return max(0, value)


def error_retry_seconds() -> int:
    raw = os.getenv("OU_RESEARCH_ERROR_RETRY_SECONDS", str(DEFAULT_ERROR_RETRY_SECONDS)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("OU_RESEARCH_ERROR_RETRY_SECONDS must be an integer") from exc
    return max(0, value)


def last_research(status: Any) -> tuple[float, bool] | None:
    """(newest attestation createdAt as unix seconds, is it a failure marker); legacy rows without createdAt are ignored."""
    if not isinstance(status, dict):
        return None
    rows = status.get("attestations")
    if not isinstance(rows, list):
        return None
    now = datetime.now(timezone.utc)
    newest: tuple[float, bool] | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = parse_updated_at(row.get("createdAt"), now)
        if ts is None:
            continue
        stamp = ts.timestamp()
        is_error = str(row.get("agent") or "").lower() == ERROR_AGENT
        if newest is None or stamp >= newest[0]:
            newest = (stamp, is_error)
    return newest


def last_research_at(status: Any) -> float | None:
    """Newest attestation createdAt as unix seconds; rows without createdAt (legacy) are ignored."""
    newest = last_research(status)
    return None if newest is None else newest[0]


def retry_at(
    getter: Callable[[str], Any],
    base: str,
    condition_id: str,
    clock: float,
    window: int,
    error_window: int | None = None,
) -> int | None:
    """Unix time research may run again, or None when the market is not cooling down.

    `error_window` applies when the newest row is a failure marker (defaults to `window`).
    A failed status read never blocks research (fail open on quota, not on safety).
    """
    if window <= 0 and not error_window:
        return None
    try:
        status = getter(f"{base}/api/v1/oracle/{condition_id}/status")
    except Exception as exc:
        log_error(f"research cooldown status failed {condition_id}: {exc}")
        return None
    newest = last_research(status)
    if newest is None:
        return None
    last, is_error = newest
    span = error_window if is_error and error_window is not None else window
    if span <= 0 or clock >= last + span:
        return None
    return int(last + span)


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


def failure_report(error: object) -> dict:
    """The single marker row recorded for a research run that raised (outcome 2, zero evidence)."""
    detail = " ".join(redact(error).split())
    return {
        "agent": ERROR_AGENT,
        "outcome": 2,
        "confidence": 0.0,
        "evidenceHash": ZERO_HASH,
        "summary": f"{ERROR_REASON}: {detail}"[:280],
    }


def record_failure(persister, condition_id: str, error: object) -> str | None:
    """Persist a research-failure marker so OU_RESEARCH_ERROR_RETRY_SECONDS applies; returns an error string."""
    return record_research(persister, condition_id, [failure_report(error)], ERROR_REASON)
