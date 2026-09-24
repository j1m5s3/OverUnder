import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from web3 import Web3

from app.config import Settings
from app.db import Base, SessionLocal, engine
from app.indexer import listener
from app.indexer.listener import index_once, index_range, next_delay, plan_ranges, resolve_start_block, run_indexer_loop
from app.models import Checkpoint, IndexerCheckpoint, Market, MarketListing

FACTORY = "0x" + "11" * 20
KEY = (FACTORY, "")
CREATED_CID = "0x" + "c1" * 32
USER_CID = "0x" + "c2" * 32
CREATOR = "0x" + "c3" * 20
USER_CRITERIA = "Resolves YES if the indexer copies verified criteria text."
USER_QUESTION = "Will the indexer record user listings?"
USER_CLOSE = 2_000_000_000
USER_SEED = 25_000_000


# --- pure helpers -----------------------------------------------------------------


def test_start_block_floor_fast_forwards_stale_checkpoint():
    assert resolve_start_block(0, 1000, 5000, 84532, 43_200) == 1000


def test_start_block_resumes_checkpoint_past_floor():
    assert resolve_start_block(1500, 1000, 5000, 84532, 43_200) == 1501


def test_start_block_anvil_default_is_block_one():
    assert resolve_start_block(0, 0, 5000, 31337, 100) == 1


def test_start_block_live_chain_default_uses_lookback_only_without_checkpoint():
    assert resolve_start_block(0, 0, 50_000, 84532, 1_000) == 49_001
    assert resolve_start_block(20, 0, 50_000, 84532, 1_000) == 21


def test_plan_ranges_caps_window_and_chunk_count():
    assert plan_ranges(1000, 1500, 200, 10) == [(1000, 1199), (1200, 1399), (1400, 1500)]
    assert plan_ranges(1000, 9999, 200, 2) == [(1000, 1199), (1200, 1399)]
    assert plan_ranges(10, 9, 200, 2) == []
    assert all(hi - lo + 1 <= 7 for lo, hi in plan_ranges(1, 100, 7, 100))


def test_next_delay_backs_off_and_resets():
    assert next_delay(5, 0, 300) == 5
    assert [next_delay(5, n, 300) for n in (1, 2, 3)] == [10, 20, 40]
    assert next_delay(5, 10, 300) == 300


def test_blank_start_block_env_parses_as_zero(monkeypatch):
    monkeypatch.setenv("INDEXER_START_BLOCK", "")
    assert Settings().indexer_start_block == 0
    monkeypatch.setenv("INDEXER_START_BLOCK", "123")
    assert Settings().indexer_start_block == 123


# --- index_once with fakes -------------------------------------------------------


class _Events:
    def __init__(self, logs_for=None, fail_from=None, calls=None):
        self._logs_for = logs_for or (lambda lo, hi: [])
        self._fail_from = fail_from
        self.calls = calls if calls is not None else []

    def __call__(self):
        return self

    def get_logs(self, from_block=None, to_block=None):
        self.calls.append((from_block, to_block))
        if self._fail_from is not None and from_block >= self._fail_from:
            raise RuntimeError("query exceeds max block range")
        return self._logs_for(from_block, to_block)


def _created_log(block):
    return {
        "blockNumber": block,
        "logIndex": 0,
        "args": {
            "conditionId": bytes.fromhex(CREATED_CID[2:]),
            "parentConditionId": b"\x00" * 32,
            "question": "Indexer range test?",
            "marketType": 0,
            "closeTime": 2_000_000_000,
        },
    }


def _fake_factory(created, paused=None):
    return SimpleNamespace(events=SimpleNamespace(MarketCreated=created, MarketPaused=paused or _Events()))


