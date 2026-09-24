"""RelayJob queue helpers shared by the matcher, the cancel route and the worker."""

from __future__ import annotations

import time

from eth_account import Account
from eth_account.signers.local import LocalAccount
from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Order, RelayJob, Trade
from app.relayer.redact import redact

TERMINAL = ("confirmed", "failed", "cancelled")
IN_FLIGHT = ("sending", "sent")


def relayer_account(s: Settings) -> LocalAccount | None:
    key = (s.relayer_private_key or "").strip()
    if not key:
        return None
    try:
        return Account.from_key(key)
    except Exception:
        return None


def relayer_ready(s: Settings) -> bool:
    """RELAYER_ENABLED, a parseable key and a configured Exchange. The key alone never enables anything."""
    from app.orderbook.eip712 import get_exchange_domain

    if not s.relayer_enabled:
        return False
    if relayer_account(s) is None:
        return False
    return get_exchange_domain() is not None


async def enqueue_match(db: AsyncSession, taker: Order, maker: Order, fill: int) -> RelayJob:
    """One new job per match call, never a reused one.

    A pair can legitimately match more than once (re-match after a failed job, or a later partial
    fill while an earlier job is in flight). Reusing the pair's old job would link the new fill to a
    terminal job (never sent, never rolled back) or to a live job whose fill_amount excludes it.
    """
    job = RelayJob(
        kind="match_orders",
        condition_id=taker.condition_id,
        taker_hash=taker.order_hash,
        maker_hash=maker.order_hash,
        fill_amount=fill,
        status="pending",
        attempts=0,
        tx_hashes=[],
        next_attempt_at=0,
        matched_at=int(time.time()),
    )
    db.add(job)
    await db.flush()
    return job


async def rollback_job(db: AsyncSession, job: RelayJob, status: str, reason: str) -> bool:
    """Undo the optimistic DB fill for a job that will never land on chain.

    Conditional on the status the caller saw: if another writer moved the job first (the worker's
    claim to 'sending', a concurrent cancel) nothing is touched and False is returned. Fills are
    lowered in SQL (filled = filled - fill), never written back as a stale absolute value.
    """
    expected = job.status
    if expected in TERMINAL:
        return False
    await db.flush()
    res = await db.execute(
        update(RelayJob)
        .where(RelayJob.id == job.id, RelayJob.status == expected)
        .values(status=status, last_error=redact(reason)[:2000])
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        await db.refresh(job)
        return False
    f = int(job.fill_amount)
    hashes = [job.taker_hash, job.maker_hash]
    await db.execute(
        update(Order)
        .where(Order.order_hash.in_(hashes))
        .values(filled=case((Order.filled > f, Order.filled - f), else_=0))
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        update(Trade)
        .where(Trade.relay_job_id == job.id)
        .values(status="failed")
        .execution_options(synchronize_session=False)
    )
    await db.refresh(job)
    # Callers (the worker's _retire_failed_side -> try_match) keep using these instances.
    await db.execute(select(Order).where(Order.order_hash.in_(hashes)).execution_options(populate_existing=True))
    return True


def job_public(job: RelayJob) -> dict:
    """API shape. raw_tx is deliberately absent."""
    return {
        "id": job.id,
        "kind": job.kind,
        "conditionId": job.condition_id,
        "takerHash": job.taker_hash,
        "makerHash": job.maker_hash,
        "fillAmount": job.fill_amount,
        "status": job.status,
        "attempts": job.attempts,
        "nonce": job.nonce,
        "txHash": job.tx_hash,
        "txHashes": list(job.tx_hashes or []),
        "gasLimit": job.gas_limit,
        "maxFeePerGas": job.max_fee_per_gas,
        "maxPriorityFeePerGas": job.max_priority_fee_per_gas,
        "blockNumber": job.block_number,
        "lastError": redact(job.last_error),
        "nextAttemptAt": job.next_attempt_at,
        "sentAt": job.sent_at,
        "confirmedAt": job.confirmed_at,
        "createdAt": job.created_at.isoformat() if job.created_at else None,
    }
