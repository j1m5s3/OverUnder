"""Research-only resolve for every market the sports dual gate does not own.

Candidates: wildcard children (marketType 1), user-listed markets (marketType 2)
and marketType 0 primaries that are not dual-gate sports. There is no objective
score, so submitConsensus needs 3/3 agreement with every confidence at or above
OU_GENERAL_RESOLVE_MIN_CONFIDENCE. Past closeTime + 24h (attest|arbitrate), a
confident 2/3 majority attests and resolveFallback runs; resolveArbitrated is
never called here.

closeTime is when trading stops, not when the event ends (a wildcard closes at
its parent's kickoff). So nothing is researched before the event is over:
- event-gated markets (wildcard children, and sports-looking type-0 primaries
  without a YES team) wait for the parent's / own score status to be final or
  cancelled, or for the parent to be resolved on chain, then
  OU_GENERAL_RESOLVE_GATED_DELAY_SECONDS after close;
- other markets wait until an explicit resolveAfter, else
  closeTime + OU_GENERAL_RESOLVE_DELAY_SECONDS (default 24h).
Agents may answer outcome 2 (undetermined); any such report blocks both
consensus and the fallback for that tick.

Like resolve/run.py: candidates come from the operator market view (paused
registered markets included), a research run that raises records a failure marker
and does not use a cap slot, a failed send is not recorded (the next tick retries),
and a send the tick budget cannot cover is deferred before broadcast.
"""

from __future__ import annotations

import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable

import budget
from agents.cursor_runtime import MAX_QUESTION_BYTES, sanitize_untrusted
from consensus.coordinator import Coordinator
from consensus.fallback import majority
from redact import log_error
from resolve import chain as chain_mod
from resolve import cooldown
from resolve import fallback as fallback_mod
from resolve import markets as markets_mod
from resolve import publish as publish_mod
from resolve.run import (
    close_order,
    deferred_send,
    env_chain_id,
    is_dual_gate_primary,
    mirror_chain_resolved,
    persist_resolution,
    research_failed,
    resolved_concurrently,
    supporter_reports,
)
from scores.job import DONE_SCORE_STATUSES, _api_url, _http_get_json, is_sports_primary

DEFAULT_MAX_MARKETS = 2
# Markets with no score feed: a fixed 1h after trading close can precede the event.
DEFAULT_DELAY_SECONDS = 86400
# Event-gated markets also need a final score (or a resolved parent) before research.
DEFAULT_GATED_DELAY_SECONDS = 3600
DEFAULT_MIN_CONFIDENCE = 0.8
MAX_CRITERIA_CHARS = 2000


