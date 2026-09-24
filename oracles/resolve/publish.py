"""Persist attestations and mirror Market.resolved after on-chain submit.

Both /oracle/attest and /oracle/resolved are operator-only: every call sends the
operator bearer JWT minted from JWT_SECRET + OPERATOR_PRIVATE_KEY.
"""

from __future__ import annotations

import json
import os

import httpx

from scores.publish import mint_operator_jwt


def _api_url() -> str:
    url = os.getenv("OU_API_URL", "").rstrip("/")
    if not url:
        raise RuntimeError("OU_API_URL required to persist resolution")
    return url


def _post(http: httpx.Client, path: str, body: dict, token: str) -> httpx.Response:
    response = http.post(f"{_api_url()}{path}", json=body, headers={"Authorization": f"Bearer {token}"})
    if response.status_code == 401:
        raise RuntimeError("operator User missing or JWT rejected")
    if response.status_code == 403:
        raise RuntimeError("operator only: API rejected the job's operator JWT")
    response.raise_for_status()
    return response


def _attest_body(condition_id: str, report: dict, evidence_json: str | None) -> dict:
    body = {
        "conditionId": condition_id,
        "agent": report.get("agent") or "",
        "outcome": report.get("outcome"),
        "evidenceHash": report.get("evidenceHash") or "",
        "summary": report.get("summary") or "",
    }
    if evidence_json is not None:
        body["evidenceJson"] = evidence_json
    return body


def persist_attestations(
    condition_id: str,
    reports: list[dict],
    client: httpx.Client | None = None,
    *,
    evidence_json: str | None = None,
) -> None:
    if not reports:
        return
    token = mint_operator_jwt()
    own = client is None
    http = client or httpx.Client(timeout=30)
    try:
        for report in reports:
            _post(http, "/api/v1/oracle/attest", _attest_body(condition_id, report, evidence_json), token)
    finally:
        if own:
            http.close()


def persist_research(condition_id: str, reports: list[dict], reason: str, client: httpx.Client | None = None) -> None:
    """Record a research attempt that did not resolve; its createdAt drives the research cooldown."""
    marker = json.dumps([{"kind": "research", "reason": reason}])
    persist_attestations(condition_id, reports, client, evidence_json=marker)


def mark_resolved(condition_id: str, outcome: int, client: httpx.Client | None = None) -> dict:
    token = mint_operator_jwt()
    own = client is None
    http = client or httpx.Client(timeout=30)
    try:
        response = _post(http, "/api/v1/oracle/resolved", {"conditionId": condition_id, "outcome": outcome}, token)
        return response.json()
    finally:
        if own:
            http.close()
