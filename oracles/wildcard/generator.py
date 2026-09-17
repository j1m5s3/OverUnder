"""Propose wildcard child markets for a primary event."""

from __future__ import annotations

from wildcard.gates import Proposal, pass_gates


TEMPLATES = [
    "{star} to fumble at least once?",
    "{away} to score first?",
    "Total points over 45.5?",
]


def propose(primary_question: str, close_time: int, star: str = "Travis Kelce", away: str = "Broncos") -> list[Proposal]:
    existing: list[str] = []
    out: list[Proposal] = []
    for tmpl in TEMPLATES:
        q = tmpl.format(star=star, away=away)
        p = Proposal(
            question=q,
            resolution_criteria=f"Resolved from official box score after {primary_question}",
            close_time=close_time,
            suggested_probability=0.35,
            parent_close_time=close_time,
            existing_questions=list(existing),
        )
        if pass_gates(p):
            out.append(p)
            existing.append(p.question)
    return out
