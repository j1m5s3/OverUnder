"""Unit tests for scripts/audit_markets.py (stdlib unittest, no network)."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import socket
import sys
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import audit_markets  # noqa: E402

NOW = 1_790_163_352
API = "https://api.test"
RPC = "https://rpc.test/v2/SECRETKEY"
ORACLE = "0x" + "f2" * 20
CTF = "0x" + "c7" * 20
FACTORY = "0x" + "fa" * 20
OPERATOR = "0x" + "0b" * 20
OTHER_ORACLE = "0x" + "a0" * 20
ZERO = audit_markets.ZERO_ADDR

SMOKE = "0xaff6568553fc111dd8438e96342d70ef6bf3d0d27b495d78e216caf89e94305a"
CHIEFS_OLD = "0x18bbc46a825718d9a62544bc80da727777a2ee2b77cb352023f09da18b6091b4"
CHILD = "0x5daf4afbffc1695a811acb8f85ad6f74c68d5bdfbcaa363273c2392411c18d59"
CHIEFS = "0x24c901d00fa55cffca7325c9a3fc91ba3da9bea048adde8e32ec3dadad9639c2"
CHIEFS_Q = "Chiefs vs Broncos: Chiefs win?"
SMOKE_Q = "Will OverUnder Sepolia smoke complete before 2026-09-26?"
CHIEFS_CLOSE = 1790034619
HOUR = 3600

TEAMS = [
    "Bills", "Lions", "Falcons", "Panthers", "Ravens", "Saints", "Bears", "Vikings",
    "Texans", "Bengals", "Patriots", "Steelers", "Jets", "Packers", "Buccaneers", "Browns",
    "Titans", "Eagles", "Broncos", "Jaguars", "Chargers", "Raiders", "Cardinals", "Seahawks",
    "Cowboys", "49ers", "Dolphins", "Commanders", "Colts", "Chiefs", "Giants", "Rams",
]
SEL = {sig.split("(")[0]: sel for sig, sel in audit_markets.SELECTORS.items()}


def cid(n: int) -> str:
    return "0x" + f"{n:064x}"


def market(condition_id, question=CHIEFS_Q, close=NOW + HOUR, mtype=0, *, resolved=False,
           payouts=(0, 0), parent=None, criteria="", **extra) -> dict:
    row = {
        "conditionId": condition_id,
        "parentConditionId": parent,
        "question": question,
        "resolutionCriteria": criteria,
        "marketType": mtype,
        "closeTime": close,
        "paused": False,
        "resolved": resolved,
        "payoutYes": payouts[0],
        "payoutNo": payouts[1],
        "suggestedProbability": 0.5,
    }
    row.update(extra)
    return row


def score(status, home_score=None, away_score=None, home="Chiefs", away="Broncos") -> dict:
    return {
        "homeLabel": home,
        "awayLabel": away,
        "homeScore": home_score,
        "awayScore": away_score,
        "status": status,
        "periodLabel": None,
        "facts": None,
        "conditionId": "",
        "updatedAt": "2026-09-22T16:20:08.103412",
    }


def detail(m: dict, sc: dict | None = None, children=()) -> dict:
    out = dict(m)
    out.update(children=list(children), score=sc, facts=None)
    return out


def card(primary: dict, *children: dict) -> dict:
    return {"primary": primary, "children": list(children)}


def game(gid, away, home, kickoff, week, status="scheduled", season=2026, listed="") -> dict:
    return {
        "id": gid,
        "away": away,
        "home": home,
        "kickoff_unix": kickoff,
        "week": week,
        "season": season,
        "status": status,
        "listedConditionId": listed,
    }


def live_schedule() -> list[dict]:
    """Week 2: 15 final + Rams @ Giants stuck scheduled (id 16). Week 3: 16 scheduled."""
    rows = []
    for i in range(16):
        status = "final" if i < 15 else "scheduled"
        kickoff = 1789923600 if i < 15 else 1790036100
        rows.append(game(i + 1, TEAMS[2 * i + 1], TEAMS[2 * i], kickoff, 2, status))
    first = NOW + int(36.6 * HOUR)
    for i in range(16):
        rows.append(game(17 + i, TEAMS[i + 16], TEAMS[i], first + i * 600, 3))
    return rows


def live_cards() -> list[dict]:
    return [
        card(market(SMOKE, SMOKE_Q, 1790433986)),
        card(
            market(CHIEFS_OLD, CHIEFS_Q, 1792522532),
            market(CHILD, "Chiefs vs Broncos: over 45.5 points?", 1792436132, 1, parent=CHIEFS_OLD),
        ),
        card(market(CHIEFS, CHIEFS_Q, CHIEFS_CLOSE, tradingHaltsAt=CHIEFS_CLOSE, tradingOpen=False, yesPriceMicros=512000)),
    ]


def live_details() -> dict:
    final = score("final", 31, 10)
    return {
        CHIEFS_OLD: detail(market(CHIEFS_OLD, CHIEFS_Q, 1792522532), final),
        CHIEFS: detail(market(CHIEFS, CHIEFS_Q, CHIEFS_CLOSE), final),
    }


def live_chain() -> "FakeChain":
    return FakeChain(
        {
            SMOKE: {"close": 0, "ctfOracle": ZERO},
            CHIEFS_OLD: {"close": 0, "ctfOracle": ZERO},
            CHILD: {"close": 0, "ctfOracle": ZERO},
            CHIEFS: {"close": CHIEFS_CLOSE},
        }
    )


class FakeHttp:
    """url -> object (deep-copied) or exception; unknown URLs are 404."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url: str):
        self.calls.append(url)
        if url not in self.routes:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        value = self.routes[url]
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)


def api_routes(cards, schedule=(), details=None) -> dict:
    routes = {f"{API}/api/v1/markets": cards, f"{API}/api/v1/markets/schedule": list(schedule)}
    for m in audit_markets.flatten_cards(cards):
        if audit_markets.is_sports_primary(m):
            routes[f"{API}/api/v1/markets/{m['conditionId']}"] = detail(m)
    for key, value in (details or {}).items():
        routes[f"{API}/api/v1/markets/{key}"] = value
    return routes


def _word(value: int) -> str:
    return "0x" + f"{value:064x}"


def _addr_word(address: str) -> str:
    return "0x" + address[2:].lower().rjust(64, "0")


