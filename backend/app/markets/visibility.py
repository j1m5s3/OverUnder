"""Which markets the public API shows, and the listing review both writers share.

A user-listed market (factory marketType 2) is public only once its
MarketListing row is `confirmed`: the criteria text matches the on-chain
criteriaHash and the on-chain question passed the listing gates. `indexed`
(seen by the indexer, criteria unverified), `prepared` and `rejected` rows stay
hidden from lists, detail, quotes and sponsored trades. This does not reuse
`Market.paused`, which the indexer and /confirm overwrite from chain events, so
an operator unpause could never re-publish a rejected market.

`review_user_listing` is the single gate check run by POST
/markets/listing/confirm and by the indexer on MarketCreated (type 2).
"""

from __future__ import annotations

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.markets import listing_gates as gates
from app.models import Market, MarketListing

MARKET_TYPE_USER = 2
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"


def listed_clause():
    """True for markets that are not type 2, or whose listing is confirmed."""
    return or_(
        Market.market_type != MARKET_TYPE_USER,
        exists().where(
            and_(
                MarketListing.condition_id == Market.condition_id,
                MarketListing.status == STATUS_CONFIRMED,
            )
        ),
    )


def visible_clause():
    return and_(Market.paused.is_(False), listed_clause())


async def is_listed(db: AsyncSession, market: Market) -> bool:
    if market.market_type != MARKET_TYPE_USER:
        return True
    row = await db.get(MarketListing, market.condition_id)
    return row is not None and row.status == STATUS_CONFIRMED


async def open_questions(db: AsyncSession, now: int, exclude_cid: str | None = None) -> list[str]:
    """Questions of visible, unresolved, still-open markets (the duplicate gate's corpus).

    `exclude_cid` leaves a market's own row out, so re-confirming it (or the
    indexer, whose Market row is already in the session) never matches itself.
    """
    stmt = select(Market.question).where(
        Market.resolved.is_(False),
        visible_clause(),
        Market.close_time > now,
    )
    if exclude_cid:
        stmt = stmt.where(Market.condition_id != exclude_cid)
    return [q for (q,) in (await db.execute(stmt)).all()]


def is_backend_prepared(listing: MarketListing | None) -> bool:
    """A row handed out by /prepare (it carries the salt); indexer/confirm-created rows do not."""
    return listing is not None and bool(listing.salt or listing.status == "prepared")


async def review_user_listing(
    db: AsyncSession,
    *,
    cid: str,
    listing: MarketListing | None,
    question: str,
    close_time: int,
    seed_usdc: int | None,
    criteria: str,
    now: int,
) -> str | None:
    """None when the on-chain listing may go public, else the rejection reason.

    `question`, `close_time` and `seed_usdc` are the on-chain values. When the
    row came from /prepare they must equal what was prepared: the question is
    not committed in the condition id or the criteria hash, so a client could
    otherwise edit it in the calldata after the gates ran.
    """
    if is_backend_prepared(listing):
        mismatch = gates.prepared_mismatch(
            prepared_question=listing.question or "",
            prepared_close_time=int(listing.close_time or 0),
            prepared_seed_usdc=int(listing.seed_usdc or 0),
            question=question,
            close_time=close_time,
            seed_usdc=seed_usdc,
        )
        if mismatch:
            return mismatch
    try:
        gates.check_criteria(criteria)
        gates.check_listed_question(question, await open_questions(db, now, exclude_cid=cid))
    except gates.GateError as exc:
        return exc.detail
    return None
