import pytest
from httpx import ASGITransport, AsyncClient

from app.db import Base, SessionLocal, engine
from app.indexer.listener import build_seed_point, build_swap_point, mid_to_micros
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
    assert payload[1]["yesPriceMicros"] == 600_000
    assert payload[2]["yesPriceMicros"] == 750_000
    assert all(p["conditionId"] == ORDERED for p in payload)


def test_mid_to_micros_pool_mid_math():
    assert mid_to_micros(100, 100) == 500_000
    assert mid_to_micros(75, 25) == 750_000
    assert mid_to_micros(0, 0) == 500_000


def test_swap_point_uses_pool_mid_not_trade_amounts():
    pt = build_swap_point(COND, 1234, 9, 2, 900, 100)
    assert pt.yes_price_micros == 900_000
    seed = build_seed_point(COND, 1000, 8, 0)
    assert seed.yes_price_micros == 500_000