class FakeChain:
    """JSON-RPC transport over per-cid state; unknown targets answer '0x' (no contract)."""

    def __init__(self, markets=None, *, chain_id="0x14a34", factory=FACTORY, fail_cids=(), chain_id_error=None):
        self.markets = {k.lower(): v for k, v in (markets or {}).items()}
        self.chain_id = chain_id
        self.factory_addr = factory
        self.fail_cids = [c[2:].lower() for c in fail_cids]
        self.chain_id_error = chain_id_error
        self.calls: list[tuple[str, list]] = []
        self.url = None

    def factory(self, url, timeout):
        self.url = url
        return self.transport

    def state(self, word: str) -> dict:
        base = {"close": 0, "resolved": False, "payouts": (0, 0), "ctfOracle": ZERO, "exists": True, "attest": 0}
        base.update(self.markets.get("0x" + word.lower(), {}))
        base.setdefault("ctfResolved", base["resolved"])
        return base

    def transport(self, method, params):
        self.calls.append((method, params))
        if method == "eth_chainId":
            if self.chain_id_error is not None:
                raise self.chain_id_error
            return self.chain_id
        assert method == "eth_call", method
        call, block = params
        assert block == "latest"
        to, data = call["to"].lower(), call["data"]
        sel, args = data[:10], data[10:]
        if any(fail in args for fail in self.fail_cids):
            raise audit_markets.RpcError("eth_call: execution timeout")
        st = self.state(args[:64]) if args else {}
        if to == ORACLE:
            if sel == SEL["ctf"]:
                return _addr_word(CTF)
            if sel == SEL["factory"]:
                return _addr_word(self.factory_addr)
            if sel == SEL["operator"]:
                return _addr_word(OPERATOR)
            if sel == SEL["closeTime"]:
                return _word(st["close"])
            if sel == SEL["resolved"]:
                return _word(int(st["resolved"]))
            if sel == SEL["attestationCount"]:
                return _word(st["attest"])
        if to == CTF:
            if sel == SEL["payoutDenominator"]:
                return _word(1 if st["ctfResolved"] else 0)
            if sel == SEL["payoutNumerators"]:
                index = int(args[64:128], 16)
                return _word(st["payouts"][index] if st["ctfResolved"] else 0)
            if sel == SEL["oracles"]:
                return _addr_word(ORACLE if st["close"] else st["ctfOracle"])
        if to == self.factory_addr.lower() and sel == SEL["marketExists"]:
            return _word(int(st["exists"]))
        return "0x"


class AuditTestCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in (
            "OU_API_URL", "OU_RPC_URL", "ORACLE_ADDRESS",
            "OU_GENERAL_RESOLVE_DELAY_SECONDS", "OU_GENERAL_RESOLVE_GATED_DELAY_SECONDS",
        ):
            os.environ.pop(key, None)

    def run_main(self, routes, *extra, chain=None, now=NOW, http=None):
        out, err = io.StringIO(), io.StringIO()
        argv = ["--api", API, *extra]
        if chain is not None:
            argv += ["--rpc", RPC, "--oracle", ORACLE]
        code = audit_markets.main(
            argv,
            http_get=http or FakeHttp(routes),
            rpc_factory=chain.factory if chain is not None else None,
            now=now,
            stdout=out,
            stderr=err,
        )
        return code, out.getvalue(), err.getvalue()

    def report(self, routes, *extra, chain=None, now=NOW):
        code, out, err = self.run_main(routes, "--json", *extra, chain=chain, now=now)
        self.assertNotEqual(code, 2, err)
        report = json.loads(out)
        self.assertEqual(report["exitCode"], code)
        return report

    @staticmethod
    def row(report, condition_id):
        return next(r for r in report["markets"] if r["conditionId"] == condition_id)


