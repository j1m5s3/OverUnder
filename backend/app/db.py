from sqlalchemy import BigInteger, Integer, String, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def ensure_live_score_facts(connection) -> None:
    inspector = inspect(connection)
    if not inspector.has_table("live_scores"):
        return
    names = {col["name"] for col in inspector.get_columns("live_scores")}
    if "facts" in names:
        return
    connection.execute(text("ALTER TABLE live_scores ADD COLUMN facts JSON"))


def ensure_user_cdp_user_id(connection) -> None:
    inspector = inspect(connection)
    if not inspector.has_table("users"):
        return
    names = {col["name"] for col in inspector.get_columns("users")}
    if "cdp_user_id" in names:
        return
    connection.execute(text("ALTER TABLE users ADD COLUMN cdp_user_id VARCHAR(100) DEFAULT ''"))


def ensure_trade_relay_columns(connection) -> None:
    """Add the OU-T003 relay columns to a pre-existing trades table."""
    inspector = inspect(connection)
    if not inspector.has_table("trades"):
        return
    names = {col["name"] for col in inspector.get_columns("trades")}
    if "status" not in names:
        connection.execute(text("ALTER TABLE trades ADD COLUMN status VARCHAR(16) DEFAULT 'offchain'"))
    if "relay_job_id" not in names:
        connection.execute(text("ALTER TABLE trades ADD COLUMN relay_job_id INTEGER"))
    if "block_number" not in names:
        connection.execute(text("ALTER TABLE trades ADD COLUMN block_number BIGINT"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_trades_relay_job_id ON trades (relay_job_id)"))


_ORDER_WIDE = ("price", "amount", "filled", "nonce", "expiry")
_TRADE_WIDE = ("fill_amount", "volume", "fee")


def ensure_order_wide_columns(connection) -> None:
    """Widen int4 order/trade columns on Postgres. SQLite integers are already 64-bit and
    SQLite stores the 0x-hex salt as TEXT under INTEGER affinity, so it needs nothing."""
    if connection.dialect.name != "postgresql":
        return
    inspector = inspect(connection)

    def narrow(table: str, cols: tuple[str, ...]) -> list[str]:
        if not inspector.has_table(table):
            return []
        types = {c["name"]: c["type"] for c in inspector.get_columns(table)}
        return [
            c for c in cols
            if c in types and isinstance(types[c], Integer) and not isinstance(types[c], BigInteger)
        ]

    for col in narrow("orders", _ORDER_WIDE):
        connection.execute(text(f"ALTER TABLE orders ALTER COLUMN {col} TYPE BIGINT"))
    if inspector.has_table("orders"):
        salt = {c["name"]: c["type"] for c in inspector.get_columns("orders")}.get("salt")
        if salt is not None and not isinstance(salt, String):
            connection.execute(text("ALTER TABLE orders ALTER COLUMN salt TYPE VARCHAR(78) USING salt::text"))
    for col in narrow("trades", _TRADE_WIDE):
        connection.execute(text(f"ALTER TABLE trades ALTER COLUMN {col} TYPE BIGINT"))


def ensure_attestation_created_at(connection) -> None:
    """Add attestations.created_at; legacy rows stay NULL (the status API reports null)."""
    inspector = inspect(connection)
    if not inspector.has_table("attestations"):
        return
    names = {col["name"] for col in inspector.get_columns("attestations")}
    if "created_at" in names:
        return
    # No DEFAULT on the ALTER: SQLite rejects non-constant defaults there, and NULL marks legacy rows.
    kind = "TIMESTAMP" if connection.dialect.name == "postgresql" else "DATETIME"
    connection.execute(text(f"ALTER TABLE attestations ADD COLUMN created_at {kind}"))
    if connection.dialect.name == "postgresql":
        connection.execute(text("ALTER TABLE attestations ALTER COLUMN created_at SET DEFAULT now()"))


def ensure_relay_job_columns(connection) -> None:
    """Columns added to relay_jobs after the table first shipped (rehearsal DBs created it earlier)."""
    from app.relayer.schema import ensure_relay_job_columns as _add

    _add(connection)


def ensure_market_listing_reason(connection) -> None:
    inspector = inspect(connection)
    if not inspector.has_table("market_listings"):
        return
    names = {col["name"] for col in inspector.get_columns("market_listings")}
    if "reject_reason" in names:
        return
    connection.execute(text("ALTER TABLE market_listings ADD COLUMN reject_reason VARCHAR(256) DEFAULT ''"))


def ensure_vote_weight_bigint(connection) -> None:
    """votes.weight holds CTF balances in base units, past int4 at ~2147 USDC. SQLite needs nothing."""
    if connection.dialect.name != "postgresql":
        return
    inspector = inspect(connection)
    if not inspector.has_table("votes"):
        return
    kind = {c["name"]: c["type"] for c in inspector.get_columns("votes")}.get("weight")
    if kind is not None and isinstance(kind, Integer) and not isinstance(kind, BigInteger):
        connection.execute(text("ALTER TABLE votes ALTER COLUMN weight TYPE BIGINT"))


PRICE_POINT_UNIQUE = "uq_price_point_event"
PRICE_POINT_KEY = ("condition_id", "block_number", "log_index")


def ensure_price_point_unique(connection) -> None:
    """One price point per (condition, block, logIndex). Drops duplicates (keeps MIN(id)) first,
    since concurrent indexers could insert the same event twice before this key existed."""
    inspector = inspect(connection)
    if not inspector.has_table("price_points"):
        return
    if PRICE_POINT_UNIQUE in {i["name"] for i in inspector.get_indexes("price_points")}:
        return
    if connection.dialect.name == "postgresql":
        connection.execute(
            text(
                "DELETE FROM price_points a USING price_points b WHERE a.id > b.id "
                "AND a.condition_id = b.condition_id AND a.block_number = b.block_number "
                "AND a.log_index = b.log_index"
            )
        )
    else:
        connection.execute(
            text(
                "DELETE FROM price_points WHERE id NOT IN "
                "(SELECT MIN(id) FROM price_points GROUP BY condition_id, block_number, log_index)"
            )
        )
    connection.execute(
        text(f"CREATE UNIQUE INDEX IF NOT EXISTS {PRICE_POINT_UNIQUE} ON price_points ({', '.join(PRICE_POINT_KEY)})")
    )


VOTE_UNIQUE = "uq_vote_condition_voter"


def ensure_vote_unique(connection) -> None:
    """One vote per (condition_id, voter). Drops later duplicates (keeps MIN(id), the first vote cast)
    first: before this key existed, concurrent POST /oracle/vote calls could each insert a row."""
    inspector = inspect(connection)
    if not inspector.has_table("votes"):
        return
    if VOTE_UNIQUE in {i["name"] for i in inspector.get_indexes("votes")}:
        return
    if connection.dialect.name == "postgresql":
        connection.execute(
            text(
                "DELETE FROM votes a USING votes b WHERE a.id > b.id "
                "AND a.condition_id = b.condition_id AND a.voter = b.voter"
            )
        )
    else:
        connection.execute(
            text("DELETE FROM votes WHERE id NOT IN (SELECT MIN(id) FROM votes GROUP BY condition_id, voter)")
        )
    connection.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {VOTE_UNIQUE} ON votes (condition_id, voter)"))


EMISSION_LIVE_UNIQUE = "uq_emission_live_payload"
EMISSION_LIVE_WHERE = "status IN ('sending', 'sent', 'confirmed')"


def ensure_emission_live_unique(connection) -> None:
    """At most one live distribution per (payload_hash, idempotency_key) (partial unique index).

    Live duplicates already on record are real signed txs, so they are never deleted: the index is
    skipped with a warning until an operator reconciles them (the route still re-checks under the
    nonce lock). Both SQLite and Postgres support partial indexes."""
    import logging

    inspector = inspect(connection)
    if not inspector.has_table("emission_distributions"):
        return
    if EMISSION_LIVE_UNIQUE in {i["name"] for i in inspector.get_indexes("emission_distributions")}:
        return
    dupes = connection.execute(
        text(
            "SELECT COUNT(*) FROM (SELECT payload_hash, idempotency_key FROM emission_distributions "
            f"WHERE {EMISSION_LIVE_WHERE} GROUP BY payload_hash, idempotency_key HAVING COUNT(*) > 1) d"
        )
    ).scalar()
    if dupes:
        logging.getLogger(__name__).warning(
            "emission_distributions has %s live duplicate payload(s); %s not created", dupes, EMISSION_LIVE_UNIQUE
        )
        return
    connection.execute(
        text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {EMISSION_LIVE_UNIQUE} ON emission_distributions "
            f"(payload_hash, idempotency_key) WHERE {EMISSION_LIVE_WHERE}"
        )
    )


def insert_ignore(dialect_name: str, model, values: dict, index_elements: list[str]):
    """INSERT ... ON CONFLICT DO NOTHING for Postgres and SQLite (both support it).

    Used where two writers (API instances, the indexer and /confirm) can insert the
    same key: the loser becomes a no-op instead of an IntegrityError that poisons
    the session. Column defaults still apply."""
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(model).values(**values).on_conflict_do_nothing(index_elements=index_elements)


def session_dialect(db: AsyncSession) -> str:
    bind = db.get_bind()
    return bind.dialect.name


# "OUMG"; distinct from the relayer leader key (RELAYER_LEADER_LOCK_KEY, "OURL")
# and the indexer leader key (INDEXER_LEADER_LOCK_KEY, "OUIN").
MIGRATION_LOCK_KEY = 0x4F554D47


def lock_migrations(connection) -> None:
    """Serialize concurrent API startups on Postgres until this transaction ends.
    Without it, instances racing create_all fail on pg_type_typname_nsp_index and
    racing ALTERs fail on duplicate columns. SQLite: no-op."""
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": MIGRATION_LOCK_KEY})


def run_migrations(connection) -> None:
    """Startup schema sync: create_all plus every idempotent upgrade helper, in one transaction.

    The only schema entry point: the API lifespan calls this and nothing else, so on Postgres every
    step (relayer ones included) runs under the advisory xact lock and concurrent Cloud Run startups
    queue instead of racing DDL. Each helper inspects first, so a second run is a no-op."""
    import app.models  # noqa: F401  (registers every table on Base.metadata)
    from app.relayer.schema import ensure_relayer_schema

    lock_migrations(connection)
    Base.metadata.create_all(connection)
    ensure_live_score_facts(connection)
    ensure_user_cdp_user_id(connection)
    ensure_trade_relay_columns(connection)
    ensure_order_wide_columns(connection)
    ensure_attestation_created_at(connection)
    # relay_jobs: late columns, then drop the old (taker_hash, maker_hash) key (SQLite rebuilds the table).
    ensure_relayer_schema(connection)
    ensure_market_listing_reason(connection)
    ensure_vote_weight_bigint(connection)
    ensure_price_point_unique(connection)
    ensure_vote_unique(connection)
    ensure_emission_live_unique(connection)


async def get_db():
    async with SessionLocal() as session:
        yield session