@pytest.fixture
async def indexer_env(monkeypatch):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _clear_rows(db):
        for row in (await db.execute(select(IndexerCheckpoint))).scalars().all():
            await db.delete(row)
        legacy = await db.get(Checkpoint, 1)
        if legacy is not None:
            await db.delete(legacy)
        row = await db.get(Market, CREATED_CID)
        if row is not None:
            await db.delete(row)

    async def reset(last_block, key=KEY):
        """Give the address set `key` a checkpoint at last_block (None: no row, a fresh set)."""
        async with SessionLocal() as db:
            await _clear_rows(db)
            await db.flush()
            if last_block is not None:
                db.add(IndexerCheckpoint(factory_address=key[0], amm_address=key[1], last_block=last_block))
            await db.commit()

    async def set_legacy(last_block):
        async with SessionLocal() as db:
            db.add(Checkpoint(id=1, last_block=last_block))
            await db.commit()

    async def last_block(key=KEY):
        async with SessionLocal() as db:
            row = await db.get(IndexerCheckpoint, key)
            return None if row is None else row.last_block

    async def legacy_block():
        async with SessionLocal() as db:
            row = await db.get(Checkpoint, 1)
            return None if row is None else row.last_block

    def configure(head, factory, addresses=None, **overrides):
        settings = Settings(**{"chain_id": 84532, **overrides})
        monkeypatch.setattr(listener, "get_settings", lambda: settings)
        monkeypatch.setattr(listener, "get_contract_addresses", lambda: dict(addresses or {"MarketFactory": FACTORY}))
        monkeypatch.setattr(listener, "_web3", lambda url, timeout: SimpleNamespace(eth=SimpleNamespace(block_number=head)))
        monkeypatch.setattr(listener, "_build_contracts", lambda w3, addresses: (factory, None))
        monkeypatch.setattr(listener, "_STATE", listener._IndexerState())

    yield SimpleNamespace(
        reset=reset, last_block=last_block, configure=configure, set_legacy=set_legacy, legacy_block=legacy_block
    )
    async with SessionLocal() as db:
        await _clear_rows(db)
        await db.commit()


@pytest.mark.asyncio
async def test_index_once_fast_forwards_and_chunks(indexer_env):
    await indexer_env.reset(0)
    created = _Events(logs_for=lambda lo, hi: [_created_log(1250)] if lo <= 1250 <= hi else [])
    indexer_env.configure(1500, _fake_factory(created), indexer_start_block=1000, indexer_max_block_range=200)
    assert await index_once() is True
    assert created.calls == [(1000, 1199), (1200, 1399), (1400, 1500)]
    assert await indexer_env.last_block() == 1500
    async with SessionLocal() as db:
        assert (await db.get(Market, CREATED_CID)) is not None


@pytest.mark.asyncio
async def test_index_once_caps_chunks_per_tick(indexer_env):
    await indexer_env.reset(0)
    created = _Events()
    indexer_env.configure(
        100_000, _fake_factory(created), indexer_start_block=1000, indexer_max_block_range=100, indexer_max_chunks_per_tick=3
    )
    assert await index_once() is True
    assert created.calls == [(1000, 1099), (1100, 1199), (1200, 1299)]
    assert await indexer_env.last_block() == 1299


@pytest.mark.asyncio
async def test_index_once_failure_holds_checkpoint_and_halves_window(indexer_env):
    await indexer_env.reset(0)
    created = _Events(fail_from=1200)
    indexer_env.configure(1500, _fake_factory(created), indexer_start_block=1000, indexer_max_block_range=200)
    assert await index_once() is False
    assert await indexer_env.last_block() == 1199
    assert listener._STATE.block_range == 100


@pytest.mark.asyncio
async def test_index_once_rpc_head_failure_returns_false(indexer_env, monkeypatch):
    await indexer_env.reset(42)
    indexer_env.configure(1500, _fake_factory(_Events()))

    class _Broken:
        @property
        def block_number(self):
            raise ConnectionError("rpc down")

    monkeypatch.setattr(listener, "_web3", lambda url, timeout: SimpleNamespace(eth=_Broken()))
    assert await index_once() is False
    assert await indexer_env.last_block() == 42


@pytest.mark.asyncio
async def test_run_indexer_loop_backs_off_then_resets(monkeypatch):
    results = iter([False, False, True])
    delays = []

    async def fake_index_once():
        return next(results)

    async def fake_sleep(seconds):
        delays.append(seconds)
        if len(delays) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(listener, "index_once", fake_index_once)
    with pytest.raises(asyncio.CancelledError):
        await run_indexer_loop(5, 300, sleep=fake_sleep)
    assert delays == [10, 20, 5]


# --- type-2 (user-listed) MarketCreated -------------------------------------------