class ClassificationTests(AuditTestCase):
    def test_open_market_no_findings(self):
        routes = api_routes([card(market(cid(1), close=NOW + HOUR))])
        report = self.report(routes)
        row = self.row(report, cid(1))
        self.assertEqual(row["bucket"], "open")
        self.assertEqual(row["kind"], "sports")
        self.assertFalse(row["finding"])
        self.assertEqual(report["exitCode"], 0)

    def test_live_within_grace(self):
        m = market(cid(1), close=NOW - HOUR)
        routes = api_routes([card(m)], details={cid(1): detail(m, score("in_progress", 7, 3))})
        report = self.report(routes)
        self.assertEqual(self.row(report, cid(1))["bucket"], "live")
        self.assertEqual(report["exitCode"], 0)

    def test_overdue_no_final_after_grace(self):
        stale = market(cid(1), close=NOW - 10 * HOUR)
        missing = market(cid(2), close=NOW - 10 * HOUR)
        routes = api_routes(
            [card(stale), card(missing)],
            details={cid(1): detail(stale, score("scheduled")), cid(2): detail(missing, None)},
        )
        report = self.report(routes)
        for condition_id in (cid(1), cid(2)):
            row = self.row(report, condition_id)
            self.assertEqual(row["bucket"], "overdue_no_final")
            self.assertTrue(row["finding"])
        self.assertIn("no score posted", self.row(report, cid(2))["notes"])
        self.assertEqual(report["exitCode"], 1)

    def test_overdue_final_unresolved_derives_yes(self):
        m = market(CHIEFS, close=CHIEFS_CLOSE)
        routes = api_routes([card(m)], details={CHIEFS: detail(m, score("final", 31, 10))})
        report = self.report(routes)
        row = self.row(report, CHIEFS)
        self.assertEqual(row["bucket"], "overdue_final_unresolved")
        self.assertEqual(row["derivedOutcome"], 0)
        self.assertIn("fallback_window", row["flags"])
        self.assertIn("gcloud run jobs execute overunder-oracle", row["hint"])
        self.assertEqual(report["exitCode"], 1)

    def test_tie_final_notes_manual(self):
        m = market(cid(1), close=NOW - 2 * HOUR)
        routes = api_routes([card(m)], details={cid(1): detail(m, score("final", 20, 20))})
        row = self.row(self.report(routes), cid(1))
        self.assertEqual(row["bucket"], "overdue_final_unresolved")
        self.assertIsNone(row["derivedOutcome"])
        self.assertIn("no score outcome -> manual", row["notes"])
        self.assertIn("resolveArbitrated", row["hint"])
        self.assertNotIn("fallback_window", row["flags"])

    def test_resolved_consistent(self):
        m = market(cid(1), close=NOW - 30 * HOUR, resolved=True, payouts=(1, 0))
        chain = FakeChain({cid(1): {"close": NOW - 30 * HOUR, "resolved": True, "payouts": (1, 0)}})
        report = self.report(api_routes([card(m)]), chain=chain)
        row = self.row(report, cid(1))
        self.assertEqual(row["bucket"], "resolved")
        self.assertEqual(row["chain"]["payouts"], [1, 0])
        self.assertEqual(row["chainOutcome"], 0)
        self.assertEqual(report["exitCode"], 0)

    def test_mismatch_chain_resolved_db_not(self):
        m = market(cid(1), close=NOW - 30 * HOUR)
        chain = FakeChain({cid(1): {"close": NOW - 30 * HOUR, "resolved": True, "payouts": (1, 0)}})
        report = self.report(api_routes([card(m)]), chain=chain)
        row = self.row(report, cid(1))
        self.assertEqual(row["bucket"], "mismatch")
        self.assertIn("resolved", row["mismatchFields"])
        self.assertEqual(row["chainOutcome"], 0)
        self.assertIn("step 9", row["hint"])
        self.assertEqual(report["exitCode"], 1)

    def test_mismatch_close_time_and_factory(self):
        m = market(cid(1), close=NOW + HOUR)
        chain = FakeChain({cid(1): {"close": NOW + HOUR + 60, "exists": False}})
        row = self.row(self.report(api_routes([card(m)]), chain=chain), cid(1))
        self.assertEqual(row["bucket"], "mismatch")
        self.assertEqual(row["mismatchFields"], ["closeTime", "marketExists"])
        self.assertEqual(row["effectiveClose"], NOW + HOUR + 60)
        self.assertIn("legacy market not imported", row["hint"])

    def test_mismatch_payouts(self):
        m = market(cid(1), close=NOW - 30 * HOUR, resolved=True, payouts=(0, 1))
        chain = FakeChain({cid(1): {"close": NOW - 30 * HOUR, "resolved": True, "payouts": (1, 0)}})
        row = self.row(self.report(api_routes([card(m)]), chain=chain), cid(1))
        self.assertEqual(row["mismatchFields"], ["payouts"])

    def test_not_registered_other_deployment(self):
        cards = [card(market(cid(1), close=NOW + HOUR)), card(market(cid(2), close=NOW + HOUR))]
        chain = FakeChain({cid(1): {"close": 0, "ctfOracle": ZERO}, cid(2): {"close": 0, "ctfOracle": OTHER_ORACLE}})
        report = self.report(api_routes(cards), chain=chain)
        first, second = self.row(report, cid(1)), self.row(report, cid(2))
        self.assertEqual(first["bucket"], "not_registered")
        self.assertEqual(first["chain"]["ctfOracle"], ZERO)
        self.assertTrue(first["finding"])
        self.assertIn("different ConsensusOracle", first["hint"])
        self.assertIn("POST /api/v1/markets/{condition_id}/archive", first["hint"])
        self.assertNotIn("manual cleanup", first["hint"])
        self.assertIn(OTHER_ORACLE, " ".join(second["notes"]))
        self.assertEqual(report["exitCode"], 1)

    def test_wildcard_and_nonsports_are_general_unless_disabled(self):
        # A resolved parent opens the child's gate; the 1h ungated delay makes both overdue at 2h.
        parent = market(cid(1), close=NOW - 3 * HOUR, resolved=True, payouts=(1, 0))
        child = market(cid(2), "Chiefs vs Broncos: over 45.5 points?", NOW - 2 * HOUR, 1, parent=cid(1))
        nonsports = market(cid(3), "Will it rain in Denver on Sunday?", NOW - 2 * HOUR)
        future = market(cid(4), "Will the smoke finish?", NOW + HOUR)
        routes = api_routes([card(parent, child), card(nonsports), card(future)],
                            [game(1, "Broncos", "Chiefs", NOW - 3 * HOUR, 3, "final")])
        report = self.report(routes, "--general-delay-seconds", "3600")
        for condition_id in (cid(2), cid(3)):
            row = self.row(report, condition_id)
            self.assertEqual((row["bucket"], row["kind"]), ("overdue_general_unresolved", "general"))
            self.assertIn("no resolution criteria: research uses the question alone", row["notes"])
        self.assertEqual(self.row(report, cid(2))["role"], "child")
        self.assertEqual(self.row(report, cid(4))["bucket"], "open")
        self.assertEqual(report["summary"]["manual"], 0)

        manual = self.report(routes, "--no-general", "--general-delay-seconds", "3600")
        self.assertFalse(manual["settings"]["generalResolver"])
        for condition_id in (cid(2), cid(3)):
            row = self.row(manual, condition_id)
            self.assertEqual((row["bucket"], row["kind"]), ("manual", "manual"))
            self.assertIn("overdue", row["flags"])
            self.assertTrue(row["finding"])
            self.assertIn("--no-general", row["hint"])
        future_row = self.row(manual, cid(4))
        self.assertEqual((future_row["bucket"], future_row["finding"]), ("manual", False))
        self.assertEqual(manual["findingCounts"], {"manual_overdue": 2})
        self.assertEqual(manual["exitCode"], 1)
        code, out, _ = self.run_main(routes, "--no-general", "--ignore-manual")
        self.assertEqual(code, 0)
        self.assertIn("general resolver off", out)

    def test_general_resolution_path(self):
        criteria = "YES if the release ships by close."
        cards = [
            card(market(cid(1), "Will v2 ship?", NOW + 100, 2, criteria=criteria)),
            card(market(cid(2), "Will v3 ship?", NOW - 600, 2, criteria=criteria)),
            card(market(cid(3), "Will v4 ship?", NOW - 25 * HOUR, 2, criteria=criteria)),
        ]
        routes = api_routes(cards)
        report = self.report(routes)
        buckets = {r["conditionId"]: r["bucket"] for r in report["markets"]}
        self.assertEqual(buckets, {cid(1): "open", cid(2): "awaiting_general", cid(3): "overdue_general_unresolved"})
        self.assertTrue(self.row(report, cid(3))["finding"])
        self.assertIn("general.py", self.row(report, cid(3))["hint"])
        self.assertFalse(self.row(report, cid(2))["finding"])
        self.assertIn("general resolver eligible in 1430 min", self.row(report, cid(2))["notes"])
        self.assertEqual(report["summary"]["awaiting_general"], 1)
        self.assertEqual(report["exitCode"], 1)

        relaxed = self.report(routes, "--general-delay-seconds", "100000")
        self.assertEqual(self.row(relaxed, cid(3))["bucket"], "awaiting_general")
        self.assertEqual(relaxed["exitCode"], 0)

        os.environ["OU_GENERAL_RESOLVE_DELAY_SECONDS"] = "100"
        strict_env = self.report(routes)
        self.assertEqual(self.row(strict_env, cid(2))["bucket"], "overdue_general_unresolved")
        self.assertEqual(strict_env["settings"]["generalDelaySeconds"], 100)

    def test_ungated_general_waits_a_day_not_the_gated_hour(self):
        """general.py waits close + 24h for ungated markets: 2h past close is not overdue."""
        rain = market(cid(1), "Will it rain in Denver on Sunday?", NOW - 2 * HOUR, 2, criteria="NWS")
        snow = market(cid(2), "Will it snow in Denver on Monday?", NOW - 25 * HOUR, 2, criteria="NWS")
        report = self.report(api_routes([card(rain), card(snow)]))
        row = self.row(report, cid(1))
        self.assertEqual((row["bucket"], row["finding"], row["gate"]), ("awaiting_general", False, None))
        self.assertIn("general resolver eligible in 1320 min", row["notes"])
        self.assertEqual(self.row(report, cid(2))["bucket"], "overdue_general_unresolved")
        self.assertEqual(report["settings"]["generalDelaySeconds"], 86400)
        self.assertEqual(report["settings"]["generalGatedDelaySeconds"], 3600)
        self.assertEqual(report["findingCounts"], {"overdue_general_unresolved": 1})

    def test_resolve_after_overrides_the_delay(self):
        m = market(cid(1), "Will v2 ship?", NOW - 25 * HOUR, 2, criteria="notes", resolveAfter=NOW + HOUR)
        row = self.row(self.report(api_routes([card(m)])), cid(1))
        self.assertEqual(row["bucket"], "awaiting_general")
        self.assertIn("general resolver eligible in 60 min", row["notes"])

    def _gated_report(self, parent_score, *, child_close=NOW - 2 * HOUR, chain=None, parent_resolved=False, extra=()):
        payouts = (1, 0) if parent_resolved else (0, 0)
        parent = market(cid(1), close=child_close, resolved=parent_resolved, payouts=payouts)
        child = market(cid(2), "Will Kelce score a TD?", child_close, 1, parent=cid(1))
        routes = api_routes([card(parent, child)], [game(1, "Broncos", "Chiefs", child_close, 3)],
                            details={cid(1): detail(parent, parent_score)})
        return self.report(routes, *extra, chain=chain)

    def test_gated_child_waits_for_the_parent_final_score(self):
        report = self._gated_report(score("in_progress", 7, 3))
        row = self.row(report, cid(2))
        self.assertEqual((row["bucket"], row["finding"]), ("awaiting_general", False))
        self.assertIn("general resolver waits for a final parent score (in_progress)", row["notes"])
        self.assertEqual(
            row["gate"], {"kind": "parent", "subject": cid(1), "status": "in_progress", "done": False, "found": True}
        )
        self.assertEqual(self.row(report, cid(1))["bucket"], "live")
        self.assertEqual(report["exitCode"], 0)

        early = self.row(self._gated_report(score("in_progress"), child_close=NOW - 30 * 60), cid(2))
        self.assertEqual(early["bucket"], "awaiting_general")
        self.assertIn("general resolver eligible in 30 min once the parent score is final", early["notes"])

        stale = self.row(self._gated_report(score("in_progress", 7, 3), child_close=NOW - 10 * HOUR), cid(2))
        self.assertEqual((stale["bucket"], stale["finding"]), ("overdue_no_final", True))
        self.assertIn("general resolver waits for a final parent score (in_progress)", stale["notes"])
        self.assertEqual(stale["hint"], audit_markets.HINTS["overdue_no_final"])

    def test_gated_child_uses_the_short_delay_once_the_gate_passes(self):
        for parent_score in (score("final", 31, 10), score("cancelled")):
            row = self.row(self._gated_report(parent_score), cid(2))
            self.assertEqual((row["bucket"], row["gate"]["done"]), ("overdue_general_unresolved", True), parent_score)
        fresh = self.row(self._gated_report(score("final", 31, 10), child_close=NOW - 30 * 60), cid(2))
        self.assertEqual(fresh["bucket"], "awaiting_general")
        self.assertIn("general resolver eligible in 30 min", fresh["notes"])
        relaxed = self._gated_report(score("final", 31, 10), extra=("--general-gated-delay-seconds", "10800"))
        self.assertEqual(self.row(relaxed, cid(2))["bucket"], "awaiting_general")
        self.assertEqual(relaxed["settings"]["generalGatedDelaySeconds"], 10800)

    def test_gated_child_parent_resolved_on_chain_with_stale_score(self):
        close = NOW - 2 * HOUR
        chain = FakeChain({cid(1): {"close": close, "resolved": True, "payouts": (1, 0)}, cid(2): {"close": close}})
        report = self._gated_report(score("in_progress"), chain=chain)
        self.assertEqual(self.row(report, cid(1))["bucket"], "mismatch")
        row = self.row(report, cid(2))
        self.assertEqual((row["bucket"], row["gate"]["done"]), ("overdue_general_unresolved", True))
        # Without the chain the DB flag stands in for the resolved check.
        db = self.row(self._gated_report(score("in_progress"), parent_resolved=True), cid(2))
        self.assertEqual(db["bucket"], "overdue_general_unresolved")

    def test_event_gated_prop_primary_waits_for_its_own_final(self):
        prop = market(cid(1), "Chiefs vs Broncos: over 45.5 points?", NOW - 2 * HOUR)
        live = api_routes([card(prop)], details={cid(1): detail(prop, score("in_progress", 21, 17))})
        row = self.row(self.report(live), cid(1))
        self.assertEqual((row["kind"], row["bucket"]), ("general", "awaiting_general"))
        self.assertIn("general resolver waits for a final event score (in_progress)", row["notes"])
        self.assertEqual(row["gate"]["kind"], "event")
        final = api_routes([card(prop)], details={cid(1): detail(prop, score("final", 24, 27))})
        self.assertEqual(self.row(self.report(final), cid(1))["bucket"], "overdue_general_unresolved")

    def test_child_of_a_non_sports_parent_waits_for_its_resolution(self):
        parent = market(cid(1), "Will the smoke finish?", NOW - 10 * HOUR)
        child = market(cid(2), "Will the smoke finish twice?", NOW - 10 * HOUR, 1, parent=cid(1))
        report = self.report(api_routes([card(parent, child)]))
        row = self.row(report, cid(2))
        self.assertEqual((row["bucket"], row["finding"]), ("awaiting_general", False))
        self.assertIn("general resolver waits for the parent to resolve (no parent score feed)", row["notes"])
        self.assertEqual(self.row(report, cid(1))["bucket"], "awaiting_general")
        resolved = market(cid(1), "Will the smoke finish?", NOW - 10 * HOUR, resolved=True, payouts=(0, 1))
        done = self.report(api_routes([card(resolved, child)]))
        self.assertEqual(self.row(done, cid(2))["bucket"], "overdue_general_unresolved")

    def test_unlisted_parent_is_read_through_its_detail(self):
        orphan = market(cid(2), "Will the backup QB start?", NOW - 10 * HOUR, 1, parent=cid(99))
        gone = market(cid(99), "Jets vs Patriots: Jets win?", NOW - 11 * HOUR)
        routes = api_routes([card(orphan)], details={cid(99): detail(gone, score("final", 20, 10))})
        http = FakeHttp(routes)
        _, out, _ = self.run_main(routes, "--json", http=http)
        row = self.row(json.loads(out), cid(2))
        self.assertEqual((row["bucket"], row["role"]), ("overdue_general_unresolved", "orphan_child"))
        self.assertIn(f"{API}/api/v1/markets/{cid(99)}", http.calls)

        missing = api_routes([card(orphan)])
        row = self.row(self.report(missing), cid(2))
        self.assertEqual((row["bucket"], row["gate"]["found"]), ("overdue_no_final", False))
        self.assertIn("parent market not found: the gate cannot pass", row["notes"])
        self.assertEqual(row["hint"], audit_markets.HINTS["gate_missing"])

        chain = FakeChain({cid(2): {"close": NOW - 10 * HOUR}, cid(99): {"close": NOW - 11 * HOUR, "resolved": True}})
        row = self.row(self.report(missing, chain=chain), cid(2))
        self.assertEqual((row["bucket"], row["gate"]["done"]), ("overdue_general_unresolved", True))

    def test_market_kind_rules(self):
        kind = audit_markets.market_kind
        self.assertEqual(kind(market(cid(1))), "sports")
        self.assertEqual(kind(market(cid(1)), general_enabled=False), "sports")
        # A " vs " primary without "<team> win?" is not dual-gate, so the general resolver owns it.
        self.assertEqual(kind(market(cid(1), "Chiefs vs Broncos: over 45.5?")), "general")
        self.assertEqual(kind(market(cid(1), "Chiefs vs Broncos: Chiefs win?", mtype=1, parent=cid(2))), "general")
        self.assertEqual(kind(market(cid(1), "Will ETH close above 5k?", criteria="Coinbase close")), "general")
        self.assertEqual(kind(market(cid(1), "Will ETH close above 5k?", mtype=2, criteria="  ")), "general")
        self.assertEqual(kind(market(cid(1), "Will ETH close above 5k?", mtype=2), general_enabled=False), "manual")

    def test_early_final_warning_only(self):
        m = market(cid(1), close=NOW + HOUR)
        routes = api_routes([card(m)], [game(1, "Broncos", "Chiefs", NOW + HOUR, 3)],
                            details={cid(1): detail(m, score("final", 24, 17))})
        report = self.report(routes)
        row = self.row(report, cid(1))
        self.assertEqual(row["bucket"], "open")
        self.assertEqual(row["warnings"], ["early_final"])
        self.assertFalse(row["finding"])
        self.assertEqual(report["warnings"], 1)
        self.assertEqual(report["exitCode"], 0)
        strict = self.report(routes, "--strict")
        self.assertEqual(strict["exitCode"], 1)
        self.assertEqual(strict["findingCounts"], {"strict_warning": 1})

    def test_cancelled_and_postponed_notes(self):
        cancelled = market(cid(1), close=NOW - 10 * HOUR)
        postponed = market(cid(2), close=NOW - 10 * HOUR)
        routes = api_routes(
            [card(cancelled), card(postponed)],
            details={cid(1): detail(cancelled, score("cancelled")), cid(2): detail(postponed, score("postponed"))},
        )
        report = self.report(routes)
        first, second = self.row(report, cid(1)), self.row(report, cid(2))
        self.assertEqual(first["bucket"], "overdue_no_final")
        self.assertIn("cancelled", first["flags"])
        self.assertIn("no payout path", " ".join(first["notes"]))
        self.assertIn("invalid outcome", first["hint"])
        self.assertIn("postponed: waits for the rescheduled final", second["notes"])
        self.assertNotIn("cancelled", second["flags"])

    def test_chain_error_is_finding(self):
        cards = [card(market(cid(1), close=NOW + HOUR)), card(market(cid(2), close=NOW + HOUR))]
        chain = FakeChain({cid(1): {"close": NOW + HOUR}, cid(2): {"close": NOW + HOUR}}, fail_cids=[cid(2)])
        report = self.report(api_routes(cards), chain=chain)
        ok, bad = self.row(report, cid(1)), self.row(report, cid(2))
        self.assertEqual(ok["bucket"], "open")
        self.assertFalse(ok["finding"])
        self.assertEqual(bad["bucket"], "open")
        self.assertIn("chain_error", bad["flags"])
        self.assertTrue(bad["finding"])
        self.assertEqual(report["findingCounts"], {"chain_error": 1})
        self.assertEqual(report["exitCode"], 1)


