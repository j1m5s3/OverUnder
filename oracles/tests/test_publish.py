import json
import os

import httpx
import jwt
import pytest

from agents.base import MockSearch
from scores.publish import mint_operator_jwt, publish_score
from scores.scout import ScoreCoordinator, ScoreReport

ANVIL_0 = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
ANVIL_0_ADDR = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"


def _report() -> ScoreReport:
    return ScoreReport(
        home_label="Chiefs",
        away_label="Broncos",
        home_score=27,
        away_score=24,
        status="final",
        period_label=None,
        summary="final",
        evidence_urls=["https://mock.local/0"],
        facts={"home_score": 27, "away_score": 24},
    )


def test_mint_operator_jwt_matches_issue_claims(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-please-use-32b-min!!")
    monkeypatch.setenv("OPERATOR_PRIVATE_KEY", ANVIL_0)
    monkeypatch.setenv("JWT_TTL_SECONDS", "3600")
    token = mint_operator_jwt()
    data = jwt.decode(token, "test-secret-please-use-32b-min!!", algorithms=["HS256"])
    assert data["sub"] == ANVIL_0_ADDR
    assert data["op"] is True
    assert "exp" in data


def test_publish_posts_bearer_and_body(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-please-use-32b-min!!")
    monkeypatch.setenv("OPERATOR_PRIVATE_KEY", ANVIL_0)
    monkeypatch.setenv("OU_API_URL", "http://api.test")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "homeScore": 27})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        body = publish_score("0xabc", _report(), client=client)
    assert body["ok"] is True
    assert captured["url"] == "http://api.test/api/v1/markets/0xabc/score"
    assert captured["auth"].startswith("Bearer ")
    token = captured["auth"].split(" ", 1)[1]
    assert jwt.decode(token, "test-secret-please-use-32b-min!!", algorithms=["HS256"])["op"] is True
    assert captured["body"]["facts"]["home_score"] == 27


def test_publish_401_fail_closed(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-please-use-32b-min!!")
    monkeypatch.setenv("OPERATOR_PRIVATE_KEY", ANVIL_0)
    monkeypatch.setenv("OU_API_URL", "http://api.test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unknown user")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(RuntimeError, match="operator User missing"):
            publish_score("0xabc", _report(), client=client)


def test_coordinator_unanimous_posts_and_disagree_does_not():
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        posted = []
        search = MockSearch(["Chiefs 27, Broncos 24. Final."])
        coord = ScoreCoordinator(searches=[search, search, search], publisher=lambda cid, report: posted.append((cid, report)))
        result = coord.run("Chiefs vs Broncos: Chiefs win?", condition_id="0xabc")
        assert result["unanimous"] is True
        assert len(posted) == 1
        assert posted[0][0] == "0xabc"

        posted.clear()
        a = MockSearch(["Chiefs 27, Broncos 24. Final."])
        b = MockSearch(["Chiefs 21, Broncos 24. Final."])
        coord = ScoreCoordinator(searches=[a, a, b], publisher=lambda cid, report: posted.append(cid))
        result = coord.run("Chiefs vs Broncos: Chiefs win?", condition_id="0xabc")
        assert result["unanimous"] is False
        assert posted == []
        assert result["apiBody"] is None
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)



# --- resolve/publish: /oracle/attest is operator-only ------------------------


def _pub_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-please-use-32b-min!!")
    monkeypatch.setenv("OPERATOR_PRIVATE_KEY", ANVIL_0)
    monkeypatch.setenv("OU_API_URL", "http://api.test")


def test_persist_attestations_sends_operator_bearer(monkeypatch):
    from resolve.publish import persist_attestations, persist_research

    _pub_env(monkeypatch)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("authorization"), json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "id": len(seen)})

    reports = [
        {"agent": "alpha", "outcome": 1, "evidenceHash": "0x" + "a1" * 32, "summary": "s"},
        {"agent": "beta", "outcome": 2, "evidenceHash": "0x" + "b2" * 32, "summary": ""},
    ]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        persist_attestations("0xabc", reports, client=client)
        persist_research("0xabc", reports[:1], "research split", client=client)
    assert [url for url, _, _ in seen] == ["http://api.test/api/v1/oracle/attest"] * 3
    for _, auth, _ in seen:
        token = auth.split(" ", 1)[1]
        assert jwt.decode(token, "test-secret-please-use-32b-min!!", algorithms=["HS256"])["op"] is True
    assert seen[0][2] == {"conditionId": "0xabc", "agent": "alpha", "outcome": 1, "evidenceHash": "0x" + "a1" * 32, "summary": "s"}
    assert json.loads(seen[2][2]["evidenceJson"]) == [{"kind": "research", "reason": "research split"}]


def test_persist_attestations_401_fails_closed(monkeypatch):
    from resolve.publish import persist_attestations

    _pub_env(monkeypatch)
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"detail": "unknown user"}))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(RuntimeError, match="operator User missing"):
            persist_attestations("0xabc", [{"agent": "alpha", "outcome": 0}], client=client)


def test_persist_attestations_requires_jwt_secret(monkeypatch):
    from resolve.publish import persist_attestations

    _pub_env(monkeypatch)
    monkeypatch.delenv("JWT_SECRET")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        persist_attestations("0xabc", [{"agent": "alpha", "outcome": 0}])
    persist_attestations("0xabc", [])
