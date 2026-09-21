"""Cursor-runtime beta agent. MockSearch / injected search for tests."""

from __future__ import annotations

from agents.base import Attestation, heuristic_infer, hits_from_search, should_use_mock
from agents.cursor_runtime import live_research


class BetaAgent:
    name = "beta"

    def __init__(self, search=None):
        self.search = search

    def research(self, question: str) -> Attestation:
        if self.search is not None or should_use_mock():
            hits = hits_from_search(self.search, question)
            outcome, confidence, summary, evidence_urls = heuristic_infer(question, hits, 0.8)
        else:
            hits, outcome, confidence, summary, evidence_urls = live_research(question, "beta")

        hit_urls = {h.url for h in hits}
        for url in evidence_urls:
            if url not in hit_urls:
                raise RuntimeError(f"Evidence URL {url} not found in search hits")

        return Attestation(outcome=outcome, confidence=confidence, evidence_urls=evidence_urls, summary=summary)
