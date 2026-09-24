import logging
import math
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.router import require_operator
from app.config import get_settings
from app.db import get_db
from app.models import EmissionDistribution, RelayJob

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/emissions", tags=["emissions"])

# Hard ceiling for a 100-recipient distribute; the relayer's matchOrders cap does not apply here.
DISTRIBUTE_GAS_CAP = 10_000_000
LIVE = ("sending", "sent", "confirmed")
IN_FLIGHT = ("sending", "sent")


class Recipient(BaseModel):
    address: str
    amount: str


class DistributeRequest(BaseModel):
    program: int
    recipients: list[Recipient]
    # Optional client key: the same payload under a new key is a deliberate second distribution.
    idempotencyKey: str = ""


def _public(row: EmissionDistribution) -> dict:
    return {
        "distributionId": row.id,
        "status": row.status,
        "txHash": row.tx_hash,
        "nonce": row.nonce,
        "program": row.program,
        "recipientCount": row.recipient_count,
        "totalAmount": int(row.total_amount or 0),
        "payloadHash": row.payload_hash,
        "blockNumber": row.block_number,
        "lastError": row.last_error,
    }


async def _nonce_floor(db: AsyncSession, sender: str) -> int | None:
    """One past the highest nonce any in-flight relay job or distribution of this sender holds."""
    tops = []
    for col, status_col, sender_col in (
        (RelayJob.nonce, RelayJob.status, RelayJob.sender),
        (EmissionDistribution.nonce, EmissionDistribution.status, EmissionDistribution.sender),
    ):
        top = (await db.execute(select(func.max(col)).where(status_col.in_(IN_FLIGHT), sender_col == sender))).scalar()
        if top is not None:
            tops.append(int(top))
    return max(tops) + 1 if tops else None


async def _refresh(chain, row: EmissionDistribution, settings, now: int) -> None:
    """Move a sending/sent row on from chain state. Failure is only concluded once the nonce is mined
    past and no receipt exists after RELAYER_RESUBMIT_AFTER_SECONDS (a lagging RPC must not trigger a resend)."""
    from app.relayer.redact import rpc_reason

    if row.status not in IN_FLIGHT or not row.tx_hash:
        return
    try:
        r = await chain.get_receipt(row.tx_hash)
        if r is not None:
            row.block_number = int(r["blockNumber"])
            if int(r["status"]) == 1:
                row.status = "confirmed"
                row.last_error = ""
            else:
                row.status = "failed"
                row.last_error = f"reverted in {row.tx_hash}"
            return
        if row.nonce is not None and now - int(row.sent_at or 0) >= settings.relayer_resubmit_after_seconds:
            if int(await chain.latest_nonce(row.sender)) > int(row.nonce):
                row.status = "failed"
                row.last_error = "nonce consumed by another tx"
    except Exception as exc:
        row.last_error = f"status check: {rpc_reason(exc)}"


async def _mined_earlier(chain, db: AsyncSession, payload_hash: str, key: str) -> EmissionDistribution | None:
    """A 'failed' row of the same payload whose tx did mine after all (e.g. the RPC lagged)."""
    rows = (
        await db.execute(
            select(EmissionDistribution)
            .where(
                EmissionDistribution.payload_hash == payload_hash,
                EmissionDistribution.idempotency_key == key,
                EmissionDistribution.status == "failed",
            )
            .order_by(EmissionDistribution.id.desc())
        )
    ).scalars().all()
    for row in rows:
        if not row.tx_hash:
            continue
        try:
            r = await chain.get_receipt(row.tx_hash)
        except Exception:
            continue
        if r is not None and int(r["status"]) == 1:
            row.status = "confirmed"
            row.block_number = int(r["blockNumber"])
            row.last_error = ""
            return row
    return None


async def _live_rows(db: AsyncSession, chain, payload_hash: str, key: str, settings, now: int) -> list[EmissionDistribution]:
    """Live (sending/sent/confirmed) rows of this payload + key, moved on from chain state first."""
    prior = (
        await db.execute(
            select(EmissionDistribution)
            .where(
                EmissionDistribution.payload_hash == payload_hash,
                EmissionDistribution.idempotency_key == key,
                EmissionDistribution.status.in_(LIVE),
            )
            .order_by(EmissionDistribution.id)
        )
    ).scalars().all()
    for row in prior:
        await _refresh(chain, row, settings, now)
    await db.commit()
    return [r for r in prior if r.status in LIVE]


