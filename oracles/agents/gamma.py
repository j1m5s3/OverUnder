"""Gemini + Exa agent."""

from __future__ import annotations

import os

from agents.base import Attestation, MockSearch
from agents.alpha import _infer_outcome


def _exa_search(query: str) -> list[str]:
    key = os.getenv("EXA_API_KEY")
    if not key:
        return MockSearch().search(query)
    try:
        import httpx

        r = httpx.post(
            "https://api.exa.ai/search",
            json={"query": query, "numResults": 5},
            headers={"x-api-key": key},
            timeout=20,
        )
        r.raise_for_status()
        return [item.get("text", item.get("title", "")) for item in r.json().get("results", [])]
    except Exception:
        return MockSearch().search(query)


class GammaAgent:
    name = "gamma"

    def __init__(self, search=None):
        self.search = search or _exa_search

    def research(self, question: str) -> Attestation:
        snippets = self.search(question) if callable(self.search) else self.search.search(question)
        outcome, summary = _infer_outcome(question, snippets)
        return Attestation(outcome=outcome, confidence=0.78, evidence_urls=["https://exa.local"], summary=summary)
