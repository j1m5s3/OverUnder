import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_account import Account
from web3.exceptions import ContractLogicError

from consensus.coordinator import Coordinator
from consensus.eip712 import sign_attestation
from resolve import chain as chain_mod
from resolve import fallback as fallback_mod
from resolve import run as resolve_run

CID = "0x" + "ab" * 32
QUESTION = "Chiefs vs Broncos: Chiefs win?"
ORACLE = "0x" + "99" * 20
WINDOW = 86400
KEYS = {"alpha": "0x" + "01" * 32, "beta": "0x" + "02" * 32, "gamma": "0x" + "03" * 32}
OPERATOR_KEY = "0x" + "04" * 32
ADDRS = {name: Account.from_key(key).address for name, key in KEYS.items()}
EVIDENCE = {"alpha": "0x" + "a1" * 32, "beta": "0x" + "b2" * 32, "gamma": "0x" + "c3" * 32}
ORACLES_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("OU_FALLBACK_POLICY", raising=False)
    monkeypatch.delenv("OU_RESOLVE_MAX_MARKETS", raising=False)
    monkeypatch.setenv("OU_API_URL", "http://api.test")
    monkeypatch.setenv("ORACLE_ADDRESS", ORACLE)
    monkeypatch.setenv("CHAIN_ID", "84532")


def _card(cid=CID, close_time=None):
    primary = {"conditionId": cid, "question": QUESTION, "marketType": 0}
    if close_time is not None:
        primary["closeTime"] = close_time
    return {"primary": primary, "children": []}


def _detail(*, status="final", resolved=False, close_time=1, home=27, away=24):
    return {
        "conditionId": CID,
        "question": QUESTION,
        "resolved": resolved,
        "closeTime": close_time,
        "score": {
            "homeLabel": "Chiefs",
            "awayLabel": "Broncos",
            "homeScore": home,
            "awayScore": away,
            "status": status,
        },
    }


def _slots(**submitted):
    """Chain agent slots keyed by lowercase address; submitted=name->outcome."""
    return {
        addr.lower(): {"submitted": name in submitted, "outcome": submitted.get(name, 0)}
        for name, addr in ADDRS.items()
    }


class FakeChain:
    def __init__(
        self,
        resolved=False,
        close=1,
        outcome=0,
        config_ok=True,
        now=1 + WINDOW,
        agents=None,
        votes=(0, 0),
        preflight=(True, ""),
        fail_attest=False,
        fail_submit=False,
        resolved_seq=None,
        fail_fallback=False,
        attest_race=None,
    ):
        self.resolved = resolved
        self.close = close
        self.outcome = outcome
        self.config_ok = config_ok
        self.now = now
        self.agents = agents if agents is not None else _slots()
        self.votes = votes
        self.preflight = preflight
        self.fail_attest = fail_attest
        self.fail_submit = fail_submit
        # resolved_seq: successive is_resolved() answers (last one repeats) to simulate a send race.
        self.resolved_seq = list(resolved_seq) if resolved_seq is not None else None
        self.fail_fallback = fail_fallback
        # attest_race: agent slots fallback_state returns after a 'dup attestation' revert.
        self.attest_race = attest_race
        self.resolved_calls = 0
        self.submits = []
        self.attests = []
        self.fallbacks = []
        self.arbitrations = []
        self.config_calls = 0

    def config_check(self):
        self.config_calls += 1
        problems = [] if self.config_ok else ["AGENT_BETA_KEY address 0xbad is not an oracle agent"]
        return {"ok": self.config_ok, "problems": problems}

    def is_resolved(self, cid):
        self.resolved_calls += 1
        if self.resolved_seq:
            value = self.resolved_seq.pop(0) if len(self.resolved_seq) > 1 else self.resolved_seq[0]
            self.resolved = value
            return value
        return self.resolved

    def onchain_close_time(self, cid):
        return self.close

    def onchain_outcome(self, cid):
        return self.outcome

    def chain_now(self):
        return self.now

    def fallback_state(self, cid):
        return {
            "resolved": self.resolved,
            "closeTime": self.close,
            "now": self.now,
            "window": WINDOW,
            "agents": self.agents,
            "votes": self.votes,
        }

    def submit_consensus(self, *args):
        if self.fail_submit:
            raise RuntimeError("submitConsensus preflight reverted: conflict")
        self.submits.append(args)
        return "0xtx"

    def submit_attestation(self, cid, outcome, evidence, deadline, sig):
        if self.attest_race is not None:
            # First send loses the race: the slot landed from another tick.
            self.agents, self.attest_race = self.attest_race, None
            raise RuntimeError("submitAttestation preflight reverted: dup attestation")
        if self.fail_attest:
            raise RuntimeError("submitAttestation preflight reverted: bad sig")
        self.attests.append((cid, outcome, evidence, deadline, sig))
        return "0xatt"

    def preflight_fallback(self, cid):
        return self.preflight

    def resolve_fallback(self, cid):
        if self.fail_fallback:
            raise RuntimeError("resolveFallback preflight reverted: resolved")
        self.fallbacks.append(cid)
        return "0xfb"

    def resolve_arbitrated(self, cid, outcome):
        self.arbitrations.append((cid, outcome))
        return "0xarb"


class FakePub:
    def __init__(self):
        self.atts = []
        self.research = []
        self.resolved = []

    def persist_attestations(self, cid, reports):
        self.atts.append((cid, reports))

    def persist_research(self, cid, reports, reason):
        self.research.append((cid, reason, reports))

    def mark_resolved(self, cid, outcome):
        self.resolved.append((cid, outcome))


def report(agent, outcome, confidence=0.9):
    return {"agent": agent, "outcome": outcome, "confidence": confidence, "summary": "ok", "evidenceHash": EVIDENCE[agent]}


