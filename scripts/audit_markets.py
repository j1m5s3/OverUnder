"""Read-only market audit: API state, NFL schedule coverage and ConsensusOracle.

For every listed market it answers "is this on a resolution path, and if not,
why": the sports dual gate (winner primaries with a posted score) or the
general resolver (every other visible market: wildcards, user-listed and
non-sports primaries; mirrors oracles/resolve/general.py candidates and
timing). Ungated markets resolve after close + OU_GENERAL_RESOLVE_DELAY_SECONDS
(default 24h); event-gated ones (wildcard children, sports-looking primaries
without a YES team) after close + OU_GENERAL_RESOLVE_GATED_DELAY_SECONDS
(default 1h) once the parent's / own score is final or cancelled, or the parent
resolved. --no-general audits a job without the resolve_general stage; those
markets are then manual (no path). It also reports week-listing coverage for
the schedule the listing job uses.

Nothing writes: only HTTP GETs against the API plus JSON-RPC eth_chainId and
eth_call. The RPC URL is printed as scheme://host only, since provider URLs
often embed keys. Paused markets are hidden by GET /api/v1/markets, so they
show up only when a schedule row points at them (orphans).

Exit codes: 0 no findings, 1 findings, 2 usage error or API/RPC unreachable.

  python scripts/audit_markets.py --api URL [--rpc URL --oracle ADDR] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, TextIO

USER_AGENT = "overunder-audit/1"
ZERO_ADDR = "0x" + "0" * 40
DEFAULT_TIMEOUT = 20.0
DEFAULT_GRACE_HOURS = 6.0
# oracles/resolve/general.py DEFAULT_DELAY_SECONDS / DEFAULT_GATED_DELAY_SECONDS.
DEFAULT_GENERAL_DELAY = 86400
DEFAULT_GENERAL_GATED_DELAY = 3600
# oracles/scores/job.py DONE_SCORE_STATUSES: a gate score in one of these lets gated research run.
GATE_DONE_STATUSES = frozenset({"final", "cancelled"})
FALLBACK_WINDOW = 86400  # ConsensusOracle.WINDOW
MIN_LEAD_SECONDS = 600  # listing skips kickoffs this close (factory rejects past closes)
# oracles/listing/run.py DEFAULT_STALE_GRACE_SECONDS: a game still scheduled or
# in progress this long after kickoff no longer blocks the week roll.
DEFAULT_LISTING_STALE_GRACE = 8 * 3600
LISTING_STALE_STATUSES = (None, "scheduled", "in_progress")

# keccak256(signature)[:4]; test_selectors_match_keccak re-derives every entry.
SELECTORS: dict[str, str] = {
    "closeTime(bytes32)": "0xc6836f18",
    "resolved(bytes32)": "0x8920870e",
    "attestationCount(bytes32)": "0xd65ddd52",
    "ctf()": "0x22a9339f",
    "factory()": "0xc45a0155",
    "operator()": "0x570ca735",
    "agents(uint256)": "0x513856c8",
    "isResolved(bytes32)": "0xde61ece1",
    "payoutNumerators(bytes32,uint256)": "0x0504c814",
    "payoutDenominator(bytes32)": "0xdd34de67",
    "oracles(bytes32)": "0xa81a2677",
    "marketExists(bytes32)": "0xd8d03989",
}

# Display order, most urgent first. Every report summary carries all of them.
BUCKETS = (
    "not_registered",
    "mismatch",
    "overdue_final_unresolved",
    "overdue_general_unresolved",
    "overdue_no_final",
    "manual",
    "live",
    "awaiting_general",
    "open",
    "resolved",
)
FINDING_BUCKETS = frozenset(
    {
        "not_registered",
        "mismatch",
        "overdue_final_unresolved",
        "overdue_general_unresolved",
        "overdue_no_final",
    }
)
WARNING_FLAGS = frozenset({"early_final", "unscheduled", "detail_missing", "detail_error"})
DONE_STATUSES = frozenset({"final", "postponed", "cancelled"})
SCORE_STATUSES = ("final", "in_progress", "scheduled", "postponed", "cancelled")

HINTS = {
    "overdue_final_unresolved": (
        "gcloud run jobs execute overunder-oracle --region=us-central1 "
        "--project=overunder-509107 --wait, then re-audit"
    ),
    "overdue_general_unresolved": (
        "general resolver (oracles/resolve/general.py) runs each oracle tick after "
        "close + OU_GENERAL_RESOLVE_DELAY_SECONDS (gated: OU_GENERAL_RESOLVE_GATED_DELAY_SECONDS "
        "and a final gate score); check the resolve_general stage log "
        "(research split, low confidence, capped, Cursor quota), execute the job, then re-audit"
    ),
    "overdue_no_final": (
        "no final score: check the oracle job scores stage (Cursor quota) or post the "
        "score through the operator API, then execute the job"
    ),
    "not_registered": (
        "created on a different ConsensusOracle; cannot resolve on the configured "
        "oracle; hide the orphaned row with the operator endpoint "
        "POST /api/v1/markets/{condition_id}/archive, then re-audit"
    ),
    "manual": (
        "no automated resolver (general resolver off via --no-general); operator "
        "resolveArbitrated after closeTime + 24h"
    ),
    "no_outcome": (
        "final score gives no outcome (tie or team label mismatch); operator "
        "resolveArbitrated after closeTime + 24h"
    ),
    "cancelled": (
        "cancelled game: ConsensusOracle has no invalid outcome, so there is no payout "
        "path; needs an operator decision"
    ),
    "gate_missing": (
        "gated on a parent the API does not return (404 or fetch error), so the general "
        "resolver never passes its gate; check the parent market, then re-audit"
    ),
    "chain_error": "RPC read failed; re-run the audit",
    "early_final": "final score posted before closeTime; check closeTime or the score scout",
    "unscheduled": "no schedule row has this winner question; listing coverage cannot track it",
    "detail_missing": "listed market has no detail (404); re-run the audit",
    "detail_error": "market detail fetch failed; score state unknown",
}

_HEX = re.compile(r"^[0-9a-fA-F]*$")
_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")


class RpcError(RuntimeError):
    """JSON-RPC failure. Messages never contain the RPC URL."""


class FatalError(RuntimeError):
    """Stops the audit with exit code 2."""


# ---------------------------------------------------------------- transport


def fetch_json(url: str, timeout: float) -> Any:
    """GET url and decode JSON. Raises urllib.error.HTTPError / URLError."""
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def rpc_host(url: str) -> str:
    """scheme://host[:port] without userinfo, path or query."""
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if not host:
        return "(invalid rpc url)"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port:
        host = f"{host}:{port}"
    return f"{parsed.scheme or 'http'}://{host}"


def _scrub(text: str, url: str) -> str:
    if not url:
        return text
    parsed = urllib.parse.urlsplit(url)
    out = text.replace(url, rpc_host(url))
    for part in (parsed.path, parsed.query, parsed.username, parsed.password):
        if part and len(part) > 1:
            out = out.replace(part, "...")
    return out


