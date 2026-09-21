from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    address: Mapped[str] = mapped_column(String(42), unique=True, index=True)
    is_operator: Mapped[bool] = mapped_column(Boolean, default=False)
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
    price: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(Integer)
    filled: Mapped[int] = mapped_column(Integer, default=0)
    salt: Mapped[int] = mapped_column(Integer)
    nonce: Mapped[int] = mapped_column(Integer)
    expiry: Mapped[int] = mapped_column(Integer)
    signature: Mapped[str] = mapped_column(Text)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)


class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    condition_id: Mapped[str] = mapped_column(String(66), index=True)
    taker: Mapped[str] = mapped_column(String(42))
    maker: Mapped[str] = mapped_column(String(42))
    fill_amount: Mapped[int] = mapped_column(Integer)
    volume: Mapped[int] = mapped_column(Integer)
    fee: Mapped[int] = mapped_column(Integer)
    tx_hash: Mapped[str] = mapped_column(String(66), default="")
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


class Vote(Base):
    __tablename__ = "votes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    condition_id: Mapped[str] = mapped_column(String(66), index=True)
    voter: Mapped[str] = mapped_column(String(42))
    outcome: Mapped[int] = mapped_column(Integer)
    weight: Mapped[int] = mapped_column(Integer, default=0)


class Checkpoint(Base):
    __tablename__ = "checkpoints"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_block: Mapped[int] = mapped_column(Integer, default=0)


class PricePoint(Base):
    __tablename__ = "price_points"
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