async def _duplicate(db: AsyncSession, chain, row: EmissionDistribution) -> JSONResponse:
    """Answer a repeat of a recorded distribution without signing anything new."""
    from app.relayer.chain import classify_rpc_error
    from app.relayer.redact import rpc_reason

    if row.status == "confirmed":
        return JSONResponse(status_code=409, content={"detail": "Already distributed", **_public(row)})
    if row.status == "sending" and row.raw_tx:
        # Write-ahead record whose broadcast is unconfirmed: resend the same bytes, never a new nonce.
        try:
            await chain.send_raw(bytes.fromhex(row.raw_tx.removeprefix("0x")))
            row.status = "sent"
        except Exception as e:
            if classify_rpc_error(e) in ("known", "underpriced"):
                row.status = "sent"
            else:
                row.last_error = f"rebroadcast: {rpc_reason(e)}"
        await db.commit()
    return JSONResponse(status_code=202, content={**_public(row), "duplicate": True})


async def _leader_conn(settings):
    """Postgres: one nonce authority across instances. Proceed when this process's relayer worker holds the
    leader lock (the shared NonceManager lock serializes with it); otherwise hold the lock for the send."""
    from app.db import engine
    from app.relayer import worker as worker_mod

    for w in (worker_mod.get_active_worker(), worker_mod.get_manual_worker()):
        if w is not None and w._leader_conn is not None:
            return None
    if engine.dialect.name != "postgresql":
        return None
    conn = await engine.connect()
    try:
        got = await worker_mod._leader(conn, settings.relayer_leader_lock_key)
    except Exception:
        await conn.close()
        raise
    if not got:
        await conn.close()
        raise HTTPException(status_code=409, detail="Relayer leader is active on another instance; retry")
    return conn


async def _release(conn, settings) -> None:
    if conn is None:
        return
    from sqlalchemy import text

    try:
        await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": settings.relayer_leader_lock_key})
        await conn.commit()
    except Exception:
        try:
            await conn.invalidate()
        except Exception:
            pass
    try:
        await conn.close()
    except Exception:
        pass


