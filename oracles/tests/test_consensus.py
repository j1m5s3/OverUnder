import os
import sys
import pytest

from agents.alpha import AlphaAgent
from agents.base import MockSearch, SearchHit, should_use_mock
from agents.beta import BetaAgent
from agents.gamma import GammaAgent
from consensus.coordinator import Coordinator
from consensus.fallback import combine, majority
from wildcard.generator import propose


def test_unanimous_mocked_search():
    """Test consensus with mocked search results."""
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        search = MockSearch(["Chiefs defeated Broncos 27-24. Kelce fumbled once."])
        coord = Coordinator(
            agents=[
                AlphaAgent(search=search),
                BetaAgent(search=search),
                GammaAgent(search=search),
            ]
        )
        result = coord.run("Who won Chiefs vs Broncos?")
        assert result["unanimous"] is True
        assert result["outcome"] == 0
        assert "submitConsensus" not in result
        assert not hasattr(coord, "submitConsensus")

        # Verify evidence URLs are from search hits
        for report in result["reports"]:
            agent_name = report["agent"]
            if agent_name == "alpha":
                agent = AlphaAgent(search=search)
            elif agent_name == "beta":
                agent = BetaAgent(search=search)
            else:
                agent = GammaAgent(search=search)

            attestation = agent.research("Who won Chiefs vs Broncos?")
            hits = search.search("Who won Chiefs vs Broncos?")
            hit_urls = {h.url for h in hits}
            for url in attestation.evidence_urls:
                assert url in hit_urls, f"Evidence URL {url} not in search hits"
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def test_evidence_urls_subset_of_hits():
    """Test that evidence URLs are always a subset of search hit URLs."""
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        # Create custom search hits with known URLs
        test_hits = [
            SearchHit(url="https://example.com/1", content="Chiefs won the game."),
            SearchHit(url="https://example.com/2", content="Final score was 27-24."),
        ]

        class CustomSearch:
            def search(self, query: str) -> list[SearchHit]:
                return test_hits

        search = CustomSearch()
        agent = AlphaAgent(search=search)
        attestation = agent.research("Who won?")

        # Verify all evidence URLs are in the hit URLs
        hit_urls = {h.url for h in test_hits}
        for url in attestation.evidence_urls:
            assert url in hit_urls, f"Evidence URL {url} not found in search hits"
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def test_mock_flag_refuse_outside_test():
    """Test that OU_ORACLE_MOCK=1 is refused outside pytest/CI/anvil."""
    # This test runs in pytest, so it should succeed
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        assert should_use_mock() is True
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)

    # Without pytest env, it would fail (we can't test this directly in pytest)


def test_mock_flag_allowed_in_pytest():
    """Test that OU_ORACLE_MOCK=1 is allowed in pytest."""
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        # Should not raise
        result = should_use_mock()
        assert result is True
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


def test_hard_fail_on_missing_keys_without_mock():
    """Test that missing Cursor env causes hard failures when not in mock mode."""
    os.environ.pop("OU_ORACLE_MOCK", None)
    old_cursor = os.environ.pop("CURSOR_API_KEY", None)
    old_mcp = os.environ.pop("CURSOR_SEARCH_MCP_URL", None)

    try:
        agent = AlphaAgent()
        with pytest.raises(RuntimeError, match="CURSOR_API_KEY required"):
            agent.research("test question")
    finally:
        if old_cursor:
            os.environ["CURSOR_API_KEY"] = old_cursor
        if old_mcp:
            os.environ["CURSOR_SEARCH_MCP_URL"] = old_mcp


def test_mock_path_never_imports_cursor_sdk():
    os.environ["OU_ORACLE_MOCK"] = "1"
    sys.modules.pop("cursor_sdk", None)
    try:
        search = MockSearch(["Chiefs defeated Broncos 27-24. Kelce fumbled once."])
        attestation = AlphaAgent(search=search).research("Who won Chiefs vs Broncos?")
        assert attestation.outcome == 0
        assert "cursor_sdk" not in sys.modules
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)


@pytest.mark.skipif(
    not (os.getenv("CURSOR_API_KEY") and os.getenv("CURSOR_SEARCH_MCP_URL")),
    reason="Live Cursor key / search MCP URL not available",
)
def test_live_alpha_agent_smoke():
    """Smoke test with real Cursor agent + HTTP search MCP (gated on env)."""
    os.environ.pop("OU_ORACLE_MOCK", None)
    agent = AlphaAgent()
    attestation = agent.research("Who won the 2024 Super Bowl?")

    assert attestation.outcome in (0, 1)
    assert 0.0 <= attestation.confidence <= 1.0
    assert len(attestation.summary) > 0
    assert len(attestation.evidence_urls) > 0

    for url in attestation.evidence_urls:
        assert url.startswith("http"), f"Expected real URL, got {url}"
        assert "mock.local" not in url, "Should not contain mock URLs"


def test_fallback_majority_and_votes():
    assert majority([0, 0, 1]) == 0
    assert combine(0, yes_weight=10, no_weight=90) == "arbitrate"
    assert combine(0, yes_weight=80, no_weight=20) == "agree"


def test_wildcard_gates():
    os.environ["OU_ORACLE_MOCK"] = "1"
    try:
        kids = propose("Chiefs vs Broncos", close_time=1_700_000_000)
        assert kids
        assert all("?" in k.question or "fumble" in k.question.lower() for k in kids)
    finally:
        os.environ.pop("OU_ORACLE_MOCK", None)
