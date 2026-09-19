"""Gemini + Exa agent."""

from __future__ import annotations

import os

from agents.base import Attestation, MockSearch, SearchHit, should_use_mock


def _exa_search(query: str) -> list[SearchHit]:
    """Search using Exa API. Hard-fails if key missing and not in mock mode."""
    if should_use_mock():
        return MockSearch().search(query)

    key = os.getenv("EXA_API_KEY")
    if not key:
        raise RuntimeError("EXA_API_KEY required for gamma agent in production")

    try:
        import httpx

        r = httpx.post(
            "https://api.exa.ai/search",
            json={"query": query, "numResults": 5},
            headers={"x-api-key": key},
            timeout=20,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        return [SearchHit(url=item.get("url", ""), content=item.get("text", item.get("title", ""))) for item in results]
    except Exception as e:
        raise RuntimeError(f"Exa search failed: {e}")


def _gemini_infer(question: str, hits: list[SearchHit]) -> tuple[int, float, str, list[str]]:
    """Use Gemini to infer outcome from search hits. Returns (outcome, confidence, summary, evidence_urls)."""
    if should_use_mock():
        text = " ".join(h.content for h in hits).lower() + " " + question.lower()
        yes_hits = sum(w in text for w in ("yes", "defeated", "won", "fumble", "true"))
        no_hits = sum(w in text for w in ("no", "did not", "false", "zero fumble"))
        if "who won" in question.lower() and "chiefs" in text:
            outcome = 0
        else:
            outcome = 0 if yes_hits >= no_hits else 1
        summary = hits[0].content[:280] if hits else "no evidence"
        evidence_urls = [h.url for h in hits[:2]]
        return outcome, 0.78, summary, evidence_urls

    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY required for gamma agent in production")

    try:
        import google.generativeai as genai

        genai.configure(api_key=key)

        context = "\n\n".join([f"[{i}] {h.content}" for i, h in enumerate(hits)])
        prompt = f"""Given this question: "{question}"

And these search results:
{context}

Respond with a JSON object (no other text) containing:
- outcome: 0 for yes/affirmative or 1 for no/negative
- confidence: a float between 0 and 1
- summary: brief explanation (max 280 chars)
- evidence_indices: list of result indices [0-{len(hits)-1}] that support your answer"""

        model = genai.GenerativeModel("gemini-1.5-flash-latest")
        response = model.generate_content(prompt)

        import json

        result = json.loads(response.text)
        outcome = int(result["outcome"])
        confidence = float(result["confidence"])
        summary = result["summary"][:280]
        evidence_indices = result.get("evidence_indices", [])

        # Filter evidence_urls to only include URLs from the search hits
        hit_urls = {h.url for h in hits}
        evidence_urls = [hits[i].url for i in evidence_indices if 0 <= i < len(hits) and hits[i].url in hit_urls]

        return outcome, confidence, summary, evidence_urls
    except Exception as e:
        raise RuntimeError(f"Gemini inference failed: {e}")


class GammaAgent:
    name = "gamma"

    def __init__(self, search=None):
        self.search = search or _exa_search

    def research(self, question: str) -> Attestation:
        hits = self.search(question) if callable(self.search) else self.search.search(question)
        outcome, confidence, summary, evidence_urls = _gemini_infer(question, hits)

        # Validate evidence_urls are subset of hit URLs
        hit_urls = {h.url for h in hits}
        for url in evidence_urls:
            if url not in hit_urls:
                raise RuntimeError(f"Evidence URL {url} not found in search hits")

        return Attestation(outcome=outcome, confidence=confidence, evidence_urls=evidence_urls, summary=summary)
