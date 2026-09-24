import math
from statistics import NormalDist

import pytest
from httpx import ASGITransport, AsyncClient
from types import SimpleNamespace

from app.db import Base, SessionLocal, engine
from app.indexer.listener import (
    _index_amm_history,
    build_seed_point,
    build_swap_point,
    mid_to_micros,
    pm_price_micros,
    pool_price_micros,
)
from app.main import create_app
from app.models import Market, PricePoint

COND = "0x" + "e1" * 32
ORDERED = "0x" + "e2" * 32
SEED_IDS = (COND, ORDERED)


def _market(condition_id: str) -> Market:
    return Market(
        condition_id=condition_id,
        parent_condition_id="",
        question="History test market?",
        resolution_criteria="",
        market_type=0,
        close_time=2_000_000_000,
        suggested_probability=0.5,
    )


@pytest.fixture
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        for existing in SEED_IDS:
            row = await session.get(Market, existing)
            if row is not None:
                await session.delete(row)
        for row in await session.execute(
            __import__("sqlalchemy").select(PricePoint).where(PricePoint.condition_id.in_(SEED_IDS))
        ):
            await session.delete(row[0])
        session.add_all([_market(COND), _market(ORDERED)])
        await session.commit()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    async with SessionLocal() as session:
        for existing in SEED_IDS:
            row = await session.get(Market, existing)
            if row is not None:
                await session.delete(row)
        for row in await session.execute(
            __import__("sqlalchemy").select(PricePoint).where(PricePoint.condition_id.in_(SEED_IDS))
        ):
            await session.delete(row[0])
        await session.commit()


@pytest.mark.asyncio
async def test_history_empty_for_market_without_swaps(client):
    r = await client.get(f"/api/v1/markets/{COND}/history")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_history_404_for_unknown_market(client):
    r = await client.get("/api/v1/markets/0xdeadbeef/history")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_history_returns_seed_then_swaps_in_order(client):
    async with SessionLocal() as session:
        session.add_all(
            [
                build_swap_point(ORDERED, 3000, 12, 1, 750, 250),
                build_seed_point(ORDERED, 1000, 10, 0),
                build_swap_point(ORDERED, 2000, 11, 0, 600, 400),
            ]
        )
        await session.commit()
    r = await client.get(f"/api/v1/markets/{ORDERED}/history")
    assert r.status_code == 200
    payload = r.json()
    assert [p["ts"] for p in payload] == [1000, 2000, 3000]
    assert payload[0]["yesPriceMicros"] == 500_000
    assert payload[1]["yesPriceMicros"] == 400_000
    assert payload[2]["yesPriceMicros"] == 250_000
    assert all(p["conditionId"] == ORDERED for p in payload)


def test_mid_to_micros_is_no_reserve_share():
    assert mid_to_micros(100, 100) == 500_000
    assert mid_to_micros(75, 25) == 250_000
    assert mid_to_micros(0, 0) == 500_000


def test_mid_rises_after_yes_buy_drains_yes_reserve():
    before = mid_to_micros(100, 100)
    after_yes_buy = mid_to_micros(80, 120)
    assert after_yes_buy > before
    assert after_yes_buy == 600_000


def test_swap_point_uses_pool_mid_not_trade_amounts():
    pt = build_swap_point(COND, 1234, 9, 2, 100, 900)
    assert pt.yes_price_micros == 900_000
    seed = build_seed_point(COND, 1000, 8, 0)
    assert seed.yes_price_micros == 500_000


FAKE_COND = "0x" + "f1" * 32


class _FakeEventFilter:
    def __init__(self, logs=None, error=None):
        self._logs = logs or []
        self._error = error

    def __call__(self):
        return self

    def get_logs(self, from_block=None, to_block=None):
        if self._error is not None:
            raise self._error
        return self._logs


class _FakePools:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def __call__(self, _cid):
        return self

    def call(self, block_identifier=None):
        if self._error is not None:
            raise self._error
        return self._result