class FakeCoord:
    keys = KEYS

    def __init__(self, unanimous=True, outcome=0, missing_key=False, reports=None, confidence=0.9):
        self.unanimous = unanimous
        self.outcome = outcome
        self.missing_key = missing_key
        self.confidence = confidence
        self.reports = reports
        self.signed = False
        self.signed_one = []
        self.runs = []
        self.calls = []

    def run(self, question, context=None, as_of=None, kickoff=None):
        self.runs.append(question)
        self.calls.append({"question": question, "context": context, "as_of": as_of, "kickoff": kickoff})
        if self.reports is not None:
            outcomes = {r["outcome"] for r in self.reports}
            unanimous = len(outcomes) == 1
            return {"unanimous": unanimous, "outcome": self.reports[0]["outcome"] if unanimous else None, "reports": self.reports}
        other = 1 - self.outcome
        reports = [
            report("alpha", self.outcome, self.confidence),
            report("beta", self.outcome, self.confidence),
            report("gamma", self.outcome if self.unanimous else other, self.confidence),
        ]
        return {"unanimous": self.unanimous, "outcome": self.outcome if self.unanimous else None, "reports": reports}

    def sign_unanimous(self, *args, **kwargs):
        if self.missing_key:
            raise RuntimeError("missing key for alpha")
        self.signed = True
        return 99, [b"\x00" * 65] * 3

    def agent_address(self, name):
        if self.missing_key:
            raise RuntimeError(f"missing key for {name}")
        return ADDRS[name]

    def sign_one(self, name, oracle, chain_id, condition_id, evidence_hash, outcome, deadline):
        self.signed_one.append((name, outcome, evidence_hash, deadline))
        return b"\x01" * 65


def _http(detail, cards=None):
    payloads = {
        "http://api.test/api/v1/markets": cards if cards is not None else [_card()],
        f"http://api.test/api/v1/markets/{CID}": detail,
    }

    def http_get(url: str):
        return payloads[url]

    return http_get


def _run(detail, coord, chain=None, publisher=None, now=100, policy=None):
    return resolve_run.run(
        http_get=_http(detail),
        coordinator_factory=lambda: coord,
        chain=chain or FakeChain(),
        publisher=publisher or FakePub(),
        now=now,
        fallback_policy=policy,
    )


def _split():
    """Research alpha 0, beta 0, gamma 1 (score-derived outcome is 0)."""
    return FakeCoord(reports=[report("alpha", 0), report("beta", 0), report("gamma", 1)])


# --- existing dual gate -----------------------------------------------------


def test_not_final_does_not_sign(monkeypatch):
    monkeypatch.setenv("OU_ORACLE_MOCK", "1")
    coord = FakeCoord()
    chain = FakeChain()
    summary = _run(_detail(status="in_progress"), coord, chain=chain)
    assert chain.submits == []
    assert coord.signed is False
    assert summary["results"][0]["submitted"] is False
    assert summary["results"][0]["reason"] == "not final"


def test_not_closed_does_not_submit():
    coord = FakeCoord()
    chain = FakeChain(close=500)
    summary = _run(_detail(close_time=500), coord, chain=chain, now=100)
    assert chain.submits == []
    assert summary["results"][0]["reason"] == "not closed"


def test_research_mismatch_does_not_submit():
    coord = FakeCoord(unanimous=True, outcome=1)
    chain = FakeChain(now=100)
    summary = _run(_detail(home=27, away=24), coord, chain=chain, now=100)
    assert chain.submits == []
    assert summary["results"][0]["reason"] == "research mismatch"
    assert summary["results"][0]["fallbackAt"] == 1 + WINDOW


def test_missing_agent_key_does_not_submit(monkeypatch):
    monkeypatch.delenv("AGENT_ALPHA_KEY", raising=False)
    coord = FakeCoord(missing_key=True)
    chain = FakeChain()
    summary = _run(_detail(), coord, chain=chain, now=100)
    assert chain.submits == []
    assert summary["results"][0]["ok"] is False


def test_unanimous_matching_score_submits(monkeypatch):
    monkeypatch.setenv("OU_ORACLE_MOCK", "1")
    coord = FakeCoord()
    chain = FakeChain()
    pub = FakePub()
    summary = _run(_detail(), coord, chain=chain, publisher=pub, now=100)
    assert len(chain.submits) == 1
    assert summary["ok"] is True
    assert summary["results"][0]["submitted"] is True
    assert summary["results"][0]["path"] == "consensus"
    assert pub.atts and pub.resolved


def test_consensus_send_failure_sets_txerror():
    chain = FakeChain(fail_submit=True)
    pub = FakePub()
    summary = _run(_detail(), FakeCoord(), chain=chain, publisher=pub)
    result = summary["results"][0]
    assert result["ok"] is False and result["txError"] is True
    assert "conflict" in result["error"]
    assert pub.resolved == []
    assert summary["ok"] is False


def test_cap_skips_not_final_still_resolves_later(monkeypatch):
    monkeypatch.setenv("OU_RESOLVE_MAX_MARKETS", "1")
    live = "0x" + "11" * 32
    done = CID
    payloads = {
        "http://api.test/api/v1/markets": [_card(live), _card(done)],
        f"http://api.test/api/v1/markets/{live}": _detail(status="in_progress"),
        f"http://api.test/api/v1/markets/{done}": _detail(),
    }

    coord = FakeCoord()
    chain = FakeChain()
    summary = resolve_run.run(http_get=payloads.__getitem__, coordinator_factory=lambda: coord, chain=chain, publisher=FakePub(), now=100)
    assert len(chain.submits) == 1
    assert summary["attempted"] == 1


def test_onchain_resolved_mirrors_sqlite():
    coord = FakeCoord()
    chain = FakeChain(resolved=True)
    pub = FakePub()
    summary = _run(_detail(resolved=False), coord, chain=chain, publisher=pub, now=100)
    assert chain.submits == []
    assert pub.resolved == [(CID, 0)]
    assert summary["results"][0]["reason"] == "already resolved"


def test_non_winner_sports_question_left_to_general():
    card = {"primary": {"conditionId": CID, "question": "Chiefs vs Broncos: over 45.5 points?", "marketType": 0}, "children": []}
    coord = FakeCoord()
    summary = resolve_run.run(http_get=_http(_detail(), cards=[card]), coordinator_factory=lambda: coord, chain=FakeChain(), publisher=FakePub(), now=100)
    assert summary["results"] == []
    assert resolve_run.is_dual_gate_primary(_card()) is True
    assert resolve_run.is_dual_gate_primary(card) is False


