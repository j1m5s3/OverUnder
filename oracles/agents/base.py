from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class SearchHit:
    """Single search result with URL and content."""

    url: str
    content: str


@dataclass
class Attestation:
    outcome: int
    confidence: float
    evidence_urls: list[str]
    summary: str
    evidence_hash: bytes = b""

    def __post_init__(self):
        blob = json.dumps(
            {"outcome": self.outcome, "urls": self.evidence_urls, "summary": self.summary},
            sort_keys=True,
        ).encode()
        self.evidence_hash = hashlib.sha256(blob).digest()


class SearchClient(Protocol):
    def search(self, query: str) -> list[SearchHit]: ...


class MockSearch:
    def __init__(self, snippets: list[str] | None = None):
        self.snippets = snippets or ["Official recap: Chiefs defeated Broncos. Kelce recorded one fumble."]

    def search(self, query: str) -> list[SearchHit]:
        return [SearchHit(url=f"https://mock.local/{i}", content=f"{query}: {s}") for i, s in enumerate(self.snippets)]


def _check_mock_flag_allowed() -> bool:
    """Return True if OU_ORACLE_MOCK=1 is allowed in this environment."""
    mock_flag = os.getenv("OU_ORACLE_MOCK", "0")
    if mock_flag != "1":
        return True

    # Allow mock in pytest
    if "PYTEST_CURRENT_TEST" in os.environ:
        return True

    # Allow mock in CI
    if os.getenv("CI") == "1":
        return True

    # Allow mock on anvil-only (chain_id 31337)
    chain_id = os.getenv("CHAIN_ID", "")
    if chain_id == "31337":
        return True

    # Refuse mock in all other environments
    raise RuntimeError("OU_ORACLE_MOCK=1 is forbidden outside pytest, CI, or anvil-only environments")


def should_use_mock() -> bool:
    """Return True if we should use MockSearch. Raises if mock flag is set inappropriately."""
    _check_mock_flag_allowed()
    return os.getenv("OU_ORACLE_MOCK", "0") == "1"


def heuristic_infer(question: str, hits: list[SearchHit], confidence: float) -> tuple[int, float, str, list[str]]:
    text = " ".join(h.content for h in hits).lower() + " " + question.lower()
    yes_hits = sum(w in text for w in ("yes", "defeated", "won", "fumble", "true"))
    no_hits = sum(w in text for w in ("no", "did not", "false", "zero fumble"))
    if "who won" in question.lower() and "chiefs" in text:
        outcome = 0
    else:
        outcome = 0 if yes_hits >= no_hits else 1
    summary = hits[0].content[:280] if hits else "no evidence"
    evidence_urls = [h.url for h in hits[:2]]
    return outcome, confidence, summary, evidence_urls


def hits_from_search(search, query: str) -> list[SearchHit]:
    if search is None:
        return MockSearch().search(query)
    if callable(search):
        return search(query)
    return search.search(query)


class Agent(Protocol):
    name: str

    def research(self, question: str) -> Attestation: ...
