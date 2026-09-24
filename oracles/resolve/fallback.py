"""ADR-0002 24h fallback behind OU_FALLBACK_POLICY=attest|manual|arbitrate.

attest (default): agents whose research matches the outcome basis (the 3/3 final
score for sports, the confident research majority for general markets) each
submitAttestation; then the permissionless resolveFallback when its preflight
passes. arbitrate adds operator resolveArbitrated when there is no on-chain agent
majority or votes force arbitration. manual never sends.
"""

from __future__ import annotations

import os

from consensus.fallback import combine, majority

POLICIES = ("manual", "attest", "arbitrate")
DEFAULT_POLICY = "attest"
WINDOW = 86400
ATTEST_DEADLINE_SECONDS = 3600
ARBITRABLE = frozenset({"no agent majority", "arbitration required"})


class SendFailed(RuntimeError):
    """A fallback transaction raised; carries the attestations already sent."""

    def __init__(self, message: str, attested: list[str]):
        super().__init__(message)
        self.attested = attested


def parse_policy(value: str | None) -> str:
    policy = (value or DEFAULT_POLICY).strip().lower()
    if policy not in POLICIES:
        raise RuntimeError("OU_FALLBACK_POLICY must be manual|attest|arbitrate")
    return policy


def fallback_policy() -> str:
    return parse_policy(os.getenv("OU_FALLBACK_POLICY"))


def _hex_bytes(value: str) -> bytes:
    hexed = value[2:] if value.startswith("0x") else value
    return bytes.fromhex(hexed)


def run_fallback(
    *,
    condition_id: str,
    derived: int,
    research: dict,
    coord,
    chain_api,
    policy: str,
    oracle: str,
    chain_id: int,
    allow_arbitrate: bool = True,
    basis: str = "score",
) -> dict:
    """Attest `derived` for matching agents, then resolveFallback (or arbitrate).

    Never attests an outcome other than `derived`. Raises SendFailed when a send raises,
    except for send races with a concurrent tick: a 'dup attestation' revert whose slot is
    now submitted is skipped, and a resolving send that fails because the market is already
    resolved returns reason "already resolved". `supporters` lists the agents whose on-chain
    attestation matches `derived` (only their reports are persisted).
    """
    reports = research.get("reports") or []
    matching = [r for r in reports if r.get("outcome") == derived]
    base = {"submitted": False, "attested": [], "outcome": derived, "matching": len(matching)}
    if parse_policy(policy) == "manual":
        return {**base, "reason": "research mismatch"}
    st = chain_api.fallback_state(condition_id)
    close = int(st["closeTime"])
    if st["resolved"]:
        return {**base, "reason": "already resolved"}
    if close == 0:
        return {**base, "reason": "not registered"}
    fallback_at = close + int(st["window"])
    if int(st["now"]) < fallback_at:
        return {**base, "reason": "research mismatch", "fallbackAt": fallback_at}

    slots = st["agents"]
    prior = majority([int(s["outcome"]) for s in slots.values() if s["submitted"]])
    if prior is not None and prior != derived:
        # Already decided on chain against `derived`; more attestations only burn gas.
        return {**base, "reason": f"agent majority conflicts {basis}"}
    cid_bytes = _hex_bytes(condition_id)
    deadline = int(st["now"]) + ATTEST_DEADLINE_SECONDS
    attested: list[str] = []
    supporters: list[str] = []
    notes: list[str] = []
    for report in matching:
        name = report.get("agent") or ""
        try:
            addr = coord.agent_address(name).lower()
        except Exception as exc:
            notes.append(f"{name}: {exc}")
            continue
        slot = slots.get(addr)
        if slot is None:
            notes.append(f"{name}: not an oracle agent")
            continue
        if slot["submitted"]:
            if int(slot["outcome"]) == derived:
                supporters.append(name)
            continue
        evidence = _hex_bytes(report["evidenceHash"])
        sig = coord.sign_one(name, oracle, chain_id, cid_bytes, evidence, derived, deadline)
        try:
            chain_api.submit_attestation(condition_id, derived, evidence, deadline, sig)
        except Exception as exc:
            # A concurrent tick may have landed the same attestation first.
            fresh = _raced_slot(chain_api, condition_id, addr) if "dup attestation" in str(exc) else None
            if fresh is None:
                raise SendFailed(f"submitAttestation: {exc}", list(attested)) from exc
            slots[addr] = fresh
            notes.append(f"{name}: attested concurrently")
            if int(fresh["outcome"]) == derived:
                supporters.append(name)
            continue
        slot["submitted"] = True
        slot["outcome"] = derived
        attested.append(name)
        supporters.append(name)
    base = {**base, "attested": attested, "supporters": supporters}
    if notes:
        base["notes"] = notes

    agent_out = majority([int(s["outcome"]) for s in slots.values() if s["submitted"]])
    if agent_out is not None and agent_out != derived:
        return {**base, "reason": f"agent majority conflicts {basis}"}
    yes_w, no_w = st["votes"]
    if agent_out is None:
        blocker = "no agent majority"
    elif combine(agent_out, int(yes_w), int(no_w)) == "arbitrate":
        blocker = "arbitration required"
    else:
        ok, why = chain_api.preflight_fallback(condition_id)
        if ok:
            if not _resolve_send("resolveFallback", attested, chain_api, condition_id, chain_api.resolve_fallback, condition_id):
                return {**base, "reason": "already resolved"}
            return {**base, "submitted": True, "path": "fallback", "reason": "fallback resolved"}
        blocker = why
    if allow_arbitrate and policy == "arbitrate" and blocker in ARBITRABLE:
        if not _resolve_send(
            "resolveArbitrated", attested, chain_api, condition_id, chain_api.resolve_arbitrated, condition_id, derived
        ):
            return {**base, "reason": "already resolved"}
        return {**base, "submitted": True, "path": "arbitrated", "blocker": blocker, "reason": "arbitrated"}
    return {**base, "reason": blocker}


def _raced_slot(chain_api, condition_id: str, addr: str) -> dict | None:
    """The agent slot re-read after a 'dup attestation' revert, when it is now submitted."""
    try:
        fresh = chain_api.fallback_state(condition_id)["agents"].get(addr)
    except Exception:
        return None
    return fresh if fresh and fresh.get("submitted") else None


def _resolve_send(label: str, attested: list[str], chain_api, condition_id: str, call, *args) -> bool:
    """Send a resolving tx; False when it failed because another sender resolved first."""
    try:
        call(*args)
    except Exception as exc:
        try:
            raced = bool(chain_api.is_resolved(condition_id))
        except Exception:
            raced = False
        if raced:
            return False
        raise SendFailed(f"{label}: {exc}", list(attested)) from exc
    return True
