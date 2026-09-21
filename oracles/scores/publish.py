"""Mint operator JWT matching backend _issue and POST LiveScore."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from eth_account import Account

from scores.scout import ScoreReport


def mint_operator_jwt() -> str:
    secret = os.getenv("JWT_SECRET", "").strip()
    if not secret:
        raise RuntimeError("JWT_SECRET required to publish scores")
    key = os.getenv("OPERATOR_PRIVATE_KEY", "").strip()
    if not key:
        raise RuntimeError("OPERATOR_PRIVATE_KEY required to publish scores")
    address = Account.from_key(key).address.lower()
    ttl = int(os.getenv("JWT_TTL_SECONDS", str(60 * 60 * 24 * 7)))
    payload = {
        "sub": address,
        "op": True,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=ttl),
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    if isinstance(token, bytes):
        return token.decode("utf-8")
    return token


def publish_score(condition_id: str, report: ScoreReport, client: httpx.Client | None = None) -> dict:
    api_url = os.getenv("OU_API_URL", "").rstrip("/")
    if not api_url:
        raise RuntimeError("OU_API_URL required to publish scores")
    token = mint_operator_jwt()
    url = f"{api_url}/api/v1/markets/{condition_id}/score"
    headers = {"Authorization": f"Bearer {token}"}
    own = client is None
    http = client or httpx.Client(timeout=30)
    try:
        response = http.post(url, json=report.as_api_body(), headers=headers)
        if response.status_code == 401:
            raise RuntimeError("operator User missing or JWT rejected")
        response.raise_for_status()
        return response.json()
    finally:
        if own:
            http.close()
