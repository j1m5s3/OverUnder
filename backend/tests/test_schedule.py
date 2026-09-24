import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.router import _issue
from app.db import Base, SessionLocal, engine, ensure_live_score_facts
from app.main import create_app
from app.models import NflScheduleGame, User

OP = "0x" + "0e" * 20
NONOP = "0x" + "1e" * 20


async def _reset(session) -> None:
    for row in (await session.execute(select(NflScheduleGame))).scalars().all():
        await session.delete(row)
    for user in (await session.execute(select(User).where(User.address.in_((OP, NONOP))))).scalars().all():
        await session.delete(user)


@pytest.fixture
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(ensure_live_score_facts)
    async with SessionLocal() as session:
        await _reset(session)
        session.add_all(
            [
                User(address=OP, is_operator=True),
                User(address=NONOP, is_operator=False),
            ]
        )
        await session.commit()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    async with SessionLocal() as session:
        await _reset(session)
        await session.commit()


def _op_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_issue(OP, True)}"}


def _game(**overrides) -> dict:
    body = {
        "away": "Bills",
        "home": "Dolphins",
        "kickoff_unix": 1_800_000_000,
        "week": 3,
        "season": 2026,
        "status": "scheduled",
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_schedule_requires_operator(client):
    payload = [_game()]
    anon = await client.post("/api/v1/markets/schedule", json=payload)
    assert anon.status_code == 401
    nonop = await client.post(
        "/api/v1/markets/schedule",
        headers={"Authorization": f"Bearer {_issue(NONOP, False)}"},
        json=payload,
    )
    assert nonop.status_code == 403


@pytest.mark.asyncio
async def test_schedule_upsert_then_get(client):
    first = await client.post("/api/v1/markets/schedule", headers=_op_headers(), json=[_game()])
    assert first.status_code == 200
    rows = first.json()
    assert len(rows) == 1
    assert rows[0]["home"] == "Dolphins"
    assert rows[0]["kickoff_unix"] == 1_800_000_000

    listed = await client.get("/api/v1/markets/schedule", params={"season": 2026, "week": 3})
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    second = await client.post(
        "/api/v1/markets/schedule",
        headers=_op_headers(),
        json=[_game(kickoff_unix=1_800_000_100, status="final")],
    )
    assert second.status_code == 200
    again = await client.get("/api/v1/markets/schedule", params={"season": 2026, "week": 3})
    assert len(again.json()) == 1
    assert again.json()[0]["kickoff_unix"] == 1_800_000_100
    assert again.json()[0]["status"] == "final"


LINKED = "0x" + "5e" * 32


@pytest.mark.asyncio
async def test_schedule_link_is_kept_when_omitted_and_cleared_by_empty_string(client):
    linked = await client.post("/api/v1/markets/schedule", headers=_op_headers(), json=[_game(listedConditionId=LINKED)])
    assert linked.json()[0]["listedConditionId"] == LINKED

    kept = await client.post("/api/v1/markets/schedule", headers=_op_headers(), json=[_game(status="in_progress")])
    assert kept.status_code == 200
    assert kept.json()[0]["listedConditionId"] == LINKED and kept.json()[0]["status"] == "in_progress"

    cleared = await client.post("/api/v1/markets/schedule", headers=_op_headers(), json=[_game(listedConditionId="")])
    assert cleared.status_code == 200
    assert cleared.json()[0]["listedConditionId"] == ""
    async with SessionLocal() as session:
        (row,) = (await session.execute(select(NflScheduleGame))).scalars().all()
        assert row.listed_condition_id == ""


@pytest.mark.asyncio
async def test_schedule_rejects_oversized_condition_id(client):
    r = await client.post(
        "/api/v1/markets/schedule", headers=_op_headers(), json=[_game(listedConditionId="0x" + "a" * 65)]
    )
    assert r.status_code == 422
