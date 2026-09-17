"""24h fallback: agent majority plus participant votes."""

from __future__ import annotations


def majority(agent_outcomes: list[int]) -> int | None:
    if not agent_outcomes:
        return None
    yes = sum(1 for o in agent_outcomes if o == 0)
    no = sum(1 for o in agent_outcomes if o == 1)
    if yes >= 2:
        return 0
    if no >= 2:
        return 1
    return None


def combine(agent_outcome: int, yes_weight: int, no_weight: int) -> str:
    total = yes_weight + no_weight
    if total == 0:
        return "agent"
    vote_out = 0 if yes_weight >= no_weight else 1
    if vote_out == agent_outcome:
        return "agree"
    opposing = no_weight if agent_outcome == 0 else yes_weight
    if opposing * 3 >= total * 2:
        return "arbitrate"
    return "agent"