class ScheduleTests(AuditTestCase):
    def test_schedule_coverage(self):
        schedule = [
            game(1, "Lions", "Bills", NOW - 30 * HOUR, 2, "final"),
            game(2, "Panthers", "Falcons", NOW - 30 * HOUR, 2, "final"),
            game(3, "Rams", "Giants", NOW - 10 * HOUR, 2, "scheduled"),
            game(4, "Saints", "Ravens", NOW - 5 * HOUR, 3, "final"),
            game(5, "Vikings", "Bears", NOW - 5 * HOUR, 3, "postponed"),
            game(6, "Bengals", "Texans", NOW - 5 * HOUR, 3, "cancelled"),
            game(7, "Steelers", "Patriots", NOW + HOUR, 4),
            game(8, "Packers", "Jets", NOW + 300, 4),
            game(9, "Browns", "Buccaneers", NOW + HOUR, 4, "postponed"),
            game(10, "Broncos", "Chiefs", NOW + HOUR, 4),
        ]
        markets = audit_markets.flatten_cards([card(market(cid(1), CHIEFS_Q, NOW + HOUR))])
        coverage = audit_markets.schedule_coverage(schedule, markets, {cid(1): None}, NOW, grace_s=6 * HOUR)
        week2, week3, week4 = coverage["weeks"]
        self.assertEqual(week2["stale"], [3])
        self.assertFalse(week2["completeLegacy"])
        # Game 3 kicked off 10h ago: past the default 8h listing grace.
        self.assertTrue(week2["completeNew"])
        within = audit_markets.schedule_coverage(
            schedule, markets, {cid(1): None}, NOW, grace_s=6 * HOUR, listing_grace_s=12 * HOUR
        )
        self.assertFalse(within["weeks"][0]["completeNew"])
        self.assertEqual(week2["nextWeekInactive"], 3)
        self.assertEqual((week3["final"], week3["postponed"], week3["cancelled"]), (1, 1, 1))
        self.assertFalse(week3["completeLegacy"])
        self.assertTrue(week3["completeNew"])
        self.assertEqual(
            (week3["nextWeekListable"], week3["nextWeekMissed"], week3["nextWeekInactive"]), (1, 1, 1)
        )
        self.assertFalse(week4["hasNextWeek"])
        self.assertEqual(coverage["unscheduledPrimaries"], [])
        self.assertEqual(coverage["orphans"], [])

    def test_week_complete_live_status_override(self):
        rows = [
            game(1, "Broncos", "Chiefs", NOW, 3, "postponed", listed=cid(1)),
            game(2, "Lions", "Bills", NOW, 3, "final"),
        ]
        self.assertFalse(audit_markets.week_complete_legacy(rows, {CHIEFS_Q: None}))
        self.assertTrue(audit_markets.week_complete_new(rows, {cid(1): None}))
        self.assertTrue(audit_markets.week_complete_new(rows, {cid(1): "cancelled"}))
        rows[0]["status"] = "final"
        self.assertFalse(audit_markets.week_complete_legacy(rows, {CHIEFS_Q: "in_progress"}))
        self.assertFalse(audit_markets.week_complete_new(rows, {cid(1): "in_progress"}))
        # Live status applies only through the row's own listedConditionId.
        self.assertTrue(audit_markets.week_complete_new(rows, {cid(9): "in_progress", CHIEFS_Q: "in_progress"}))
        self.assertTrue(audit_markets.week_complete_legacy(rows, {CHIEFS_Q: "final"}))
        self.assertFalse(audit_markets.week_complete_new([], {}))

    def test_week_complete_stale_rule(self):
        rows = [game(1, "Broncos", "Chiefs", NOW - 9 * HOUR, 3, "scheduled", listed=cid(1))]
        self.assertFalse(audit_markets.week_complete_new(rows, {}))  # no clock, no stale rule
        self.assertTrue(audit_markets.week_complete_new(rows, {}, NOW))
        self.assertFalse(audit_markets.week_complete_new(rows, {}, NOW, 10 * HOUR))
        self.assertTrue(audit_markets.week_complete_new(rows, {cid(1): "in_progress"}, NOW))

    def test_link_games_falls_back_to_question_and_kickoff(self):
        sports = [market(cid(1), CHIEFS_Q, NOW + HOUR)]
        rows = [
            game(1, "Broncos", "Chiefs", NOW + HOUR, 3),
            game(2, "Broncos", "Chiefs", NOW + 2 * HOUR, 3),
            game(3, "Broncos", "Chiefs", NOW + HOUR, 3, listed=cid(7)),
        ]
        linked = audit_markets.link_games(rows, sports)
        self.assertEqual([g["listedConditionId"] for g in linked], [cid(1), None, cid(7)])

    def test_schedule_orphans(self):
        listed = market(cid(1), CHIEFS_Q, NOW + HOUR)
        paused = market(cid(3), "Jets vs Packers: Jets win?", NOW + HOUR, paused=True)
        schedule = [
            game(1, "Broncos", "Chiefs", NOW + HOUR, 3, listed=cid(1)),
            game(2, "Lions", "Bills", NOW + HOUR, 3, listed=cid(2)),
            game(3, "Packers", "Jets", NOW + HOUR, 3, listed=cid(3)),
            game(4, "Saints", "Ravens", NOW + HOUR, 3, listed=cid(4)),
        ]
        routes = api_routes(
            [card(listed)],
            schedule,
            details={cid(3): detail(paused), cid(4): urllib.error.HTTPError("u", 500, "boom", {}, None)},
        )
        report = self.report(routes)
        reasons = {o["scheduleId"]: o["reason"] for o in report["schedule"]["orphans"]}
        self.assertEqual(reasons, {2: "missing", 3: "paused", 4: "detail_error"})
        week = report["schedule"]["weeks"][0]
        self.assertEqual((week["listed"], week["listedFound"]), (4, 1))
        self.assertEqual(report["findingCounts"], {"schedule_orphan": 3})
        self.assertEqual(report["exitCode"], 1)

    def test_unscheduled_primary_is_warning(self):
        routes = api_routes([card(market(cid(1), close=NOW + HOUR))], [game(1, "Lions", "Bills", NOW + HOUR, 3)])
        report = self.report(routes)
        self.assertIn("unscheduled", self.row(report, cid(1))["warnings"])
        self.assertEqual(report["exitCode"], 0)
        self.assertEqual(self.report(routes, "--strict")["exitCode"], 1)