def _user_created_log(block=10, question=USER_QUESTION):
    return {
        "blockNumber": block,
        "logIndex": 0,
        "args": {
            "conditionId": bytes.fromhex(USER_CID[2:]),
            "parentConditionId": b"\x00" * 32,
            "creator": Web3.to_checksum_address(CREATOR),
            "question": question,
            "marketType": 2,
            "closeTime": USER_CLOSE,
        },
    }


class _View:
    def __init__(self, value=None, error=None):
        self._value, self._error = value, error

    def __call__(self, *_args):
        return self

    def call(self):
        if self._error is not None:
            raise self._error
        return self._value


def _user_factory(criteria_hash=None, seed=USER_SEED, error=None, question=USER_QUESTION):
    hash_bytes = bytes.fromhex((criteria_hash or Web3.to_hex(Web3.keccak(text=USER_CRITERIA)))[2:])
    return SimpleNamespace(
        events=SimpleNamespace(
            MarketCreated=_Events(logs_for=lambda lo, hi: [_user_created_log(question=question)]), MarketPaused=_Events()
        ),
        functions=SimpleNamespace(criteriaHashOf=_View(hash_bytes, error), seedOf=_View(seed, error)),
    )


@pytest.fixture
async def user_listing_rows():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def clean():
        async with SessionLocal() as db:
            for model in (Market, MarketListing):
                row = await db.get(model, USER_CID)
                if row is not None:
                    await db.delete(row)
            await db.commit()

    await clean()
    yield
    await clean()


async def _run_user_range(factory):
    async with SessionLocal() as db:
        assert await index_range(None, factory, None, db, 1, 20) is True
        await db.commit()
    async with SessionLocal() as db:
        return await db.get(Market, USER_CID), await db.get(MarketListing, USER_CID)


@pytest.mark.asyncio
async def test_type2_created_event_records_indexed_listing(user_listing_rows):
    market, listing = await _run_user_range(_user_factory())
    assert market.market_type == 2
    assert market.resolution_criteria == ""
    assert listing.creator == CREATOR
    assert listing.status == "indexed"
    assert listing.seed_usdc == 25_000_000
    assert listing.criteria_hash == Web3.to_hex(Web3.keccak(text=USER_CRITERIA))


def _prepared(question=USER_QUESTION, close_time=USER_CLOSE, seed=USER_SEED):
    return MarketListing(
        condition_id=USER_CID,
        creator=CREATOR,
        salt="0x" + "5a" * 32,
        question=question,
        resolution_criteria=USER_CRITERIA,
        close_time=close_time,
        seed_usdc=seed,
        status="prepared",
    )


@pytest.mark.asyncio
async def test_type2_copies_prepared_criteria_only_when_hash_matches(user_listing_rows):
    async with SessionLocal() as db:
        db.add(_prepared())
        await db.commit()
    market, listing = await _run_user_range(_user_factory())
    assert market.resolution_criteria == USER_CRITERIA
    assert listing.status == "confirmed"


@pytest.mark.asyncio
async def test_type2_hash_mismatch_leaves_criteria_blank(user_listing_rows):
    async with SessionLocal() as db:
        db.add(_prepared())
        await db.commit()
    market, listing = await _run_user_range(_user_factory(criteria_hash=Web3.to_hex(Web3.keccak(text="tampered"))))
    assert market.resolution_criteria == ""
    assert listing.status == "prepared"


@pytest.mark.asyncio
async def test_type2_commitment_rpc_failure_does_not_fail_range(user_listing_rows):
    market, listing = await _run_user_range(_user_factory(error=ConnectionError("rpc down")))
    assert market is not None
    assert listing.status == "indexed"
    assert listing.criteria_hash == ""


async def _public_ids():
    from sqlalchemy import select

    from app.markets.visibility import visible_clause

    async with SessionLocal() as db:
        return {cid for (cid,) in (await db.execute(select(Market.condition_id).where(visible_clause()))).all()}


@pytest.mark.asyncio
async def test_type2_indexed_only_market_is_hidden_and_not_tradable(user_listing_rows):
    from app.markets.trading import trading_halt_reason

    market, listing = await _run_user_range(_user_factory())
    assert listing.status == "indexed"
    assert USER_CID not in await _public_ids()
    async with SessionLocal() as db:
        assert await trading_halt_reason(db, USER_CID, Settings(trading_halt_at_close=True)) == "listing not confirmed"


