"""Server-side sports gate for operator-fed LiveScore writes.

Mirrors the web heuristic in `web/src/shared/utils/categorize.ts` so the
API never trusts the client about which primaries may carry scores.
Keep the token lists in sync when the web list changes.
"""

import re

SPORTS_TOKENS = (
    "nfl",
    "nba",
    "mlb",
    "nhl",
    "mls",
    "ncaa",
    "epl",
    "uefa",
    "soccer",
    "football",
    "basketball",
    "baseball",
    "hockey",
    "tennis",
    "golf",
    "boxing",
    "ufc",
    "mma",
    "nascar",
    "f1",
    "formula 1",
    "chiefs",
    "broncos",
    "yankees",
    "red sox",
    "dodgers",
    "mets",
    "lakers",
    "celtics",
    "warriors",
    "knicks",
    "cowboys",
    "patriots",
    "packers",
    "seahawks",
    "chargers",
    "steelers",
    "bills",
    "dolphins",
    "jets",
    "ravens",
    "browns",
    "bengals",
    "texans",
    "colts",
    "jaguars",
    "titans",
    "raiders",
    "eagles",
    "giants",
    "commanders",
    "lions",
    "bears",
    "vikings",
    "saints",
    "buccaneers",
    "falcons",
    "panthers",
    "49ers",
    "rams",
    "cardinals",
    "touchdown",
    "fumble",
    "home run",
    "strikeout",
    "grand slam",
    "three-pointer",
    "slam dunk",
    "hat trick",
)

_PATTERNS = tuple(re.compile(r"\b%s\b" % re.escape(token), re.IGNORECASE) for token in SPORTS_TOKENS)


def is_sports_market(question: str | None) -> bool:
    """True when the market question matches the sports allowlist."""
    if not question:
        return False
    return any(pattern.search(question) for pattern in _PATTERNS)