def make_transport(url: str, timeout: float) -> Callable[[str, list], Any]:
    """Generic JSON-RPC POST; returns `result`, raises RpcError (URL scrubbed)."""

    def call(method: str, params: list) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise RpcError(f"{method}: {_scrub(str(exc), url)}") from None
        if not isinstance(payload, dict):
            raise RpcError(f"{method}: malformed response")
        error = payload.get("error")
        if error is not None:
            message = error.get("message") if isinstance(error, dict) else error
            raise RpcError(f"{method}: {_scrub(str(message), url)}")
        if "result" not in payload:
            raise RpcError(f"{method}: response has no result")
        return payload["result"]

    return call


def eth_call_via(transport: Callable[[str, list], Any]) -> Callable[[str, str], str]:
    def call(to: str, data: str) -> str:
        result = transport("eth_call", [{"to": to, "data": data}, "latest"])
        if not isinstance(result, str):
            raise RpcError("eth_call: non-string result")
        return result

    return call


def make_rpc(url: str, timeout: float) -> Callable[[str, str], str]:
    """eth_call(to, data) -> hex result against `latest`."""
    return eth_call_via(make_transport(url, timeout))


def _parse_chain_id(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 16)
    raise RpcError("eth_chainId: non-hex result")


def rpc_chain_id(url: str, timeout: float) -> int:
    return _parse_chain_id(make_transport(url, timeout)("eth_chainId", []))


# ---------------------------------------------------------------- ABI words


def encode_call(selector: str, *args: str | int) -> str:
    """0x + selector + one 32-byte word per arg (hex left-padded, ints big-endian)."""
    sel = selector[2:] if selector.startswith("0x") else selector
    if len(sel) != 8 or not _HEX.match(sel):
        raise ValueError(f"bad selector {selector!r}")
    words = []
    for arg in args:
        if isinstance(arg, bool) or not isinstance(arg, (int, str)):
            raise TypeError(f"unsupported argument {arg!r}")
        if isinstance(arg, int):
            if arg < 0 or arg >= 1 << 256:
                raise ValueError("uint256 out of range")
            words.append(f"{arg:064x}")
            continue
        body = arg[2:] if arg[:2].lower() == "0x" else arg
        if len(body) > 64 or not _HEX.match(body):
            raise ValueError(f"bad hex argument {arg!r}")
        words.append(body.lower().rjust(64, "0"))
    return "0x" + sel.lower() + "".join(words)


def _word(h: str, index: int = 0) -> str:
    body = h[2:] if isinstance(h, str) and h[:2].lower() == "0x" else (h or "")
    if not body:
        raise ValueError("empty eth_call result (no contract at that address?)")
    if not _HEX.match(body) or len(body) < 64 * (index + 1):
        raise ValueError("short or malformed eth_call result")
    return body[64 * index : 64 * (index + 1)]


def decode_uint(h: str) -> int:
    return int(_word(h), 16)


def decode_bool(h: str) -> bool:
    value = decode_uint(h)
    if value > 1:
        raise ValueError(f"not a bool word: {value}")
    return value == 1


def decode_address(h: str) -> str:
    return "0x" + _word(h)[-40:].lower()


# ---------------------------------------------------------------- chain


class ChainReader:
    """Read-only views on ConsensusOracle, ConditionalTokens and MarketFactory."""

    def __init__(
        self,
        rpc_call: Callable[[str, str], str],
        oracle: str,
        ctf: str | None = None,
        factory: str | None = None,
    ) -> None:
        self._call = rpc_call
        self.oracle = oracle.lower()
        self._ctf = ctf.lower() if ctf else None
        self._factory = factory.lower() if factory else None
        self._operator: str | None = None

    def _read(self, to: str, signature: str, *args: str | int) -> str:
        return self._call(to, encode_call(SELECTORS[signature], *args))

    def ctf_address(self) -> str:
        if self._ctf is None:
            self._ctf = decode_address(self._read(self.oracle, "ctf()"))
        return self._ctf

    def factory_address(self) -> str:
        if self._factory is None:
            self._factory = decode_address(self._read(self.oracle, "factory()"))
        return self._factory

    def operator(self) -> str:
        if self._operator is None:
            self._operator = decode_address(self._read(self.oracle, "operator()"))
        return self._operator

    def close_time(self, cid: str) -> int:
        return decode_uint(self._read(self.oracle, "closeTime(bytes32)", cid))

    def oracle_resolved(self, cid: str) -> bool:
        return decode_bool(self._read(self.oracle, "resolved(bytes32)", cid))

    def attestation_count(self, cid: str) -> int:
        return decode_uint(self._read(self.oracle, "attestationCount(bytes32)", cid))

    def ctf_resolved(self, cid: str) -> bool:
        return decode_uint(self._read(self.ctf_address(), "payoutDenominator(bytes32)", cid)) > 0

    def payouts(self, cid: str) -> tuple[int, int]:
        ctf = self.ctf_address()
        yes = decode_uint(self._read(ctf, "payoutNumerators(bytes32,uint256)", cid, 0))
        no = decode_uint(self._read(ctf, "payoutNumerators(bytes32,uint256)", cid, 1))
        return yes, no

    def ctf_oracle(self, cid: str) -> str:
        return decode_address(self._read(self.ctf_address(), "oracles(bytes32)", cid))

    def market_exists(self, cid: str) -> bool | None:
        factory = self.factory_address()
        if factory == ZERO_ADDR:
            return None
        return decode_bool(self._read(factory, "marketExists(bytes32)", cid))

    def snapshot(self, cid: str) -> dict:
        """Per-market chain state. Payouts only when resolved; ctfOracle only when unregistered."""
        snap: dict[str, Any] = {
            "closeTime": None,
            "oracleResolved": None,
            "ctfResolved": None,
            "payouts": None,
            "ctfOracle": None,
            "marketExists": None,
            "attestations": None,
            "error": None,
        }
        try:
            snap["closeTime"] = self.close_time(cid)
            if snap["closeTime"] == 0:
                snap["ctfOracle"] = self.ctf_oracle(cid)
                return snap
            snap["oracleResolved"] = self.oracle_resolved(cid)
            snap["ctfResolved"] = self.ctf_resolved(cid)
            if snap["ctfResolved"]:
                snap["payouts"] = list(self.payouts(cid))
            else:
                snap["attestations"] = self.attestation_count(cid)
            snap["marketExists"] = self.market_exists(cid)
        except Exception as exc:  # keep auditing the other markets
            snap["error"] = str(exc) or type(exc).__name__
        return snap


