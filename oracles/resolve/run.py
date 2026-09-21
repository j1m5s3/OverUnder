"""Dual-gate sports auto-resolve. Coordinator.run stays research-only."""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Callable

from consensus.coordinator import Coordinator
from resolve import chain as chain_mod
from resolve import publish as publish_mod
from resolve.winner import score_outcome
from scores.job import sports_primaries, _api_url, _http_get_json

DEFAULT_MAX_MARKETS = 3


def max_markets() -> int:
    raw = os.getenv("OU_RESOLVE_MAX_MARKETS", str(DEFAULT_MAX_MARKETS)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("OU_RESOLVE_MAX_MARKETS must be an integer") from exc
    return max(1, value)


def _hex_bytes(value: str) -> bytes:
    hexed = value[2:] if value.startswith("0x") else value
    return bytes.fromhex(hexed)


def run(
    *,
    http_get: Callable[[str], Any] | None = None,
    coordinator_factory: Callable[..., Coordinator] | None = None,
    chain=None,
    publisher=None,
    now: float | None = None,
) -> dict:
    base = _api_url()
    getter = http_get or _http_get_json
    clock = now if now is not None else time.time()
    cap = max_markets()
    cards = getter(f"{base}/api/v1/markets")
    if not isinstance(cards, list):
        raise RuntimeError("markets list must be an array")
    primaries = sports_primaries(cards)
    chain_api = chain or chain_mod
    persist = publisher or publish_mod
    factory = coordinator_factory or Coordinator
    attempted = 0
    results = []
    for primary in primaries:
        if attempted >= cap:
            break
        cid = primary.get("conditionId") or ""
        question = primary.get("question") or ""
        if not cid:
            continue
        attempted += 1
        try:
            detail = getter(f"{base}/api/v1/markets/{cid}")
            if not isinstance(detail, dict):
                raise RuntimeError("market detail must be an object")
            score = detail.get("score") if isinstance(detail.get("score"), dict) else {}
            if (score.get("status") or "") != "final":
                results.append({"conditionId": cid, "ok": True, "submitted": False, "reason": "not final"})
                continue
            if detail.get("resolved") or chain_api.is_resolved(cid):
                results.append({"conditionId": cid, "ok": True, "submitted": False, "reason": "already resolved"})
                continue
            close_time = int(detail.get("closeTime") or 0)
            onchain_close = int(chain_api.onchain_close_time(cid) or 0)
            if onchain_close:
                close_time = max(close_time, onchain_close)
            if clock < close_time:
                results.append({"conditionId": cid, "ok": True, "submitted": False, "reason": "not closed"})
                continue
            derived = score_outcome(
                question,
                score.get("homeLabel") or "",
                score.get("awayLabel") or "",
                score.get("homeScore"),
                score.get("awayScore"),
            )
            if derived is None:
                results.append({"conditionId": cid, "ok": True, "submitted": False, "reason": "no score outcome"})
                continue
            coord = factory()
            research = coord.run(question)
            if not research.get("unanimous") or research.get("outcome") != derived:
                results.append({"conditionId": cid, "ok": True, "submitted": False, "reason": "research mismatch"})
                continue
            evidence = _hex_bytes(research["reports"][0]["evidenceHash"])
            oracle = (os.getenv("ORACLE_ADDRESS") or "").strip()
            chain_id = int(os.getenv("CHAIN_ID") or "0")
            deadline, sigs = coord.sign_unanimous(oracle, chain_id, _hex_bytes(cid), evidence, derived)
            chain_api.submit_consensus(cid, derived, evidence, deadline, sigs)
            persist.persist_attestations(cid, research.get("reports") or [])
            persist.mark_resolved(cid, derived)
            results.append({"conditionId": cid, "ok": True, "submitted": True, "outcome": derived})
        except Exception as exc:
            print(f"resolve failed {cid}: {exc}", file=sys.stderr)
            results.append({"conditionId": cid, "ok": False, "submitted": False, "error": str(exc)})
    return {"attempted": attempted, "results": results}
