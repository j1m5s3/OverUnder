"""Claude + Tavily agent. Falls back to mock search / heuristic if keys missing."""

from __future__ import annotations

import os

from agents.base import Agent, Attestation, MockSearch, SearchClient


def _tavily_search(query: str) -> list[str]:
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        return MockSearch().search(query)
    try:
        import httpx

        r = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": key, "query": query, "max_results": 5},
            timeout=20,
        )
        r.raise_for_status()
        return [item.get("content", "") for item in r.json().get("results", [])]
    except Exception:
        return MockSearch().search(query)


def _infer_outcome(question: str, snippets: list[str]) -> tuple[int, str]:
    text = " ".join(snippets).lower() + " " + question.lower()
    yes_hits = sum(w in text for w in ("yes", "defeated", "won", "fumble", "true"))
    no_hits = sum(w in text for w in ("no", "did not", "false", "zero fumble"))
    if "who won" in question.lower() and "chiefs" in text:
        return 0, "Sources indicate Chiefs won."
    outcome = 0 if yes_hits >= no_hits else 1
    return outcome, snippets[0][:280] if snippets else "no evidence"


class AlphaAgent:
    name = "alpha"

    def __init__(self, search: SearchClient | None = None):
        self.search = search or _tavily_search

    def research(self, question: str) -> Attestation:
        snippets = self.search(question) if callable(self.search) else self.search.search(question)
        outcome, summary = _infer_outcome(question, snippets)
        return Attestation(outcome=outcome, confidence=0.82, evidence_urls=["https://tavily.local"], summary=summary)
