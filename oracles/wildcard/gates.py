"""Schema, resolvability, closeTime, and dedupe gates for wildcard proposals."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Proposal:
    question: str
    resolution_criteria: str
    close_time: int
    suggested_probability: float
    parent_close_time: int
    existing_questions: list[str]


def schema_valid(p: Proposal) -> bool:
    if not p.question or len(p.question) > 256:
        return False
    if not p.resolution_criteria:
        return False
    if not 0 < p.suggested_probability < 1:
        return False
    return True


def resolvable(p: Proposal) -> bool:
    q = p.question.lower()
    banned = ("feel", "should", "best", "underrated")
    if any(w in q for w in banned):
        return False
    return bool(re.search(r"\?$|fumble|win|score|yards|over|under", q))


def close_ok(p: Proposal) -> bool:
    return p.close_time <= p.parent_close_time


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def unique(p: Proposal) -> bool:
    n = _norm(p.question)
    for existing in p.existing_questions:
        e = _norm(existing)
        if n == e:
            return False
        shared = set(n.split()) & set(e.split())
        if len(shared) >= max(3, len(n.split()) - 1):
            return False
    return True


def pass_gates(p: Proposal) -> bool:
    return schema_valid(p) and resolvable(p) and close_ok(p) and unique(p)