class LiveShapeTests(AuditTestCase):
    def test_live_fixture_with_chain(self):
        routes = api_routes(live_cards(), live_schedule(), live_details())
        report = self.report(routes, chain=live_chain())
        buckets = {r["conditionId"]: r["bucket"] for r in report["markets"]}
        self.assertEqual(
            buckets,
            {
                SMOKE: "not_registered",
                CHIEFS_OLD: "not_registered",
                CHILD: "not_registered",
                CHIEFS: "overdue_final_unresolved",
            },
        )
        chiefs = self.row(report, CHIEFS)
        self.assertEqual(chiefs["derivedOutcome"], 0)
        self.assertIn("unscheduled", chiefs["flags"])
        self.assertIn("attestations 0/3", " ".join(chiefs["notes"]))
        self.assertEqual(chiefs["chain"]["marketExists"], True)
        week2, week3 = report["schedule"]["weeks"]
        self.assertEqual(week2["stale"], [16])
        self.assertFalse(week2["completeLegacy"])
        # Rams @ Giants is still "scheduled" 35h after kickoff: past the listing
        # stale grace, so it no longer blocks the week roll.
        self.assertTrue(week2["completeNew"])
        self.assertEqual(week2["nextWeekListable"], 16)
        self.assertEqual(week3["games"], 16)
        self.assertEqual(report["findingCounts"], {"not_registered": 3, "overdue_final_unresolved": 1})
        self.assertEqual(report["chain"]["chainId"], 84532)
        self.assertEqual(report["chain"]["ctf"], CTF)
        self.assertEqual(report["chain"]["operator"], OPERATOR)
        self.assertEqual(report["exitCode"], 1)
        self.assertNotIn("tradingOpen", chiefs)

    def test_live_fixture_without_chain(self):
        routes = api_routes(live_cards(), live_schedule(), live_details())
        http = FakeHttp(routes)
        code, out, _ = self.run_main(routes, "--json", http=http)
        report = json.loads(out)
        self.assertEqual(code, 1)
        smoke, old, child, chiefs = (self.row(report, c) for c in (SMOKE, CHIEFS_OLD, CHILD, CHIEFS))
        self.assertEqual((smoke["bucket"], smoke["kind"], smoke["flags"]), ("open", "general", []))
        self.assertEqual(old["bucket"], "open")
        self.assertIn("early_final", old["flags"])
        self.assertEqual((child["bucket"], child["kind"]), ("open", "general"))
        self.assertEqual(chiefs["bucket"], "overdue_final_unresolved")
        self.assertIsNone(chiefs["chain"])
        detail_calls = [u for u in http.calls if u.count("/") > 5 and not u.endswith("/schedule")]
        self.assertEqual(sorted(detail_calls), sorted(f"{API}/api/v1/markets/{c}" for c in (CHIEFS_OLD, CHIEFS)))

    def test_no_chain_skips_chain_buckets(self):
        routes = api_routes(live_cards(), live_schedule(), live_details())
        code, out, _ = self.run_main(routes)
        self.assertEqual(code, 1)
        self.assertIn("chain checks skipped", out)
        self.assertNotIn("not_registered", out.split("Findings:")[0].split("CHAIN")[1])
        report = self.report(routes, "--rpc", RPC)
        self.assertFalse(report["chain"]["enabled"])
        self.assertEqual(report["chain"]["skippedReason"], "need both --rpc and --oracle")
        self.assertEqual(report["summary"]["not_registered"] + report["summary"]["mismatch"], 0)

    def test_json_output_shape(self):
        routes = api_routes(live_cards(), live_schedule(), live_details())
        code, out, err = self.run_main(routes, "--json", chain=live_chain())
        report = json.loads(out)
        self.assertEqual(err, "")
        self.assertEqual(report["exitCode"], code)
        self.assertEqual(set(report["summary"]), set(audit_markets.BUCKETS))
        self.assertEqual(sum(report["summary"].values()), len(report["markets"]))
        for key in ("generatedAt", "now", "api", "chain", "findings", "warnings", "schedule"):
            self.assertIn(key, report)
        self.assertEqual(report["generatedAt"], "2026-09-23T11:35:52Z")
        row = report["markets"][0]
        for key in (
            "conditionId", "parentConditionId", "role", "question", "marketType", "kind", "bucket", "finding",
            "flags", "dbClose", "chainClose", "effectiveClose", "ageSeconds", "dbResolved", "payoutYes",
            "payoutNo", "score", "derivedOutcome", "chain", "mismatchFields", "hint",
        ):
            self.assertIn(key, row)

    def test_text_output_is_ascii_table(self):
        routes = api_routes(live_cards(), live_schedule(), live_details())
        code, out, _ = self.run_main(routes, chain=live_chain())
        self.assertEqual(code, 1)
        out.encode("ascii")
        self.assertIn("BUCKET", out)
        self.assertIn("COMPLETE(legacy/new)", out)
        self.assertIn("Findings: 4 (not_registered 3, overdue_final_unresolved 1)", out)
        self.assertIn("general resolve delay 86400s (gated 3600s)", out)
        self.assertIn("0x24c901d0..", out)
        self.assertIn("gcloud run jobs execute overunder-oracle", out)
        self.assertIn("not_registered: created on a different ConsensusOracle", out)
        self.assertIn("/api/v1/markets/{condition_id}/archive", out)


