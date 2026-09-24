"""Off-chain crossing engine for the leftover Exchange CLOB.

Fills are applied to the DB optimistically. With the relayer ready each fill
becomes one RelayJob (sent by app.relayer.worker, rolled back if it never
lands); with the relayer off the fill is recorded as `offchain` only.

`Order.filled` only ever moves through guarded SQL
(`filled = filled + q WHERE filled + q <= amount`), so concurrent POST /orders
calls and worker rollbacks on the same resting order cannot lose updates or
queue more fill than the order's amount.
"""

from __future__ import annotations

import time

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.markets.trading import trading_halt_reason
from app.models import Order, Trade
from app.relayer.queue import enqueue_match, relayer_ready


def _remaining(o: Order) -> int:
    return o.amount - o.filled


def _crosses(taker: Order, maker: Order, min_expiry: int = 0) -> bool:
    if taker.condition_id != maker.condition_id:
        return False
    if taker.outcome != maker.outcome:
        return False
    if taker.is_buy == maker.is_buy:
        return False
    if taker.cancelled or maker.cancelled:
        return False
    if taker.maker == maker.maker:
        # Self-match: skip rather than wash-trade against yourself.
        return False
    if taker.expiry <= min_expiry or maker.expiry <= min_expiry:
        return False
    if taker.is_buy:
        return taker.price >= maker.price
    return maker.price >= taker.price


async def _take(db: AsyncSession, o: Order, qty: int) -> bool:
    """Atomically add `qty` to o.filled if the order is live and has room. Syncs the instance."""
    res = await db.execute(
        update(Order)
        .where(Order.id == o.id, Order.cancelled.is_(False), Order.filled + qty <= Order.amount)
        .values(filled=Order.filled + qty)
        .execution_options(synchronize_session=False)
    )
    await db.refresh(o, ["filled", "cancelled"])
    return res.rowcount == 1


async def _give_back(db: AsyncSession, o: Order, qty: int) -> None:
    await db.execute(
        update(Order)
        .where(Order.id == o.id, Order.filled >= qty)
        .values(filled=Order.filled - qty)
        .execution_options(synchronize_session=False)
    )
    await db.refresh(o, ["filled", "cancelled"])


async def try_match(db: AsyncSession, incoming: Order) -> list[dict]:
    s = get_settings()
    if await trading_halt_reason(db, incoming.condition_id, s):
        # TRADING_HALT_AT_CLOSE (or resolved): nothing crosses once the market is halted.
        return []
    # Pending ORM changes (e.g. a culprit's cancelled flag) must be in the DB before guarded updates.
    await db.flush()
    min_expiry = int(time.time()) + s.relayer_min_expiry_seconds
    others = (
        await db.execute(
            select(Order).where(
                Order.condition_id == incoming.condition_id,
                Order.cancelled.is_(False),
                Order.expiry > min_expiry,
                Order.id != incoming.id,
            )
        )
    ).scalars().all()
    makers = [o for o in others if _crosses(incoming, o, min_expiry) and _remaining(o) > 0]
    if incoming.is_buy:
        makers.sort(key=lambda o: (o.price, o.id))
    else:
        makers.sort(key=lambda o: (-o.price, o.id))

    relay = relayer_ready(s)
    fills = []
    for maker in makers:
        qty = 0
        # Two tries: a concurrent match may have taken part of the maker since we read it.
        for _ in range(2):
            qty = min(_remaining(incoming), _remaining(maker))
            if qty <= 0 or maker.cancelled:
                qty = 0
                break
            if not await _take(db, maker, qty):
                qty = 0
                continue
            if not await _take(db, incoming, qty):
                await _give_back(db, maker, qty)
                qty = 0
                if incoming.cancelled:
                    return fills
                continue
            break
        if qty <= 0:
            if incoming.cancelled or _remaining(incoming) <= 0:
                break
            continue
        volume = qty * maker.price // 1_000_000
        fee = volume * s.fee_bps_taker // 10_000
        job = await enqueue_match(db, incoming, maker, qty) if relay else None
        status = "pending" if job is not None else "offchain"
        trade = Trade(
            condition_id=incoming.condition_id,
            taker=incoming.maker,
            maker=maker.maker,
            fill_amount=qty,
            volume=volume,
            fee=fee,
            tx_hash="",
            status=status,
            relay_job_id=job.id if job is not None else None,
        )
        db.add(trade)
        fills.append(
            {
                "makerHash": maker.order_hash,
                "takerHash": incoming.order_hash,
                "fillAmount": qty,
                "volume": volume,
                "fee": fee,
                "txHash": "",
                "status": status,
                "relayJobId": job.id if job is not None else None,
            }
        )
        if _remaining(incoming) <= 0:
            break
    return fills