# ---------------------------------------------------------------- market rules


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def flatten_cards(cards: Any) -> list[dict]:
    """EventCards (or bare MarketPublic items) -> markets deduped by conditionId, with `role`."""
    if not isinstance(cards, list):
        raise ValueError("markets list must be an array")
    out: list[dict] = []
    seen: set[str] = set()

    def add(market: Any, role: str, card_parent: str = "") -> None:
        if not isinstance(market, dict):
            return
        cid = str(market.get("conditionId") or "")
        if not cid or cid.lower() in seen:
            return
        seen.add(cid.lower())
        row = dict(market)
        row["role"] = role
        if card_parent and not row.get("parentConditionId"):
            # general.py gate_subject falls back to the card's primary for the parent.
            row["parentConditionId"] = card_parent
        out.append(row)

    for card in cards:
        if not isinstance(card, dict):
            continue
        if isinstance(card.get("primary"), dict):
            primary = card["primary"]
            add(primary, "orphan_child" if _int(primary.get("marketType")) == 1 else "primary")
            children = card.get("children")
            for child in children if isinstance(children, list) else []:
                add(child, "child", str(primary.get("conditionId") or ""))
        elif _int(card.get("marketType")) == 1:
            add(card, "child" if card.get("parentConditionId") else "orphan_child")
        else:
            add(card, "primary")
    return out


def is_sports_primary(m: dict) -> bool:
    """Mirrors oracles/scores/job.py is_sports_primary."""
    if _int(m.get("marketType")) != 0:
        return False
    question = str(m.get("question") or "").lower()
    return " vs " in question or " vs. " in question


# yes_team / _same / score_outcome are copied from oracles/resolve/winner.py so the
# script stays standalone; test_score_outcome_parity keeps them in step.
_YES = re.compile(r":\s*(.+?)\s+win\??\s*$", re.IGNORECASE)


def yes_team(question: str) -> str | None:
    match = _YES.search((question or "").strip())
    if not match:
        return None
    name = match.group(1).strip()
    return name or None


def _same(left: str, right: str) -> bool:
    a = (left or "").casefold().strip()
    b = (right or "").casefold().strip()
    if not a or not b:
        return False
    return a == b or a in b or b in a


def score_outcome(
    question: str,
    home_label: str,
    away_label: str,
    home_score: int | None,
    away_score: int | None,
) -> int | None:
    yes = yes_team(question)
    if yes is None or home_score is None or away_score is None:
        return None
    if home_score == away_score:
        return None
    if _same(yes, home_label):
        yes_score, no_score = home_score, away_score
    elif _same(yes, away_label):
        yes_score, no_score = away_score, home_score
    else:
        return None
    if yes_score > no_score:
        return 0
    if yes_score < no_score:
        return 1
    return None


def winner_question(home: str, away: str) -> str:
    """Mirrors oracles/listing/questions.py winner_question."""
    return f"{home} vs {away}: {home} win?"


def is_automatable(m: dict) -> bool:
    """Dual-gate primary; mirrors oracles/resolve/run.py is_dual_gate_primary."""
    return is_sports_primary(m) and yes_team(str(m.get("question") or "")) is not None


def market_kind(m: dict, general_enabled: bool = True) -> str:
    """sports (dual gate), general (general resolver) or manual (no resolution path).

    oracles/resolve/general.py takes every visible market the dual gate does not
    own, with or without resolution criteria, so manual only appears when the
    general stage is off.
    """
    if is_automatable(m):
        return "sports"
    return "general" if general_enabled else "manual"


def gate_subject(m: dict) -> tuple[str, str] | None:
    """Mirrors oracles/resolve/general.py gate_subject for a general market.

    (conditionId whose score must be final, "parent" | "event"), or None when ungated.
    flatten_cards already filled parentConditionId from the card for children.
    """
    if _int(m.get("marketType")) == 1 or m.get("parentConditionId"):
        return str(m.get("parentConditionId") or ""), "parent"
    if is_sports_primary(m) and not is_automatable(m):
        return str(m.get("conditionId") or ""), "event"
    return None


def general_resolve_at(m: dict, effective_close: int, delay_s: int, gated_delay_s: int) -> int:
    """Earliest general research time (oracles/resolve/general.py run): an explicit
    resolveAfter, else close + the gated or ungated delay. Gated markets also need gate_view done."""
    explicit = _int(m.get("resolveAfter"))
    if explicit:
        return max(effective_close, explicit)
    return effective_close + (gated_delay_s if gate_subject(m) else delay_s)


def gate_view(subject: dict | None, detail: dict | None, resolved: bool | None = None) -> dict:
    """What general.py _event_status sees of a gate subject: its score status and whether it
    resolved (the chain read when given, else the DB flag). scoreFeed is False for a parent
    that is not a sports primary: only its resolution can open the gate."""
    score = _score_view(detail)
    if resolved is None:
        resolved = bool(subject and subject.get("resolved"))
    return {
        "found": subject is not None,
        "status": score["status"] if score else None,
        "resolved": bool(resolved),
        "scoreFeed": subject is None or is_sports_primary(subject),
    }


def _score_view(detail: dict | None) -> dict | None:
    if not isinstance(detail, dict) or not isinstance(detail.get("score"), dict):
        return None
    score = detail["score"]
    return {
        "status": score.get("status"),
        "homeLabel": score.get("homeLabel"),
        "awayLabel": score.get("awayLabel"),
        "homeScore": score.get("homeScore"),
        "awayScore": score.get("awayScore"),
        "updatedAt": score.get("updatedAt"),
    }


def _payout_outcome(payouts: Any) -> int | None:
    if not payouts:
        return None
    pair = [_int(v) for v in payouts]
    if pair == [1, 0]:
        return 0
    if pair == [0, 1]:
        return 1
    return None


def _mismatch_hint(fields: list[str], chain: dict, db_resolved: bool) -> str:
    parts = []
    if "resolved" in fields:
        if chain.get("oracleResolved") and not db_resolved:
            parts.append("resolve job mirror (step 9) will persist")
        else:
            parts.append("DB says resolved but the oracle does not; the resolvers re-run it (repair path)")
    if "ctfResolved" in fields:
        parts.append("oracle and CTF disagree on resolution; inspect the resolve transaction")
    if "payouts" in fields:
        parts.append("DB payouts differ from CTF payoutNumerators; fix the DB mirror")
    if "closeTime" in fields:
        parts.append("DB closeTime differs from ConsensusOracle.closeTime; resolvers use the later one")
    if "marketExists" in fields:
        parts.append("configured MarketFactory does not know this condition (legacy market not imported?)")
    return "; ".join(parts)


def _hint(row: dict, chain: dict | None) -> str | None:
    bucket = row["bucket"]
    flags = row["flags"]
    if bucket == "mismatch":
        return _mismatch_hint(row["mismatchFields"], chain or {}, row["dbResolved"])
    if bucket == "overdue_final_unresolved" and row["derivedOutcome"] is None:
        return HINTS["no_outcome"]
    if bucket == "overdue_no_final" and "cancelled" in flags:
        return HINTS["cancelled"]
    if bucket == "overdue_no_final" and row.get("gate") and not row["gate"]["found"]:
        return HINTS["gate_missing"]
    if bucket in FINDING_BUCKETS:
        return HINTS[bucket]
    if bucket == "manual" and "overdue" in flags:
        return HINTS["manual"]
    if "chain_error" in flags:
        return HINTS["chain_error"]
    for flag in flags:
        if flag in WARNING_FLAGS:
            return HINTS[flag]
    return None