class CliTests(AuditTestCase):
    def test_api_unreachable_exit_2(self):
        routes = {f"{API}/api/v1/markets": urllib.error.URLError("connection refused")}
        code, out, err = self.run_main(routes)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("/api/v1/markets", err)
        routes = {f"{API}/api/v1/markets": [], f"{API}/api/v1/markets/schedule": urllib.error.HTTPError("u", 502, "x", {}, None)}
        code, _, err = self.run_main(routes)
        self.assertEqual(code, 2)
        self.assertIn("schedule", err)

    def test_usage_errors_exit_2(self):
        err = io.StringIO()
        self.assertEqual(audit_markets.main([], http_get=FakeHttp({}), stdout=io.StringIO(), stderr=err), 2)
        self.assertIn("OU_API_URL", err.getvalue())
        code, _, err_text = self.run_main({}, "--oracle", "0x1234")
        self.assertEqual(code, 2)
        self.assertIn("--oracle", err_text)
        with mock.patch("sys.stderr", io.StringIO()):
            code, _, _ = self.run_main({}, "--now", "soon")
        self.assertEqual(code, 2)

    def test_general_delay_env_and_flag_validation(self):
        routes = api_routes([card(market(cid(1), "Will v2 ship?", NOW - 2 * HOUR, 2))])
        os.environ["OU_GENERAL_RESOLVE_GATED_DELAY_SECONDS"] = "60"
        self.assertEqual(self.report(routes)["settings"]["generalGatedDelaySeconds"], 60)
        os.environ["OU_GENERAL_RESOLVE_GATED_DELAY_SECONDS"] = "soon"
        code, _, err = self.run_main(routes)
        self.assertEqual(code, 2)
        self.assertIn("OU_GENERAL_RESOLVE_GATED_DELAY_SECONDS must be an integer", err)
        del os.environ["OU_GENERAL_RESOLVE_GATED_DELAY_SECONDS"]
        for flag in ("--general-delay-seconds", "--general-gated-delay-seconds"):
            code, _, err = self.run_main(routes, flag, "-1")
            self.assertEqual(code, 2, flag)
            self.assertIn("general resolve delays must be >= 0", err)

    def test_env_defaults(self):
        os.environ["OU_API_URL"] = API + "/"
        os.environ["OU_RPC_URL"] = RPC
        os.environ["ORACLE_ADDRESS"] = ORACLE
        routes = api_routes([card(market(cid(1), close=NOW + HOUR))], [game(1, "Broncos", "Chiefs", NOW + HOUR, 3)])
        chain = FakeChain({cid(1): {"close": NOW + HOUR}})
        out = io.StringIO()
        code = audit_markets.main(["--json"], http_get=FakeHttp(routes), rpc_factory=chain.factory, now=NOW,
                                  stdout=out, stderr=io.StringIO())
        report = json.loads(out.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report["api"], API)
        self.assertTrue(report["chain"]["enabled"])
        self.assertEqual(chain.url, RPC)

    def test_rpc_url_host_only(self):
        routes = api_routes(live_cards(), live_schedule(), live_details())
        _, text, _ = self.run_main(routes, chain=live_chain())
        _, js, _ = self.run_main(routes, "--json", chain=live_chain())
        for out in (text, js):
            self.assertIn("rpc.test", out)
            self.assertNotIn("SECRETKEY", out)
            self.assertNotIn("/v2", out)
        failing = FakeChain(chain_id_error=audit_markets.RpcError(f"eth_chainId: cannot reach {RPC}"))
        code, out, err = self.run_main(routes, chain=failing)
        self.assertEqual(code, 2)
        self.assertIn("https://rpc.test", err)
        self.assertNotIn("SECRETKEY", err + out)
        self.assertEqual(audit_markets.rpc_host("https://user:pw@node.example:8443/key?x=1"), "https://node.example:8443")

    def test_oracle_getters_fail_exit_2(self):
        routes = api_routes([card(market(cid(1)))])
        out, err = io.StringIO(), io.StringIO()
        chain = FakeChain()
        code = audit_markets.main(
            ["--api", API, "--rpc", RPC, "--oracle", OTHER_ORACLE],
            http_get=FakeHttp(routes), rpc_factory=chain.factory, now=NOW, stdout=out, stderr=err,
        )
        self.assertEqual(code, 2)
        self.assertIn("ConsensusOracle getters failed", err.getvalue())
        self.assertNotIn("SECRETKEY", err.getvalue())

    def test_explicit_ctf_and_factory_skip_getters(self):
        routes = api_routes([card(market(cid(1), close=NOW + HOUR))], [game(1, "Broncos", "Chiefs", NOW + HOUR, 3)])
        chain = FakeChain({cid(1): {"close": NOW + HOUR}})
        report = self.report(routes, "--ctf", CTF, "--factory", FACTORY, chain=chain)
        self.assertEqual(report["exitCode"], 0)
        selectors = [params[0]["data"][:10] for method, params in chain.calls if method == "eth_call"]
        self.assertNotIn(SEL["ctf"], selectors)
        self.assertNotIn(SEL["factory"], selectors)