# --- step 8: config guard and not registered --------------------------------


def test_config_mismatch_submits_nothing():
    calls = []

    class Tracking(FakeCoord):
        def run(self, question, **kwargs):
            calls.append(question)
            return super().run(question, **kwargs)

    coord = Tracking()
    chain = FakeChain(config_ok=False)
    summary = _run(_detail(), coord, chain=chain)
    assert chain.submits == []
    assert coord.signed is False
    assert calls == []
    assert summary["results"][0]["reason"] == "config mismatch"
    assert summary["ok"] is False
    assert summary["config"]["problems"]


def test_config_mismatch_still_mirrors():
    chain = FakeChain(resolved=True, config_ok=False)
    pub = FakePub()
    _run(_detail(resolved=False), FakeCoord(), chain=chain, publisher=pub)
    assert pub.resolved == [(CID, 0)]


def test_not_registered_skipped():
    coord = FakeCoord()
    chain = FakeChain(close=0)
    summary = _run(_detail(), coord, chain=chain)
    assert summary["results"][0]["reason"] == "not registered"
    assert summary["notRegistered"] == [CID]
    assert coord.runs == []
    assert chain.submits == []
    assert summary["ok"] is True


def test_compare_config_ok_and_mismatch():
    expected = {name: ADDRS[name] for name in KEYS}
    expected["operator"] = Account.from_key(OPERATOR_KEY).address
    onchain = [ADDRS["gamma"].lower(), ADDRS["alpha"], ADDRS["beta"]]
    assert chain_mod.compare_config(expected, onchain, expected["operator"]) == []

    stranger = Account.from_key("0x" + "05" * 32).address
    problems = chain_mod.compare_config(expected, [ADDRS["alpha"], stranger, ADDRS["gamma"]], expected["operator"])
    assert len(problems) == 1
    assert "AGENT_BETA_KEY" in problems[0]
    assert "02" * 32 not in problems[0]

    problems = chain_mod.compare_config(expected, onchain, stranger)
    assert len(problems) == 1 and "oracle.operator()" in problems[0]

    dup = dict(expected, gamma=ADDRS["alpha"])
    assert "duplicate agent keys" in chain_mod.compare_config(dup, onchain, expected["operator"])

    missing = dict(expected, beta=None)
    assert chain_mod.compare_config(missing, onchain, expected["operator"]) == ["AGENT_BETA_KEY missing or invalid"]


def _set_keys(monkeypatch, beta=KEYS["beta"]):
    monkeypatch.setenv("AGENT_ALPHA_KEY", KEYS["alpha"])
    monkeypatch.setenv("AGENT_BETA_KEY", beta)
    monkeypatch.setenv("AGENT_GAMMA_KEY", KEYS["gamma"] + "\n")
    monkeypatch.setenv("OPERATOR_PRIVATE_KEY", OPERATOR_KEY)


def _fake_contract(monkeypatch, *, chain_id=84532, code=b"\x01", agents=None, operator=None):
    agents = agents or [ADDRS["alpha"], ADDRS["beta"], ADDRS["gamma"]]
    operator = operator or Account.from_key(OPERATOR_KEY).address
    call = lambda value: SimpleNamespace(call=lambda *a, **k: value)  # noqa: E731
    functions = SimpleNamespace(agents=lambda i: call(agents[i]), operator=lambda: call(operator))
    w3 = SimpleNamespace(eth=SimpleNamespace(chain_id=chain_id, get_code=lambda addr: code))
    contract = SimpleNamespace(address=ORACLE, functions=functions)
    monkeypatch.setattr(chain_mod, "_contract", lambda w3_arg=None: (w3, contract))


def test_config_check_ok(monkeypatch):
    _set_keys(monkeypatch)
    _fake_contract(monkeypatch)
    result = chain_mod.config_check()
    assert result["ok"] is True and result["problems"] == []


def test_config_check_chain_id_mismatch(monkeypatch):
    _set_keys(monkeypatch)
    _fake_contract(monkeypatch, chain_id=1)
    result = chain_mod.config_check()
    assert result["ok"] is False
    assert any("CHAIN_ID" in p for p in result["problems"])


def test_config_check_no_code(monkeypatch):
    _set_keys(monkeypatch)
    _fake_contract(monkeypatch, code=b"")
    result = chain_mod.config_check()
    assert result["ok"] is False
    assert result["problems"] == ["no contract code at ORACLE_ADDRESS"]


def test_config_check_invalid_key_never_leaks(monkeypatch):
    _set_keys(monkeypatch, beta="0xnot-a-key-SENTINEL")
    _fake_contract(monkeypatch)
    result = chain_mod.config_check()
    assert "AGENT_BETA_KEY missing or invalid" in result["problems"]
    assert "SENTINEL" not in json.dumps(result)


def _backend_abi(name):
    path = ORACLES_ROOT.parent / "backend" / "app" / "abi" / f"{name}.json"
    if not path.exists():
        pytest.skip(f"backend ABI {name} not present (oracle image)")
    return {e["name"]: e for e in json.loads(path.read_text(encoding="utf-8")) if e.get("name")}


@pytest.mark.parametrize("name", ["ConsensusOracle", "ConditionalTokens"])
def test_trimmed_abi_matches_backend(name):
    backend = _backend_abi(name)
    trimmed = json.loads((ORACLES_ROOT / "abi" / f"{name}.json").read_text(encoding="utf-8"))
    assert trimmed
    for entry in trimmed:
        assert entry == backend[entry["name"]], entry["name"]


def test_trimmed_abi_has_fallback_entries():
    names = {e["name"] for e in json.loads((ORACLES_ROOT / "abi" / "ConsensusOracle.json").read_text(encoding="utf-8"))}
    assert {
        "agents", "operator", "ctf", "isAgent", "submitAttestation", "resolveFallback", "resolveArbitrated",
        "WINDOW", "agentSubmitted", "agentOutcome", "attestationCount", "voteWeight",
    } <= names


