"""Leftover Exchange CLOB API. Orders are EIP-712 verified here or never stored.

CDP smart accounts cannot make orders: the deployed Exchange has no EIP-1271,
so only EOA (SIWE) makers can produce a signature that recovers to `maker`.

TRADING_HALT_AT_CLOSE covers the CLOB too: POST /orders answers 409 once the
market is closed or resolved, and nothing matches after the halt.

DELETE /orders/{hash} is an off-chain cancel. Once an order's signature has
appeared on chain (any relayed matchOrders, even a reverted one) anyone can
still fill its remainder through the permissionless Exchange.matchOrders, so
the response flags `onchainCancelRequired` and carries the Exchange order
tuple for the maker's own Exchange.cancelOrder / incrementNonce.
"""

import re
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.router import get_current_user
from app.config import get_settings
from app.db import get_db
from app.markets.trading import trading_halt_reason
from app.models import Order, RelayJob, Trade, User
from app.orderbook.eip712 import (
    BadOrderSignature,
    OrderFields,
    get_exchange_domain,
    order_hash_hex,
    u256,
    verify_order_signature,
)
from app.orderbook.matcher import try_match
from app.relayer.chain import get_chain_client
from app.relayer.preflight import EXCHANGE_TAKER_FEE_BPS, order_usdc_need, preflight_order
from app.relayer.queue import IN_FLIGHT, relayer_ready, rollback_job

router = APIRouter(tags=["orderbook"])

_HEX32 = re.compile(r"^0x[0-9a-fA-F]{64}$")
_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")
_I63 = 2**63


class OrderIn(BaseModel):
    maker: str
    isBuy: bool
    conditionId: str
    outcome: int
    price: int
    amount: int
    # uint256: JSON number or decimal / 0x-hex string (JS cannot hold 2**255 as a number).
    salt: int | str
    nonce: int
    expiry: int
    signature: str
    orderHash: str


def _strip0x(v: str) -> str:
    return v[2:] if v.lower().startswith("0x") else v


def _check_fields(body: OrderIn) -> int:
    """Field-level validation mirroring Exchange._validate. Returns the parsed salt."""
    if not _ADDR.match(body.maker):
        raise HTTPException(400, "maker must be a 20-byte hex address")
    if not _HEX32.match(body.conditionId):
        raise HTTPException(400, "conditionId must be 0x + 64 hex")
    if not _HEX32.match(body.orderHash):
        raise HTTPException(400, "orderHash must be 0x + 64 hex")
    if body.outcome not in (0, 1):
        raise HTTPException(400, "outcome must be 0 or 1")
    if not 0 < body.price <= 1_000_000:
        raise HTTPException(400, "price must be in (0, 1000000]")
    if not 0 < body.amount < _I63:
        raise HTTPException(400, "amount out of range")
    if not 0 <= body.nonce < _I63:
        raise HTTPException(400, "nonce out of range")
    if not 0 < body.expiry < _I63:
        raise HTTPException(400, "expiry out of range")
    try:
        return u256(body.salt)
    except ValueError as exc:
        raise HTTPException(400, "salt must be a uint256") from exc


@router.post("/orders")
async def post_order(
    body: OrderIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = get_settings()
    if user.address != body.maker.lower():
        raise HTTPException(400, "maker must match session")
    salt = _check_fields(body)
    halt = await trading_halt_reason(db, body.conditionId, s)
    if halt:
        raise HTTPException(409, halt)
    dom = get_exchange_domain()
    if dom is None:
        raise HTTPException(503, "exchange not configured")
    exchange, chain_id = dom
    fields = OrderFields.from_body(body)
    order_hash = order_hash_hex(fields, exchange, chain_id)
    if order_hash != body.orderHash.lower():
        raise HTTPException(400, "orderHash mismatch")
    try:
        verify_order_signature(fields, body.signature, exchange, chain_id)
    except BadOrderSignature as exc:
        raise HTTPException(400, "bad signature") from exc
    if body.expiry <= int(time.time()) + s.relayer_min_expiry_seconds:
        raise HTTPException(400, "order expired")

    existing = (await db.execute(select(Order).where(Order.order_hash == order_hash))).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "duplicate order")

    if s.relayer_enabled:
        if not relayer_ready(s):
            raise HTTPException(503, "relayer misconfigured")
        open_orders = await _open_orders(db, body.maker.lower(), body.nonce)
        if len(open_orders) >= s.relayer_max_open_orders_per_maker:
            raise HTTPException(400, "too many open orders")
        if s.relayer_preflight_on_post:
            reserved = _reserved(open_orders, fields)
            try:
                reasons = await preflight_order(get_chain_client(), fields, exchange, reserved=reserved)
            except Exception as exc:
                raise HTTPException(503, "chain unavailable for preflight") from exc
            if reasons:
                return JSONResponse(status_code=400, content={"detail": "preflight failed", "reasons": reasons})

    order = Order(
        order_hash=order_hash,
        maker=body.maker.lower(),
        condition_id=body.conditionId.lower(),
        is_buy=body.isBuy,
        outcome=body.outcome,
        price=body.price,
        amount=body.amount,
        filled=0,
        salt=hex(salt),
        nonce=body.nonce,
        expiry=body.expiry,
        signature="0x" + _strip0x(body.signature).lower(),
        cancelled=False,
    )
    db.add(order)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "duplicate order") from exc
    fills = await try_match(db, order)
    await db.commit()
    return {"order": _order(order), "fills": fills}


