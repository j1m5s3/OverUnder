"""RelayJob worker: preflight, sign, broadcast, reconcile receipts, bump stuck txs.

State machine (see RelayJob.status):
  pending -> sending (write-ahead: nonce, raw tx and hash committed before broadcast)
          -> sent -> confirmed | failed (receipt status 0, rolled back)
  pending -> failed (preflight / estimate revert / out of attempts / matched after close) | cancelled

Invariants:
- A crash between commit and broadcast leaves `sending`; the next tick rebroadcasts the identical
  raw tx (with backoff), so a fill is never signed under a second nonce while the first can mine.
- Once a job has signed a tx it is never made terminal or re-signed on a fresh nonce on the
  strength of one RPC answer. "nonce too low" for our own raw tx is ambiguous (our tx may be the
  one that used the nonce, on a lagging or load-balanced RPC): the job stays in flight
  (`nonce_consumed_at`) and keeps polling every hash in `tx_hashes`. Only after
  RELAYER_RESUBMIT_AFTER_SECONDS, with the latest (mined) nonce past `job.nonce`, no receipt, no
  OrderFilled log from any of our hashes and no unexplained on-chain fill, does it go back to
  pending. A rebroadcast that keeps failing leaves the job `sent` + "stuck", never failed.
- A job with prior `tx_hashes` re-checks the chain for its fill before any fresh send.
- Preflight counts what other in-flight jobs will still spend ("busy:" -> defer, not fail).
- Every stored or logged error goes through app.relayer.redact (the RPC URL carries an API key).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Callable

from eth_account.signers.local import LocalAccount
from sqlalchemy import case, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker
from web3 import Web3

from app.config import Settings
from app.db import SessionLocal, engine as default_engine
from app.markets.trading import REASON_CLOSED, halts_at, norm_cid, trading_halt_reason
from app.models import EmissionDistribution, Market, Order, RelayJob, Trade
from app.orderbook.eip712 import OrderFields, get_exchange_domain
from app.relayer.chain import ORDER_FILLED_TOPIC, ChainClient, classify_rpc_error, get_chain_client
from app.relayer.gas import FeeQuote, bump_fees, gas_limit, quote_fees
from app.relayer.nonce import NonceManager, get_nonce_manager
from app.relayer.preflight import Committed, preflight_match
from app.relayer.queue import IN_FLIGHT, relayer_account, relayer_ready, rollback_job
from app.relayer.redact import redact, rpc_reason

logger = logging.getLogger(__name__)

# Jobs that still hold (or may hold) fill on the orders they reference.
LIVE = ("pending", "sending", "sent")


def _b32(h: str) -> bytes:
    return bytes.fromhex(h[2:] if h.startswith("0x") else h)


def _sig(s: str) -> bytes:
    return bytes.fromhex(s[2:] if s.lower().startswith("0x") else s)


async def _leader(conn: AsyncConnection, key: int) -> bool:
    """Session-level Postgres advisory lock; held for as long as `conn` stays open."""
    got = (await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})).scalar()
    await conn.commit()
    return bool(got)


class RelayerWorker:
    def __init__(
        self,
        chain: ChainClient,
        account: LocalAccount,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
        now_fn: Callable[[], float] = time.time,
        engine: AsyncEngine | None = None,
    ):
        self.chain = chain
        self.account = account
        self.settings = settings
        self.session_factory = session_factory
        self.now_fn = now_fn
        self.engine = engine or default_engine
        self._tick_lock = asyncio.Lock()
        self._leader_conn: AsyncConnection | None = None
        self.last_error = ""
        self.last_tick_at: int | None = None

    # ------------------------------------------------------------------ helpers
    def _now(self) -> int:
        return int(self.now_fn())

    def _nm(self) -> NonceManager:
        return get_nonce_manager(self.chain, self.account.address)

    def _sender(self) -> str:
        return self.account.address.lower()

    @staticmethod
    def _hashes(job: RelayJob) -> list[str]:
        return [h for h in dict.fromkeys([*(job.tx_hashes or []), job.tx_hash]) if h]

    async def _ensure_leader(self) -> bool:
        if self.engine.dialect.name != "postgresql":
            return True
        if self._leader_conn is not None:
            conn = self._leader_conn
            try:
                await conn.execute(text("SELECT 1"))
                await conn.commit()
                return True
            except Exception:
                logger.warning("relayer lost its leader connection; re-electing")
                await self._drop_leader_conn(conn, invalidate=True)
        conn = await self.engine.connect()
        try:
            got = await _leader(conn, self.settings.relayer_leader_lock_key)
        except Exception:
            await conn.close()
            raise
        if not got:
            await conn.close()
            return False
        self._leader_conn = conn
        # A previous leader may have used nonces this process never saw.
        self._nm().invalidate()
        return True

    async def _drop_leader_conn(self, conn: AsyncConnection | None, invalidate: bool = False) -> None:
        """Compare-and-clear: never drops a newer leader connection than the one the caller saw.
        `invalidate` ends the server session so a session advisory lock cannot survive in the pool
        (a pooled close() only rolls back)."""
        if conn is None:
            return
        if self._leader_conn is conn:
            self._leader_conn = None
        if invalidate:
            try:
                await conn.invalidate()
            except Exception:
                pass
        try:
            await conn.close()
        except Exception:
            pass

    async def release_leadership(self) -> None:
        conn = self._leader_conn
        if conn is None:
            return
        unlocked = False
        try:
            res = await conn.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": self.settings.relayer_leader_lock_key}
            )
            unlocked = bool(res.scalar())
            await conn.commit()
        except Exception:
            unlocked = False
        await self._drop_leader_conn(conn, invalidate=not unlocked)

    async def _orders(self, db: AsyncSession, job: RelayJob) -> tuple[Order | None, Order | None]:
        rows = (
            await db.execute(select(Order).where(Order.order_hash.in_([job.taker_hash, job.maker_hash])))
        ).scalars().all()
        by_hash = {o.order_hash: o for o in rows}
        return by_hash.get(job.taker_hash), by_hash.get(job.maker_hash)

    async def _nonce_floor(self, db: AsyncSession) -> int | None:
        """One past the highest nonce held by an in-flight relay job or emissions distribution."""
        top_job = (
            await db.execute(
                select(func.max(RelayJob.nonce)).where(
                    RelayJob.status.in_(IN_FLIGHT), RelayJob.sender == self._sender()
                )
            )
        ).scalar()
        top_dist = (
            await db.execute(
                select(func.max(EmissionDistribution.nonce)).where(
                    EmissionDistribution.status.in_(IN_FLIGHT), EmissionDistribution.sender == self._sender()
                )
            )
        ).scalar()
        tops = [int(t) for t in (top_job, top_dist) if t is not None]
        return max(tops) + 1 if tops else None

    def _tx(self, nonce: int, to: str, chain_id: int, data: bytes, gas: int, q: FeeQuote) -> dict:
        tx = {
            "chainId": chain_id,
            "nonce": nonce,
            "to": Web3.to_checksum_address(to),
            "value": 0,
            "data": Web3.to_hex(data),
            "gas": gas,
        }
        if q.legacy_gas_price is not None:
            tx["gasPrice"] = q.legacy_gas_price
        else:
            tx["type"] = 2
            tx["maxFeePerGas"] = q.max_fee
            tx["maxPriorityFeePerGas"] = q.tip
        return tx

    @staticmethod
    def _stored_quote(job: RelayJob) -> FeeQuote:
        if job.max_priority_fee_per_gas is None:
            gp = int(job.max_fee_per_gas or 0)
            return FeeQuote(max_fee=gp, tip=gp, legacy_gas_price=gp)
        return FeeQuote(max_fee=int(job.max_fee_per_gas or 0), tip=int(job.max_priority_fee_per_gas))

    @staticmethod
    def _store_quote(job: RelayJob, q: FeeQuote) -> None:
        job.max_fee_per_gas = q.max_fee
        job.max_priority_fee_per_gas = None if q.legacy_gas_price is not None else q.tip

    async def _broadcast(self, raw_hex: str) -> tuple[str | None, str]:
        try:
            await self.chain.send_raw(_sig(raw_hex))
            return None, ""
        except Exception as exc:
            return classify_rpc_error(exc), redact(str(exc))[:500]

    async def _resync(self, nm: NonceManager) -> None:
        async with nm.lock:
            await nm.resync()

    def _back_to_pending(self, job: RelayJob, reason: str, delay: int = 0) -> None:
        job.status = "pending"
        job.nonce = None
        job.raw_tx = ""
        job.nonce_consumed_at = None
        job.next_attempt_at = self._now() + delay
        job.last_error = redact(reason)[:2000]

    async def _retry_or_fail(self, db: AsyncSession, job: RelayJob, stats: dict, reason: str) -> None:
        """Transient failure before anything was signed for this attempt."""
        job.attempts += 1
        if job.attempts >= self.settings.relayer_max_attempts:
            if await rollback_job(db, job, "failed", f"gave up: {reason}"):
                stats["failed"] += 1
            return
        job.last_error = redact(reason)[:2000]
        job.next_attempt_at = self._now() + self.settings.relayer_retry_backoff_seconds * job.attempts
        stats["deferred"] += 1

    def _has_fill_log(self, receipt: dict, job: RelayJob, exchange: str) -> bool:
        logs = receipt.get("logs")
        if logs is None:
            return True
        return any(self._is_fill_log(lg, job, exchange) for lg in logs)

    @staticmethod
    def _is_fill_log(lg: dict, job: RelayJob, exchange: str) -> bool:
        topics = [str(t).lower() for t in lg.get("topics", [])]
        return (
            str(lg.get("address", "")).lower() == exchange.lower()
            and len(topics) >= 3
            and topics[0] == ORDER_FILLED_TOPIC.lower()
            and topics[1] == job.taker_hash.lower()
            and topics[2] == job.maker_hash.lower()
        )

    # --------------------------------------------------------------------- tick
    async def tick(self, release: bool = False) -> dict:
        """One pass. `release=True` (manual / serverless ticks) gives up the Postgres leader lock before the
        tick lock is released, so a queued tick can never run on a leader connection that is being closed."""
        stats = {"reconciled": 0, "sent": 0, "bumped": 0, "confirmed": 0, "failed": 0, "cancelled": 0, "deferred": 0}
        async with self._tick_lock:
            try:
                if not relayer_ready(self.settings):
                    stats["skipped"] = "not ready"
                    return stats
                dom = get_exchange_domain()
                if dom is None:
                    stats["skipped"] = "not ready"
                    return stats
                exchange, chain_id = dom
                if not await self._ensure_leader():
                    stats["skipped"] = "not leader"
                    return stats
                nm = self._nm()
                async with self.session_factory() as db:
                    inflight = (
                        await db.execute(
                            select(RelayJob.id).where(RelayJob.status.in_(IN_FLIGHT)).order_by(RelayJob.id)
                        )
                    ).scalars().all()
                    for job_id in inflight:
                        await self._guard(db, job_id, IN_FLIGHT, stats, self._reconcile, nm, exchange, chain_id)
                    now = self._now()
                    pending = (
                        await db.execute(
                            select(RelayJob.id)
                            .where(RelayJob.status == "pending", RelayJob.next_attempt_at <= now)
                            .order_by(RelayJob.id)
                            .limit(self.settings.relayer_batch_size)
                        )
                    ).scalars().all()
                    for job_id in pending:
                        await self._guard(db, job_id, ("pending",), stats, self._send, nm, exchange, chain_id)
                self.last_tick_at = self._now()
                return stats
            finally:
                if release:
                    await self.release_leadership()

    async def _guard(self, db: AsyncSession, job_id: int, statuses: tuple, stats: dict, step, *args) -> None:
        """Run one job step in isolation: commit on success, roll back and move on otherwise."""
        try:
            # populate_existing: a rollback on an earlier job expires every instance in the session.
            job = await db.get(RelayJob, job_id, populate_existing=True)
            if job is None or job.status not in statuses:
                return
            await step(db, job, stats, *args)
            await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await db.rollback()
            self.last_error = redact(f"job {job_id}: {exc}")[:500]
            stats["deferred"] += 1
            logger.error("relayer job %s failed unexpectedly: %s", job_id, redact(repr(exc))[:500])

    # ---------------------------------------------------------------- reconcile
    async def _reconcile(
        self, db: AsyncSession, job: RelayJob, stats: dict, nm: NonceManager, exchange: str, chain_id: int
    ) -> None:
        s = self.settings
        hashes = self._hashes(job)
        try:
            receipt, mined = await self._find_receipt(hashes)
        except Exception as exc:
            job.last_error = f"receipt lookup: {rpc_reason(exc)}"[:2000]
            stats["deferred"] += 1
            return
        stats["reconciled"] += 1
        now = self._now()

        if receipt is not None:
            await self._apply_receipt(db, job, receipt, mined, stats, exchange, now)
            return

        if job.status == "sending":
            await self._rebroadcast(db, job, stats, nm, exchange, hashes, now)
            return

        if job.nonce_consumed_at is not None:
            await self._settle_consumed(db, job, stats, nm, exchange, now)
            return

        # sent, no receipt yet
        if job.attempts >= s.relayer_max_attempts:
            if not (job.last_error or "").startswith("stuck"):
                job.last_error = "stuck: max attempts"
            # No more bumps, but keep the signed tx alive in case a node dropped it (never terminal).
            due = now - int(job.sent_at or 0) >= s.relayer_resubmit_after_seconds
            if job.raw_tx and due and now >= int(job.next_attempt_at or 0):
                job.next_attempt_at = now + s.relayer_resubmit_after_seconds
                kind, _ = await self._broadcast(job.raw_tx)
                if kind == "nonce_low":
                    await self._nonce_consumed(db, job, stats, exchange, hashes, now)
                    await self._resync(nm)
            return
        if now - int(job.sent_at or 0) < s.relayer_resubmit_after_seconds:
            return
        await self._bump(db, job, nm, stats, exchange, chain_id, hashes)

    async def _find_receipt(self, hashes: list[str]) -> tuple[dict | None, str]:
        for h in hashes:
            r = await self.chain.get_receipt(h)
            if r is not None:
                return r, h
        return None, ""

    async def _confirm(self, db: AsyncSession, job: RelayJob, mined: str, bn: int, now: int, stats: dict) -> None:
        job.status = "confirmed"
        job.tx_hash = mined
        job.block_number = bn
        job.confirmed_at = now
        job.nonce_consumed_at = None
        job.last_error = ""
        await db.execute(
            update(Trade).where(Trade.relay_job_id == job.id).values(status="confirmed", tx_hash=mined, block_number=bn)
        )
        stats["confirmed"] += 1

    async def _apply_receipt(
        self, db: AsyncSession, job: RelayJob, receipt: dict, mined: str, stats: dict, exchange: str, now: int
    ) -> None:
        s = self.settings
        bn = int(receipt["blockNumber"])
        if int(receipt["status"]) == 1 and self._has_fill_log(receipt, job, exchange):
            if job.status == "sending":
                job.status = "sent"
                job.sent_at = job.sent_at or now
            need = max(1, s.relayer_confirmations)
            if need > 1:
                latest = await self.chain.latest_block()
                if int(latest["number"]) - bn + 1 < need:
                    return
            await self._confirm(db, job, mined, bn, now, stats)
            return
        why = "reverted" if int(receipt["status"]) != 1 else "no matching OrderFilled log"
        rolled = await rollback_job(db, job, "failed", f"{why} in {mined}")
        job.tx_hash = mined
        job.block_number = bn
        await db.execute(update(Trade).where(Trade.relay_job_id == job.id).values(tx_hash=mined))
        stats["failed"] += 1
        if rolled:
            await self._retire_after_revert(db, job, exchange)

    async def _retire_after_revert(self, db: AsyncSession, job: RelayJob, exchange: str) -> None:
        """A mined revert restores both orders, still crossing. Re-run preflight against the mined state and
        retire the side at fault, as the preflight path does. No clear cause: leave both resting (no re-match)."""
        taker, maker = await self._orders(db, job)
        if taker is None or maker is None or taker.cancelled or maker.cancelled:
            return
        try:
            block = await self.chain.latest_block()
            reasons = await preflight_match(
                self.chain, OrderFields.from_model(taker), OrderFields.from_model(maker), _b32(job.taker_hash),
                _b32(job.maker_hash), int(job.fill_amount), exchange, int(block["timestamp"]), self.settings,
            )
        except Exception as exc:
            logger.warning("relayer job %s: post-revert preflight failed: %s", job.id, rpc_reason(exc))
            return
        if reasons:
            await self._retire_failed_side(db, taker, maker, reasons)

    async def _rebroadcast(
        self, db: AsyncSession, job: RelayJob, stats: dict, nm: NonceManager, exchange: str, hashes: list[str],
        now: int,
    ) -> None:
        """`sending`: the write-ahead raw tx may or may not be in a mempool. Rebroadcast the same bytes."""
        s = self.settings
        if now < int(job.next_attempt_at or 0):
            return
        kind, err = await self._broadcast(job.raw_tx)
        if kind in (None, "known", "underpriced"):
            # underpriced: a tx with this nonce is already pooled (an earlier copy of ours).
            job.status = "sent"
            job.sent_at = job.sent_at or now
            job.next_attempt_at = 0
            job.last_error = ""
        elif kind == "nonce_low":
            await self._nonce_consumed(db, job, stats, exchange, hashes, now)
            await self._resync(nm)
        elif kind == "insufficient_funds":
            job.last_error = "relayer balance low"
            job.next_attempt_at = now + s.relayer_retry_backoff_seconds
            stats["deferred"] += 1
        else:
            job.attempts += 1
            job.next_attempt_at = now + s.relayer_retry_backoff_seconds * max(1, job.attempts)
            if job.attempts >= s.relayer_max_attempts:
                # The first broadcast may sit in a pool: never terminal while it can still mine.
                job.status = "sent"
                job.sent_at = job.sent_at or now
                job.last_error = f"stuck: rebroadcast failing: {err}"[:2000]
            else:
                job.last_error = f"rebroadcast: {err}"[:2000]
            stats["deferred"] += 1

    async def _nonce_consumed(
        self, db: AsyncSession, job: RelayJob, stats: dict, exchange: str, hashes: list[str], now: int
    ) -> None:
        """'nonce too low' for a tx this job signed: ours may be what used it. Re-check receipts, then wait."""
        try:
            receipt, mined = await self._find_receipt(hashes)
        except Exception:
            receipt, mined = None, ""
        if receipt is not None:
            await self._apply_receipt(db, job, receipt, mined, stats, exchange, now)
            return
        job.status = "sent"
        job.sent_at = job.sent_at or now
        job.nonce_consumed_at = job.nonce_consumed_at or now
        job.last_error = "nonce consumed; awaiting receipt"
        stats["deferred"] += 1

    async def _settle_consumed(
        self, db: AsyncSession, job: RelayJob, stats: dict, nm: NonceManager, exchange: str, now: int
    ) -> None:
        """After the lag window: resend only when a foreign tx provably consumed the nonce."""
        s = self.settings
        if now < int(job.nonce_consumed_at or 0) + s.relayer_resubmit_after_seconds:
            return
        try:
            latest = await self.chain.latest_nonce(job.sender or self._sender())
        except Exception as exc:
            job.last_error = f"nonce check: {rpc_reason(exc)}"[:2000]
            stats["deferred"] += 1
            return
        if job.nonce is None or int(latest) <= int(job.nonce):
            # Not mined past our nonce: the 'nonce too low' came from a pool view. Normal sent flow again.
            job.nonce_consumed_at = None
            job.sent_at = now
            job.last_error = ""
            return
        try:
            verdict, where = await self._fill_verdict(db, job, exchange)
        except Exception as exc:
            job.last_error = f"fill check: {rpc_reason(exc)}"[:2000]
            stats["deferred"] += 1
            return
        if verdict == "landed":
            await self._confirm(db, job, where[0], where[1], now, stats)
        elif verdict == "maybe":
            job.nonce_consumed_at = now
            job.last_error = "stuck: fill may have landed; awaiting receipt"
            stats["deferred"] += 1
        else:
            self._back_to_pending(job, "nonce consumed by another tx; resending")
            await self._resync(nm)
            stats["deferred"] += 1

    async def _fill_verdict(
        self, db: AsyncSession, job: RelayJob, exchange: str
    ) -> tuple[str, tuple[str, int] | None]:
        """("landed", (tx, block)) when an OrderFilled log from one of our hashes exists; ("maybe", None) when
        both orders show at least this fill on chain beyond what confirmed jobs explain; else ("absent", None)."""
        mine = {h.lower() for h in self._hashes(job)}
        if job.first_sent_block is not None:
            from_block = int(job.first_sent_block)
        else:
            latest = await self.chain.latest_block()
            from_block = max(0, int(latest["number"]) - self.settings.relayer_log_lookback_blocks)
        logs = await self.chain.get_logs(
            exchange, [ORDER_FILLED_TOPIC.lower(), job.taker_hash.lower(), job.maker_hash.lower()], from_block
        )
        for lg in logs:
            h = str(lg.get("transactionHash", "")).lower()
            if h in mine and self._is_fill_log(lg, job, exchange):
                return "landed", (h, int(lg.get("blockNumber") or 0))
        fill = int(job.fill_amount)
        for oh in (job.taker_hash, job.maker_hash):
            onchain = await self.chain.ex_filled(_b32(oh))
            accounted = (
                await db.execute(
                    select(func.coalesce(func.sum(RelayJob.fill_amount), 0)).where(
                        RelayJob.status == "confirmed",
                        RelayJob.id != job.id,
                        or_(RelayJob.taker_hash == oh, RelayJob.maker_hash == oh),
                    )
                )
            ).scalar()
            if int(onchain) - int(accounted or 0) < fill:
                return "absent", None
        return "maybe", None

    async def _bump(
        self,
        db: AsyncSession,
        job: RelayJob,
        nm: NonceManager,
        stats: dict,
        exchange: str,
        chain_id: int,
        prior_hashes: list[str],
    ) -> None:
        taker, maker = await self._orders(db, job)
        if taker is None or maker is None or job.nonce is None or not job.gas_limit:
            job.last_error = "stuck: cannot rebuild tx"
            return
        try:
            block = await self.chain.latest_block()
        except Exception as exc:
            job.last_error = f"bump: {rpc_reason(exc)}"[:2000]
            stats["deferred"] += 1
            return
        q = bump_fees(self._stored_quote(job), self.settings, block.get("baseFeePerGas"))
        if q is None:
            job.last_error = "stuck: fee cap reached"
            return
        data = self.chain.encode_match(
            OrderFields.from_model(taker).as_tuple(),
            OrderFields.from_model(maker).as_tuple(),
            int(job.fill_amount),
            _sig(taker.signature),
            _sig(maker.signature),
        )
        signed = self.account.sign_transaction(self._tx(int(job.nonce), exchange, chain_id, data, int(job.gas_limit), q))
        h = Web3.to_hex(signed.hash).lower()
        job.raw_tx = Web3.to_hex(signed.raw_transaction)
        job.tx_hash = h
        job.tx_hashes = [*(job.tx_hashes or []), h]
        self._store_quote(job, q)
        job.attempts += 1
        job.sent_at = self._now()
        await db.commit()  # write-ahead: the replacement is on record before it is broadcast
        kind, err = await self._broadcast(job.raw_tx)
        stats["bumped"] += 1
        if kind in (None, "known"):
            job.last_error = ""
        elif kind == "underpriced":
            job.last_error = "bump underpriced; will bump again"
        elif kind == "nonce_low":
            # Same lag tolerance as a rebroadcast: one missed receipt lookup never resets the job.
            await self._nonce_consumed(db, job, stats, exchange, [*prior_hashes, h], self._now())
            await self._resync(nm)
        else:
            job.last_error = f"bump broadcast: {err}"[:2000]

    @staticmethod
    def _culprits(reasons: list[str], taker: Order, maker: Order) -> list[Order]:
        """Orders whose own on-chain state failed preflight ("taker ..." / "maker ..."; both when resolved).
        Overfill is DB/chain drift, not the order's fault, so it never makes a culprit."""
        out: list[Order] = []
        if any(r == "market resolved" for r in reasons):
            return [taker, maker]
        if any(r.startswith("taker ") and r != "taker overfill" for r in reasons):
            out.append(taker)
        if any(r.startswith("maker ") and r != "maker overfill" for r in reasons):
            out.append(maker)
        return out

    async def _resync_filled(self, db: AsyncSession, o: Order) -> None:
        """filled := on-chain filled + fill of this order's live jobs (capped at amount), in one statement."""
        try:
            onchain = int(await self.chain.ex_filled(_b32(o.order_hash)))
        except Exception as exc:
            logger.warning("relayer: filled resync for %s failed: %s", o.order_hash, rpc_reason(exc))
            return
        live = (
            select(func.coalesce(func.sum(RelayJob.fill_amount), 0))
            .where(
                RelayJob.status.in_(LIVE),
                or_(RelayJob.taker_hash == o.order_hash, RelayJob.maker_hash == o.order_hash),
            )
            .scalar_subquery()
        )
        target = onchain + live
        await db.execute(
            update(Order)
            .where(Order.id == o.id)
            .values(filled=case((target > Order.amount, Order.amount), else_=target))
            .execution_options(synchronize_session=False)
        )
        await db.refresh(o, ["filled"])

    async def _retire_failed_side(self, db: AsyncSession, taker: Order, maker: Order, reasons: list[str]) -> None:
        """After a preflight rollback both orders are back on the book and still cross. Cancel the side that
        failed (unfunded, unapproved, stale nonce, expired, cancelled on chain) so it cannot be matched again
        and grief every later counterparty; then re-match an innocent taker against the rest of the book so it
        does not sit crossed. The resting maker just rests again (it never crossed the book). An overfill only
        resyncs that order's filled from chain; nobody is cancelled for it."""
        for role, o in (("taker", taker), ("maker", maker)):
            if f"{role} overfill" in reasons:
                await self._resync_filled(db, o)
        culprits = self._culprits(reasons, taker, maker)
        for o in culprits:
            o.cancelled = True
        if culprits and taker not in culprits and not taker.cancelled and taker.filled < taker.amount:
            from app.orderbook.matcher import try_match  # local: matcher imports the relayer queue

            await db.flush()
            await try_match(db, taker)

    async def _committed(self, db: AsyncSession, job: RelayJob) -> Committed:
        """What every other in-flight job will still spend once it mines."""
        c = Committed()
        rows = (
            await db.execute(select(RelayJob).where(RelayJob.status.in_(IN_FLIGHT), RelayJob.id != job.id))
        ).scalars().all()
        if not rows:
            return c
        hashes = {h for j in rows for h in (j.taker_hash, j.maker_hash)}
        orders = {
            o.order_hash: o
            for o in (await db.execute(select(Order).where(Order.order_hash.in_(hashes)))).scalars().all()
        }
        for j in rows:
            t, m = orders.get(j.taker_hash), orders.get(j.maker_hash)
            if t is None or m is None:
                continue
            c.add_match(
                OrderFields.from_model(t), OrderFields.from_model(m), j.taker_hash, j.maker_hash, int(j.fill_amount)
            )
        return c

    async def _matched_after_halt(self, db: AsyncSession, job: RelayJob) -> bool:
        """TRADING_HALT_AT_CLOSE: a fill matched at/after closeTime must never reach the chain."""
        s = self.settings
        if await trading_halt_reason(db, job.condition_id, s, now=self._now()) != REASON_CLOSED:
            return False
        m = await db.get(Market, norm_cid(job.condition_id))
        at = halts_at(m, s) if m is not None else None
        return at is not None and int(job.matched_at or 0) >= at

    # --------------------------------------------------------------------- send
    async def _send(
        self, db: AsyncSession, job: RelayJob, stats: dict, nm: NonceManager, exchange: str, chain_id: int
    ) -> None:
        s = self.settings
        chain = self.chain
        now = self._now()
        if job.tx_hashes:
            # This job signed before (its old nonce went to someone else). Never resend, roll back or
            # cancel over a fill that landed: check the chain before anything else.
            try:
                verdict, where = await self._fill_verdict(db, job, exchange)
            except Exception as exc:
                job.last_error = f"fill check: {rpc_reason(exc)}"[:2000]
                job.next_attempt_at = now + s.relayer_retry_backoff_seconds
                stats["deferred"] += 1
                return
            if verdict == "landed":
                await self._confirm(db, job, where[0], where[1], now, stats)
                return
            if verdict == "maybe":
                job.last_error = "stuck: fill may have landed; not resending"
                job.next_attempt_at = now + s.relayer_resubmit_after_seconds
                stats["deferred"] += 1
                return

        taker, maker = await self._orders(db, job)
        if taker is None or maker is None or taker.cancelled or maker.cancelled:
            if await rollback_job(db, job, "cancelled", "order missing or cancelled"):
                stats["cancelled"] += 1
            return
        if await self._matched_after_halt(db, job):
            # Not the makers' fault: undo the fill, cancel nobody.
            if await rollback_job(db, job, "failed", "preflight: market closed"):
                stats["failed"] += 1
            return
        tf, mf = OrderFields.from_model(taker), OrderFields.from_model(maker)
        fill = int(job.fill_amount)

        try:
            committed = await self._committed(db, job)
            block = await chain.latest_block()
            reasons = await preflight_match(
                chain, tf, mf, _b32(job.taker_hash), _b32(job.maker_hash), fill, exchange, int(block["timestamp"]), s,
                committed=committed,
            )
        except Exception as exc:
            await self._retry_or_fail(db, job, stats, f"preflight rpc: {rpc_reason(exc)}")
            return
        hard = [r for r in reasons if not r.startswith("busy:")]
        if hard:
            if await rollback_job(db, job, "failed", "preflight: " + "; ".join(hard)):
                stats["failed"] += 1
                await self._retire_failed_side(db, taker, maker, hard)
            return
        if reasons:
            # Funds or fill are held by an earlier in-flight job: wait for it to mine, fail nobody.
            job.last_error = "; ".join(reasons)[:2000]
            job.next_attempt_at = now + max(1, math.ceil(s.relayer_poll_seconds))
            stats["deferred"] += 1
            return

        data = chain.encode_match(tf.as_tuple(), mf.as_tuple(), fill, _sig(taker.signature), _sig(maker.signature))
        try:
            est = await chain.estimate_gas(
                {"from": self.account.address, "to": Web3.to_checksum_address(exchange), "data": Web3.to_hex(data)}
            )
        except Exception as exc:
            if classify_rpc_error(exc) == "revert":
                if await rollback_job(db, job, "failed", f"estimate reverted: {redact(str(exc))[:500]}"):
                    stats["failed"] += 1
            else:
                await self._retry_or_fail(db, job, stats, f"estimate: {rpc_reason(exc)}")
            return
        gas = gas_limit(int(est), s)
        if gas is None:
            if await rollback_job(db, job, "failed", f"gas estimate {est} above cap {s.relayer_gas_limit_cap}"):
                stats["failed"] += 1
            return

        base = block.get("baseFeePerGas")
        try:
            tip = await chain.max_priority_fee()
        except Exception:
            tip = 0
        gas_price = None
        if base is None:
            try:
                gas_price = await chain.gas_price()
            except Exception as exc:
                await self._retry_or_fail(db, job, stats, f"gas price: {rpc_reason(exc)}")
                return
        q = quote_fees(base, tip, gas_price, s)
        if q is None:
            job.last_error = "fee above cap"
            job.next_attempt_at = now + s.relayer_retry_backoff_seconds
            stats["deferred"] += 1
            return
        try:
            balance = await chain.eth_balance(self.account.address)
        except Exception as exc:
            await self._retry_or_fail(db, job, stats, f"balance: {rpc_reason(exc)}")
            return
        if balance < gas * q.max_fee:
            job.last_error = "relayer balance low"
            job.next_attempt_at = now + s.relayer_retry_backoff_seconds
            stats["deferred"] += 1
            return

        floor = await self._nonce_floor(db)
        async with nm.lock:
            # Conditional claim: only one worker (or instance) can move this job out of pending.
            claimed = await db.execute(
                update(RelayJob)
                .where(RelayJob.id == job.id, RelayJob.status == "pending")
                .values(status="sending")
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                return
            nonce = await nm.reserve(floor)
            try:
                signed = self.account.sign_transaction(self._tx(nonce, exchange, chain_id, data, gas, q))
            except Exception:
                nm.release(nonce)
                raise
            h = Web3.to_hex(signed.hash).lower()
            job.status = "sending"
            job.sender = self._sender()
            job.nonce = nonce
            job.raw_tx = Web3.to_hex(signed.raw_transaction)
            job.tx_hash = h
            job.tx_hashes = [*(job.tx_hashes or []), h]
            job.gas_limit = gas
            job.nonce_consumed_at = None
            job.next_attempt_at = 0
            if job.first_sent_block is None:
                job.first_sent_block = int(block["number"])
            self._store_quote(job, q)
            await db.commit()  # write-ahead before broadcast
            kind, err = await self._broadcast(job.raw_tx)
            if kind in (None, "known"):
                job.status = "sent"
                job.sent_at = self._now()
                job.attempts += 1
                job.last_error = ""
                stats["sent"] += 1
            elif kind == "nonce_low":
                # Ambiguous even on a first broadcast (a transport-level retry may have landed it).
                await nm.resync()
                await self._nonce_consumed(db, job, stats, exchange, self._hashes(job), self._now())
            elif kind == "underpriced":
                # Another tx holds this nonce in the pool; ours was not accepted.
                await nm.resync()
                self._back_to_pending(job, "underpriced; resynced nonce")
                stats["deferred"] += 1
            elif kind == "insufficient_funds":
                nm.release(nonce)
                self._back_to_pending(job, "relayer balance low", s.relayer_retry_backoff_seconds)
                stats["deferred"] += 1
            else:
                # The node may have taken it: keep the write-ahead record; reconcile rebroadcasts the same bytes.
                job.attempts += 1
                job.next_attempt_at = self._now() + s.relayer_retry_backoff_seconds
                job.last_error = f"broadcast: {err}"[:2000]
                stats["deferred"] += 1

    # ---------------------------------------------------------------------- run
    async def run_forever(self, poll: float) -> None:
        try:
            while True:
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.last_error = redact(str(exc))[:500]
                    logger.error("relayer tick failed: %s", redact(repr(exc))[:500])
                await asyncio.sleep(poll)
        finally:
            await self.release_leadership()


_ACTIVE_WORKER: RelayerWorker | None = None
_ACTIVE_TASK: asyncio.Task | None = None


def get_active_worker() -> RelayerWorker | None:
    return _ACTIVE_WORKER


def worker_running() -> bool:
    return _ACTIVE_TASK is not None and not _ACTIVE_TASK.done()


def build_worker(settings: Settings) -> RelayerWorker | None:
    account = relayer_account(settings)
    if account is None:
        return None
    return RelayerWorker(get_chain_client(), account, settings)


_MANUAL_WORKER: RelayerWorker | None = None


def get_manual_worker() -> RelayerWorker | None:
    """The shared POST /relayer/tick worker, if a manual tick has run in this process."""
    return _MANUAL_WORKER


def manual_worker(settings: Settings) -> RelayerWorker | None:
    """Shared worker for POST /relayer/tick so concurrent manual ticks serialize on one tick lock."""
    global _MANUAL_WORKER
    account = relayer_account(settings)
    if account is None:
        return None
    chain = get_chain_client()
    w = _MANUAL_WORKER
    if w is None or w.account.address != account.address or w.chain is not chain or w.settings != settings:
        w = RelayerWorker(chain, account, settings)
        _MANUAL_WORKER = w
    return w


def maybe_start_relayer(settings: Settings) -> asyncio.Task | None:
    """Start the background loop only when RELAYER_ENABLED, RELAYER_WORKER_ENABLED and the relayer is ready."""
    global _ACTIVE_WORKER, _ACTIVE_TASK
    if not (settings.relayer_enabled and settings.relayer_worker_enabled):
        return None
    if not relayer_ready(settings):
        logger.warning("relayer enabled but not ready (key or Exchange missing); worker not started")
        return None
    worker = build_worker(settings)
    if worker is None:
        return None
    _ACTIVE_WORKER = worker
    _ACTIVE_TASK = asyncio.create_task(worker.run_forever(settings.relayer_poll_seconds), name="relayer-worker")
    return _ACTIVE_TASK


def stop_relayer_state() -> None:
    global _ACTIVE_WORKER, _ACTIVE_TASK
    _ACTIVE_WORKER = None
    _ACTIVE_TASK = None