# --- step 9: mirror and ordering --------------------------------------------


def test_mirror_chain_resolved_without_final_score():
    coord = FakeCoord()
    chain = FakeChain(resolved=True, outcome=1)
    pub = FakePub()
    summary = _run(_detail(status="in_progress"), coord, chain=chain, publisher=pub)
    result = summary["results"][0]
    assert pub.resolved == [(CID, 1)]
    assert result["mirrored"] is True and result["outcome"] == 1
    assert coord.runs == []
    assert chain.submits == []


def test_mirror_payout_unreadable():
    chain = FakeChain(resolved=True, outcome=None)
    pub = FakePub()
    summary = _run(_detail(), FakeCoord(), chain=chain, publisher=pub)
    result = summary["results"][0]
    assert pub.resolved == []
    assert result["ok"] is False
    assert result["reason"] == "chain resolved, payout unreadable"
    assert summary["ok"] is True


def test_db_and_chain_resolved_skips():
    pub = FakePub()
    summary = _run(_detail(resolved=True), FakeCoord(), chain=FakeChain(resolved=True), publisher=pub)
    assert summary["results"][0]["reason"] == "already resolved"
    assert pub.resolved == []


def test_resolve_oldest_close_first(monkeypatch):
    monkeypatch.setenv("OU_RESOLVE_MAX_MARKETS", "1")
    cids = {300: "0x" + "33" * 32, 100: "0x" + "11" * 32, 200: "0x" + "22" * 32}
    payloads = {"http://api.test/api/v1/markets": [_card(cids[c], close_time=c) for c in (300, 100, 200)]}
    for close, cid in cids.items():
        payloads[f"http://api.test/api/v1/markets/{cid}"] = dict(_detail(close_time=close), conditionId=cid)
    chain = FakeChain()
    summary = resolve_run.run(http_get=payloads.__getitem__, coordinator_factory=FakeCoord, chain=chain, publisher=FakePub(), now=1000)
    assert [s[0] for s in chain.submits] == [cids[100]]
    assert [r["conditionId"] for r in summary["results"]] == [cids[100], cids[200], cids[300]]
    assert [r["reason"] for r in summary["results"][1:]] == ["capped", "capped"]


def test_payout_outcome():
    assert chain_mod.payout_outcome(1, 0) == 0
    assert chain_mod.payout_outcome(0, 1) == 1
    assert chain_mod.payout_outcome(0, 0) is None
    assert chain_mod.payout_outcome(1, 1) is None


def test_onchain_outcome_reads_ctf(monkeypatch):
    ctf_addr = "0x" + "77" * 20
    call = lambda value: SimpleNamespace(call=lambda *a, **k: value)  # noqa: E731
    denominators = {"value": 1}
    ctf = SimpleNamespace(
        functions=SimpleNamespace(
            payoutDenominator=lambda cid: call(denominators["value"]),
            payoutNumerators=lambda cid, i: call([0, 1][i]),
        )
    )
    w3 = SimpleNamespace(eth=SimpleNamespace(contract=lambda address, abi: ctf))
    oracle = SimpleNamespace(functions=SimpleNamespace(ctf=lambda: call(ctf_addr)))
    monkeypatch.setattr(chain_mod, "_contract", lambda w3_arg=None: (w3, oracle))
    assert chain_mod.onchain_outcome(CID) == 1
    denominators["value"] = 0
    assert chain_mod.onchain_outcome(CID) is None


def test_send_preflight_revert_names_reason(monkeypatch):
    monkeypatch.setenv("OPERATOR_PRIVATE_KEY", OPERATOR_KEY)

    def reverting(*args, **kwargs):
        raise ContractLogicError("execution reverted: window")

    fn = SimpleNamespace(call=reverting, build_transaction=lambda tx: pytest.fail("must not build after revert"))
    with pytest.raises(RuntimeError, match="resolveFallback preflight reverted: window"):
        chain_mod._send(SimpleNamespace(), Account.from_key(OPERATOR_KEY), fn, 300_000, "resolveFallback")
    assert chain_mod._reason(ContractLogicError("execution reverted")) == "reverted"


# --- step 13: 24h fallback --------------------------------------------------


def test_fallback_policy_parse(monkeypatch):
    monkeypatch.delenv("OU_FALLBACK_POLICY", raising=False)
    assert fallback_mod.fallback_policy() == "attest"
    monkeypatch.setenv("OU_FALLBACK_POLICY", " ATTEST ")
    assert fallback_mod.fallback_policy() == "attest"
    monkeypatch.setenv("OU_FALLBACK_POLICY", "manual")
    assert fallback_mod.fallback_policy() == "manual"
    monkeypatch.setenv("OU_FALLBACK_POLICY", "arbitrate")
    assert fallback_mod.fallback_policy() == "arbitrate"
    monkeypatch.setenv("OU_FALLBACK_POLICY", "bogus")
    with pytest.raises(RuntimeError, match="OU_FALLBACK_POLICY"):
        fallback_mod.fallback_policy()
    with pytest.raises(RuntimeError, match="OU_FALLBACK_POLICY"):
        _run(_detail(), FakeCoord(), policy=None)
    with pytest.raises(RuntimeError, match="OU_FALLBACK_POLICY"):
        _run(_detail(), FakeCoord(), policy="yolo")


def test_default_policy_attests_past_window(monkeypatch):
    monkeypatch.delenv("OU_FALLBACK_POLICY", raising=False)
    chain = FakeChain()
    summary = _run(_detail(), _split(), chain=chain)
    assert summary["policy"] == "attest"
    assert chain.fallbacks == [CID]


def test_manual_policy_never_attests():
    chain = FakeChain()
    summary = _run(_detail(), _split(), chain=chain, policy="manual")
    assert chain.attests == [] and chain.fallbacks == [] and chain.arbitrations == []
    assert summary["results"][0]["reason"] == "research mismatch"


