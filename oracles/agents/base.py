from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Protocol


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
    def search(self, query: str) -> list[str]: ...


class MockSearch:
    def __init__(self, snippets: list[str] | None = None):
        self.snippets = snippets or ["Official recap: Chiefs defeated Broncos. Kelce recorded one fumble."]

    def search(self, query: str) -> list[str]:
        return [f"{query}: {s}" for s in self.snippets]


class Agent(Protocol):
    name: str

    def research(self, question: str) -> Attestation: ...
