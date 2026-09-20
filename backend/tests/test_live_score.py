import pytest
from httpx import ASGITransport, AsyncClient

from sqlalchemy import select

from app.auth.router import _issue
from app.db import Base, SessionLocal, engine
from app.main import create_app
from app.models import LiveScore, Market, User

PRIMARY = "0x" + "b0" * 32
WILDCARD = "0x" + "b1" * 32
OP = "0x" + "0e" * 20
NONOP = "0x" + "1e" * 20
SEED_IDS = (PRIMARY, WILDCARD)


def _market(condition_id: str, market_type: int, parent: str = "") -> Market:
    return Market(
        condition_id=condition_id,
        parent_condition_id=parent,
        question="Chiefs vs Broncos: Chiefs win?",
        resolution_criteria="",
        market_type=market_type,
        close_time=2_000_000_000,
        suggested_probability=0.5,
    )


async def _reset(session) -> None:
    for existing in SEED_IDS:
        row = await session.get(Market, existing)
        if row is not None:
            await session.delete(row)
        score = await session.get(LiveScore, existing)
        if score is not None:
            await session.delete(score)
    for user in (await session.execute(select(User).where(User.address.in_((OP, NONOP))))).scalars().all():
        await session.delete(user)


@pytest.fixture
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        await _reset(session)
        session.add_all(
            [
                _market(PRIMARY, 0),
                _market(WILDCARD, 1, parent=PRIMARY),
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


@pytest.mark.asyncio
async def test_upsert_and_detail_includes_score(client):
    r = await client.post(
        f"/api/v1/markets/{PRIMARY}/score",
        headers=_op_headers(),
        json={
            "homeLabel": "Chiefs",
            "awayLabel": "Broncos",
            "homeScore": 14,
            "awayScore": 10,
            "status": "in_progress",
            "periodLabel": "Q2",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["conditionId"] == PRIMARY
    assert body["homeScore"] == 14
    assert body["awayScore"] == 10
    assert body["status"] == "in_progress"
    assert body["periodLabel"] == "Q2"

    detail = await client.get(f"/api/v1/markets/{PRIMARY}")
    assert detail.status_code == 200
    assert detail.json()["score"]["homeScore"] == 14


@pytest.mark.asyncio
async def test_upsert_updates_existing_row(client):
    first = {
        "homeLabel": "Chiefs",
        "awayLabel": "Broncos",
        "homeScore": 7,
        "awayScore": 0,
        "status": "in_progress",
        "periodLabel": "Q1",
    }
    second = {**first, "homeScore": 14, "status": "final", "periodLabel": None}
    assert (await client.post(f"/api/v1/markets/{PRIMARY}/score", headers=_op_headers(), json=first)).status_code == 200
    r = await client.post(f"/api/v1/markets/{PRIMARY}/score", headers=_op_headers(), json=second)
    assert r.status_code == 200
    assert r.json()["homeScore"] == 14
    assert r.json()["status"] == "final"


@pytest.mark.asyncio
async def test_detail_null_when_absent(client):
    detail = await client.get(f"/api/v1/markets/{PRIMARY}")
    assert detail.status_code == 200
    assert detail.json()["score"] is None


@pytest.mark.asyncio
async def test_scheduled_null_scores_allowed(client):
    r = await client.post(
        f"/api/v1/markets/{PRIMARY}/score",
        headers=_op_headers(),
        json={"homeLabel": "Chiefs", "awayLabel": "Broncos", "status": "scheduled"},
    )
    assert r.status_code == 200
    assert r.json()["homeScore"] is None
    assert r.json()["awayScore"] is None


@pytest.mark.asyncio
async def test_wildcard_write_rejected_and_detail_null(client):
    r = await client.post(
        f"/api/v1/markets/{WILDCARD}/score",
        headers=_op_headers(),
        json={"homeLabel": "A", "awayLabel": "B", "status": "scheduled"},
    )
    assert r.status_code == 400
    detail = await client.get(f"/api/v1/markets/{WILDCARD}")
    assert detail.status_code == 200
    assert detail.json()["score"] is None


@pytest.mark.asyncio
async def test_score_requires_operator(client):
    payload = {"homeLabel": "A", "awayLabel": "B", "status": "scheduled"}
    anon = await client.post(f"/api/v1/markets/{PRIMARY}/score", json=payload)
    assert anon.status_code == 401
    nonop = await client.post(
        f"/api/v1/markets/{PRIMARY}/score",
        headers={"Authorization": f"Bearer {_issue(NONOP, False)}"},
        json=payload,
    )
    assert nonop.status_code == 403


@pytest.mark.asyncio
async def test_score_404_for_unknown_market(client):
    r = await client.post(
        "/api/v1/markets/0xdeadbeef/score",
        headers=_op_headers(),
        json={"homeLabel": "A", "awayLabel": "B", "status": "scheduled"},
    )
    assert r.status_code == 404