def test_attest_before_window_no_calls():
    chain = FakeChain(now=1 + 100)
    summary = _run(_detail(), _split(), chain=chain, policy="attest")
    assert chain.attests == [] and chain.fallbacks == []
    assert summary["results"][0]["reason"] == "research mismatch"


def test_attest_two_matching_resolves_fallback():
    chain = FakeChain()
    coord = _split()
    pub = FakePub()
    summary = _run(_detail(), coord, chain=chain, publisher=pub, policy="attest")
    assert [(a[1], a[2]) for a in chain.attests] == [
        (0, bytes.fromhex(EVIDENCE["alpha"][2:])),
        (0, bytes.fromhex(EVIDENCE["beta"][2:])),
    ]
    assert all(a[3] == chain.now + 3600 for a in chain.attests)
    assert [s[0] for s in coord.signed_one] == ["alpha", "beta"]
    assert chain.fallbacks == [CID]
    assert pub.resolved == [(CID, 0)]
    result = summary["results"][0]
    assert result["path"] == "fallback" and result["submitted"] is True
    assert result["attested"] == ["alpha", "beta"]
    assert summary["ok"] is True


def test_attest_skips_already_submitted():
    chain = FakeChain(agents=_slots(alpha=0))
    coord = _split()
    _run(_detail(), coord, chain=chain, policy="attest")
    assert [s[0] for s in coord.signed_one] == ["beta"]
    assert chain.fallbacks == [CID]


def test_attest_one_match_reports_no_majority():
    chain = FakeChain()
    coord = FakeCoord(reports=[report("alpha", 0), report("beta", 1), report("gamma", 1)])
    summary = _run(_detail(), coord, chain=chain, policy="attest")
    assert len(chain.attests) == 1 and chain.attests[0][1] == 0
    assert chain.fallbacks == [] and chain.arbitrations == []
    assert summary["results"][0]["reason"] == "no agent majority"


def test_arbitrate_one_match_arbitrates():
    chain = FakeChain()
    pub = FakePub()
    coord = FakeCoord(reports=[report("alpha", 0), report("beta", 1), report("gamma", 1)])
    summary = _run(_detail(), coord, chain=chain, publisher=pub, policy="arbitrate")
    assert chain.arbitrations == [(CID, 0)]
    assert summary["results"][0]["path"] == "arbitrated"
    assert pub.resolved == [(CID, 0)]


def test_votes_force_arbitration():
    chain = FakeChain(votes=(10, 90))
    summary = _run(_detail(), _split(), chain=chain, policy="attest")
    assert summary["results"][0]["reason"] == "arbitration required"
    assert chain.fallbacks == [] and chain.arbitrations == []

    chain = FakeChain(votes=(10, 90))
    _run(_detail(), _split(), chain=chain, policy="arbitrate")
    assert chain.arbitrations == [(CID, 0)]
    assert chain.fallbacks == []


def test_majority_conflicts_score_no_action():
    chain = FakeChain(agents=_slots(alpha=1, beta=1))
    summary = _run(_detail(), _split(), chain=chain, policy="arbitrate")
    assert chain.fallbacks == [] and chain.arbitrations == [] and chain.attests == []
    assert summary["results"][0]["reason"] == "agent majority conflicts score"


def test_never_attests_non_derived_outcome():
    chain = FakeChain()
    coord = FakeCoord(reports=[report("alpha", 1), report("beta", 1), report("gamma", 0)])
    _run(_detail(), coord, chain=chain, policy="attest")
    assert [a[1] for a in chain.attests] == [0]
    assert [s[0] for s in coord.signed_one] == ["gamma"]


def test_fallback_preflight_revert_reported():
    chain = FakeChain(preflight=(False, "window"))
    summary = _run(_detail(), _split(), chain=chain, policy="attest")
    assert chain.fallbacks == []
    assert summary["results"][0]["reason"] == "window"


def test_fallback_blocked_by_config_mismatch():
    chain = FakeChain(config_ok=False)
    _run(_detail(), _split(), chain=chain, policy="attest")
    assert chain.attests == [] and chain.fallbacks == []


def test_fallback_send_failure_sets_txerror():
    chain = FakeChain(fail_attest=True)
    pub = FakePub()
    summary = _run(_detail(), _split(), chain=chain, publisher=pub, policy="attest")
    result = summary["results"][0]
    assert result["ok"] is False and result["txError"] is True
    assert pub.resolved == []
    assert summary["ok"] is False


def test_fallback_missing_agent_key_noted():
    chain = FakeChain()
    coord = _split()
    coord.missing_key = True
    summary = _run(_detail(), coord, chain=chain, policy="attest")
    result = summary["results"][0]
    assert chain.attests == []
    assert result["reason"] == "no agent majority"
    assert any("missing key" in n for n in result["notes"])


def test_sign_one_matches_sign_attestation(monkeypatch):
    for name, key in KEYS.items():
        monkeypatch.setenv(f"AGENT_{name.upper()}_KEY", key + "\n")
    coord = Coordinator(agents=[object()])
    cid = bytes.fromhex(CID[2:])
    ev = bytes.fromhex(EVIDENCE["alpha"][2:])
    assert coord.sign_one("alpha", ORACLE, 84532, cid, ev, 0, 99) == sign_attestation(KEYS["alpha"], ORACLE, 84532, cid, 0, ev, 99)
    assert coord.agent_address("beta") == ADDRS["beta"]
    monkeypatch.setenv("AGENT_GAMMA_KEY", "")
    with pytest.raises(RuntimeError, match="missing key for gamma"):
        Coordinator(agents=[object()]).agent_address("gamma")


# --- chain clock (anvil time travel / wall clock behind the chain) ----------


class ClockCoord(FakeCoord):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sign_now = []

    def sign_unanimous(self, *args, now=None, **kwargs):
        self.sign_now.append(now)
        return super().sign_unanimous(*args, **kwargs)


