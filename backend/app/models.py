from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    address: Mapped[str] = mapped_column(String(42), unique=True, index=True)
    is_operator: Mapped[bool] = mapped_column(Boolean, default=False)
    cdp_user_id: Mapped[str] = mapped_column(String(100), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Nonce(Base):
    __tablename__ = "nonces"
    address: Mapped[str] = mapped_column(String(42), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(64))


class Market(Base):
    __tablename__ = "markets"
    condition_id: Mapped[str] = mapped_column(String(66), primary_key=True)
    parent_condition_id: Mapped[str] = mapped_column(String(66), default="")
    question: Mapped[str] = mapped_column(String(512))
    resolution_criteria: Mapped[str] = mapped_column(Text, default="")
    market_type: Mapped[int] = mapped_column(Integer, default=0)
    close_time: Mapped[int] = mapped_column(Integer)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    payout_yes: Mapped[int] = mapped_column(Integer, default=0)
    payout_no: Mapped[int] = mapped_column(Integer, default=0)
    suggested_probability: Mapped[float] = mapped_column(default=0.5)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_hash: Mapped[str] = mapped_column(String(66), unique=True, index=True)
    maker: Mapped[str] = mapped_column(String(42), index=True)
    condition_id: Mapped[str] = mapped_column(String(66), index=True)
    is_buy: Mapped[bool] = mapped_column(Boolean)
    outcome: Mapped[int] = mapped_column(Integer)
    # BigInteger: Postgres int4 overflows past ~2147 USDC; salt is a full
    # uint256 stored as 0x-hex text (see app.orderbook.eip712.u256).
    price: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[int] = mapped_column(BigInteger)
    filled: Mapped[int] = mapped_column(BigInteger, default=0)
    salt: Mapped[str] = mapped_column(String(78))
    nonce: Mapped[int] = mapped_column(BigInteger)
    expiry: Mapped[int] = mapped_column(BigInteger)
    signature: Mapped[str] = mapped_column(Text)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)


class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    condition_id: Mapped[str] = mapped_column(String(66), index=True)
    taker: Mapped[str] = mapped_column(String(42))
    maker: Mapped[str] = mapped_column(String(42))
    fill_amount: Mapped[int] = mapped_column(BigInteger)
    volume: Mapped[int] = mapped_column(BigInteger)
    fee: Mapped[int] = mapped_column(BigInteger)
    tx_hash: Mapped[str] = mapped_column(String(66), default="")
    # offchain (relayer disabled) | pending (RelayJob queued) | confirmed | failed
    status: Mapped[str] = mapped_column(String(16), default="offchain", server_default="offchain")
    relay_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True, default=None)
    block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Attestation(Base):
    __tablename__ = "attestations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    condition_id: Mapped[str] = mapped_column(String(66), index=True)
    agent: Mapped[str] = mapped_column(String(42))
    outcome: Mapped[int] = mapped_column(Integer)
    evidence_hash: Mapped[str] = mapped_column(String(66))
    summary: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    signature: Mapped[str] = mapped_column(Text, default="")
    # NULL on rows written before the column existed (see db.ensure_attestation_created_at).
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, server_default=func.now())


class Vote(Base):
    __tablename__ = "votes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    condition_id: Mapped[str] = mapped_column(String(66), index=True)
    voter: Mapped[str] = mapped_column(String(42))
    outcome: Mapped[int] = mapped_column(Integer)
    # CTF YES+NO balance in base units (computed server-side); BigInteger past int4 (~2147 USDC).
    weight: Mapped[int] = mapped_column(BigInteger, default=0)


class Checkpoint(Base):
    __tablename__ = "checkpoints"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_block: Mapped[int] = mapped_column(Integer, default=0)


