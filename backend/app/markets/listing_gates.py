"""Off-chain gates for user listings (OU-T010).

The factory enforces seed, close window, question length, criteria hash and
cooldown on chain; these checks mirror them (so users get a 4xx instead of a
reverted user op) and add the soft gates the chain cannot express: subjective
wording, duplicates of open markets and a per-creator prepare rate limit.
Shape follows oracles/wildcard/gates.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MIN_QUESTION_BYTES = 10
MAX_QUESTION_BYTES = 256  # Vyper String[256] counts UTF-8 bytes
MIN_CRITERIA_CHARS = 20
MAX_CRITERIA_CHARS = 2000
MAX_PENDING_PER_DAY = 5
PENDING_WINDOW_SECONDS = 24 * 60 * 60
# market_listings.seed_usdc is BigInteger (int64); larger seeds would 500 on insert.
MAX_SEED_USDC = 2**63 - 1

# Words that never distinguish two markets. The subject (team, city, asset) and
# every number stay, so "Bills win" vs "Chiefs win" or "October" vs "December"
# are different markets while word order, case, punctuation and filler are not.
DUPLICATE_STOPWORDS = frozenset(
    (
        "will", "the", "a", "an", "it", "is", "be", "on", "in", "at", "of", "by", "to", "for",
        "before", "after", "than", "and", "or", "their", "his", "her", "this", "that",
        "game", "market", "question",
    )
)
DUPLICATE_JACCARD = 0.9

BANNED_WORDS = ("feel", "feels", "should", "best", "worst", "underrated", "overrated", "deserve", "deserves")
_BANNED_RE = re.compile(r"\b(" + "|".join(BANNED_WORDS) + r")\b")


class GateError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class ListingBounds:
    min_seed_usdc: int
    min_lead_seconds: int
    max_horizon_seconds: int


def question_bytes(question: str) -> int:
    return len(question.encode("utf-8"))


def check_question(question: str) -> None:
    n = question_bytes(question)
    if n < MIN_QUESTION_BYTES:
        raise GateError(400, f"question must be at least {MIN_QUESTION_BYTES} bytes")
    if n > MAX_QUESTION_BYTES:
        raise GateError(400, f"question must be at most {MAX_QUESTION_BYTES} bytes")
    if not question.endswith("?"):
        raise GateError(400, "question must be a yes/no question ending in '?'")
    if _BANNED_RE.search(question.lower()):
        raise GateError(400, "question uses subjective wording that cannot be resolved")


def check_criteria(criteria: str) -> None:
    if len(criteria) < MIN_CRITERIA_CHARS:
        raise GateError(400, f"resolution criteria must be at least {MIN_CRITERIA_CHARS} characters")
    if len(criteria) > MAX_CRITERIA_CHARS:
        raise GateError(400, f"resolution criteria must be at most {MAX_CRITERIA_CHARS} characters")


def check_close_time(close_time: int, bounds: ListingBounds, now: int) -> None:
    if close_time < now + bounds.min_lead_seconds:
        raise GateError(400, f"closeTime must be at least {bounds.min_lead_seconds}s from now")
    if close_time > now + bounds.max_horizon_seconds:
        raise GateError(400, f"closeTime must be within {bounds.max_horizon_seconds}s from now")


def check_seed(seed_usdc: int, bounds: ListingBounds) -> None:
    if seed_usdc < bounds.min_seed_usdc:
        raise GateError(400, f"seedUsdc must be at least {bounds.min_seed_usdc}")
    if seed_usdc > MAX_SEED_USDC:
        raise GateError(400, f"seedUsdc must be at most {MAX_SEED_USDC}")


def check_pending(pending: int) -> None:
    if pending >= MAX_PENDING_PER_DAY:
        raise GateError(429, f"at most {MAX_PENDING_PER_DAY} unconfirmed listings per 24h")


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def meaningful_tokens(text: str) -> frozenset[str]:
    return frozenset(t for t in normalize(text).split() if t not in DUPLICATE_STOPWORDS)


def _numbers(tokens: frozenset[str]) -> frozenset[str]:
    return frozenset(t for t in tokens if any(ch.isdigit() for ch in t))


def is_duplicate(question: str, existing_questions: list[str]) -> bool:
    """Near-identical to an open market: same normalized text, the same meaningful
    tokens in any order, or Jaccard >= 0.9 over meaningful tokens with identical
    numbers (a different date or threshold is a different market)."""
    n = normalize(question)
    words = meaningful_tokens(question)
    numbers = _numbers(words)
    for existing in existing_questions:
        if n == normalize(existing):
            return True
        other = meaningful_tokens(existing)
        if not words or not other:
            continue
        if words == other:
            return True
        jaccard = len(words & other) / len(words | other)
        if jaccard >= DUPLICATE_JACCARD and numbers == _numbers(other):
            return True
    return False


def check_listed_question(question: str, open_questions: list[str]) -> None:
    """Gates for a question as it is stored on chain (exact string, not re-stripped).

    Run by /confirm and the indexer on the factory's question, which the client
    controls independently of /prepare. The caller leaves the market's own
    condition id out of `open_questions`.
    """
    if question != question.strip():
        raise GateError(400, "question must not start or end with whitespace")
    check_question(question)
    if is_duplicate(question, open_questions):
        raise GateError(409, "a similar market is already open")


def prepared_mismatch(
    *,
    prepared_question: str,
    prepared_close_time: int,
    prepared_seed_usdc: int,
    question: str,
    close_time: int,
    seed_usdc: int | None,
) -> str | None:
    """Reason the on-chain listing differs from what /prepare validated, else None."""
    if question != prepared_question:
        return "on-chain question differs from the prepared question"
    if int(close_time) != int(prepared_close_time):
        return "on-chain closeTime differs from the prepared closeTime"
    if seed_usdc is not None and int(seed_usdc) != int(prepared_seed_usdc):
        return "on-chain seed differs from the prepared seed"
    return None


def validate_listing(question: str, criteria: str, close_time: int, seed_usdc: int, bounds: ListingBounds, now: int) -> None:
    """Raise GateError on the first failing gate. Inputs are already stripped."""
    check_question(question)
    check_criteria(criteria)
    check_close_time(close_time, bounds, now)
    check_seed(seed_usdc, bounds)