def test_chain_clock_ahead_of_wall_closes_and_signs_on_chain_time(monkeypatch):
    import time as time_mod

    wall = 1_000_000
    monkeypatch.setattr(time_mod, "time", lambda: wall)
    coord = ClockCoord()
    chain = FakeChain(close=wall + 500, now=wall + 900)
    summary = _run(_detail(close_time=wall + 500), coord, chain=chain, now=None)
    assert summary["results"][0]["submitted"] is True
    assert len(chain.submits) == 1
    assert coord.sign_now == [wall + 900]


def test_wall_clock_ahead_of_chain_still_uses_wall(monkeypatch):
    import time as time_mod

    wall = 1_000_000
    monkeypatch.setattr(time_mod, "time", lambda: wall)
    coord = ClockCoord()
    chain = FakeChain(close=wall - 10, now=wall - 50)
    summary = _run(_detail(close_time=wall - 10), coord, chain=chain, now=None)
    assert summary["results"][0]["submitted"] is True
    assert coord.sign_now == [wall]


def test_chain_clock_behind_close_stays_not_closed(monkeypatch):
    import time as time_mod

    wall = 1_000_000
    monkeypatch.setattr(time_mod, "time", lambda: wall)
    coord = ClockCoord()
    chain = FakeChain(close=wall + 500, now=wall + 100)
    summary = _run(_detail(close_time=wall + 500), coord, chain=chain, now=None)
    assert summary["results"][0]["reason"] == "not closed"
    assert coord.runs == []


def test_sign_unanimous_deadline_from_given_clock(monkeypatch):
    for name, key in KEYS.items():
        monkeypatch.setenv(f"AGENT_{name.upper()}_KEY", key)
    coord = Coordinator(agents=[])
    deadline, sigs = coord.sign_unanimous(ORACLE, 84532, b"\xab" * 32, b"\xcd" * 32, 0, now=5_000)
    assert deadline == 5_000 + 3600
    assert len(sigs) == 3



# --- research cooldown and send races (sports) --------------------------------


def _http_with(detail, extra):
    payloads = {"http://api.test/api/v1/markets": [_card()], f"http://api.test/api/v1/markets/{CID}": detail, **extra}
    return payloads.__getitem__


def test_sports_research_mismatch_persisted_then_cooldown(monkeypatch):
    from datetime import datetime, timezone

    pub = FakePub()
    chain = FakeChain(now=100)
    summary = _run(_detail(), FakeCoord(unanimous=True, outcome=1), chain=chain, publisher=pub, now=100)
    assert summary["results"][0]["reason"] == "research mismatch"
    assert [(cid, reason) for cid, reason, _ in pub.research] == [(CID, "research mismatch")]

    recent = datetime.fromtimestamp(100 - 60, tz=timezone.utc).isoformat()
    status = {f"http://api.test/api/v1/oracle/{CID}/status": {"attestations": [{"agent": "alpha", "createdAt": recent}, {"agent": "beta", "createdAt": None}]}}
    coord = FakeCoord()
    summary = resolve_run.run(
        http_get=_http_with(_detail(), status), coordinator_factory=lambda: coord, chain=FakeChain(), publisher=FakePub(), now=100
    )
    result = summary["results"][0]
    assert result["reason"] == "research cooldown" and result["retryAt"] == 40 + 21600
    assert coord.runs == [] and summary["attempted"] == 0


def test_sports_resolved_concurrently_before_submit():
    chain = FakeChain(resolved_seq=[False, True], outcome=0)
    pub = FakePub()
    summary = _run(_detail(), FakeCoord(), chain=chain, publisher=pub)
    result = summary["results"][0]
    assert result["reason"] == "resolved concurrently"
    assert chain.submits == [] and pub.resolved == [(CID, 0)]
    assert summary["ok"] is True


def test_sports_submit_race_not_tx_error():
    from job import exit_code

    chain = FakeChain(fail_submit=True, resolved_seq=[False, False, True], outcome=0)
    pub = FakePub()
    summary = _run(_detail(), FakeCoord(), chain=chain, publisher=pub)
    result = summary["results"][0]
    assert "txError" not in result and result["reason"] == "resolved concurrently"
    assert pub.resolved == [(CID, 0)]
    assert exit_code({"resolve": summary}) == 0


def test_sports_fallback_dup_attestation_race():
    chain = FakeChain(attest_race=_slots(alpha=0))
    coord = _split()
    pub = FakePub()
    summary = _run(_detail(), coord, chain=chain, publisher=pub, policy="attest")
    result = summary["results"][0]
    assert "txError" not in result
    assert result["path"] == "fallback" and chain.fallbacks == [CID]
    assert result["attested"] == ["beta"] and result["supporters"] == ["alpha", "beta"]
    assert [r["agent"] for r in pub.atts[0][1]] == ["alpha", "beta"]


def test_sports_fallback_persists_only_supporters():
    chain = FakeChain()
    pub = FakePub()
    _run(_detail(), _split(), chain=chain, publisher=pub, policy="attest")
    assert [r["agent"] for r in pub.atts[0][1]] == ["alpha", "beta"]
    assert pub.resolved == [(CID, 0)]


def test_sports_fallback_resolve_race_already_resolved():
    chain = FakeChain(fail_fallback=True, resolved_seq=[False, False, True], outcome=0)
    pub = FakePub()
    summary = _run(_detail(), _split(), chain=chain, publisher=pub, policy="attest")
    result = summary["results"][0]
    assert "txError" not in result and summary["ok"] is True
    assert result["reason"] == "resolved concurrently"
    assert pub.resolved == [(CID, 0)]


def test_sports_fallback_real_failure_still_tx_error():
    chain = FakeChain(fail_fallback=True)
    summary = _run(_detail(), _split(), chain=chain, policy="attest")
    result = summary["results"][0]
    assert result["txError"] is True and "resolveFallback" in result["error"]
    assert summary["ok"] is False


# --- PR #30 review fixes: tx errors, research errors, kickoff, send budget, paused markets ---


def test_consensus_send_failure_not_recorded_as_research():
    pub = FakePub()
    summary = _run(_detail(), FakeCoord(), chain=FakeChain(fail_submit=True), publisher=pub)
    assert summary["results"][0]["txError"] is True
    assert pub.research == [] and pub.atts == []  # no cooldown: the next tick retries the send