@router.post("/distribute")
async def distribute(
    req: DistributeRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Distribute OU emissions from treasury to recipients.

    Operator-only. Calls EmissionsDistributor.distribute which transfers from treasury.
    Never mints. Program IDs: 0=LP, 1=maker, 2=agent, 3=quest.
    Signs with RELAYER_PRIVATE_KEY through the relayer's shared NonceManager (above the in-flight nonce
    floor) so it never races the CLOB worker for a nonce. The contract has no idempotency key, so every
    signed tx is recorded write-ahead: a retry of the same payload (and idempotencyKey) returns the
    recorded distribution instead of paying twice, and an ambiguous broadcast answers 202 with its hash.
    The duplicate check is repeated under the nonce lock and backed by a partial unique index, so two
    concurrent identical requests send exactly one tx.
    """
    settings = get_settings()
    if not settings.emissions_distributor_address:
        raise HTTPException(status_code=503, detail="Emissions distributor not configured")

    if not settings.ou_token_address or not settings.relayer_private_key:
        raise HTTPException(status_code=503, detail="OU token or relayer not configured")

    if req.program not in (0, 1, 2, 3):
        raise HTTPException(status_code=400, detail="Invalid program ID (must be 0-3)")

    if len(req.recipients) == 0:
        raise HTTPException(status_code=400, detail="No recipients provided")

    if len(req.recipients) > 100:
        raise HTTPException(status_code=400, detail="Too many recipients (max 100)")

    from eth_abi import encode
    from web3 import Web3

    from app.relayer.chain import classify_rpc_error, get_chain_client
    from app.relayer.gas import quote_fees
    from app.relayer.nonce import get_nonce_manager
    from app.relayer.queue import relayer_account
    from app.relayer.redact import redact, rpc_reason

    account = relayer_account(settings)
    if account is None:
        raise HTTPException(status_code=503, detail="OU token or relayer not configured")

    try:
        distributor_address = Web3.to_checksum_address(settings.emissions_distributor_address)
        recipient_addresses = [Web3.to_checksum_address(r.address) for r in req.recipients]
        amounts = [int(r.amount) for r in req.recipients]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid recipient: {e}")
    if any(a <= 0 for a in amounts):
        raise HTTPException(status_code=400, detail="Amounts must be positive")

    # distribute(uint256,address[],uint256[])
    func_selector = Web3.keccak(text="distribute(uint256,address[],uint256[])")[:4]
    encoded_params = encode(["uint256", "address[]", "uint256[]"], [req.program, recipient_addresses, amounts])
    data = Web3.to_hex(func_selector + encoded_params)
    payload_hash = Web3.to_hex(Web3.keccak(encoded_params)).lower()
    key = req.idempotencyKey.strip()[:128]
    sender = account.address.lower()
    chain = get_chain_client()
    now = int(time.time())

    # Fast path; the authoritative check is repeated under the nonce lock below.
    live = await _live_rows(db, chain, payload_hash, key, settings, now)
    if live:
        return await _duplicate(db, chain, live[-1])

    chain_estimate_tx = {"from": account.address, "to": distributor_address, "data": data}
    try:
        estimate = await chain.estimate_gas(chain_estimate_tx)
    except Exception as e:
        if classify_rpc_error(e) == "revert":
            raise HTTPException(status_code=400, detail=f"Distribution would revert: {redact(str(e))[:500]}")
        logger.error("emissions estimate failed: %s", redact(repr(e))[:500])
        raise HTTPException(status_code=503, detail="RPC unavailable")
    gas = min(math.ceil(estimate * (10_000 + settings.relayer_gas_buffer_bps) / 10_000), DISTRIBUTE_GAS_CAP)

    try:
        block = await chain.latest_block()
        base = block.get("baseFeePerGas")
        try:
            tip = await chain.max_priority_fee()
        except Exception:
            tip = 0
        gas_price = await chain.gas_price() if base is None else None
    except Exception as e:
        logger.error("emissions fee lookup failed: %s", redact(repr(e))[:500])
        raise HTTPException(status_code=503, detail="RPC unavailable")
    fees = quote_fees(base, tip, gas_price, settings)
    if fees is None:
        raise HTTPException(status_code=503, detail="Gas price above RELAYER_MAX_FEE_PER_GAS_WEI")

    tx = {
        "chainId": settings.chain_id,
        "to": distributor_address,
        "value": 0,
        "data": data,
        "gas": gas,
    }
    if fees.legacy_gas_price is not None:
        tx["gasPrice"] = fees.legacy_gas_price
    else:
        tx["type"] = 2
        tx["maxFeePerGas"] = fees.max_fee
        tx["maxPriorityFeePerGas"] = fees.tip

    nm = get_nonce_manager(chain, account.address)
    # Nonce lock first, then (Postgres) the leader lock, released before the nonce lock: a second identical
    # request in this process waits here until the first has recorded and sent, then sees its row.
    async with nm.lock:
        leader = await _leader_conn(settings)
        try:
            # Re-check under both locks: an identical request that raced this one through the estimate
            # (double-click, client retry) has recorded its write-ahead row by now. Across instances the
            # partial unique index below decides instead.
            live = await _live_rows(db, chain, payload_hash, key, settings, now)
            if live:
                return await _duplicate(db, chain, live[-1])
            nonce = await nm.reserve(await _nonce_floor(db, sender))
            tx["nonce"] = nonce
            signed_tx = account.sign_transaction(tx)
            row = EmissionDistribution(
                program=req.program,
                payload_hash=payload_hash,
                idempotency_key=key,
                sender=sender,
                nonce=nonce,
                raw_tx=Web3.to_hex(signed_tx.raw_transaction),
                tx_hash=Web3.to_hex(signed_tx.hash).lower(),
                status="sending",
                recipient_count=len(req.recipients),
                total_amount=str(sum(amounts)),
                sent_at=now,
                last_error="",
            )
            db.add(row)
            try:
                await db.commit()  # write-ahead: the signed tx is on record before it can reach a node
            except IntegrityError:
                # uq_emission_live_payload: another process recorded the same payload first. Nothing was sent.
                await db.rollback()
                nm.release(nonce)
                live = await _live_rows(db, chain, payload_hash, key, settings, now)
                if live:
                    return await _duplicate(db, chain, live[-1])
                raise HTTPException(status_code=409, detail="Concurrent distribution of this payload; retry")
            try:
                await chain.send_raw(signed_tx.raw_transaction)
                kind, err = None, ""
            except Exception as e:
                kind, err, reason = classify_rpc_error(e), redact(str(e))[:500], rpc_reason(e)

            if kind in (None, "known"):
                row.status = "sent"
                await db.commit()
                return _public(row)
            if kind in ("insufficient_funds", "revert"):
                # Definitely rejected by the node: the nonce was never used.
                nm.release(nonce)
                row.status = "failed"
                row.last_error = err
                await db.commit()
                return JSONResponse(status_code=502, content={"detail": "Distribution failed", **_public(row)})
            if kind in ("nonce_low", "underpriced"):
                await nm.resync()
                # Retire this row first: flipping an earlier row to confirmed while it is still live
                # would violate uq_emission_live_payload.
                row.status = "failed"
                row.last_error = err
                await db.flush()
                earlier = await _mined_earlier(chain, db, payload_hash, key)
                await db.commit()
                if earlier is not None:
                    return JSONResponse(status_code=200, content={**_public(earlier), "duplicate": True})
                return JSONResponse(status_code=502, content={"detail": "Distribution failed", **_public(row)})
            # Timeout / dropped connection: the node may have accepted it. Keep the nonce and the record.
            row.last_error = f"broadcast unconfirmed: {reason}"
            await db.commit()
            return JSONResponse(status_code=202, content={**_public(row), "status": "unknown"})
        finally:
            await _release(leader, settings)


@router.get("/distributions/{distribution_id}")
async def get_distribution(
    distribution_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Poll a distribution: moves sending/sent on from its receipt."""
    from app.relayer.chain import get_chain_client

    row = await db.get(EmissionDistribution, distribution_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    await _refresh(get_chain_client(), row, get_settings(), int(time.time()))
    await db.commit()
    return _public(row)
