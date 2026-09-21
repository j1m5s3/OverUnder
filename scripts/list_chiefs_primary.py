"""List the Chiefs vs Broncos winner primary and post a final score.

Run only after the API points at NEW contract addresses.
Does not score a different or older condition id.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from eth_account import Account

QUESTION = "Chiefs vs Broncos: Chiefs win?"


def _api_url() -> str:
    return os.getenv("OU_API_URL", "http://127.0.0.1:8000").rstrip("/")


def _seed_usdc() -> int:
    raw = os.getenv("OU_SEED_USDC", "200000000").strip()
    value = int(raw)
    if value <= 0:
        raise RuntimeError("OU_SEED_USDC must be > 0")
    return value


def question_id() -> str:
    return "0x" + hashlib.sha256(b"ou:chiefs-primary").hexdigest()


def mint_operator_jwt() -> str:
    secret = os.getenv("JWT_SECRET", "").strip()
    if not secret:
        raise RuntimeError("JWT_SECRET required")
    key = os.getenv("OPERATOR_PRIVATE_KEY", "").strip()
    if not key:
        raise RuntimeError("OPERATOR_PRIVATE_KEY required")
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


def _cards(items) -> list[dict]:
    if not isinstance(items, list):
        raise RuntimeError("markets list must be an array")
    return items


def existing_condition_id(cards: list[dict]) -> str | None:
    for card in cards:
        primary = card.get("primary") if isinstance(card, dict) else None
        if isinstance(primary, dict) and primary.get("question") == QUESTION:
            cid = primary.get("conditionId") or ""
            return cid if cid else None
        if isinstance(card, dict) and card.get("question") == QUESTION:
            cid = card.get("conditionId") or ""
            return cid if cid else None
    return None


def main() -> None:
    base = _api_url()
    http = httpx.Client(timeout=60)
    try:
        listed = http.get(f"{base}/api/v1/markets")
        listed.raise_for_status()
        cid = existing_condition_id(_cards(listed.json()))
        token = mint_operator_jwt()
        headers = {"Authorization": f"Bearer {token}"}
        if cid is None:
            body = {
                "question": QUESTION,
                "resolution_criteria": "NFL winner; YES if Chiefs win",
                "close_time": int(time.time()) + 3600,
                "question_id": question_id(),
                "seed_usdc": _seed_usdc(),
                "market_type": 0,
            }
            created = http.post(f"{base}/api/v1/markets", json=body, headers=headers)
            if created.status_code == 401:
                raise RuntimeError("operator User missing or JWT rejected")
            created.raise_for_status()
            row = created.json()
            cid = row.get("conditionId") if isinstance(row, dict) else None
            if not cid:
                raise RuntimeError("create did not return conditionId")
            print(f"created {QUESTION} {cid}")
        else:
            print(f"exists {QUESTION} {cid}")
        score = http.post(
            f"{base}/api/v1/markets/{cid}/score",
            json={
                "homeLabel": "Chiefs",
                "awayLabel": "Broncos",
                "homeScore": 31,
                "awayScore": 10,
                "status": "final",
            },
            headers=headers,
        )
        if score.status_code == 401:
            raise RuntimeError("operator User missing or JWT rejected")
        score.raise_for_status()
        print(f"scored {cid} Chiefs 31 Broncos 10 final")
    finally:
        http.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
