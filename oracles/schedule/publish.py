"""Operator JWT POST of the NFL schedule."""

from __future__ import annotations

import os

import httpx

from scores.publish import mint_operator_jwt


def publish_schedule(games: list[dict], client: httpx.Client | None = None) -> list[dict]:
    api_url = os.getenv("OU_API_URL", "").rstrip("/")
    if not api_url:
        raise RuntimeError("OU_API_URL required to publish schedule")
    token = mint_operator_jwt()
    own = client is None
    http = client or httpx.Client(timeout=30)
    try:
        response = http.post(
            f"{api_url}/api/v1/markets/schedule",
            json=games,
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 401:
            raise RuntimeError("operator User missing or JWT rejected")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []
    finally:
        if own:
            http.close()