class EncodingTests(unittest.TestCase):
    def test_encode_decode(self):
        encoded = audit_markets.encode_call("0xc6836f18", CHIEFS)
        self.assertEqual(encoded, "0xc6836f18" + CHIEFS[2:])
        payout = audit_markets.encode_call(audit_markets.SELECTORS["payoutNumerators(bytes32,uint256)"], CHIEFS, 1)
        self.assertEqual(payout, "0x0504c814" + CHIEFS[2:] + "0" * 63 + "1")
        self.assertEqual(audit_markets.encode_call("0x22a9339f"), "0x22a9339f")
        self.assertEqual(audit_markets.encode_call("0xa81a2677", "0xABC"), "0xa81a2677" + "abc".rjust(64, "0"))
        with self.assertRaises(ValueError):
            audit_markets.encode_call("0xc6836f18", -1)
        with self.assertRaises(ValueError):
            audit_markets.encode_call("0xc6836f18", "0x" + "1" * 65)
        with self.assertRaises(TypeError):
            audit_markets.encode_call("0xc6836f18", True)
        self.assertEqual(audit_markets.decode_uint(_word(CHIEFS_CLOSE)), CHIEFS_CLOSE)
        self.assertTrue(audit_markets.decode_bool(_word(1)))
        self.assertFalse(audit_markets.decode_bool(_word(0)))
        self.assertEqual(audit_markets.decode_address(_addr_word(ORACLE.upper().replace("0X", "0x"))), ORACLE)
        with self.assertRaises(ValueError):
            audit_markets.decode_uint("0x")
        with self.assertRaises(ValueError):
            audit_markets.decode_bool(_word(2))

    def test_selectors_match_keccak(self):
        try:
            from eth_utils import keccak
        except ImportError:
            self.skipTest("eth_utils not installed")
        for signature, selector in audit_markets.SELECTORS.items():
            self.assertEqual("0x" + keccak(text=signature)[:4].hex(), selector, signature)

    def test_flatten_cards(self):
        orphan = market(cid(9), "Orphan wildcard?", NOW, 1, parent=cid(8))
        cards = [
            card(market(cid(1)), market(cid(2), mtype=1, parent=cid(1))),
            card(market(cid(1))),
            card(orphan),
            market(cid(3), mtype=1, parent=cid(1)),
            market(cid(4), yesPriceMicros=500000),
            "garbage",
        ]
        flat = audit_markets.flatten_cards(cards)
        self.assertEqual([m["conditionId"] for m in flat], [cid(1), cid(2), cid(9), cid(3), cid(4)])
        self.assertEqual([m["role"] for m in flat], ["primary", "child", "orphan_child", "child", "primary"])
        with self.assertRaises(ValueError):
            audit_markets.flatten_cards({"primary": {}})
        row = audit_markets.classify(flat[-1], None, None, NOW, 6 * HOUR)
        self.assertNotIn("yesPriceMicros", row)