def finalize_row(row: dict, *, ignore_manual: bool = False, strict: bool = False) -> dict:
    """Recompute warnings, finding and hint after flags change."""
    warnings = [flag for flag in row["flags"] if flag in WARNING_FLAGS]
    finding = row["bucket"] in FINDING_BUCKETS or "chain_error" in row["flags"]
    if row["bucket"] == "manual" and "overdue" in row["flags"] and not ignore_manual:
        finding = True
    if strict and warnings:
        finding = True
    row["warnings"] = warnings
    row["finding"] = finding
    row["hint"] = _hint(row, row.get("chain"))
    return row


def classify(
    m: dict,
    detail: dict | None,
    chain: dict | None,
    now: int,
    grace_s: int,
    *,
    general_delay_s: int = DEFAULT_GENERAL_DELAY,
    general_gated_delay_s: int = DEFAULT_GENERAL_GATED_DELAY,
    general_enabled: bool = True,
    gate: dict | None = None,
    ignore_manual: bool = False,
    strict: bool = False,
) -> dict:
    """One market row; the first matching bucket wins (see BUCKETS).

    `gate` is gate_view() of a general market's parent (build_report supplies it); an
    event-gated market is its own subject and uses `detail`.
    """
    kind = market_kind(m, general_enabled)
    question = str(m.get("question") or "")
    db_close = _int(m.get("closeTime"))
    db_resolved = bool(m.get("resolved"))
    db_payouts = [_int(m.get("payoutYes")), _int(m.get("payoutNo"))]
    score = _score_view(detail)
    status = score["status"] if score else None
    flags: list[str] = []
    notes: list[str] = []
    mismatch: list[str] = []

    chain_close = chain.get("closeTime") if chain else None
    if chain and chain.get("error"):
        flags.append("chain_error")
        notes.append(f"chain read failed: {chain['error']}")
    # Resolvers use max(db, chain) when the chain has a close (oracles/resolve/run.py).
    effective = max(db_close, chain_close) if chain_close else db_close
    age = now - effective
    derived = None
    if score:
        derived = score_outcome(
            question,
            score["homeLabel"] or "",
            score["awayLabel"] or "",
            score["homeScore"],
            score["awayScore"],
        )

    bucket: str | None = None
    if chain is not None and chain_close == 0:
        bucket = "not_registered"
        ctf_oracle = chain.get("ctfOracle")
        if ctf_oracle == ZERO_ADDR:
            notes.append("CTF has no oracle for this condition: prepared on another deployment")
        elif ctf_oracle:
            notes.append(f"CTF oracle is {ctf_oracle}, not the configured ConsensusOracle")
    if bucket is None and chain is not None:
        oracle_res = chain.get("oracleResolved")
        ctf_res = chain.get("ctfResolved")
        payouts = chain.get("payouts")
        if oracle_res is not None and oracle_res != db_resolved:
            mismatch.append("resolved")
        if oracle_res is not None and ctf_res is not None and oracle_res != ctf_res:
            mismatch.append("ctfResolved")
        if db_resolved and ctf_res and payouts is not None and [_int(v) for v in payouts] != db_payouts:
            mismatch.append("payouts")
        if chain_close is not None and chain_close != db_close:
            mismatch.append("closeTime")
        if chain.get("marketExists") is False:
            mismatch.append("marketExists")
        if mismatch:
            bucket = "mismatch"
            notes.append("mismatch: " + ", ".join(mismatch))
    if bucket is None and db_resolved:
        bucket = "resolved"
    if bucket is None and kind == "manual":
        bucket = "manual"
        if age >= 0:
            flags.append("overdue")
            notes.append("past close with no automated resolver")
    if bucket is None and kind == "general" and not str(m.get("resolutionCriteria") or "").strip():
        notes.append("no resolution criteria: research uses the question alone")
    if bucket is None and now < effective:
        bucket = "open"
        if kind == "sports" and status == "final":
            flags.append("early_final")
            notes.append("final score posted before close")
    gate_row = None
    subject = gate_subject(m) if kind == "general" else None
    if subject is not None:
        view = gate_view(m, detail, False) if subject[1] == "event" else (gate or gate_view(None, None))
        done = view["resolved"] or view["status"] in GATE_DONE_STATUSES
        gate_row = {
            "kind": subject[1], "subject": subject[0], "status": view["status"], "done": done, "found": view["found"]
        }
    if bucket is None and kind == "general":
        resolve_at = general_resolve_at(m, effective, general_delay_s, general_gated_delay_s)
        label = gate_row["kind"] if gate_row else ""
        if now < resolve_at:
            bucket = "awaiting_general"
            suffix = f" once the {label} score is final" if gate_row and not gate_row["done"] else ""
            notes.append(f"general resolver eligible in {(resolve_at - now) / 60:.0f} min{suffix}")
        elif gate_row and not gate_row["done"]:
            # general.py skips it ("parent/event not final"); judge the gate like a sports game.
            gate_status = gate_row["status"]
            waiting = f"general resolver waits for a final {label} score ({gate_status or 'none posted'})"
            if not view["scoreFeed"]:
                bucket = "awaiting_general"
                notes.append("general resolver waits for the parent to resolve (no parent score feed)")
            elif age <= grace_s and gate_status in (None, "scheduled", "in_progress"):
                bucket = "awaiting_general"
                notes.append(waiting)
            else:
                bucket = "overdue_no_final"
                notes.append(waiting if view["found"] else "parent market not found: the gate cannot pass")
        else:
            bucket = "overdue_general_unresolved"
    if bucket is None:
        if age <= grace_s and status in (None, "scheduled", "in_progress"):
            bucket = "live"
        elif status == "final":
            bucket = "overdue_final_unresolved"
            if derived is None:
                notes.append("no score outcome -> manual")
        else:
            bucket = "overdue_no_final"
            if status == "postponed":
                notes.append("postponed: waits for the rescheduled final")
            elif status is None:
                notes.append("no score posted")
            elif status != "cancelled":
                notes.append(f"score still {status} past grace")
    if bucket in ("overdue_final_unresolved", "overdue_general_unresolved") and age >= FALLBACK_WINDOW:
        flags.append("fallback_window")
        attested = chain.get("attestations") if chain else None
        suffix = f"; attestations {attested}/3" if attested is not None else ""
        notes.append(f"past closeTime + 24h fallback window (OU_FALLBACK_POLICY){suffix}")
    if status == "cancelled" and not db_resolved:
        flags.append("cancelled")
        notes.append("cancelled: no payout path (ConsensusOracle has no invalid outcome)")

    chain_outcome = _payout_outcome(chain.get("payouts")) if chain else None
    row = {
        "conditionId": str(m.get("conditionId") or ""),
        "parentConditionId": m.get("parentConditionId") or None,
        "role": m.get("role") or "primary",
        "question": question,
        "marketType": _int(m.get("marketType")),
        "kind": kind,
        "bucket": bucket,
        "finding": False,
        "flags": flags,
        "warnings": [],
        "notes": notes,
        "dbClose": db_close,
        "chainClose": chain_close,
        "effectiveClose": effective,
        "ageSeconds": age,
        "dbResolved": db_resolved,
        "payoutYes": db_payouts[0],
        "payoutNo": db_payouts[1],
        "score": score,
        "derivedOutcome": derived,
        "chainOutcome": chain_outcome,
        "chain": chain,
        "mismatchFields": mismatch,
        "gate": gate_row,
        "hint": None,
    }
    return finalize_row(row, ignore_manual=ignore_manual, strict=strict)