@pytest.mark.asyncio
async def test_type2_prepared_listing_becomes_public(user_listing_rows):
    async with SessionLocal() as db:
        db.add(_prepared())
        await db.commit()
    await _run_user_range(_user_factory())
    assert USER_CID in await _public_ids()


@pytest.mark.asyncio
async def test_type2_swapped_onchain_question_is_rejected(user_listing_rows):
    async with SessionLocal() as db:
        db.add(_prepared())
        await db.commit()
    swapped = "Will the indexer record user listings before 2030?"
    market, listing = await _run_user_range(_user_factory(question=swapped))
    assert listing.status == "rejected"
    assert "differs from the prepared question" in listing.reject_reason
    assert listing.question == USER_QUESTION  # prepared value kept for /confirm's comparison
    assert market.resolution_criteria == ""
    assert USER_CID not in await _public_ids()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad,fragment",
    [
        ("Who is the best QB and should he feel underrated", "ending in '?'"),
        ("Is this the best indexer of 2026?", "subjective"),
        (" Will the indexer record user listings?", "whitespace"),
    ],
)
async def test_type2_gate_failing_question_is_rejected_and_hidden(user_listing_rows, bad, fragment):
    async with SessionLocal() as db:
        db.add(_prepared(question=bad))
        await db.commit()
    market, listing = await _run_user_range(_user_factory(question=bad))
    assert listing.status == "rejected"
    assert fragment in listing.reject_reason
    assert market.resolution_criteria == ""
    assert USER_CID not in await _public_ids()


@pytest.mark.asyncio
async def test_type2_rows_inserted_concurrently_do_not_break_the_range(user_listing_rows):
    """/listing/confirm committed first: the indexer's inserts are no-ops, not IntegrityErrors."""
    async with SessionLocal() as db:
        db.add(
            Market(
                condition_id=USER_CID,
                question=USER_QUESTION,
                resolution_criteria=USER_CRITERIA,
                market_type=2,
                close_time=USER_CLOSE,
            )
        )
        db.add(
            MarketListing(
                condition_id=USER_CID,
                creator=CREATOR,
                question=USER_QUESTION,
                resolution_criteria=USER_CRITERIA,
                status="confirmed",
            )
        )
        await db.commit()
    market, listing = await _run_user_range(_user_factory())
    assert listing.status == "confirmed"
    assert market.resolution_criteria == USER_CRITERIA
    assert USER_CID in await _public_ids()


# --- monotonic checkpoint + leader lock -------------------------------------------


@pytest.mark.asyncio
async def test_advance_checkpoint_never_moves_backward(indexer_env):
    from app.indexer.listener import advance_checkpoint

    await indexer_env.reset(9_999)
    async with SessionLocal() as db:
        assert await advance_checkpoint(db, KEY, 1_500) == 9_999
        assert await advance_checkpoint(db, KEY, 10_050) == 10_050
    assert await indexer_env.last_block() == 10_050


@pytest.mark.asyncio
async def test_stale_tick_does_not_rewind_checkpoint(indexer_env, monkeypatch):
    """Another instance advances the checkpoint mid-tick; this tick's writes must not lower it."""
    from sqlalchemy import update

    await indexer_env.reset(0)
    real_index_range = listener.index_range

    async def other_instance_advances_first(w3, factory, amm, db, lo, hi):
        if lo == 1000:
            async with SessionLocal() as other:
                await other.execute(
                    update(IndexerCheckpoint)
                    .where(IndexerCheckpoint.factory_address == KEY[0], IndexerCheckpoint.amm_address == KEY[1])
                    .values(last_block=50_000)
                )
                await other.commit()
        return await real_index_range(w3, factory, amm, db, lo, hi)

    monkeypatch.setattr(listener, "index_range", other_instance_advances_first)
    indexer_env.configure(1500, _fake_factory(_Events()), indexer_start_block=1000, indexer_max_block_range=200)
    assert await index_once() is True
    assert await indexer_env.last_block() == 50_000


