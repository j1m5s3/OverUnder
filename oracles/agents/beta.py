"""GPT + Brave agent."""

from __future__ import annotations

import os

from agents.base import Attestation, MockSearch
from agents.alpha import _infer_outcome


def _brave_search(query: str) -> list[str]:
    key = os.getenv("BRAVE_API_KEY")
    if not key:
        return MockSearch().search(query)
    try:
        import httpx

        r = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query},
            headers={"X-Subscription-Token": key},
            timeout=20,
        )
        r.raise_for_status()
        return [item.get("description", "") for item in r.json().get("web", {}).get("results", [])]
    except Exception:
        return MockSearch().search(query)


class BetaAgent:
    name = "beta"

    def __init__(self, search=None):
        self.search = search or _brave_search

    def research(self, question: str) -> Attestation:
        snippets = self.search(question) if callable(self.search) else self.search.search(question)
        outcome, summary = _infer_outcome(question, snippets)
        return Attestation(outcome=outcome, confidence=0.8, evidence_urls=["https://brave.local"], summary=summary)