def test_fallback_send_failure_not_recorded_as_research():
    pub = FakePub()
    summary = _run(_detail(), _split(), chain=FakeChain(fail_attest=True), publisher=pub, policy="attest")
    assert summary["results"][0]["txError"] is True
    assert pub.research == []


def test_research_gets_kickoff_from_close_time():
    coord = FakeCoord()
    _run(_detail(close_time=1_758_412_800), coord, chain=FakeChain(close=1_758_412_800, now=1_758_412_800 + 5), now=1_758_412_900)
    assert coord.calls[0]["kickoff"] == "2025-09-21T00:00:00Z"
    assert coord.calls[0]["as_of"] is None


class RaisingCoord(FakeCoord):
    def __init__(self, fail_questions=(), **kwargs):
        super().__init__(**kwargs)
        self.fail_questions = set(fail_questions)

    def run(self, question, context=None, as_of=None, kickoff=None):
        if question in self.fail_questions:
            self.runs.append(question)
            raise RuntimeError("cursor agent outcome must be 0, 1 or 2, got '1'")
        return super().run(question, context=context, as_of=as_of, kickoff=kickoff)


def _two_market_http(old, new, extra=None):
    q_old, q_new = "Chiefs vs Broncos: Chiefs win?", "Bills vs Jets: Bills win?"
    cards = [
        {"primary": {"conditionId": old, "question": q_old, "marketType": 0, "closeTime": 1}, "children": []},
        {"primary": {"conditionId": new, "question": q_new, "marketType": 0, "closeTime": 2}, "children": []},
    ]
    new_detail = dict(_detail(), conditionId=new, question=q_new)
    new_detail["score"] = dict(new_detail["score"], homeLabel="Bills", awayLabel="Jets")
    payloads = {
        "http://api.test/api/v1/markets": cards,
        f"http://api.test/api/v1/markets/{old}": _detail(),
        f"http://api.test/api/v1/markets/{new}": new_detail,
        **(extra or {}),
    }
    return payloads.__getitem__, q_old, q_new


def test_research_error_records_failure_and_does_not_use_cap_slot(monkeypatch):
    monkeypatch.setenv("OU_RESOLVE_MAX_MARKETS", "1")
    old, new = CID, "0x" + "cd" * 32
    getter, q_old, q_new = _two_market_http(old, new)
    coord = RaisingCoord(fail_questions={q_old})
    pub = FakePub()
    summary = resolve_run.run(http_get=getter, coordinator_factory=lambda: coord, chain=FakeChain(), publisher=pub, now=100)
    first, second = summary["results"]
    assert first["reason"] == "research error" and first["ok"] is False and "got '1'" in first["error"]
    # The failure did not take the only cap slot: the newer market was researched and resolved.
    assert second["submitted"] is True and coord.runs == [q_old, q_new]
    assert summary["researchErrors"] == 1 and summary["attempted"] == 2
    ((cid, reason, reports),) = pub.research
    assert cid == old and reason == "research error"
    assert reports[0]["agent"] == "oracle-job" and reports[0]["outcome"] == 2
    assert reports[0]["evidenceHash"] == "0x" + "00" * 32 and reports[0]["summary"].startswith("research error: ")