def _int_env(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    return max(minimum, value)


def max_markets() -> int:
    return _int_env("OU_GENERAL_RESOLVE_MAX_MARKETS", DEFAULT_MAX_MARKETS, 1)


def delay_seconds() -> int:
    return _int_env("OU_GENERAL_RESOLVE_DELAY_SECONDS", DEFAULT_DELAY_SECONDS, 0)


def gated_delay_seconds() -> int:
    return _int_env("OU_GENERAL_RESOLVE_GATED_DELAY_SECONDS", DEFAULT_GATED_DELAY_SECONDS, 0)


def min_confidence() -> float:
    raw = os.getenv("OU_GENERAL_RESOLVE_MIN_CONFIDENCE", str(DEFAULT_MIN_CONFIDENCE)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError("OU_GENERAL_RESOLVE_MIN_CONFIDENCE must be a number") from exc
    if not 0.0 <= value <= 1.0:
        raise RuntimeError("OU_GENERAL_RESOLVE_MIN_CONFIDENCE must be between 0 and 1")
    return value


def candidates(cards: list[dict]) -> list[dict]:
    """[{"market", "parentQuestion", "parentConditionId"}] for non-dual-gate markets, oldest closeTime first."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(market: dict, parent_question: str | None, parent_cid: str | None) -> None:
        cid = market.get("conditionId") or ""
        if not cid or cid in seen:
            return
        seen.add(cid)
        out.append({"market": market, "parentQuestion": parent_question, "parentConditionId": parent_cid})

    for card in cards:
        if not isinstance(card, dict):
            continue
        primary = card.get("primary") if isinstance(card.get("primary"), dict) else card
        if not is_dual_gate_primary(primary):
            add(primary, None, None)
        for child in card.get("children") or []:
            if isinstance(child, dict):
                add(child, primary.get("question") or None, primary.get("conditionId") or None)
    return sorted(out, key=lambda item: close_order(item["market"]))


def gate_subject(market: dict, parent_cid: str | None = None) -> tuple[str, str] | None:
    """(conditionId whose score must be final, "parent" | "event"), or None when ungated.

    Wildcard children gate on their parent game; a sports-looking type-0 primary
    that the dual gate does not own (no YES team, e.g. "over 45.5 points?") gates
    on its own score.
    """
    parent = market.get("parentConditionId") or parent_cid or ""
    if int(market.get("marketType") or 0) == 1 or market.get("parentConditionId"):
        return parent, "parent"
    if is_sports_primary(market) and not is_dual_gate_primary(market):
        return market.get("conditionId") or "", "event"
    return None


def _event_status(getter, base: str, chain_api, subject: str, kind: str) -> tuple[bool, str | None, dict | None]:
    """(done, score status, subject detail). Done means final/cancelled score or a chain-resolved parent."""
    if not subject:
        return False, None, None
    if kind == "parent":
        try:
            if chain_api.is_resolved(subject):
                return True, "resolved", None
        except Exception as exc:
            log_error(f"general parent resolve check failed {subject}: {exc}")
    try:
        detail = getter(f"{base}/api/v1/markets/{subject}")
    except Exception as exc:
        log_error(f"general gate lookup failed {subject}: {exc}")
        return False, None, None
    if not isinstance(detail, dict):
        return False, None, None
    score = detail.get("score") if isinstance(detail.get("score"), dict) else None
    status = (score or {}).get("status")
    return status in DONE_SCORE_STATUSES, status, detail


def _resolve_after(market: dict) -> int:
    try:
        return int(market.get("resolveAfter") or 0)
    except (TypeError, ValueError):
        return 0


def _hex_bytes(value: str) -> bytes:
    hexed = value[2:] if value.startswith("0x") else value
    return bytes.fromhex(hexed)


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def research_input(market: dict, parent_question: str | None, close_time: int) -> dict:
    """Coordinator.run kwargs: creator text goes to question/context (untrusted, sanitized);
    the job's close-time guidance goes to as_of (trusted, rendered outside the fences)."""
    question = sanitize_untrusted(market.get("question"), MAX_QUESTION_BYTES, limit_bytes=True)
    lines = []
    parent = sanitize_untrusted(parent_question, MAX_QUESTION_BYTES, limit_bytes=True)
    if parent:
        lines.append(f"Parent market: {parent}")
    criteria = sanitize_untrusted(market.get("resolutionCriteria"), MAX_CRITERIA_CHARS, keep_newlines=True)
    if criteria:
        lines.append(f"Resolution criteria: {criteria}")
    return {"question": question, "context": "\n".join(lines) or None, "as_of": _iso(close_time)}


def _parent_question(getter, base: str, market: dict, known: str | None, detail: dict | None = None) -> str | None:
    if known:
        return known
    if isinstance(detail, dict) and detail.get("question"):
        return detail["question"]
    parent = market.get("parentConditionId") or ""
    if not parent:
        return None
    try:
        detail = getter(f"{base}/api/v1/markets/{parent}")
    except Exception as exc:
        log_error(f"general parent lookup failed {parent}: {exc}")
        return None
    return (detail.get("question") or None) if isinstance(detail, dict) else None


def _confidences(reports: list[dict]) -> list[float]:
    """Out-of-range or non-numeric confidences count as 0 so nothing bypasses the floor."""
    out = []
    for report in reports:
        raw = report.get("confidence")
        try:
            value = 0.0 if isinstance(raw, bool) else float(raw or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        out.append(value if math.isfinite(value) and 0.0 <= value <= 1.0 else 0.0)
    return out


def run(
    *,
    http_get: Callable[[str], Any] | None = None,
    coordinator_factory: Callable[..., Coordinator] | None = None,
    chain=None,
    publisher=None,
    now: float | None = None,
    fallback_policy: str | None = None,
    operator_get: Callable[[str], Any] | None = None,
) -> dict:
    """`operator_get` reads the operator market view; it defaults to the JWT GET only when
    `http_get` is not injected (tests pass it explicitly)."""
    policy = fallback_mod.parse_policy(fallback_policy) if fallback_policy else fallback_mod.fallback_policy()
    base = _api_url()
    getter = http_get or _http_get_json
    if operator_get is None and http_get is None:
        operator_get = markets_mod.operator_get_json
    clock = now if now is not None else time.time()
    cap = max_markets()
    delay = delay_seconds()
    gated_delay = gated_delay_seconds()
    floor = min_confidence()
    retry_window = cooldown.retry_seconds()
    error_window = cooldown.error_retry_seconds()
    min_seconds = budget.research_min_seconds()
    cards, view = markets_mod.market_cards(base, getter, operator_get)
    items = candidates(cards)
    chain_api = chain or chain_mod
    persister = publisher or publish_mod
    factory = coordinator_factory or Coordinator
    config = chain_api.config_check()
    can_submit = bool(config.get("ok"))
    oracle = (os.getenv("ORACLE_ADDRESS") or "").strip()
    chain_id = env_chain_id()
    chain_clock: list[int] = []

    def chain_now() -> int:
        if not chain_clock:
            chain_clock.append(int(chain_api.chain_now()))
        return chain_clock[0]

    def effective_now() -> float:
        # The contract gates on block.timestamp: never treat a market as open (or sign an
        # already-expired deadline) because the wall clock lags the chain.
        return clock if now is not None else max(clock, chain_now())

    researched = 0
    failed = 0
    results: list[dict] = []
    not_registered: list[str] = []
    for item in items:
        market = item["market"]
        cid = market["conditionId"]
        entry = {"conditionId": cid, "marketType": int(market.get("marketType") or 0)}
        try:
            if chain_api.is_resolved(cid):
                results.append({**entry, **mirror_chain_resolved(cid, bool(market.get("resolved")), chain_api, persister)})
                continue
            onchain_close = int(chain_api.onchain_close_time(cid) or 0)
            if onchain_close == 0:
                not_registered.append(cid)
                results.append({**entry, "ok": True, "submitted": False, "reason": "not registered"})
                continue
            close_time = max(int(market.get("closeTime") or 0), onchain_close)
            gate = gate_subject(market, item.get("parentConditionId"))
            explicit = _resolve_after(market)
            if explicit:
                resolve_at = max(close_time, explicit)
            else:
                resolve_at = close_time + (gated_delay if gate else delay)
            current = clock if clock >= resolve_at else effective_now()
            if current < close_time:
                results.append({**entry, "ok": True, "submitted": False, "reason": "not closed"})
                continue
            if current < resolve_at:
                results.append({**entry, "ok": True, "submitted": False, "reason": "resolve delay", "resolveAt": resolve_at})
                continue
            gate_detail = None
            if gate:
                subject, kind = gate
                done, status, gate_detail = _event_status(getter, base, chain_api, subject, kind)
                if not done:
                    reason = "parent not final" if kind == "parent" else "event not final"
                    results.append({**entry, "ok": True, "submitted": False, "reason": reason, "gateStatus": status})
                    continue
            if not can_submit:
                results.append({**entry, "ok": True, "submitted": False, "reason": "config mismatch"})
                continue
            retry = cooldown.retry_at(getter, base, cid, clock, retry_window, error_window)
            if retry is not None:
                results.append({**entry, "ok": True, "submitted": False, "reason": "research cooldown", "retryAt": retry})
                continue
            if budget.exhausted() or not budget.can_start(min_seconds):
                results.append({**entry, "ok": True, "submitted": False, "reason": "budget"})
                continue
            # Failed runs do not use cap slots; attempts stop at 2 * cap so errors stay bounded.
            if researched >= cap or researched + failed >= 2 * cap:
                results.append({**entry, "ok": True, "submitted": False, "reason": "capped"})
                continue
            known_parent = item["parentQuestion"]
            parent_detail = gate_detail if gate and gate[1] == "parent" else None
            parent_question = _parent_question(getter, base, market, known_parent, parent_detail)
            coord = factory()
            try:
                research = coord.run(**research_input(market, parent_question, close_time))
            except Exception as research_exc:
                if budget.exhausted():
                    # Cut off by the budget watchdog: not this market's fault, retry next tick.
                    results.append({**entry, "ok": True, "submitted": False, "reason": "budget"})
                    continue
                failed += 1
                results.append(research_failed(entry, persister, cid, research_exc))
                continue
            researched += 1
            reports = research.get("reports") or []
            confidences = _confidences(reports)
            entry["confidence"] = confidences

            def unresolved(fragment: dict, reports=reports, cid=cid) -> dict:
                # Research ran and nothing resolved: persist it so the research cooldown applies.
                error = cooldown.record_research(persister, cid, reports, fragment.get("reason") or "unresolved")
                return {**fragment, "persistError": error} if error else fragment

            raced = resolved_concurrently(cid, chain_api, persister)
            if raced is not None:
                results.append({**entry, **raced})
                continue
            if not reports or any(r.get("outcome") not in (0, 1) for r in reports):
                # An agent could not verify a final result: neither consensus nor fallback.
                results.append(unresolved({**entry, "ok": True, "submitted": False, "reason": "undetermined"}))
                continue
            confident = all(c >= floor for c in confidences)
            outcome = research.get("outcome")
            if research.get("unanimous") and confident and outcome in (0, 1):
                evidence = _hex_bytes(reports[0]["evidenceHash"])
                deadline, sigs = coord.sign_unanimous(
                    oracle, chain_id, _hex_bytes(cid), evidence, outcome, now=int(effective_now())
                )
                try:
                    chain_api.submit_consensus(cid, outcome, evidence, deadline, sigs)
                except budget.SendDeferred:
                    results.append(deferred_send(entry))
                    continue
                except Exception as send_exc:
                    raced = resolved_concurrently(cid, chain_api, persister)
                    if raced is not None:
                        results.append({**entry, **raced})
                        continue
                    log_error(f"general submit failed {cid}: {send_exc}")
                    # Not recorded as research: the next tick retries the send.
                    results.append(
                        {**entry, "ok": False, "submitted": False, "txError": True, "error": str(send_exc), "reason": "tx error"}
                    )
                    continue
                results.append({**entry, **persist_resolution(persister, cid, reports, outcome, {"path": "consensus"})})
                continue
            reason = "low confidence" if research.get("unanimous") else "research split"
            if policy == "manual":
                results.append(unresolved({**entry, "ok": True, "submitted": False, "reason": reason}))
                continue
            fallback_at = onchain_close + fallback_mod.WINDOW
            if chain_now() < fallback_at:
                results.append(unresolved({**entry, "ok": True, "submitted": False, "reason": reason, "fallbackAt": fallback_at}))
                continue
            backed = [r for r, c in zip(reports, confidences) if c >= floor]
            agreed = majority([int(r.get("outcome")) for r in backed if r.get("outcome") in (0, 1)])
            if agreed is None:
                results.append(unresolved({**entry, "ok": True, "submitted": False, "reason": "no confident majority"}))
                continue
            try:
                fragment = fallback_mod.run_fallback(
                    condition_id=cid,
                    derived=agreed,
                    research={"reports": [r for r in backed if r.get("outcome") == agreed]},
                    coord=coord,
                    chain_api=chain_api,
                    policy=policy,
                    oracle=oracle,
                    chain_id=chain_id,
                    allow_arbitrate=False,
                    basis="research",
                )
            except budget.SendDeferred:
                results.append(deferred_send(entry))
                continue
            except fallback_mod.SendFailed as send_exc:
                log_error(f"general fallback send failed {cid}: {send_exc}")
                results.append(
                    {
                        **entry,
                        "ok": False,
                        "submitted": False,
                        "txError": True,
                        "error": str(send_exc),
                        "attested": send_exc.attested,
                        "reason": "tx error",
                    }
                )
                continue
            if fragment.get("submitted"):
                persisted = supporter_reports(reports, fragment)
                results.append({**entry, **persist_resolution(persister, cid, persisted, agreed, fragment)})
                continue
            if fragment.get("reason") == "already resolved":
                results.append({**entry, **(resolved_concurrently(cid, chain_api, persister) or {"ok": True, **fragment})})
                continue
            results.append(unresolved({**entry, "ok": True, **fragment}))
        except Exception as exc:
            log_error(f"general resolve failed {cid}: {exc}")
            results.append({**entry, "ok": False, "submitted": False, "error": str(exc)})
    return {
        "ok": can_submit and not any(r.get("txError") for r in results),
        "attempted": researched + failed,
        "researchErrors": failed,
        "marketsView": view,
        "policy": policy,
        "config": config,
        "notRegistered": not_registered,
        "results": results,
    }