class PricePoint(Base):
    __tablename__ = "price_points"
    # One point per chain event; the indexer inserts ON CONFLICT DO NOTHING (db.ensure_price_point_unique).
    __table_args__ = (Index("uq_price_point_event", "condition_id", "block_number", "log_index", unique=True),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    condition_id: Mapped[str] = mapped_column(String(66), index=True)
    ts: Mapped[int] = mapped_column(Integer, index=True)
    block_number: Mapped[int] = mapped_column(Integer, default=0)
    log_index: Mapped[int] = mapped_column(Integer, default=0)
    yes_price_micros: Mapped[int] = mapped_column(Integer, default=500_000)


class LiveScore(Base):
    __tablename__ = "live_scores"
    condition_id: Mapped[str] = mapped_column(String(66), primary_key=True)
    home_label: Mapped[str] = mapped_column(String(128))
    away_label: Mapped[str] = mapped_column(String(128))
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    status: Mapped[str] = mapped_column(String(16), default="scheduled")
    period_label: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    facts: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class NflScheduleGame(Base):
    __tablename__ = "nfl_schedule_games"
    __table_args__ = (UniqueConstraint("season", "week", "home", "away", name="uq_nfl_schedule_game"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season: Mapped[int] = mapped_column(Integer)
    week: Mapped[int] = mapped_column(Integer)
    home: Mapped[str] = mapped_column(String(128))
    away: Mapped[str] = mapped_column(String(128))
    kickoff_unix: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="scheduled")
    listed_condition_id: Mapped[str] = mapped_column(String(66), default="")


class RampTx(Base):
    __tablename__ = "ramp_txs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(42), index=True)
    amount: Mapped[str] = mapped_column(String(64))
    provider_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class KycRecord(Base):
    __tablename__ = "kyc_records"
    address: Mapped[str] = mapped_column(String(42), primary_key=True, index=True)
    status: Mapped[str] = mapped_column(String(32))
    jurisdiction: Mapped[str] = mapped_column(String(2), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class MarketListing(Base):
    """User-listed market (factory marketType 2). A separate table so `markets` never needs an ALTER."""

    __tablename__ = "market_listings"
    condition_id: Mapped[str] = mapped_column(String(66), primary_key=True)
    creator: Mapped[str] = mapped_column(String(42), index=True)
    salt: Mapped[str] = mapped_column(String(66), default="")
    question: Mapped[str] = mapped_column(String(512), default="")
    resolution_criteria: Mapped[str] = mapped_column(Text, default="")
    criteria_hash: Mapped[str] = mapped_column(String(66), default="")
    close_time: Mapped[int] = mapped_column(Integer, default=0)
    # USDC base units; BigInteger because Postgres int4 caps near 2147 USDC.
    seed_usdc: Mapped[int] = mapped_column(BigInteger, default=0)
    # prepared (calldata handed out) | indexed (seen by the indexer, criteria not verified yet)
    # | confirmed (criteria hash + listing gates passed; the only publicly visible state)
    # | rejected (on-chain question failed the gates or differs from the prepared one; hidden)
    status: Mapped[str] = mapped_column(String(16), default="prepared")
    reject_reason: Mapped[str] = mapped_column(String(256), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# --- OU-T003 relayer (track B2) ---------------------------------------------
class RelayJob(Base):
    """One matchOrders submission per off-chain fill. raw_tx is a signed tx, not a secret, but never exposed by the API."""

    __tablename__ = "relay_jobs"
    # No (taker_hash, maker_hash) unique key: every match call is its own job, so a re-match of a pair
    # after a failed job (or while one is in flight) gets a fresh job instead of reusing a dead one.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), default="match_orders")
    condition_id: Mapped[str] = mapped_column(String(66), index=True)
    taker_hash: Mapped[str] = mapped_column(String(66), index=True)
    maker_hash: Mapped[str] = mapped_column(String(66), index=True)
    fill_amount: Mapped[int] = mapped_column(BigInteger)
    # Relayer EOA that signs this job (lowercase); scopes the in-flight nonce floor.
    sender: Mapped[str] = mapped_column(String(42), default="")
    # pending | sending (write-ahead, raw_tx committed) | sent | confirmed | failed | cancelled
    status: Mapped[str] = mapped_column(String(16), index=True, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    nonce: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    tx_hash: Mapped[str] = mapped_column(String(66), default="")
    tx_hashes: Mapped[list] = mapped_column(JSON, default=list)
    raw_tx: Mapped[str] = mapped_column(Text, default="")
    gas_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    max_fee_per_gas: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    max_priority_fee_per_gas: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    last_error: Mapped[str] = mapped_column(Text, default="")
    next_attempt_at: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    sent_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    confirmed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    # Epoch seconds when try_match created the job (the CLOB trading-halt cut-off compares against it).
    matched_at: Mapped[int] = mapped_column(BigInteger, default=0)
    # Latest block when this job first signed a tx: lower bound for its OrderFilled log search.
    first_sent_block: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    # Set when a broadcast of an already-signed tx said "nonce too low": our own tx may be what used it.
    nonce_consumed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)


class EmissionDistribution(Base):
    """Write-ahead record of one EmissionsDistributor.distribute tx (the contract has no idempotency key)."""

    __tablename__ = "emission_distributions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    program: Mapped[int] = mapped_column(Integer)
    # keccak(abi.encode(program, recipients, amounts)), 0x-hex
    payload_hash: Mapped[str] = mapped_column(String(66), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), default="", index=True)
    sender: Mapped[str] = mapped_column(String(42), default="")
    nonce: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    raw_tx: Mapped[str] = mapped_column(Text, default="")
    tx_hash: Mapped[str] = mapped_column(String(66), default="")
    # sending (write-ahead) | sent | confirmed | failed
    status: Mapped[str] = mapped_column(String(16), index=True, default="sending")
    recipient_count: Mapped[int] = mapped_column(Integer, default=0)
    # Decimal string: an 18-decimal OU total can exceed int64.
    total_amount: Mapped[str] = mapped_column(String(80), default="0")
    block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    last_error: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