# ---------------------------------------------------------------- schedule


def week_complete_legacy(games: list[dict], live_status: dict[str, str | None]) -> bool:
    """The all-final gate of the original oracles/listing/run.py week_complete."""
    if not games:
        return False
    for game in games:
        question = winner_question(str(game.get("home") or ""), str(game.get("away") or ""))
        if question in live_status:
            if live_status[question] != "final":
                return False
        elif game.get("status") != "final":
            return False
    return True


def link_games(schedule: list[dict], sports: list[dict]) -> list[dict]:
    """Mirror listing.run: listedConditionId, else a winner primary with the same
    question and closeTime == kickoff (legacy rows, or a failed schedule upsert)."""
    by_key: dict[tuple[str, int], str] = {}
    for m in sports:
        question = str(m.get("question") or "")
        cid = str(m.get("conditionId") or "")
        close = _int(m.get("closeTime"))
        if question and cid and close:
            by_key.setdefault((question, close), cid)
    out = []
    for game in schedule:
        cid = game.get("listedConditionId")
        if not cid:
            question = winner_question(str(game.get("home") or ""), str(game.get("away") or ""))
            cid = by_key.get((question, _int(game.get("kickoff_unix"))))
        out.append({**game, "listedConditionId": cid or None})
    return out


def game_status(game: dict, live_status: dict[str, str | None]) -> str | None:
    """Live score status of the row's own linked market (live_status keyed by
    lowercased conditionId), else the schedule row status (listing.run rule)."""
    cid = str(game.get("listedConditionId") or "").lower()
    live = live_status.get(cid) if cid else None
    return live if live is not None else game.get("status")


def listing_stale(game: dict, status: str | None, now: int | None, grace_s: int) -> bool:
    """listing.run _stale: still scheduled/in progress grace_s after kickoff."""
    return now is not None and status in LISTING_STALE_STATUSES and _int(game.get("kickoff_unix")) + grace_s < now


def week_complete_new(
    games: list[dict],
    live_status: dict[str, str | None],
    now: int | None = None,
    grace_s: int = DEFAULT_LISTING_STALE_GRACE,
) -> bool:
    """Listing gate: every game final, postponed or cancelled, or stale past grace_s
    (no stale rule when now is None)."""
    if not games:
        return False
    for game in games:
        status = game_status(game, live_status)
        if status not in DONE_STATUSES and not listing_stale(game, status, now, grace_s):
            return False
    return True


def schedule_coverage(
    schedule: list,
    markets: list[dict],
    details: dict[str, dict | None],
    now: int,
    *,
    grace_s: int = int(DEFAULT_GRACE_HOURS * 3600),
    listing_grace_s: int = DEFAULT_LISTING_STALE_GRACE,
    detail_errors: dict[str, str] | None = None,
) -> dict:
    """Per-week counts, both listing gates, next-week listability, orphans, unscheduled primaries.

    Status counts use the listing job's effective status (the live score status
    of the row's own linked market when present, else the schedule row's).
    `completeNew` mirrors listing.run week_complete, including its stale rule
    (`listing_grace_s`). `details` maps lowercased condition ids to the detail
    object, or None for a 404.
    """
    if not isinstance(schedule, list):
        raise ValueError("schedule must be an array")
    errors = detail_errors or {}
    by_cid = {str(m.get("conditionId") or "").lower() for m in markets}
    sports = [m for m in markets if is_sports_primary(m)]
    listed_questions = {str(m.get("question") or "") for m in sports}
    # The legacy gate keyed live scores by winner question.
    legacy_status: dict[str, str | None] = {}
    for m in sports:
        question = str(m.get("question") or "")
        cid = str(m.get("conditionId") or "").lower()
        if not question or not cid:
            continue
        score = _score_view(details.get(cid))
        legacy_status[question] = score["status"] if score else None

    games_ok = [g for g in schedule if isinstance(g, dict)]
    linked = link_games(games_ok, sports)
    live_status: dict[str, str | None] = {}
    for game in linked:
        cid = str(game.get("listedConditionId") or "").lower()
        if cid and cid not in live_status:
            score = _score_view(details.get(cid))
            live_status[cid] = score["status"] if score else None
    grouped: dict[tuple[int, int], list[dict]] = {}
    for game in linked:
        try:
            key = (int(game["season"]), int(game["week"]))
        except (KeyError, TypeError, ValueError):
            continue
        grouped.setdefault(key, []).append(game)

    weeks = []
    for (season, week), games in sorted(grouped.items()):
        counts = {status: 0 for status in SCORE_STATUSES}
        stale = []
        for game in games:
            status = game_status(game, live_status)
            if status in counts:
                counts[status] += 1
            if status in ("scheduled", "in_progress") and _int(game.get("kickoff_unix")) + grace_s < now:
                stale.append(game.get("id"))
        listed = [str(g.get("listedConditionId") or "") for g in games if g.get("listedConditionId")]
        nxt = grouped.get((season, week + 1)) or []
        listable = missed = inactive = 0
        for game in nxt:
            question = winner_question(str(game.get("home") or ""), str(game.get("away") or ""))
            if question in listed_questions:
                continue
            if (game.get("status") or "scheduled") != "scheduled":
                inactive += 1
            elif _int(game.get("kickoff_unix")) <= now + MIN_LEAD_SECONDS:
                missed += 1
            else:
                listable += 1
        weeks.append(
            {
                "season": season,
                "week": week,
                "games": len(games),
                **counts,
                "listed": len(listed),
                "listedFound": sum(1 for cid in listed if cid.lower() in by_cid),
                "stale": stale,
                "completeLegacy": week_complete_legacy(games, legacy_status),
                "completeNew": week_complete_new(games, live_status, now, listing_grace_s),
                "hasNextWeek": bool(nxt),
                "nextWeekListable": listable,
                "nextWeekMissed": missed,
                "nextWeekInactive": inactive,
            }
        )

    orphans = []
    for game in games_ok:
        listed_cid = str(game.get("listedConditionId") or "")
        key = listed_cid.lower()
        if not listed_cid or key in by_cid:
            continue
        if key in errors:
            reason = "detail_error"
        elif not isinstance(details.get(key), dict):
            reason = "missing"
        elif details[key].get("paused"):
            reason = "paused"
        else:
            reason = "not_in_list"
        orphans.append(
            {
                "scheduleId": game.get("id"),
                "season": game.get("season"),
                "week": game.get("week"),
                "game": f"{game.get('away')} @ {game.get('home')}",
                "listedConditionId": listed_cid,
                "reason": reason,
            }
        )

    scheduled_questions = {
        winner_question(str(g.get("home") or ""), str(g.get("away") or "")) for g in games_ok
    }
    unscheduled = [
        str(m.get("conditionId"))
        for m in markets
        if is_automatable(m) and not m.get("resolved") and str(m.get("question") or "") not in scheduled_questions
    ]
    return {"weeks": weeks, "orphans": orphans, "unscheduledPrimaries": unscheduled}


