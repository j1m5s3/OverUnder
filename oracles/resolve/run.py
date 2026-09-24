"""Dual-gate sports auto-resolve plus the ADR-0002 24h fallback. Coordinator.run stays research-only.

Research gets the kickoff (closeTime) so agents resolve on that game, not an earlier
meeting of the same teams. Candidates come from the operator market view
(resolve/markets.py), so paused registered markets still resolve. Per market:
- research that runs and does not resolve is recorded (research cooldown);
- research that raises records a failure marker (shorter cooldown) and does not use
  a cap slot, so one failing market cannot starve newer ones;
- a failed send is not recorded, so the next tick retries it;
- a send the tick budget cannot cover is deferred (reason "budget") before broadcast.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Callable

import budget
from consensus.coordinator import Coordinator
from resolve import chain as chain_mod
from resolve import cooldown
from resolve import fallback as fallback_mod
from resolve import markets as markets_mod
from redact import log_error
from resolve import publish as publish_mod
from resolve.winner import score_outcome, yes_team
from scores.job import is_sports_primary, _api_url, _http_get_json

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


def env_chain_id() -> int:
    try:
        return int((os.getenv("CHAIN_ID") or "0").strip())
    except ValueError:
        return 0


def close_order(market: dict) -> tuple[int, str]:
    """Oldest closeTime first; ties by conditionId so ticks are deterministic."""
    return int(market.get("closeTime") or 0), market.get("conditionId") or ""


def iso_utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def deferred_send(entry: dict) -> dict:
    """Result for a send the tick budget could not cover: nothing broadcast, nothing recorded."""
    return {**entry, "ok": True, "submitted": False, "reason": "budget", "deferred": "send"}


def research_failed(entry: dict, persister, cid: str, exc: Exception) -> dict:
    """Result for a research run that raised; records the failure marker (error cooldown)."""
    log_error(f"research failed {cid}: {exc}")
    fragment = {**entry, "ok": False, "submitted": False, "reason": cooldown.ERROR_REASON, "error": str(exc)}
    error = cooldown.record_failure(persister, cid, exc)
    return {**fragment, "persistError": error} if error else fragment


def is_dual_gate_primary(card: dict) -> bool:
    """Sports primary whose question names a YES team, so a final score derives the outcome.

    resolve/general.py handles every visible market this returns False for.
    """
    primary = card.get("primary") if isinstance(card.get("primary"), dict) else card
    return is_sports_primary(primary) and yes_team(primary.get("question") or "") is not None


def dual_gate_primaries(cards: list[dict]) -> list[dict]:
    out = []
    for card in cards:
        if not is_dual_gate_primary(card):
            continue
        out.append(card.get("primary") if isinstance(card.get("primary"), dict) else card)
    return sorted(out, key=close_order)


def mirror_chain_resolved(condition_id: str, db_resolved: bool, chain_api, persister) -> dict:
    """Copy the CTF payout of a chain-resolved market into the DB. No score needed."""
    if db_resolved:
        return {"conditionId": condition_id, "ok": True, "submitted": False, "reason": "already resolved"}
    outcome = chain_api.onchain_outcome(condition_id)
    if outcome is None:
        return {"conditionId": condition_id, "ok": False, "submitted": False, "reason": "chain resolved, payout unreadable"}
    persister.mark_resolved(condition_id, outcome)
    return {
        "conditionId": condition_id,
        "ok": True,
        "submitted": False,
        "reason": "already resolved",
        "mirrored": True,
        "outcome": outcome,
    }


def resolved_concurrently(condition_id: str, chain_api, persister) -> dict | None:
    """Mirror result when another sender resolved the market while we researched or sent."""
    try:
        if not chain_api.is_resolved(condition_id):
            return None
    except Exception as exc:
        log_error(f"resolve recheck failed {condition_id}: {exc}")
        return None
    fragment = mirror_chain_resolved(condition_id, False, chain_api, persister)
    if fragment.get("ok"):
        fragment["reason"] = "resolved concurrently"
    return fragment


def supporter_reports(reports: list[dict], fragment: dict) -> list[dict]:
    """Fallback resolution: persist only agents whose on-chain attestation matches the outcome."""
    names = fragment.get("supporters")
    if names is None:
        return reports
    outcome = fragment.get("outcome")
    return [r for r in reports if r.get("agent") in names and r.get("outcome") == outcome]


def persist_resolution(persister, condition_id: str, reports: list[dict], outcome: int, extra: dict | None = None) -> dict:
    result = {"conditionId": condition_id, "ok": True, "submitted": True, "outcome": outcome, **(extra or {})}
    try:
        persister.persist_attestations(condition_id, reports)
        persister.mark_resolved(condition_id, outcome)
    except Exception as persist_exc:
        log_error(f"resolve persist failed {condition_id}: {persist_exc}")
        result["persistError"] = str(persist_exc)
    return result


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
    retry_window = cooldown.retry_seconds()
    error_window = cooldown.error_retry_seconds()
    min_seconds = budget.research_min_seconds()
    cards, view = markets_mod.market_cards(base, getter, operator_get)
    primaries = dual_gate_primaries(cards)
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
    results = []
    not_registered: list[str] = []
    for primary in primaries:
        cid = primary.get("conditionId") or ""
        question = primary.get("question") or ""
        if not cid:
            continue
        try:
            detail = getter(f"{base}/api/v1/markets/{cid}")
            if not isinstance(detail, dict):
                raise RuntimeError("market detail must be an object")
            score = detail.get("score") if isinstance(detail.get("score"), dict) else {}
            if chain_api.is_resolved(cid):
                results.append(mirror_chain_resolved(cid, bool(detail.get("resolved")), chain_api, persister))
                continue
            onchain_close = int(chain_api.onchain_close_time(cid) or 0)
            if onchain_close == 0:
                # Created on another oracle (or never registered): unresolvable here.
                not_registered.append(cid)
                results.append({"conditionId": cid, "ok": True, "submitted": False, "reason": "not registered"})
                continue
            close_time = max(int(detail.get("closeTime") or 0), onchain_close)
            if clock < close_time and effective_now() < close_time:
                results.append({"conditionId": cid, "ok": True, "submitted": False, "reason": "not closed"})
                continue
            if (score.get("status") or "") != "final":
                results.append({"conditionId": cid, "ok": True, "submitted": False, "reason": "not final"})
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
            if not can_submit:
                results.append({"conditionId": cid, "ok": True, "submitted": False, "reason": "config mismatch"})
                continue
            retry = cooldown.retry_at(getter, base, cid, clock, retry_window, error_window)
            if retry is not None:
                results.append({"conditionId": cid, "ok": True, "submitted": False, "reason": "research cooldown", "retryAt": retry})
                continue
            if budget.exhausted() or not budget.can_start(min_seconds):
                results.append({"conditionId": cid, "ok": True, "submitted": False, "reason": "budget"})
                continue
            # Failed runs do not use cap slots; attempts stop at 2 * cap so errors stay bounded.
            if researched >= cap or researched + failed >= 2 * cap:
                results.append({"conditionId": cid, "ok": True, "submitted": False, "reason": "capped"})
                continue
            coord = factory()
            try:
                research = coord.run(question, kickoff=iso_utc(close_time))
            except Exception as research_exc:
                if budget.exhausted():
                    # Cut off by the budget watchdog: not this market's fault, retry next tick.
                    results.append({"conditionId": cid, "ok": True, "submitted": False, "reason": "budget"})
                    continue
                failed += 1
                results.append(research_failed({"conditionId": cid}, persister, cid, research_exc))
                continue
            researched += 1
            reports = research.get("reports") or []

            def unresolved(fragment: dict, reports=reports, cid=cid) -> dict:
                # Research ran and nothing resolved: persist it so the cooldown applies.
                error = cooldown.record_research(persister, cid, reports, fragment.get("reason") or "unresolved")
                return {**fragment, "persistError": error} if error else fragment

            raced = resolved_concurrently(cid, chain_api, persister)
            if raced is not None:
                results.append(raced)
                continue
            if research.get("unanimous") and research.get("outcome") == derived:
                evidence = _hex_bytes(reports[0]["evidenceHash"])
                deadline, sigs = coord.sign_unanimous(
                    oracle, chain_id, _hex_bytes(cid), evidence, derived, now=int(effective_now())
                )
                try:
                    chain_api.submit_consensus(cid, derived, evidence, deadline, sigs)
                except budget.SendDeferred:
                    results.append(deferred_send({"conditionId": cid}))
                    continue
                except Exception as send_exc:
                    raced = resolved_concurrently(cid, chain_api, persister)
                    if raced is not None:
                        results.append(raced)
                        continue
                    log_error(f"resolve submit failed {cid}: {send_exc}")
                    # Not recorded as research: the next tick retries the send.
                    results.append(
                        {"conditionId": cid, "ok": False, "submitted": False, "txError": True, "error": str(send_exc), "reason": "tx error"}
                    )
                    continue
                results.append(persist_resolution(persister, cid, reports, derived, {"path": "consensus"}))
                continue
            if policy == "manual":
                results.append(unresolved({"conditionId": cid, "ok": True, "submitted": False, "reason": "research mismatch"}))
                continue
            fallback_at = onchain_close + fallback_mod.WINDOW
            if chain_now() < fallback_at:
                results.append(
                    unresolved(
                        {"conditionId": cid, "ok": True, "submitted": False, "reason": "research mismatch", "fallbackAt": fallback_at}
                    )
                )
                continue
            try:
                fragment = fallback_mod.run_fallback(
                    condition_id=cid,
                    derived=derived,
                    research=research,
                    coord=coord,
                    chain_api=chain_api,
                    policy=policy,
                    oracle=oracle,
                    chain_id=chain_id,
                )
            except budget.SendDeferred:
                results.append(deferred_send({"conditionId": cid}))
                continue
            except fallback_mod.SendFailed as send_exc:
                log_error(f"resolve fallback send failed {cid}: {send_exc}")
                results.append(
                    {
                        "conditionId": cid,
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
                results.append(persist_resolution(persister, cid, supporter_reports(reports, fragment), derived, fragment))
                continue
            if fragment.get("reason") == "already resolved":
                results.append(resolved_concurrently(cid, chain_api, persister) or {"conditionId": cid, "ok": True, **fragment})
                continue
            results.append(unresolved({"conditionId": cid, "ok": True, **fragment}))
        except Exception as exc:
            log_error(f"resolve failed {cid}: {exc}")
            results.append({"conditionId": cid, "ok": False, "submitted": False, "error": str(exc)})
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