@router.delete("/orders/{order_hash}")
async def cancel(
    order_hash: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    h = order_hash.lower()
    order = (await db.execute(select(Order).where(Order.order_hash == h))).scalar_one_or_none()
    if order is None:
        raise HTTPException(404, "not found")
    if order.maker != user.address:
        raise HTTPException(403, "not maker")
    # FOR UPDATE (Postgres): wait out a worker claim in progress, then see its 'sending' row.
    jobs = (
        await db.execute(
            select(RelayJob)
            .where(or_(RelayJob.taker_hash == h, RelayJob.maker_hash == h))
            .order_by(RelayJob.id)
            .with_for_update()
        )
    ).scalars().all()
    # Exposed: the signed order has been (or may be) in matchOrders calldata. Checked before any
    # rollback lowers `filled`; a reverted tx still published the signature. Conservatively, any
    # fill not explained by never-broadcast pending jobs also counts.
    unsent = sum(int(j.fill_amount) for j in jobs if j.status == "pending" and not j.tx_hashes)
    exposed = order.filled - unsent > 0 or any(
        bool(j.tx_hashes) or j.status in ("sending", "sent", "confirmed") for j in jobs
    )
    order.cancelled = True
    cancelled_jobs: list[int] = []
    in_flight: list[int] = []
    for job in jobs:
        if job.status == "pending" and await rollback_job(db, job, "cancelled", "order cancelled"):
            cancelled_jobs.append(job.id)
        elif job.status in IN_FLIGHT:
            # Already signed/broadcast (or claimed by the worker meanwhile): the chain decides.
            in_flight.append(job.id)
    await db.commit()
    return {
        "cancelled": True,
        "orderHash": h,
        "cancelledJobs": cancelled_jobs,
        "inFlightJobs": in_flight,
        "offchainOnly": True,
        "onchainCancelRequired": bool(exposed or in_flight),
        "cancelOrderArgs": _cancel_args(order),
    }


@router.get("/orderbook/{condition_id}")
async def book(condition_id: str, db: AsyncSession = Depends(get_db)):
    now = int(time.time())
    halt = await trading_halt_reason(db, condition_id, get_settings(), now=now)
    rows = (
        await db.execute(
            select(Order).where(
                Order.condition_id == condition_id.lower(),
                Order.cancelled.is_(False),
                Order.expiry > now,
            )
        )
    ).scalars().all()
    bids = [_order(o) for o in rows if o.is_buy and o.filled < o.amount]
    asks = [_order(o) for o in rows if (not o.is_buy) and o.filled < o.amount]
    bids.sort(key=lambda o: -o["price"])
    asks.sort(key=lambda o: o["price"])
    return {"bids": bids, "asks": asks, "tradingOpen": halt is None, "haltReason": halt}


@router.get("/trades/{condition_id}")
async def trades(condition_id: str, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(Trade).where(Trade.condition_id == condition_id.lower()).order_by(Trade.id.desc())
        )
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
            "status": t.status,
            "relayJobId": t.relay_job_id,
            "blockNumber": t.block_number,
        }
        for t in rows
    ]


def _order(o: Order) -> dict:
    remaining = o.amount - o.filled
    if o.cancelled:
        status = "cancelled"
    elif remaining <= 0:
        status = "filled"
    elif o.expiry <= int(time.time()):
        status = "expired"
    else:
        status = "open"
    return {
        "orderHash": o.order_hash,
        "maker": o.maker,
        "conditionId": o.condition_id,
        "isBuy": o.is_buy,
        "outcome": o.outcome,
        "price": o.price,
        "amount": o.amount,
        "filled": o.filled,
        "remaining": remaining,
        "salt": _salt_text(o.salt),
        "nonce": o.nonce,
        "expiry": o.expiry,
        "cancelled": o.cancelled,
        "status": status,
    }


def _salt_text(salt) -> str:
    """Decimal uint256; legacy rows (e.g. negative decimal text from the old schema) fall back to the raw value."""
    try:
        return str(u256(salt))
    except (ValueError, TypeError):
        return str(salt)


def _cancel_args(o: Order) -> dict:
    """The Exchange Order tuple for Exchange.cancelOrder(order), as JSON (uint256 values as decimal strings)."""
    return {
        "maker": o.maker,
        "isBuy": o.is_buy,
        "conditionId": o.condition_id,
        "outcome": o.outcome,
        "price": str(o.price),
        "amount": str(o.amount),
        "salt": _salt_text(o.salt),
        "nonce": str(o.nonce),
        "expiry": str(o.expiry),
    }


async def _open_orders(db: AsyncSession, maker: str, nonce: int) -> list[Order]:
    """The maker's live orders under the same Exchange nonce (a nonce bump voids the rest on chain)."""
    now = int(time.time())
    return list(
        (
            await db.execute(
                select(Order).where(
                    Order.maker == maker,
                    Order.cancelled.is_(False),
                    Order.expiry > now,
                    Order.nonce == nonce,
                    Order.filled < Order.amount,
                )
            )
        ).scalars().all()
    )


def _reserved(open_orders: list[Order], new: OrderFields) -> int:
    """What the maker's other open orders already claim from the funds the new order needs."""
    if new.is_buy:
        return sum(
            order_usdc_need(o.amount - o.filled, o.price, EXCHANGE_TAKER_FEE_BPS) for o in open_orders if o.is_buy
        )
    cid = "0x" + new.condition_id.hex()
    return sum(
        o.amount - o.filled
        for o in open_orders
        if not o.is_buy and o.condition_id == cid and o.outcome == new.outcome
    )
