"""Canonical NFL nicknames for schedule extract and listing."""

from __future__ import annotations

NICKNAMES: frozenset[str] = frozenset(
    {
        "bills",
        "dolphins",
        "jets",
        "patriots",
        "ravens",
        "browns",
        "bengals",
        "steelers",
        "texans",
        "colts",
        "jaguars",
        "titans",
        "chiefs",
        "raiders",
        "chargers",
        "broncos",
        "eagles",
        "giants",
        "commanders",
        "cowboys",
        "lions",
        "bears",
        "vikings",
        "packers",
        "saints",
        "buccaneers",
        "falcons",
        "panthers",
        "49ers",
        "rams",
        "seahawks",
        "cardinals",
    }
)

_ORDERED = tuple(sorted(NICKNAMES, key=len, reverse=True))


def canonicalize_team(raw: str) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    folded = text.casefold()
    for nick in _ORDERED:
        if folded == nick or nick in folded or folded in nick:
            return nick[0].upper() + nick[1:] if nick != "49ers" else "49ers"
    return None