class _FakePgConn:
    def __init__(self, got):
        self.got, self.sql, self.closed = got, [], False

    async def execute(self, stmt, params=None):
        self.sql.append((str(stmt), params))
        return SimpleNamespace(scalar=lambda: self.got)

    async def commit(self):
        pass

    async def close(self):
        self.closed = True


class _FakePgEngine:
    def __init__(self, got):
        self.dialect = SimpleNamespace(name="postgresql")
        self.conns = []
        self._got = got

    async def connect(self):
        conn = _FakePgConn(self._got)
        self.conns.append(conn)
        return conn


@pytest.mark.asyncio
async def test_non_leader_skips_indexing_without_backoff(indexer_env, monkeypatch):
    await indexer_env.reset(0)
    created = _Events()
    indexer_env.configure(1500, _fake_factory(created), indexer_start_block=1000)
    fake = _FakePgEngine(got=False)
    monkeypatch.setattr(listener, "engine", fake)
    monkeypatch.setattr(listener, "_LEADER", listener._LeaderState())
    assert await index_once() is True
    assert created.calls == []
    assert await indexer_env.last_block() == 0
    (conn,) = fake.conns
    assert "pg_try_advisory_lock" in conn.sql[0][0]
    assert conn.sql[0][1] == {"k": 0x4F55494E}
    assert conn.closed is True
    assert listener._LEADER.conn is None


@pytest.mark.asyncio
async def test_leader_holds_one_connection_and_releases_it(monkeypatch):
    fake = _FakePgEngine(got=True)
    monkeypatch.setattr(listener, "engine", fake)
    monkeypatch.setattr(listener, "_LEADER", listener._LeaderState())
    settings = Settings(chain_id=84532)
    assert await listener.ensure_indexer_leader(settings) is True
    assert await listener.ensure_indexer_leader(settings) is True
    assert len(fake.conns) == 1  # the lock connection is reused across ticks
    conn = fake.conns[0]
    await listener.release_indexer_leadership()
    assert any("pg_advisory_unlock" in sql for sql, _ in conn.sql)
    assert conn.closed is True and listener._LEADER.conn is None


def test_indexer_lock_key_is_distinct():
    from app.db import MIGRATION_LOCK_KEY

    settings = Settings(_env_file=None)
    keys = {settings.indexer_leader_lock_key, settings.relayer_leader_lock_key, MIGRATION_LOCK_KEY}
    assert len(keys) == 3


# --- checkpoint keyed by contract addresses (v2 cutover) --------------------------

NEW_FACTORY = "0x" + "AB" * 20
NEW_AMM = "0x" + "CD" * 20
NEW_KEY = (NEW_FACTORY.lower(), NEW_AMM.lower())


def test_checkpoint_key_is_lowercase_and_amm_optional():
    assert listener.checkpoint_key({"MarketFactory": NEW_FACTORY, "MarketAMM": NEW_AMM}) == NEW_KEY
    assert listener.checkpoint_key({"MarketFactory": FACTORY}) == KEY


@pytest.mark.asyncio
async def test_new_address_set_starts_at_start_block_not_old_checkpoint(indexer_env):
    """Cutover: the old revision advanced the old set (and the legacy row) past the v2 deploy block."""
    await indexer_env.reset(60_000)
    await indexer_env.set_legacy(60_000)
    created = _Events()
    indexer_env.configure(
        1500, _fake_factory(created), addresses={"MarketFactory": NEW_FACTORY, "MarketAMM": NEW_AMM},
        indexer_start_block=1000, indexer_max_block_range=1000,
    )
    assert await index_once() is True
    assert created.calls == [(1000, 1500)]
    assert await indexer_env.last_block(NEW_KEY) == 1500
    assert await indexer_env.last_block() == 60_000  # the old set's checkpoint is untouched
    assert await indexer_env.legacy_block() == 60_000


@pytest.mark.asyncio
async def test_existing_address_set_is_never_rewound_by_start_block(indexer_env):
    await indexer_env.reset(1_400)
    created = _Events()
    indexer_env.configure(1500, _fake_factory(created), indexer_start_block=1000)
    assert await index_once() is True
    assert created.calls == [(1401, 1500)]