# ---------------------------------------------------------------- report


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_report(
    api: str,
    http_get: Callable[[str], Any],
    chain_reader: ChainReader | None,
    now: int,
    grace_s: int,
    ignore_manual: bool,
    strict: bool,
    *,
    general_delay_s: int = DEFAULT_GENERAL_DELAY,
    general_gated_delay_s: int = DEFAULT_GENERAL_GATED_DELAY,
    general_enabled: bool = True,
    chain_meta: dict | None = None,
    listing_grace_s: int = DEFAULT_LISTING_STALE_GRACE,
) -> dict:
    """Fetch, classify and summarize. Raises FatalError when the list or schedule is unusable."""
    base = api.rstrip("/")
    try:
        cards = http_get(f"{base}/api/v1/markets")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise FatalError(f"GET /api/v1/markets failed: {exc}") from exc
    try:
        markets = flatten_cards(cards)
    except ValueError as exc:
        raise FatalError(f"GET /api/v1/markets: {exc}") from exc
    try:
        schedule = http_get(f"{base}/api/v1/markets/schedule")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise FatalError(f"GET /api/v1/markets/schedule failed: {exc}") from exc
    if not isinstance(schedule, list):
        raise FatalError("GET /api/v1/markets/schedule: schedule must be an array")

    chain_info: dict[str, Any] = {
        "enabled": chain_reader is not None,
        "chainId": None,
        "rpcHost": None,
        "oracle": None,
        "ctf": None,
        "factory": None,
        "operator": None,
    }
    chain_info.update(chain_meta or {})
    if chain_reader is not None:
        try:
            chain_info["oracle"] = chain_reader.oracle
            chain_info["ctf"] = chain_reader.ctf_address()
            chain_info["factory"] = chain_reader.factory_address()
            chain_info["operator"] = chain_reader.operator()
        except Exception as exc:
            raise FatalError(f"ConsensusOracle getters failed (check --oracle and the RPC chain): {exc}") from exc
        if chain_info["ctf"] == ZERO_ADDR:
            raise FatalError("ConsensusOracle.ctf() is zero (wrong --oracle?)")

    details: dict[str, dict | None] = {}
    detail_errors: dict[str, str] = {}

    def load_detail(cid: str) -> None:
        key = cid.lower()
        if key in details or key in detail_errors:
            return
        try:
            detail = http_get(f"{base}/api/v1/markets/{cid}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                details[key] = None
            else:
                detail_errors[key] = f"HTTP {exc.code}"
            return
        except (urllib.error.URLError, OSError, ValueError) as exc:
            detail_errors[key] = str(exc)
            return
        if isinstance(detail, dict):
            details[key] = detail
        else:
            detail_errors[key] = "market detail must be an object"

    listed_cids = {str(m.get("conditionId") or "").lower() for m in markets}
    # Only winner primaries carry scores; children never do.
    for m in markets:
        if is_sports_primary(m):
            load_detail(str(m["conditionId"]))
    for game in schedule:
        listed_cid = str(game.get("listedConditionId") or "") if isinstance(game, dict) else ""
        if listed_cid and listed_cid.lower() not in listed_cids:
            load_detail(listed_cid)
    # general.py reads an unlisted gate parent through its detail (orphan wildcards).
    parents: dict[str, str] = {}
    for m in markets:
        subject = gate_subject(m) if market_kind(m, general_enabled) == "general" else None
        if subject and subject[1] == "parent" and subject[0]:
            parents[subject[0].lower()] = subject[0]
            if subject[0].lower() not in listed_cids:
                load_detail(subject[0])

    by_cid = {str(m["conditionId"]).lower(): m for m in markets}
    snaps = {}
    if chain_reader is not None:
        snaps = {key: chain_reader.snapshot(str(m["conditionId"])) for key, m in by_cid.items()}

    def parent_gate(key: str) -> dict:
        resolved = (snaps.get(key) or {}).get("oracleResolved")
        if resolved is None and chain_reader is not None and key not in by_cid:
            try:
                resolved = chain_reader.oracle_resolved(parents[key])
            except Exception:  # unknown on chain: fall back to the DB flag like a failed read
                resolved = None
        return gate_view(by_cid.get(key) or details.get(key), details.get(key), resolved)

    gates = {key: parent_gate(key) for key in parents}
    rows = []
    for m in markets:
        cid = str(m["conditionId"])
        key = cid.lower()
        subject = gate_subject(m)
        row = classify(
            m,
            details.get(key),
            snaps.get(key),
            now,
            grace_s,
            general_delay_s=general_delay_s,
            general_gated_delay_s=general_gated_delay_s,
            general_enabled=general_enabled,
            gate=gates.get(subject[0].lower()) if subject and subject[1] == "parent" else None,
        )
        if is_sports_primary(m):
            if key in detail_errors:
                row["flags"].append("detail_error")
                row["notes"].append(f"detail fetch failed: {detail_errors[key]}")
            elif key in details and details[key] is None:
                row["flags"].append("detail_missing")
        rows.append(row)

    coverage = schedule_coverage(
        schedule, markets, details, now, grace_s=grace_s, listing_grace_s=listing_grace_s, detail_errors=detail_errors
    )
    unscheduled = {cid.lower() for cid in coverage["unscheduledPrimaries"]}
    for row in rows:
        if row["conditionId"].lower() in unscheduled:
            row["flags"].append("unscheduled")
            row["notes"].append("no schedule row with this winner question")
        finalize_row(row, ignore_manual=ignore_manual, strict=strict)
    order = {bucket: i for i, bucket in enumerate(BUCKETS)}
    rows.sort(key=lambda r: (order[r["bucket"]], r["effectiveClose"], r["conditionId"]))

    summary = {bucket: 0 for bucket in BUCKETS}
    finding_counts: dict[str, int] = {}
    warning_counts: dict[str, int] = {}
    for row in rows:
        summary[row["bucket"]] += 1
        for flag in row["warnings"]:
            warning_counts[flag] = warning_counts.get(flag, 0) + 1
        if not row["finding"]:
            continue
        if row["bucket"] in FINDING_BUCKETS:
            reason = row["bucket"]
        elif row["bucket"] == "manual" and "overdue" in row["flags"] and not ignore_manual:
            reason = "manual_overdue"
        elif "chain_error" in row["flags"]:
            reason = "chain_error"
        else:
            reason = "strict_warning"
        finding_counts[reason] = finding_counts.get(reason, 0) + 1
    stale_total = sum(len(week["stale"]) for week in coverage["weeks"])
    missed_total = sum(week["nextWeekMissed"] for week in coverage["weeks"])
    if stale_total:
        warning_counts["stale_schedule_status"] = stale_total
    if missed_total:
        warning_counts["missed_listing"] = missed_total
    if coverage["orphans"]:
        finding_counts["schedule_orphan"] = len(coverage["orphans"])
    schedule_warnings = stale_total + missed_total
    if strict and schedule_warnings:
        finding_counts["schedule_warning"] = schedule_warnings
    findings = sum(finding_counts.values())
    return {
        "generatedAt": _iso(now),
        "now": now,
        "api": base,
        "chain": chain_info,
        "settings": {
            "graceSeconds": grace_s,
            "generalDelaySeconds": general_delay_s,
            "generalGatedDelaySeconds": general_gated_delay_s,
            "listingStaleGraceSeconds": listing_grace_s,
            "generalResolver": general_enabled,
            "ignoreManual": ignore_manual,
            "strict": strict,
        },
        "summary": summary,
        "findings": findings,
        "warnings": sum(warning_counts.values()),
        "findingCounts": finding_counts,
        "warningCounts": warning_counts,
        "markets": rows,
        "schedule": coverage,
        "exitCode": 1 if findings else 0,
    }


# ---------------------------------------------------------------- text


def _short(cid: str) -> str:
    return cid[:10] + ".." if len(cid) > 12 else cid


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 2] + ".."


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(cells: list[str]) -> str:
        padded = [cell.ljust(widths[i]) for i, cell in enumerate(cells[:-1])]
        return "  ".join(padded + [cells[-1]]).rstrip()

    return [fmt(headers), fmt(["-" * w for w in widths])] + [fmt(row) for row in rows]


