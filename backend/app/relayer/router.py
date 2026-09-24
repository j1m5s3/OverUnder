"""Relayer visibility and manual drain. raw_tx never leaves the server."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.router import get_current_user, require_operator
from app.config import get_settings
from app.db import get_db
from app.models import Order, RelayJob, User
from app.relayer import worker as worker_mod
from app.relayer.chain import get_chain_client
from app.relayer.nonce import peek_nonce_manager
from app.relayer.queue import job_public, relayer_account, relayer_ready
from app.relayer.redact import redact

router = APIRouter(prefix="/relayer", tags=["relayer"])

STATUSES = ("pending", "sending", "sent", "confirmed", "failed", "cancelled")


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    job = await db.get(RelayJob, job_id)
    if job is None:
        raise HTTPException(404, "not found")
    if not user.is_operator:
        makers = (
            await db.execute(select(Order.maker).where(Order.order_hash.in_([job.taker_hash, job.maker_hash])))
        ).scalars().all()
        if user.address not in {m.lower() for m in makers}:
            # 404, not 403: do not reveal that the job exists.
            raise HTTPException(404, "not found")
    return job_public(job)


@router.get("/jobs")
async def list_jobs(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_operator),
):
    q = select(RelayJob).order_by(RelayJob.id.desc()).limit(limit)
    if status:
        if status not in STATUSES:
            raise HTTPException(400, f"status must be one of {', '.join(STATUSES)}")
        q = q.where(RelayJob.status == status)
    rows = (await db.execute(q)).scalars().all()
    return {"jobs": [job_public(j) for j in rows]}


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db), _: User = Depends(require_operator)):
    s = get_settings()
    ready = relayer_ready(s)
    account = relayer_account(s)
    address = account.address if account is not None else None
    counts = {k: 0 for k in STATUSES}
    for st, n in (await db.execute(select(RelayJob.status, func.count()).group_by(RelayJob.status))).all():
        counts[st] = int(n)
    last = (
        await db.execute(
            select(RelayJob.last_error).where(RelayJob.last_error != "").order_by(RelayJob.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    nm = peek_nonce_manager(address) if address else None
    balance = None
    if ready and address:
        try:
            balance = await get_chain_client().eth_balance(address)
        except Exception:
            balance = None
    # With RELAYER_WORKER_ENABLED=false (Cloud Run + scheduler ticks) only the manual worker has run.
    active = worker_mod.get_active_worker() or worker_mod.get_manual_worker()
    return {
        "enabled": s.relayer_enabled,
        "workerEnabled": s.relayer_worker_enabled,
        "ready": ready,
        "workerRunning": worker_mod.worker_running(),
        "relayerAddress": address,
        "nextNonce": nm.peek() if nm else None,
        "ethBalanceWei": balance,
        "counts": counts,
        "lastError": redact(last or (active.last_error if active else "") or ""),
        "lastTickAt": active.last_tick_at if active else None,
    }


@router.post("/tick")
async def tick(_: User = Depends(require_operator)):
    """One worker pass: the manual / serverless drain when the background loop is off."""
    s = get_settings()
    if not relayer_ready(s):
        raise HTTPException(409, "relayer not enabled or not ready")
    active = worker_mod.get_active_worker()
    if active is not None:
        return await active.tick()
    w = worker_mod.manual_worker(s)
    if w is None:
        raise HTTPException(409, "relayer not enabled or not ready")
    # release=True unlocks the Postgres leader lock inside the tick lock, so a concurrent manual tick
    # queued on the same worker can never run on a leader connection this one is closing.
    return await w.tick(release=True)