@pytest.mark.asyncio
async def test_fresh_set_without_start_block_never_starts_past_the_legacy_checkpoint(indexer_env):
    await indexer_env.reset(None)
    await indexer_env.set_legacy(40_000)
    created = _Events()
    indexer_env.configure(50_000, _fake_factory(created), indexer_lookback_blocks=1_000, indexer_max_block_range=100_000)
    assert await index_once() is True
    assert created.calls == [(40_001, 50_000)]


@pytest.mark.asyncio
async def test_fresh_set_without_start_block_rescans_lookback_when_legacy_is_ahead(indexer_env):
    await indexer_env.reset(None)
    await indexer_env.set_legacy(49_990)
    created = _Events()
    indexer_env.configure(50_000, _fake_factory(created), indexer_lookback_blocks=1_000, indexer_max_block_range=100_000)
    assert await index_once() is True
    assert created.calls == [(49_001, 50_000)]


@pytest.mark.asyncio
async def test_fresh_set_defaults_lookback_or_anvil_block_one(indexer_env):
    await indexer_env.reset(None)
    created = _Events()
    indexer_env.configure(50_000, _fake_factory(created), indexer_lookback_blocks=1_000, indexer_max_block_range=100_000)
    assert await index_once() is True
    assert created.calls == [(49_001, 50_000)]

    await indexer_env.reset(None)
    anvil = _Events()
    indexer_env.configure(300, _fake_factory(anvil), chain_id=31337, indexer_max_block_range=1_000)
    assert await index_once() is True
    assert anvil.calls == [(1, 300)]


# --- leader connection is invalidated, not pooled, when dropped on an error -------


class _FlakyPgConn(_FakePgConn):
    def __init__(self, got, fail_on=()):
        super().__init__(got)
        self.fail_on, self.invalidated = fail_on, False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if any(f in sql for f in self.fail_on):
            self.sql.append((sql, params))
            raise RuntimeError("server closed the connection unexpectedly")
        return await super().execute(stmt, params)

    async def invalidate(self):
        self.invalidated = True


class _FlakyPgEngine(_FakePgEngine):
    def __init__(self, got, fail_on=()):
        super().__init__(got)
        self.fail_on = fail_on

    async def connect(self):
        conn = _FlakyPgConn(self._got, self.fail_on)
        self.conns.append(conn)
        return conn


@pytest.mark.asyncio
async def test_failed_leader_ping_invalidates_the_lock_connection(monkeypatch):
    fake = _FlakyPgEngine(got=True)
    monkeypatch.setattr(listener, "engine", fake)
    monkeypatch.setattr(listener, "_LEADER", listener._LeaderState())
    settings = Settings(chain_id=84532)
    assert await listener.ensure_indexer_leader(settings) is True
    first = fake.conns[0]
    first.fail_on = ("SELECT 1",)
    assert await listener.ensure_indexer_leader(settings) is True  # re-elected on a fresh connection
    assert first.invalidated is True and first.closed is True
    assert len(fake.conns) == 2 and listener._LEADER.conn is fake.conns[1]


@pytest.mark.asyncio
async def test_failed_try_lock_and_failed_unlock_invalidate(monkeypatch):
    fake = _FlakyPgEngine(got=True, fail_on=("pg_try_advisory_lock",))
    monkeypatch.setattr(listener, "engine", fake)
    monkeypatch.setattr(listener, "_LEADER", listener._LeaderState())
    settings = Settings(chain_id=84532)
    with pytest.raises(RuntimeError):
        await listener.ensure_indexer_leader(settings)
    assert fake.conns[0].invalidated is True and fake.conns[0].closed is True

    fake.fail_on = ()
    assert await listener.ensure_indexer_leader(settings) is True
    leader = fake.conns[1]
    leader.fail_on = ("pg_advisory_unlock",)
    await listener.release_indexer_leadership()
    assert leader.invalidated is True and leader.closed is True and listener._LEADER.conn is None


@pytest.mark.asyncio
async def test_clean_release_does_not_invalidate(monkeypatch):
    fake = _FlakyPgEngine(got=True)
    monkeypatch.setattr(listener, "engine", fake)
    monkeypatch.setattr(listener, "_LEADER", listener._LeaderState())
    assert await listener.ensure_indexer_leader(Settings(chain_id=84532)) is True
    await listener.release_indexer_leadership()
    assert fake.conns[0].invalidated is False and fake.conns[0].closed is True
