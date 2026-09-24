"""Idempotent upgrades for the relayer tables. Called only from app.db.run_migrations, inside its
transaction and (on Postgres) under its advisory xact lock, so concurrent API startups never race
this DDL.

relay_jobs first shipped (rehearsal DBs) with a (taker_hash, maker_hash) unique key; a pair can now
hold several jobs, so that key is dropped, and columns added since are added to a pre-existing table.
New tables (emission_distributions) come from Base.metadata.create_all.
"""

from __future__ import annotations

from sqlalchemy import inspect, text

OLD_PAIR_KEY = "uq_relay_job_pair"
PAIR_COLUMNS = ["maker_hash", "taker_hash"]
# Columns added to relay_jobs after the table first shipped. Nullable or defaulted, so ADD COLUMN
# works on a populated table in both dialects.
NEW_COLUMNS = {
    "matched_at": "BIGINT DEFAULT 0",
    "first_sent_block": "BIGINT",
    "nonce_consumed_at": "BIGINT",
}


def ensure_relay_job_columns(connection) -> None:
    insp = inspect(connection)
    if not insp.has_table("relay_jobs"):
        return
    names = {c["name"] for c in insp.get_columns("relay_jobs")}
    for col, ddl in NEW_COLUMNS.items():
        if col not in names:
            connection.execute(text(f"ALTER TABLE relay_jobs ADD COLUMN {col} {ddl}"))


def _pair_keys(connection) -> tuple[list[str], list[str]]:
    """(unique constraint names, unique index names) covering exactly (taker_hash, maker_hash)."""
    insp = inspect(connection)
    constraints = [
        uc.get("name") or ""
        for uc in insp.get_unique_constraints("relay_jobs")
        if uc.get("name") == OLD_PAIR_KEY or sorted(uc.get("column_names") or []) == PAIR_COLUMNS
    ]
    indexes = [
        ix.get("name") or ""
        for ix in insp.get_indexes("relay_jobs")
        if ix.get("unique") and sorted(ix.get("column_names") or []) == PAIR_COLUMNS
        and ix.get("name") not in constraints
    ]
    return constraints, indexes


def _rebuild_sqlite(connection) -> None:
    """SQLite cannot drop a table constraint: copy the rows into a table built from the current model."""
    from app.models import RelayJob

    table = RelayJob.__table__
    old_cols = {c["name"] for c in inspect(connection).get_columns("relay_jobs")}
    for ix in inspect(connection).get_indexes("relay_jobs"):
        if ix.get("name") and not ix["name"].startswith("sqlite_autoindex"):
            connection.execute(text(f'DROP INDEX IF EXISTS "{ix["name"]}"'))
    connection.execute(text("ALTER TABLE relay_jobs RENAME TO relay_jobs_pairkey_old"))
    table.create(connection)
    names = [c.name for c in table.columns if c.name in old_cols]
    picks = [f"COALESCE({n}, CURRENT_TIMESTAMP)" if n in ("created_at", "updated_at") else n for n in names]
    connection.execute(
        text(f"INSERT INTO relay_jobs ({', '.join(names)}) SELECT {', '.join(picks)} FROM relay_jobs_pairkey_old")
    )
    connection.execute(text("DROP TABLE relay_jobs_pairkey_old"))


def ensure_relayer_schema(connection) -> None:
    insp = inspect(connection)
    if not insp.has_table("relay_jobs"):
        return
    ensure_relay_job_columns(connection)
    if connection.dialect.name == "postgresql":
        connection.execute(text(f"ALTER TABLE relay_jobs DROP CONSTRAINT IF EXISTS {OLD_PAIR_KEY}"))
        constraints, indexes = _pair_keys(connection)
        for name in constraints:
            if name and name != OLD_PAIR_KEY:
                connection.execute(text(f'ALTER TABLE relay_jobs DROP CONSTRAINT IF EXISTS "{name}"'))
        for name in indexes:
            if name:
                connection.execute(text(f'DROP INDEX IF EXISTS "{name}"'))
    elif connection.dialect.name == "sqlite" and any(_pair_keys(connection)):
        _rebuild_sqlite(connection)
