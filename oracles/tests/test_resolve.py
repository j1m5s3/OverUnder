import os

import pytest

from resolve import run as resolve_run

CID = "0x" + "ab" * 32
QUESTION = "Chiefs vs Broncos: Chiefs win?"


def _card():
    return {"primary": {"conditionId": CID, "question": QUESTION, "marketType": 0}, "children": []}


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


class FakeChain:
    def __init__(self, resolved=False, close=0):
        self.resolved = resolved
        self.close = close
        self.submits = []

    def is_resolved(self, cid):
        return self.resolved

    def onchain_close_time(self, cid):
        return self.close

    def submit_consensus(self, *args):
        self.submits.append(args)
        return "0xtx"


class FakePub:
    def __init__(self):
        self.atts = []
        self.resolved = []

    def persist_attestations(self, cid, reports):
        self.atts.append((cid, reports))

    def mark_resolved(self, cid, outcome):
        self.resolved.append((cid, outcome))


class FakeCoord:
    def __init__(self, unanimous=True, outcome=0, missing_key=False):
        self.unanimous = unanimous
        self.outcome = outcome
        self.missing_key = missing_key
        self.signed = False

    def run(self, question):
        return {
            "unanimous": self.unanimous,
            "outcome": self.outcome if self.unanimous else None,
            "reports": [
                {"agent": "alpha", "outcome": self.outcome, "summary": "ok", "evidenceHash": "0x" + "cd" * 32}
            ],
        }

    def sign_unanimous(self, *args, **kwargs):
        if self.missing_key:
            raise RuntimeError("missing key for alpha")
        self.signed = True
        return 99, [b"\x00" * 65] * 3


def _http(detail):
    payloads = {
        "http://api.test/api/v1/markets": [_card()],
        f"http://api.test/api/v1/markets/{CID}": detail,
    }

    def http_get(url: str):
        return payloads[url]

    return http_get


def _run(detail, coord, chain=None, publisher=None, now=100):
    return resolve_run.run(
        http_get=_http(detail),
        coordinator_factory=lambda: coord,
        chain=chain or FakeChain(),
        publisher=publisher or FakePub(),
        now=now,
    )


def test_not_final_does_not_sign(monkeypatch):
    monkeypatch.setenv("OU_API_URL", "http://api.test")
    monkeypatch.setenv("OU_ORACLE_MOCK", "1")
    coord = FakeCoord()
    chain = FakeChain()
    summary = _run(_detail(status="in_progress"), coord, chain=chain)
    assert chain.submits == []
    assert coord.signed is False
    assert summary["results"][0]["submitted"] is False


def test_not_closed_does_not_submit(monkeypatch):
    monkeypatch.setenv("OU_API_URL", "http://api.test")
    coord = FakeCoord()
    chain = FakeChain(close=500)
    summary = _run(_detail(close_time=500), coord, chain=chain, now=100)
    assert chain.submits == []
    assert summary["results"][0]["reason"] == "not closed"


def test_research_mismatch_does_not_submit(monkeypatch):
    monkeypatch.setenv("OU_API_URL", "http://api.test")
    coord = FakeCoord(unanimous=True, outcome=1)
    chain = FakeChain()
    summary = _run(_detail(home=27, away=24), coord, chain=chain, now=100)
    assert chain.submits == []
    assert summary["results"][0]["reason"] == "research mismatch"


def test_missing_agent_key_does_not_submit(monkeypatch):
    monkeypatch.setenv("OU_API_URL", "http://api.test")
    monkeypatch.delenv("AGENT_ALPHA_KEY", raising=False)
    coord = FakeCoord(missing_key=True)
    chain = FakeChain()
    summary = _run(_detail(), coord, chain=chain, now=100)
    assert chain.submits == []
    assert summary["results"][0]["ok"] is False


def test_unanimous_matching_score_submits(monkeypatch):
    monkeypatch.setenv("OU_API_URL", "http://api.test")
    monkeypatch.setenv("OU_ORACLE_MOCK", "1")
    coord = FakeCoord()
    chain = FakeChain()
    pub = FakePub()
    summary = _run(_detail(), coord, chain=chain, publisher=pub, now=100)
    assert len(chain.submits) == 1
    assert summary["results"][0]["submitted"] is True
    assert pub.atts and pub.resolved
    os.environ.pop("OU_ORACLE_MOCK", None)
