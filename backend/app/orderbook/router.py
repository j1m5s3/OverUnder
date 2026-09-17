from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.router import get_current_user
from app.db import get_db
from app.models import Order, Trade, User
from app.orderbook.matcher import try_match

router = APIRouter(tags=["orderbook"])


class OrderIn(BaseModel):
    maker: str
    isBuy: bool
    conditionId: str
    outcome: int
    price: int
    amount: int
    salt: int
    nonce: int
    expiry: int
    signature: str
    orderHash: str


@router.post("/orders")
async def post_order(
    body: OrderIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.address != body.maker.lower():
        raise HTTPException(400, "maker must match session")
    existing = (
        await db.execute(select(Order).where(Order.order_hash == body.orderHash))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "duplicate order")
    order = Order(
        order_hash=body.orderHash,
        maker=body.maker.lower(),
        condition_id=body.conditionId,
        is_buy=body.isBuy,
        outcome=body.outcome,
        price=body.price,
        amount=body.amount,
        salt=body.salt,
        nonce=body.nonce,
        expiry=body.expiry,
        signature=body.signature,
    )
    db.add(order)
    await db.commit()
    fills = await try_match(db, order)
    await db.commit()
    return {"order": _order(order), "fills": fills}


@router.delete("/orders/{order_hash}")
async def cancel(
    order_hash: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = (
        await db.execute(select(Order).where(Order.order_hash == order_hash))
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(404, "not found")
    if order.maker != user.address:
        raise HTTPException(403, "not maker")
    order.cancelled = True
    await db.commit()
    return {"cancelled": True, "orderHash": order_hash}


@router.get("/orderbook/{condition_id}")
async def book(condition_id: str, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(Order).where(
                Order.condition_id == condition_id,
                Order.cancelled.is_(False),
            )
        )
    ).scalars().all()
    bids = [_order(o) for o in rows if o.is_buy and o.filled < o.amount]
    asks = [_order(o) for o in rows if (not o.is_buy) and o.filled < o.amount]
    bids.sort(key=lambda o: -o["price"])
    asks.sort(key=lambda o: o["price"])
    return {"bids": bids, "asks": asks}


@router.get("/trades/{condition_id}")
async def trades(condition_id: str, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(Trade).where(Trade.condition_id == condition_id).order_by(Trade.id.desc()))
    ).scalars().all()
    return [
        {
            "id": t.id,
            "taker": t.taker,
            "maker": t.maker,
            "fillAmount": t.fill_amount,
            "volume": t.volume,
            "fee": t.fee,
            "txHash": t.tx_hash,
        }
        for t in rows
    ]


def _order(o: Order) -> dict:
    return {
        "orderHash": o.order_hash,
        "maker": o.maker,
        "conditionId": o.condition_id,
        "isBuy": o.is_buy,
        "outcome": o.outcome,
        "price": o.price,
        "amount": o.amount,
        "filled": o.filled,
        "remaining": o.amount - o.filled,
        "expiry": o.expiry,
        "cancelled": o.cancelled,
    }
