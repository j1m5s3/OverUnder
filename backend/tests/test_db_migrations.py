"""Startup schema sync (app.db.run_migrations) on SQLite: a pre-relayer schema upgrades in place,
twice, with rows intact. The Postgres paths (int4 -> BIGINT widening, salt -> VARCHAR, the advisory
xact lock under concurrent instances) are rehearsed against a real Postgres 16: the `_on_postgres` tests
below run when the suite's DATABASE_URL points at Postgres (the rehearsal harness); on SQLite only
the dialect gating is checked."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.main as main_mod
from app.config import Settings
from app.db import MIGRATION_LOCK_KEY, engine, lock_migrations, run_migrations

# Pre-OU-T003 tables as the last released API created them (orders/trades int columns, no relay columns).
_LEGACY_DDL = (
    "CREATE TABLE users (id INTEGER PRIMARY KEY, address VARCHAR(42) NOT NULL UNIQUE, "
    "is_operator BOOLEAN NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL)",
    "CREATE TABLE live_scores (condition_id VARCHAR(66) PRIMARY KEY, home_label VARCHAR(128) NOT NULL, "
    "away_label VARCHAR(128) NOT NULL, home_score INTEGER, away_score INTEGER, status VARCHAR(16) NOT NULL, "
    "period_label VARCHAR(16), updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL)",
    "CREATE TABLE orders (id INTEGER PRIMARY KEY, order_hash VARCHAR(66) NOT NULL UNIQUE, maker VARCHAR(42) NOT NULL, "
    "condition_id VARCHAR(66) NOT NULL, is_buy BOOLEAN NOT NULL, outcome INTEGER NOT NULL, price INTEGER NOT NULL, "
    "amount INTEGER NOT NULL, filled INTEGER NOT NULL, salt INTEGER NOT NULL, nonce INTEGER NOT NULL, "
    "expiry INTEGER NOT NULL, signature TEXT NOT NULL, cancelled BOOLEAN NOT NULL)",
    "CREATE TABLE trades (id INTEGER PRIMARY KEY, condition_id VARCHAR(66) NOT NULL, taker VARCHAR(42) NOT NULL, "
    "maker VARCHAR(42) NOT NULL, fill_amount INTEGER NOT NULL, volume INTEGER NOT NULL, fee INTEGER NOT NULL, "
    "tx_hash VARCHAR(66) NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL)",
)
_LEGACY_ROWS = (
    "INSERT INTO users (address, is_operator) VALUES ('0xa1', 0)",
    "INSERT INTO live_scores (condition_id, home_label, away_label, home_score, away_score, status) "
    "VALUES ('0x11', 'Chiefs', 'Bills', 14, 10, 'in_progress')",
    "INSERT INTO orders (order_hash, maker, condition_id, is_buy, outcome, price, amount, filled, salt, nonce, "
    "expiry, signature, cancelled) VALUES ('0xc1', '0xa1', '0x11', 1, 0, 550000, 20000000, 5000000, 42, 0, "
    "1800000000, '0xab', 0)",
    "INSERT INTO trades (condition_id, taker, maker, fill_amount, volume, fee, tx_hash) "
    "VALUES ('0x11', '0xa1', '0xb0', 5000000, 2700000, 20250, '')",
)


def _columns(conn, table: str) -> set[str]:
    return {c["name"] for c in inspect(conn).get_columns(table)}


def test_run_migrations_upgrades_legacy_sqlite_twice(tmp_path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    try:
        with eng.begin() as conn:
            for stmt in (*_LEGACY_DDL, *_LEGACY_ROWS):
                conn.execute(text(stmt))
        for _ in range(2):  # a restarted instance runs the same sync again
            with eng.begin() as conn:
                run_migrations(conn)
        with eng.connect() as conn:
            insp = inspect(conn)
            assert {"relay_jobs", "market_listings", "nfl_schedule_games"} <= set(insp.get_table_names())
            assert "cdp_user_id" in _columns(conn, "users")
            assert "facts" in _columns(conn, "live_scores")
            assert {"status", "relay_job_id", "block_number"} <= _columns(conn, "trades")
            assert "ix_trades_relay_job_id" in {i["name"] for i in insp.get_indexes("trades")}
            trade = conn.execute(text("SELECT fill_amount, status, relay_job_id FROM trades")).one()
            assert tuple(trade) == (5_000_000, "offchain", None)
            order = conn.execute(text("SELECT amount, filled, salt FROM orders WHERE order_hash = '0xc1'")).one()
            assert tuple(order) == (20_000_000, 5_000_000, 42)
            assert conn.execute(text("SELECT cdp_user_id FROM users")).scalar() == ""
            assert conn.execute(text("SELECT home_score FROM live_scores")).scalar() == 14
    finally:
        eng.dispose()


def test_run_migrations_on_empty_sqlite_matches_models(tmp_path):
    from app.db import Base

    eng = create_engine(f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    try:
        with eng.begin() as conn:
            run_migrations(conn)
        with eng.connect() as conn:
            assert set(Base.metadata.tables) <= set(inspect(conn).get_table_names())
    finally:
        eng.dispose()


def test_migration_lock_is_postgres_only():
    sqlite = MagicMock()
    sqlite.dialect.name = "sqlite"
    lock_migrations(sqlite)
    sqlite.execute.assert_not_called()

    pg = MagicMock()
    pg.dialect.name = "postgresql"
    lock_migrations(pg)
    (stmt, params), _ = pg.execute.call_args
    assert "pg_advisory_xact_lock" in str(stmt)
    assert params == {"k": MIGRATION_LOCK_KEY}
    # Must never collide with the relayer's session-level leader lock.
    assert MIGRATION_LOCK_KEY != Settings(_env_file=None).relayer_leader_lock_key


async def test_lifespan_runs_the_locked_migration():
    calls = []

    def spy(conn):
        calls.append(conn.dialect.name)
        run_migrations(conn)

    quiet = Settings(_env_file=None, indexer_enabled=False, relayer_enabled=False, relayer_worker_enabled=False)
    app = main_mod.create_app()
    with patch.object(main_mod, "run_migrations", spy), patch.object(main_mod, "get_settings", return_value=quiet):
        async with app.router.lifespan_context(app):
            pass
    assert calls == [engine.dialect.name]
    # No schema step may run outside the locked migration (concurrent startups would race it).
    assert not hasattr(main_mod, "ensure_relayer_schema")


def test_run_migrations_runs_the_relayer_schema_under_the_lock():
    import app.relayer.schema as relayer_schema

    order = []
    conn = MagicMock()
    conn.dialect.name = "postgresql"
    with patch("app.db.lock_migrations", side_effect=lambda c: order.append("lock")), \
            patch("app.db.Base.metadata.create_all", side_effect=lambda c: order.append("create_all")), \
            patch.object(relayer_schema, "ensure_relayer_schema", side_effect=lambda c: order.append("relayer")), \
            patch("app.db.inspect") as insp:
        insp.return_value.has_table.return_value = False
        run_migrations(conn)
    assert order == ["lock", "create_all", "relayer"]


_LEGACY_LIFECYCLE_DDL = (
    "CREATE TABLE attestations (id INTEGER PRIMARY KEY, condition_id VARCHAR(66) NOT NULL, agent VARCHAR(42) NOT NULL, "
    "outcome INTEGER NOT NULL, evidence_hash VARCHAR(66) NOT NULL, summary TEXT NOT NULL, evidence_json TEXT NOT NULL, "
    "signature TEXT NOT NULL)",
    "CREATE TABLE votes (id INTEGER PRIMARY KEY, condition_id VARCHAR(66) NOT NULL, voter VARCHAR(42) NOT NULL, "
    "outcome INTEGER NOT NULL, weight INTEGER NOT NULL)",
    "CREATE TABLE market_listings (condition_id VARCHAR(66) PRIMARY KEY, creator VARCHAR(42) NOT NULL, salt VARCHAR(66) NOT NULL, "
    "question VARCHAR(512) NOT NULL, resolution_criteria TEXT NOT NULL, criteria_hash VARCHAR(66) NOT NULL, "
    "close_time INTEGER NOT NULL, seed_usdc BIGINT NOT NULL, status VARCHAR(16) NOT NULL, "
    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL)",
    "CREATE TABLE price_points (id INTEGER PRIMARY KEY, condition_id VARCHAR(66) NOT NULL, ts INTEGER NOT NULL, "
    "block_number INTEGER NOT NULL, log_index INTEGER NOT NULL, yes_price_micros INTEGER NOT NULL)",
    "INSERT INTO attestations (condition_id, agent, outcome, evidence_hash, summary, evidence_json, signature) "
    "VALUES ('0x11', '0xa1', 0, '0x00', '', '[]', '')",
    "INSERT INTO market_listings (condition_id, creator, salt, question, resolution_criteria, criteria_hash, close_time, "
    "seed_usdc, status) VALUES ('0x22', '0xa1', '', 'q?', '', '', 0, 0, 'indexed')",
    "INSERT INTO price_points (condition_id, ts, block_number, log_index, yes_price_micros) VALUES ('0x11', 1, 5, 0, 500000)",
    "INSERT INTO price_points (condition_id, ts, block_number, log_index, yes_price_micros) VALUES ('0x11', 1, 5, 0, 500000)",
)


def test_run_migrations_adds_lifecycle_columns_and_price_point_key(tmp_path):
    from app.db import PRICE_POINT_UNIQUE

    eng = create_engine(f"sqlite:///{(tmp_path / 'lifecycle.db').as_posix()}")
    try:
        with eng.begin() as conn:
            for stmt in _LEGACY_LIFECYCLE_DDL:
                conn.execute(text(stmt))
        for _ in range(2):
            with eng.begin() as conn:
                run_migrations(conn)
        with eng.connect() as conn:
            assert "created_at" in _columns(conn, "attestations")
            assert "reject_reason" in _columns(conn, "market_listings")
            assert conn.execute(text("SELECT created_at FROM attestations")).scalar() is None  # legacy row
            assert conn.execute(text("SELECT reject_reason FROM market_listings")).scalar() == ""
            assert conn.execute(text("SELECT COUNT(*) FROM price_points")).scalar() == 1
            assert PRICE_POINT_UNIQUE in {i["name"] for i in inspect(conn).get_indexes("price_points")}
        with eng.begin() as conn:
            conn.execute(text("INSERT INTO votes (condition_id, voter, outcome, weight) VALUES ('0x11', '0xa1', 0, 5000000000)"))
            assert conn.execute(text("SELECT weight FROM votes")).scalar() == 5_000_000_000
    finally:
        eng.dispose()


def test_vote_weight_widening_is_postgres_only():
    from sqlalchemy import Integer

    from app.db import ensure_vote_weight_bigint

    sqlite = MagicMock()
    sqlite.dialect.name = "sqlite"
    ensure_vote_weight_bigint(sqlite)
    sqlite.execute.assert_not_called()

    pg = MagicMock()
    pg.dialect.name = "postgresql"
    fake_inspector = MagicMock()
    fake_inspector.has_table.return_value = True
    fake_inspector.get_columns.return_value = [{"name": "weight", "type": Integer()}]
    with patch("app.db.inspect", return_value=fake_inspector):
        ensure_vote_weight_bigint(pg)
    (stmt,), _ = pg.execute.call_args
    assert "ALTER TABLE votes ALTER COLUMN weight TYPE BIGINT" in str(stmt)


def test_relay_job_late_columns_are_added_to_an_existing_table(tmp_path):
    from app.db import ensure_relay_job_columns

    eng = create_engine(f"sqlite:///{(tmp_path / 'relay.db').as_posix()}")
    try:
        with eng.begin() as conn:
            conn.execute(text("CREATE TABLE relay_jobs (id INTEGER PRIMARY KEY, status VARCHAR(16))"))
            conn.execute(text("INSERT INTO relay_jobs (status) VALUES ('pending')"))
        for _ in range(2):
            with eng.begin() as conn:
                ensure_relay_job_columns(conn)
        with eng.connect() as conn:
            assert {"matched_at", "first_sent_block", "nonce_consumed_at"} <= _columns(conn, "relay_jobs")
            assert conn.execute(text("SELECT matched_at FROM relay_jobs")).scalar() == 0
    finally:
        eng.dispose()


# relay_jobs as the first rehearsal builds of OU-T003 created it: a (taker_hash, maker_hash) unique key
# and none of the later columns.
_REHEARSAL_RELAY_JOBS = (
    "CREATE TABLE relay_jobs (id INTEGER PRIMARY KEY, kind VARCHAR(32), condition_id VARCHAR(66), "
    "taker_hash VARCHAR(66), maker_hash VARCHAR(66), fill_amount BIGINT, sender VARCHAR(42), "
    "status VARCHAR(16), attempts INTEGER, nonce BIGINT, tx_hash VARCHAR(66), tx_hashes JSON, raw_tx TEXT, "
    "gas_limit BIGINT, max_fee_per_gas BIGINT, max_priority_fee_per_gas BIGINT, block_number BIGINT, "
    "last_error TEXT, next_attempt_at BIGINT, created_at {ts}, updated_at {ts}, sent_at BIGINT, "
    "confirmed_at BIGINT, CONSTRAINT uq_relay_job_pair UNIQUE (taker_hash, maker_hash))",
    "CREATE INDEX ix_relay_jobs_status ON relay_jobs (status)",
    "INSERT INTO relay_jobs (id, kind, condition_id, taker_hash, maker_hash, fill_amount, sender, status, attempts, "
    "tx_hash, tx_hashes, raw_tx, last_error, next_attempt_at) VALUES "
    "(7, 'match_orders', '0x01', '0xt', '0xm', 5, '', 'failed', 1, '', '[]', '', 'x', 0)",
)
_SECOND_PAIR_JOB = (
    "INSERT INTO relay_jobs (id, kind, condition_id, taker_hash, maker_hash, fill_amount, sender, status, attempts, "
    "tx_hash, tx_hashes, raw_tx, last_error, next_attempt_at, matched_at) VALUES "
    "(8, 'match_orders', '0x01', '0xt', '0xm', 5, '', 'pending', 0, '', '[]', '', '', 0, 1)"
)


def _assert_relay_jobs_upgraded(conn) -> None:
    insp = inspect(conn)
    assert {"matched_at", "first_sent_block", "nonce_consumed_at"} <= _columns(conn, "relay_jobs")
    assert not any(
        sorted(u.get("column_names") or []) == ["maker_hash", "taker_hash"]
        for u in insp.get_unique_constraints("relay_jobs")
    )
    assert not any(
        i.get("unique") and sorted(i.get("column_names") or []) == ["maker_hash", "taker_hash"]
        for i in insp.get_indexes("relay_jobs")
    )
    row = conn.execute(text("SELECT id, status, fill_amount, matched_at FROM relay_jobs WHERE id = 7")).one()
    assert tuple(row) == (7, "failed", 5, 0)


def test_run_migrations_upgrades_rehearsal_relay_jobs_on_sqlite(tmp_path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'relay_old.db').as_posix()}")
    try:
        with eng.begin() as conn:
            for stmt in _REHEARSAL_RELAY_JOBS:
                conn.execute(text(stmt.format(ts="DATETIME")))
        for _ in range(2):  # restarted instances run the same sync again
            with eng.begin() as conn:
                run_migrations(conn)
        with eng.begin() as conn:
            _assert_relay_jobs_upgraded(conn)
            # Rebuilt from the model: its indexes are back and a pair can hold two jobs.
            assert {"ix_relay_jobs_status", "ix_relay_jobs_taker_hash"} <= {
                i["name"] for i in inspect(conn).get_indexes("relay_jobs")
            }
            conn.execute(text(_SECOND_PAIR_JOB))
            assert conn.execute(text("SELECT COUNT(*) FROM relay_jobs WHERE taker_hash = '0xt'")).scalar() == 2
            assert "relay_jobs_pairkey_old" not in inspect(conn).get_table_names()
    finally:
        eng.dispose()


# --- Real Postgres: runs only when the suite's DATABASE_URL is Postgres (the rehearsal harness).
_on_postgres = pytest.mark.skipif(engine.dialect.name != "postgresql", reason="needs a Postgres DATABASE_URL")


async def _with_scratch_schema(schema: str, body):
    """Run body(engine) against a throwaway schema (search_path), so run_migrations sees an empty or legacy DB."""
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    eng = create_async_engine(
        engine.url, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        await body(eng)
    finally:
        await eng.dispose()
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


@_on_postgres
async def test_postgres_run_migrations_upgrades_rehearsal_relay_jobs():
    async def body(eng):
        async with eng.begin() as conn:
            for stmt in _REHEARSAL_RELAY_JOBS:
                await conn.execute(text(stmt.format(ts="TIMESTAMP")))
        for _ in range(2):
            async with eng.begin() as conn:
                await conn.run_sync(run_migrations)
        async with eng.begin() as conn:
            await conn.run_sync(_assert_relay_jobs_upgraded)
            await conn.execute(text(_SECOND_PAIR_JOB))
            count = await conn.execute(text("SELECT COUNT(*) FROM relay_jobs WHERE taker_hash = '0xt'"))
            assert count.scalar() == 2

    await _with_scratch_schema("ou_mig_relay", body)


@_on_postgres
async def test_postgres_concurrent_startups_all_migrate():
    """The advisory xact lock queues concurrent instances: none crashes on racing create_all/ALTER."""
    from app.db import Base

    async def body(eng):
        async def one():
            async with eng.begin() as conn:
                await conn.run_sync(run_migrations)

        results = await asyncio.gather(*(one() for _ in range(4)), return_exceptions=True)
        assert [r for r in results if isinstance(r, BaseException)] == []
        async with eng.connect() as conn:
            tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        assert set(Base.metadata.tables) <= tables

    await _with_scratch_schema("ou_mig_race", body)