def _fake_log(block_number: int, log_index: int):
    return {
        "blockNumber": block_number,
        "logIndex": log_index,
        "args": {"conditionId": bytes.fromhex(FAKE_COND[2:])},
    }


def _fake_w3(ts: int = 1700):
    return SimpleNamespace(eth=SimpleNamespace(get_block=lambda _b: {"timestamp": ts}))


def _fake_amm(seeded=None, swaps=None, pools=None):
    return SimpleNamespace(
        events=SimpleNamespace(PoolSeeded=seeded or _FakeEventFilter(), Swap=swaps or _FakeEventFilter()),
        functions=SimpleNamespace(pools=pools or _FakePools(result=(100, 100))),
    )


@pytest.fixture
async def history_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with SessionLocal() as session:
        for row in await session.execute(
            __import__("sqlalchemy").select(PricePoint).where(PricePoint.condition_id == FAKE_COND)
        ):
            await session.delete(row[0])
        await session.commit()


@pytest.mark.asyncio
async def test_amm_history_get_logs_failure_returns_false(history_tables):
    amm = _fake_amm(swaps=_FakeEventFilter(error=RuntimeError("rpc down")))
    async with SessionLocal() as session:
        assert await _index_amm_history(_fake_w3(), amm, session, 10, 11) is False
        await session.rollback()


@pytest.mark.asyncio
async def test_amm_history_pools_failure_returns_false(history_tables):
    amm = _fake_amm(swaps=_FakeEventFilter(logs=[_fake_log(11, 0)]), pools=_FakePools(error=RuntimeError("bad block")))
    async with SessionLocal() as session:
        assert await _index_amm_history(_fake_w3(), amm, session, 10, 11) is False
        await session.rollback()


@pytest.mark.asyncio
async def test_amm_history_happy_path_writes_inverted_mid(history_tables):
    amm = _fake_amm(
        seeded=_FakeEventFilter(logs=[_fake_log(10, 0)]),
        swaps=_FakeEventFilter(logs=[_fake_log(11, 2)]),
        pools=_FakePools(result=(100, 300)),
    )
    async with SessionLocal() as session:
        assert await _index_amm_history(_fake_w3(), amm, session, 10, 11) is True
        rows = (
            await session.execute(
                __import__("sqlalchemy")
                .select(PricePoint)
                .where(PricePoint.condition_id == FAKE_COND)
                .order_by(PricePoint.block_number)
            )
        ).scalars().all()
        assert [r.yes_price_micros for r in rows] == [500_000, 750_000]
        for row in rows:
            await session.delete(row)
        await session.commit()


# --- MarketAMM v2 (pm-AMM): YES = Phi((no - yes) / L) from pools(cid)[4] ----------------


def test_pm_price_balanced_pool_is_half():
    s = 1_000_000_000
    assert pm_price_micros(s, s, round(s * math.sqrt(2 * math.pi))) == 500_000


def test_pm_price_matches_normal_cdf():
    assert pm_price_micros(100, 300, 200) == round(NormalDist().cdf(1.0) * 1_000_000) == 841_345
    assert pm_price_micros(300, 100, 200) == 158_655


def test_pm_price_rises_after_yes_buy_drains_yes_reserve():
    liquidity = 2_506_628
    before = pm_price_micros(1_000_000, 1_000_000, liquidity)
    after_yes_buy = pm_price_micros(800_000, 1_150_000, liquidity)
    assert after_yes_buy > before
    assert pm_price_micros(1_150_000, 800_000, liquidity) < before


def test_pm_price_without_liquidity_falls_back_to_cpmm_mid():
    assert pm_price_micros(75, 25, 0) == mid_to_micros(75, 25) == 250_000