def _score_cell(score: dict | None) -> str:
    if not score:
        return "-"
    home, away = score.get("homeScore"), score.get("awayScore")
    tally = f" {home}-{away}" if home is not None and away is not None else ""
    return f"{score.get('status') or '?'}{tally}"


def _db_cell(row: dict) -> str:
    if not row["dbResolved"]:
        return "open"
    outcome = _payout_outcome([row["payoutYes"], row["payoutNo"]])
    return {0: "res YES", 1: "res NO"}.get(outcome, "res ?")


def _chain_cell(row: dict) -> str:
    chain = row["chain"]
    if chain is None:
        return "-"
    if chain.get("closeTime") is None:
        return "err"
    if chain["closeTime"] == 0:
        return "close=0"
    close = "same" if chain["closeTime"] == row["dbClose"] else "diff"
    if chain.get("oracleResolved") is None:
        return f"{close}/?"
    if not chain["oracleResolved"]:
        return f"{close}/unres"
    outcome = {0: "YES", 1: "NO"}.get(row["chainOutcome"], "?")
    return f"{close}/res {outcome}"


def _yn(value: bool) -> str:
    return "yes" if value else "no"


def _counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{key} {value}" for key, value in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def render_text(report: dict) -> str:
    lines = [f"OverUnder market audit  {report['generatedAt']}  (now={report['now']})", f"API    {report['api']}"]
    chain = report["chain"]
    if chain.get("enabled"):
        lines.append(f"Chain  id {chain.get('chainId')} via {chain.get('rpcHost')}")
        lines.append(f"       oracle {chain.get('oracle')}  operator {chain.get('operator')}")
        lines.append(f"       ctf {chain.get('ctf')}  factory {chain.get('factory')}")
    else:
        lines.append(f"Chain  chain checks skipped ({chain.get('skippedReason') or 'need --rpc and --oracle'})")
    settings = report["settings"]
    lines.append(
        f"Rules  live grace {settings['graceSeconds'] / 3600:g}h, general resolve delay "
        f"{settings['generalDelaySeconds']}s (gated {settings['generalGatedDelaySeconds']}s), "
        f"fallback window {FALLBACK_WINDOW // 3600}h"
        + ("" if settings["generalResolver"] else ", general resolver off")
        + (", strict" if settings["strict"] else "")
        + (", ignore-manual" if settings["ignoreManual"] else "")
    )
    lines.append("")

    body = []
    for row in report["markets"]:
        note = "; ".join(row["notes"]) or "-"
        body.append(
            [
                ("! " if row["finding"] else "  ") + row["bucket"],
                _short(row["conditionId"]),
                _clip(row["question"], 34),
                row["kind"],
                datetime.fromtimestamp(row["effectiveClose"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                f"{row['ageSeconds'] / 3600:.1f}",
                _score_cell(row["score"]),
                _db_cell(row),
                _chain_cell(row),
                note,
            ]
        )
    if body:
        headers = ["  BUCKET", "CONDITION", "QUESTION", "KIND", "CLOSE (UTC)", "AGE h", "SCORE", "DB", "CHAIN", "NOTE"]
        lines.extend(_table(headers, body))
        lines.append("(! = finding)")
    else:
        lines.append("No listed markets.")
    lines.append("")

    coverage = report["schedule"]
    week_rows = []
    for week in coverage["weeks"]:
        if not week["hasNextWeek"]:
            nxt = "-"
        else:
            nxt = str(week["nextWeekListable"])
            if week["nextWeekMissed"]:
                nxt += f" (missed {week['nextWeekMissed']})"
            if week["nextWeekInactive"]:
                nxt += f" (inactive {week['nextWeekInactive']})"
        week_rows.append(
            [
                str(week["season"]),
                str(week["week"]),
                str(week["games"]),
                str(week["final"]),
                str(week["in_progress"]),
                str(week["scheduled"]),
                f"{week['postponed']}/{week['cancelled']}",
                f"{week['listedFound']}/{week['listed']}",
                ",".join(str(i) for i in week["stale"]) or "-",
                f"{_yn(week['completeLegacy'])}/{_yn(week['completeNew'])}",
                nxt,
            ]
        )
    if week_rows:
        headers = [
            "SEASON", "WEEK", "GAMES", "FINAL", "LIVE", "SCHED", "POST/CANC",
            "LISTED", "STALE", "COMPLETE(legacy/new)", "NEXT-LISTABLE",
        ]
        lines.extend(_table(headers, week_rows))
    else:
        lines.append("Schedule is empty.")
    lines.append("")

    if coverage["orphans"]:
        lines.append("Schedule orphans (listedConditionId not in the market list):")
        for orphan in coverage["orphans"]:
            lines.append(
                f"  ! schedule #{orphan['scheduleId']} {orphan['game']} (S{orphan['season']} W{orphan['week']}) "
                f"-> {_short(orphan['listedConditionId'])}: {orphan['reason']}"
            )
    else:
        lines.append("Schedule orphans: none")
    if coverage["unscheduledPrimaries"]:
        lines.append("Unscheduled winner primaries: " + ", ".join(_short(c) for c in coverage["unscheduledPrimaries"]))
    lines.append("")

    counts = report["findingCounts"]
    lines.append(f"Findings: {report['findings']}" + (f" ({_counts(counts)})" if counts else ""))
    warnings = report["warningCounts"]
    lines.append(f"Warnings: {report['warnings']}" + (f" ({_counts(warnings)})" if warnings else ""))
    hints: dict[str, list[str]] = {}
    for row in report["markets"]:
        if row["hint"] and (row["finding"] or row["warnings"]):
            hints.setdefault(f"{row['bucket']}: {row['hint']}", []).append(_short(row["conditionId"]))
    if hints:
        lines.append("Hints:")
        for text, cids in hints.items():
            lines.append(f"  [{', '.join(cids)}] {text}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- CLI


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit_markets.py",
        description="Read-only audit of listed markets against the API schedule and ConsensusOracle.",
    )
    parser.add_argument("--api", help="API base URL (env OU_API_URL)")
    parser.add_argument("--rpc", help="JSON-RPC URL (env OU_RPC_URL); printed host-only")
    parser.add_argument("--oracle", help="ConsensusOracle address (env ORACLE_ADDRESS)")
    parser.add_argument("--ctf", help="ConditionalTokens address (default oracle.ctf())")
    parser.add_argument("--factory", help="MarketFactory address (default oracle.factory())")
    parser.add_argument("--json", action="store_true", help="print only the JSON report")
    parser.add_argument("--now", type=int, help="audit clock as unix seconds (default: now)")
    parser.add_argument("--live-grace-hours", type=float, default=DEFAULT_GRACE_HOURS, help="hours after close a game may stay live")
    parser.add_argument(
        "--general-delay-seconds",
        type=int,
        help=(
            "general resolver delay after close for ungated markets "
            f"(env OU_GENERAL_RESOLVE_DELAY_SECONDS, default {DEFAULT_GENERAL_DELAY})"
        ),
    )
    parser.add_argument(
        "--general-gated-delay-seconds",
        type=int,
        help=(
            "general resolver delay after close for event-gated markets, which also wait for a final gate score "
            f"(env OU_GENERAL_RESOLVE_GATED_DELAY_SECONDS, default {DEFAULT_GENERAL_GATED_DELAY})"
        ),
    )
    parser.add_argument(
        "--no-general",
        action="store_true",
        help="the oracle job has no resolve_general stage: non-dual-gate markets are manual",
    )
    parser.add_argument("--ignore-manual", action="store_true", help="overdue manual markets are not findings")
    parser.add_argument("--strict", action="store_true", help="warnings count as findings")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="per-request timeout in seconds")
    return parser


def _write(out: TextIO, text: str) -> None:
    try:
        out.write(text)
    except UnicodeEncodeError:
        out.write(text.encode("ascii", "replace").decode("ascii"))


def main(
    argv: list[str] | None = None,
    *,
    http_get: Callable[[str], Any] | None = None,
    rpc_factory: Callable[[str, float], Callable[[str, list], Any]] | None = None,
    now: int | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """rpc_factory(url, timeout) returns a transport(method, params) -> result (default make_transport)."""
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    api = (args.api or os.getenv("OU_API_URL") or "").strip().rstrip("/")
    if not api:
        print("audit: --api or OU_API_URL is required", file=err)
        return 2
    rpc_url = (args.rpc or os.getenv("OU_RPC_URL") or "").strip()
    oracle = (args.oracle or os.getenv("ORACLE_ADDRESS") or "").strip()
    for name, value in (("--oracle", oracle), ("--ctf", args.ctf), ("--factory", args.factory)):
        if value and not _ADDR.match(value):
            print(f"audit: {name} must be a 0x-prefixed 20-byte address", file=err)
            return 2
    if args.live_grace_hours < 0 or args.timeout <= 0:
        print("audit: --live-grace-hours must be >= 0 and --timeout > 0", file=err)
        return 2
    delays = []
    for value, env, default in (
        (args.general_delay_seconds, "OU_GENERAL_RESOLVE_DELAY_SECONDS", DEFAULT_GENERAL_DELAY),
        (args.general_gated_delay_seconds, "OU_GENERAL_RESOLVE_GATED_DELAY_SECONDS", DEFAULT_GENERAL_GATED_DELAY),
    ):
        if value is None:
            raw = (os.getenv(env) or str(default)).strip()
            try:
                value = int(raw)
            except ValueError:
                print(f"audit: {env} must be an integer", file=err)
                return 2
        if value < 0:
            print("audit: general resolve delays must be >= 0", file=err)
            return 2
        delays.append(value)
    delay, gated_delay = delays
    raw = (os.getenv("OU_LISTING_STALE_GRACE_SECONDS") or str(DEFAULT_LISTING_STALE_GRACE)).strip()
    try:
        listing_grace_s = int(raw)
    except ValueError:
        print("audit: OU_LISTING_STALE_GRACE_SECONDS must be an integer", file=err)
        return 2
    if listing_grace_s < 0:
        print("audit: OU_LISTING_STALE_GRACE_SECONDS must be >= 0", file=err)
        return 2
    grace_s = int(args.live_grace_hours * 3600)
    clock = args.now if args.now is not None else (now if now is not None else int(time.time()))
    getter = http_get or (lambda url: fetch_json(url, args.timeout))

    chain_reader = None
    chain_meta: dict[str, Any] = {}
    if rpc_url and oracle:
        host = rpc_host(rpc_url)
        chain_meta["rpcHost"] = host
        transport = (rpc_factory or make_transport)(rpc_url, args.timeout)
        try:
            chain_meta["chainId"] = _parse_chain_id(transport("eth_chainId", []))
        except Exception as exc:
            print(f"audit: eth_chainId failed on {host}: {_scrub(str(exc), rpc_url)}", file=err)
            return 2
        chain_reader = ChainReader(eth_call_via(transport), oracle, ctf=args.ctf, factory=args.factory)
    elif rpc_url or oracle:
        chain_meta["skippedReason"] = "need both --rpc and --oracle"

    try:
        report = build_report(
            api,
            getter,
            chain_reader,
            clock,
            grace_s,
            args.ignore_manual,
            args.strict,
            general_delay_s=delay,
            general_gated_delay_s=gated_delay,
            general_enabled=not args.no_general,
            chain_meta=chain_meta,
            listing_grace_s=listing_grace_s,
        )
    except FatalError as exc:
        print(f"audit: {_scrub(str(exc), rpc_url)}", file=err)
        return 2
    if args.json:
        _write(out, json.dumps(report, indent=2) + "\n")
    else:
        _write(out, render_text(report))
    return report["exitCode"]


if __name__ == "__main__":
    raise SystemExit(main())