def _load(path: Path, name: str):
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ParityTests(unittest.TestCase):
    def test_score_outcome_parity(self):
        winner = _load(REPO / "oracles" / "resolve" / "winner.py", "_ou_winner_parity")
        if winner is None:
            self.skipTest("oracles/resolve/winner.py not found")
        cases = [
            (CHIEFS_Q, "Chiefs", "Broncos", 31, 10),
            (CHIEFS_Q, "Broncos", "Chiefs", 31, 10),
            (CHIEFS_Q, "Chiefs", "Broncos", 20, 20),
            (CHIEFS_Q, "Kansas City Chiefs", "Denver Broncos", 3, 27),
            (CHIEFS_Q, "Jets", "Bills", 14, 7),
            (CHIEFS_Q, "Chiefs", "Broncos", None, 7),
            ("Chiefs vs Broncos: over 45.5 points?", "Chiefs", "Broncos", 31, 10),
            ("Will it rain?", "Chiefs", "Broncos", 1, 0),
        ]
        for case in cases:
            self.assertEqual(audit_markets.score_outcome(*case), winner.score_outcome(*case), case)
            self.assertEqual(audit_markets.yes_team(case[0]), winner.yes_team(case[0]))

    def _oracle_module(self, name: str):
        oracles = str(REPO / "oracles")
        added = oracles not in sys.path
        if added:
            sys.path.insert(0, oracles)
        try:
            return importlib.import_module(name)
        except Exception as exc:  # optional deps of the oracle package may be missing
            self.skipTest(f"{name} not importable: {exc}")
        finally:
            if added:
                sys.path.remove(oracles)

    def test_general_candidates_parity(self):
        """Audit 'general' rows are exactly oracles/resolve/general.py candidates."""
        candidates = self._oracle_module("resolve.general").candidates
        cards = live_cards() + [
            card(market(cid(1), "Will v2 ship?", NOW, 2, criteria="release notes")),
            card(market(cid(2), "Jets vs Bills: over 40.5?", NOW)),
            card(market(cid(3), "Jets vs Bills: Jets win?", NOW), market(cid(4), "Jets 3+ TDs?", NOW, 1, parent=cid(3))),
            card(market(cid(5), "Orphan wildcard?", NOW, 1, parent=cid(99))),
        ]
        expected = {item["market"]["conditionId"] for item in candidates(cards)}
        flat = audit_markets.flatten_cards(cards)
        general = {m["conditionId"] for m in flat if audit_markets.market_kind(m) == "general"}
        self.assertEqual(general, expected)
        self.assertEqual({m["conditionId"] for m in flat} - general, {CHIEFS_OLD, CHIEFS, cid(3)})

    def test_general_gate_parity(self):
        """Audit gate subjects, delays and done statuses match oracles/resolve/general.py."""
        general = self._oracle_module("resolve.general")
        scores_job = self._oracle_module("scores.job")
        self.assertEqual(general.DEFAULT_DELAY_SECONDS, audit_markets.DEFAULT_GENERAL_DELAY)
        self.assertEqual(general.DEFAULT_GATED_DELAY_SECONDS, audit_markets.DEFAULT_GENERAL_GATED_DELAY)
        self.assertEqual(set(scores_job.DONE_SCORE_STATUSES), set(audit_markets.GATE_DONE_STATUSES))
        cards = live_cards() + [
            card(market(cid(1), "Will v2 ship?", NOW, 2, criteria="release notes")),
            card(market(cid(2), "Jets vs Bills: over 40.5?", NOW)),
            card(market(cid(3), "Jets vs Bills: Jets win?", NOW), market(cid(4), "Jets 3+ TDs?", NOW, 1, parent=cid(3))),
            card(market(cid(5), "Orphan wildcard?", NOW, 1, parent=cid(99))),
            card(market(cid(6), "Lions vs Bears: Lions win?", NOW), market(cid(7), "Lions 3+ sacks?", NOW, 1)),
            card(market(cid(8), "Will the smoke finish?", NOW)),
        ]
        expected = {
            item["market"]["conditionId"]: general.gate_subject(item["market"], item["parentConditionId"])
            for item in general.candidates(cards)
        }
        flat = {m["conditionId"]: m for m in audit_markets.flatten_cards(cards)}
        self.assertEqual({c: audit_markets.gate_subject(flat[c]) for c in expected}, expected)
        self.assertEqual(expected[cid(7)], (cid(6), "parent"))
        self.assertEqual(expected[cid(2)], (cid(2), "event"))
        self.assertIsNone(expected[cid(8)])

    def test_week_complete_parity(self):
        listing = self._oracle_module("listing.run")
        if not hasattr(listing, "DONE_STATUSES"):
            self.skipTest("listing.run predates the step-10 week rule")
        self.assertEqual(listing.MIN_LEAD_SECONDS, audit_markets.MIN_LEAD_SECONDS)
        grace = getattr(listing, "DEFAULT_STALE_GRACE_SECONDS", None)
        if grace is not None:
            self.assertEqual(grace, audit_markets.DEFAULT_LISTING_STALE_GRACE)
        clocks = (None, NOW, NOW + 20 * HOUR) if grace is not None else (None,)
        base = [
            game(1, "Broncos", "Chiefs", NOW, 3, "final", listed=cid(1)),
            game(2, "Lions", "Bills", NOW, 3, "final"),
        ]
        for status in ("final", "postponed", "cancelled", "scheduled", "in_progress"):
            for live in (None, "final", "cancelled", "in_progress"):
                for clock in clocks:
                    rows = copy.deepcopy(base)
                    rows[0]["status"] = status
                    rows[1]["status"] = status
                    live_status = {cid(1): live}
                    ours = audit_markets.week_complete_new(rows, live_status, clock)
                    theirs = (
                        listing.week_complete(rows, live_status, clock)
                        if grace is not None
                        else listing.week_complete(rows, live_status)
                    )
                    self.assertEqual(ours, theirs, (status, live, clock))

    def test_winner_question_parity(self):
        questions = _load(REPO / "oracles" / "listing" / "questions.py", "_ou_questions_parity")
        if questions is None:
            self.skipTest("oracles/listing/questions.py not found")
        self.assertEqual(audit_markets.winner_question("Chiefs", "Broncos"), questions.winner_question("Chiefs", "Broncos"))
        self.assertEqual(audit_markets.winner_question("Chiefs", "Broncos"), CHIEFS_Q)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output clean
        pass

    def _send(self, status: int, body) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.server.seen_headers = dict(self.headers)
        if self.path == "/api/v1/markets":
            self._send(200, live_cards())
        else:
            self._send(404, {"detail": "market not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if body["method"] == "eth_chainId":
            self._send(200, {"jsonrpc": "2.0", "id": body["id"], "result": "0x7a69"})
        elif body["method"] == "eth_call" and body["params"][0]["data"].startswith(SEL["closeTime"]):
            self._send(200, {"jsonrpc": "2.0", "id": body["id"], "result": _word(CHIEFS_CLOSE)})
        else:
            message = f"method not allowed at {self.path}"
            self._send(200, {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601, "message": message}})


class LocalServerTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_fetch_json_local_server(self):
        cards = audit_markets.fetch_json(f"{self.base}/api/v1/markets", 5)
        self.assertEqual(cards, live_cards())
        self.assertEqual(self.server.seen_headers.get("User-Agent"), audit_markets.USER_AGENT)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            audit_markets.fetch_json(f"{self.base}/api/v1/markets/0xdead", 5)
        self.assertEqual(ctx.exception.code, 404)

    def test_json_rpc_transport_local_server(self):
        url = f"{self.base}/v2/SECRETKEY"
        self.assertEqual(audit_markets.rpc_chain_id(url, 5), 31337)
        rpc = audit_markets.make_rpc(url, 5)
        reader = audit_markets.ChainReader(rpc, ORACLE, ctf=CTF, factory=FACTORY)
        self.assertEqual(reader.close_time(CHIEFS), CHIEFS_CLOSE)
        with self.assertRaises(audit_markets.RpcError) as ctx:
            reader.oracle_resolved(CHIEFS)
        self.assertIn("method not allowed", str(ctx.exception))
        self.assertNotIn("SECRETKEY", str(ctx.exception))

    def test_transport_connection_error_is_scrubbed(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        with self.assertRaises(audit_markets.RpcError) as ctx:
            audit_markets.rpc_chain_id(f"http://127.0.0.1:{port}/v2/SECRETKEY", 2)
        self.assertNotIn("SECRETKEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