def test_research_error_marker_uses_error_cooldown(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.delenv("OU_RESEARCH_ERROR_RETRY_SECONDS", raising=False)
    recent = datetime.fromtimestamp(100 - 60, tz=timezone.utc).isoformat()
    marker = {"agent": "oracle-job", "createdAt": recent, "kind": "research", "outcome": 2}
    status = {f"http://api.test/api/v1/oracle/{CID}/status": {"attestations": [marker]}}
    coord = FakeCoord()
    summary = resolve_run.run(http_get=_http_with(_detail(), status), coordinator_factory=lambda: coord, chain=FakeChain(), publisher=FakePub(), now=100)
    assert summary["results"][0]["reason"] == "research cooldown"
    assert summary["results"][0]["retryAt"] == 40 + 3600
    # After the 1h error window (well inside the 6h research window) research runs again.
    summary = resolve_run.run(
        http_get=_http_with(_detail(), status), coordinator_factory=lambda: coord, chain=FakeChain(), publisher=FakePub(), now=40 + 3600
    )
    assert coord.runs == [QUESTION] and summary["results"][0]["submitted"] is True


def test_research_errors_bounded_per_tick(monkeypatch):
    """Failures use no cap slot, but attempts stop at 2 * cap so a quota outage stays cheap."""
    monkeypatch.setenv("OU_RESOLVE_MAX_MARKETS", "1")
    old, new, third = CID, "0x" + "cd" * 32, "0x" + "ef" * 32
    q_third = "Rams vs Giants: Rams win?"
    third_detail = dict(_detail(), conditionId=third, question=q_third)
    third_detail["score"] = dict(third_detail["score"], homeLabel="Rams", awayLabel="Giants")
    base_get, q_old, q_new = _two_market_http(old, new, {f"http://api.test/api/v1/markets/{third}": third_detail})

    def getter(url):
        if url == "http://api.test/api/v1/markets":
            card = {"primary": {"conditionId": third, "question": q_third, "marketType": 0, "closeTime": 3}, "children": []}
            return [*base_get(url), card]
        return base_get(url)

    coord = RaisingCoord(fail_questions={q_old, q_new, q_third})
    summary = resolve_run.run(http_get=getter, coordinator_factory=lambda: coord, chain=FakeChain(), publisher=FakePub(), now=100)
    assert [r["reason"] for r in summary["results"]] == ["research error", "research error", "capped"]
    assert coord.runs == [q_old, q_new] and summary["researchErrors"] == 2


def test_research_cut_off_by_budget_is_deferred_not_recorded():
    import budget

    class TimingOut(FakeCoord):
        def run(self, question, **kwargs):
            budget._deadline[0] = 0  # the watchdog fired mid-run
            raise RuntimeError("cursor agent timed out")

    pub = FakePub()
    budget.start(600)
    try:
        summary = _run(_detail(), TimingOut(), publisher=pub)
    finally:
        budget.clear()
    assert summary["results"][0]["reason"] == "budget" and pub.research == []
    assert summary["researchErrors"] == 0


def test_research_not_started_without_min_budget(monkeypatch):
    import budget

    monkeypatch.setenv("OU_RESEARCH_MIN_SECONDS", "120")
    coord = FakeCoord()
    budget.start(60)
    try:
        summary = _run(_detail(), coord)
    finally:
        budget.clear()
    assert coord.runs == [] and summary["results"][0]["reason"] == "budget"


def test_consensus_send_deferred_for_budget_is_not_recorded():
    import budget

    class DeferringChain(FakeChain):
        def submit_consensus(self, *args):
            raise budget.SendDeferred("tick budget too short for a send (15s left)")

    pub = FakePub()
    summary = _run(_detail(), FakeCoord(), chain=DeferringChain(), publisher=pub)
    result = summary["results"][0]
    assert result == {"conditionId": CID, "ok": True, "submitted": False, "reason": "budget", "deferred": "send"}
    assert summary["ok"] is True and pub.research == [] and pub.resolved == []


def test_fallback_send_deferred_propagates_not_send_failed():
    import budget

    class DeferringChain(FakeChain):
        def submit_attestation(self, *args):
            raise budget.SendDeferred("tick budget too short for a send (15s left)")

    pub = FakePub()
    summary = _run(_detail(), _split(), chain=DeferringChain(), publisher=pub, policy="attest")
    result = summary["results"][0]
    assert result["reason"] == "budget" and result["deferred"] == "send" and "txError" not in result
    assert pub.research == []


class _FakeW3:
    def __init__(self):
        self.timeouts = []
        self.sent = []
        self.eth = self

    def get_transaction_count(self, *args):
        return 7

    @property
    def gas_price(self):
        return 1

    def send_raw_transaction(self, raw):
        self.sent.append(raw)
        return b"\x11" * 32

    def wait_for_transaction_receipt(self, txh, timeout):
        self.timeouts.append(timeout)
        # Sealed and canonical (get_block below): chain_tx waits past Flashblocks pre-confirmations.
        return {"status": 1, "blockNumber": 100, "blockHash": b"\xb1" * 32}

    def get_block(self, number):
        return {"number": number, "hash": b"\xb1" * 32}


class _FakeFn:
    def call(self, *args, **kwargs):
        return None

    def build_transaction(self, tx):
        return {**tx, "to": "0x" + "22" * 20, "data": "0x", "value": 0}


def test_send_receipt_wait_bounded_by_tick_budget():
    import budget

    account = Account.from_key(OPERATOR_KEY)
    w3 = _FakeW3()
    budget.start(0)
    try:
        chain_mod._send(w3, account, _FakeFn(), 100_000, "submitConsensus")
    finally:
        budget.clear()
    assert w3.timeouts == [chain_mod.RECEIPT_TIMEOUT]
    budget.start(60)
    try:
        chain_mod._send(w3, account, _FakeFn(), 100_000, "submitConsensus")
        assert 45 < w3.timeouts[-1] <= 50  # min(180, remaining - 10)
        budget._deadline[0] = budget.time.monotonic() + 25
        with pytest.raises(budget.SendDeferred):
            chain_mod._send(w3, account, _FakeFn(), 100_000, "submitConsensus")
    finally:
        budget.clear()
    assert len(w3.sent) == 2  # the deferred send never broadcast


def test_operator_view_includes_paused_markets():
    import httpx

    paused_card = {"primary": {"conditionId": CID, "question": QUESTION, "marketType": 0, "paused": True}, "children": []}
    urls = []

    def operator_get(url):
        urls.append(url)
        return [paused_card]

    def public_get(url):
        if url == "http://api.test/api/v1/markets":
            return []  # the public list hides paused markets
        return _detail()

    coord = FakeCoord()
    summary = resolve_run.run(
        http_get=public_get, operator_get=operator_get, coordinator_factory=lambda: coord, chain=FakeChain(), publisher=FakePub(), now=100
    )
    assert urls == ["http://api.test/api/v1/markets?includePaused=1"]
    assert summary["marketsView"] == "operator" and summary["results"][0]["submitted"] is True

    def rejecting(url):
        request = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("422", request=request, response=httpx.Response(422, request=request))

    summary = resolve_run.run(
        http_get=public_get, operator_get=rejecting, coordinator_factory=lambda: FakeCoord(), chain=FakeChain(), publisher=FakePub(), now=100
    )
    assert summary["marketsView"] == "public" and summary["results"] == []

    def server_error(url):
        request = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("503", request=request, response=httpx.Response(503, request=request))

    with pytest.raises(httpx.HTTPStatusError):
        resolve_run.run(http_get=public_get, operator_get=server_error, chain=FakeChain(), publisher=FakePub(), now=100)


def test_operator_view_default_sends_operator_jwt(monkeypatch):
    import httpx

    from resolve import markets as markets_mod

    seen = {}

    class Client:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, headers):
            seen["url"], seen["auth"] = url, headers.get("Authorization", "")
            return httpx.Response(200, json=[], request=httpx.Request("GET", url))

    monkeypatch.setattr(markets_mod, "mint_operator_jwt", lambda: "tok")
    monkeypatch.setattr(markets_mod.httpx, "Client", Client)
    assert markets_mod.operator_get_json("http://api.test/api/v1/markets?includePaused=1") == []
    assert seen == {"url": "http://api.test/api/v1/markets?includePaused=1", "auth": "Bearer tok"}

    def no_jwt():
        raise RuntimeError("JWT_SECRET required to publish scores")

    # Missing JWT config falls back to the public list.
    monkeypatch.setattr(markets_mod, "mint_operator_jwt", no_jwt)
    cards, view = markets_mod.market_cards("http://api.test", lambda url: [], markets_mod.operator_get_json)
    assert (cards, view) == ([], "public")
