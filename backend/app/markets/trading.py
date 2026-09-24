"""API-side trading halt (TRADING_HALT_AT_CLOSE).

MarketAMM v2 also reverts buys/sells with "market closed" once closeTime
passes (when its closeGate is on); this module stops quotes and the mobile
cdp-send path earlier so users get a clean 409 instead of a failed user op.
"""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Market, MarketListing

REASON_CLOSED = "market closed"
REASON_RESOLVED = "market resolved"
REASON_UNLISTED = "listing not confirmed"
MARKET_TYPE_USER = 2


def norm_cid(cid: str) -> str:
    """DB ids are lowercase 0x hex (from bytes.hex())."""
    return "0x" + (cid or "").strip().lower().removeprefix("0x")


def halts_at(m: Market, settings) -> int | None:
    if not settings.trading_halt_at_close or not m.close_time:
        return None
    return int(m.close_time)


def trading_open(m: Market, settings, now: int | None = None) -> bool:
    if m.resolved:
        return False
    at = halts_at(m, settings)
    if at is None:
        return True
    return int(time.time() if now is None else now) < at


async def trading_halt_reason(db: AsyncSession, condition_id: str, settings, now: int | None = None) -> str | None:
    """None when trading is allowed. Unknown markets pass (the chain decides)."""
    m = await db.get(Market, norm_cid(condition_id))
    if m is None:
        return None
    if m.resolved:
        # Unconditional: MarketAMM already reverts buys/sells once the CTF is resolved.
        return REASON_RESOLVED
    if m.market_type == MARKET_TYPE_USER:
        # Unconfirmed (indexed/prepared) or rejected user listings are hidden; never sponsor trades on them.
        listing = await db.get(MarketListing, m.condition_id)
        if listing is None or listing.status != "confirmed":
            return REASON_UNLISTED
    at = halts_at(m, settings)
    if at is not None and int(time.time() if now is None else now) >= at:
        return REASON_CLOSED
    return None