def test_pool_price_uses_liquidity_field_only_when_present():
    v2_pool = (100, 300, 1_000, True, 200, 0, 2_000_000_000)
    assert pool_price_micros(v2_pool) == 841_345
    assert pool_price_micros((100, 300, 1_000, True, 0, 0, 0)) == 750_000
    assert pool_price_micros((100, 300, 1_000, True)) == 750_000
    assert pool_price_micros((100, 300)) == 750_000


def test_swap_point_with_liquidity_uses_pm_price():
    assert build_swap_point(COND, 1, 1, 0, 100, 300, 200).yes_price_micros == 841_345
    assert build_swap_point(COND, 1, 1, 0, 100, 300).yes_price_micros == 750_000


@pytest.mark.asyncio
async def test_amm_history_v2_pool_writes_pm_price(history_tables):
    amm = _fake_amm(
        swaps=_FakeEventFilter(logs=[_fake_log(21, 0)]),
        pools=_FakePools(result=(100, 300, 1_000, True, 200, 0, 2_000_000_000)),
    )
    async with SessionLocal() as session:
        assert await _index_amm_history(_fake_w3(), amm, session, 20, 21) is True
        rows = (
            await session.execute(
                __import__("sqlalchemy").select(PricePoint).where(PricePoint.condition_id == FAKE_COND)
            )
        ).scalars().all()
        assert [r.yes_price_micros for r in rows] == [841_345]
        for row in rows:
            await session.delete(row)
        await session.commit()


# --- unique (condition_id, block_number, log_index) + ON CONFLICT DO NOTHING ---------------


@pytest.mark.asyncio
async def test_amm_history_concurrent_writers_store_one_point_per_event(history_tables, monkeypatch):
    """Two indexers whose pre-check both missed (concurrent instances) still store one row per event."""
    from sqlalchemy import func, select

    from app.indexer import listener

    def amm():
        return _fake_amm(
            seeded=_FakeEventFilter(logs=[_fake_log(30, 0)]),
            swaps=_FakeEventFilter(logs=[_fake_log(31, 1), _fake_log(31, 4)]),
            pools=_FakePools(result=(100, 300)),
        )

    async with SessionLocal() as first:
        assert await _index_amm_history(_fake_w3(), amm(), first, 30, 31) is True
        await first.commit()

    async def never_seen(*_a):
        return False

    monkeypatch.setattr(listener, "_point_exists", never_seen)
    async with SessionLocal() as second:
        assert await _index_amm_history(_fake_w3(), amm(), second, 30, 31) is True
        await second.commit()

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(PricePoint.block_number, PricePoint.log_index, func.count())
                .where(PricePoint.condition_id == FAKE_COND)
                .group_by(PricePoint.block_number, PricePoint.log_index)
            )
        ).all()
    assert sorted(rows) == [(30, 0, 1), (31, 1, 1), (31, 4, 1)]


def test_price_point_unique_migration_dedupes_legacy_rows(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from app.db import PRICE_POINT_UNIQUE, ensure_price_point_unique

    eng = create_engine(f"sqlite:///{(tmp_path / 'pp.db').as_posix()}")
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE price_points (id INTEGER PRIMARY KEY, condition_id VARCHAR(66), ts INTEGER, "
                    "block_number INTEGER, log_index INTEGER, yes_price_micros INTEGER)"
                )
            )
            for pid, block, idx in ((1, 5, 0), (2, 5, 0), (3, 5, 1), (4, 5, 0)):
                conn.execute(
                    text("INSERT INTO price_points VALUES (:id, '0xaa', 1, :b, :i, 500000)"),
                    {"id": pid, "b": block, "i": idx},
                )
        for _ in range(2):
            with eng.begin() as conn:
                ensure_price_point_unique(conn)
        with eng.connect() as conn:
            ids = [r[0] for r in conn.execute(text("SELECT id FROM price_points ORDER BY id"))]
            assert ids == [1, 3]
            assert PRICE_POINT_UNIQUE in {i["name"] for i in inspect(conn).get_indexes("price_points")}
    finally:
        eng.dispose()
