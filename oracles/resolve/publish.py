"""Persist attestations and mirror Market.resolved after on-chain submit."""

from __future__ import annotations

import httpx

from scores.publish import mint_operator_jwt


def _api_url() -> str:
    import os

    url = os.getenv("OU_API_URL", "").rstrip("/")
    if not url:
        raise RuntimeError("OU_API_URL required to persist resolution")
    return url


def persist_attestations(condition_id: str, reports: list[dict], client: httpx.Client | None = None) -> None:
    own = client is None
    http = client or httpx.Client(timeout=30)
    try:
        for report in reports:
            body = {
                "conditionId": condition_id,
                "agent": report.get("agent") or "",
                "outcome": report.get("outcome"),
                "evidenceHash": report.get("evidenceHash") or "",
                "summary": report.get("summary") or "",
            }
            response = http.post(f"{_api_url()}/api/v1/oracle/attest", json=body)
            response.raise_for_status()
    finally:
        if own:
            http.close()


def mark_resolved(condition_id: str, outcome: int, client: httpx.Client | None = None) -> dict:
    token = mint_operator_jwt()
    own = client is None
    http = client or httpx.Client(timeout=30)
    try:
        response = http.post(
            f"{_api_url()}/api/v1/oracle/resolved",
            json={"conditionId": condition_id, "outcome": outcome},
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 401:
            raise RuntimeError("operator User missing or JWT rejected")
        response.raise_for_status()
        return response.json()
    finally:
        if own:
            http.close()
