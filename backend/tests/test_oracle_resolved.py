import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.router import _issue
from app.db import Base, SessionLocal, engine, ensure_live_score_facts
from app.main import create_app
from app.models import Market, User

PRIMARY = "0x" + "c0" * 32
OP = "0x" + "0e" * 20
NONOP = "0x" + "1e" * 20


async def _reset(session) -> None:
    row = await session.get(Market, PRIMARY)
    if row is not None:
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
                Market(
                    condition_id=PRIMARY,
                    parent_condition_id="",
                    question="Chiefs vs Broncos: Chiefs win?",
                    resolution_criteria="",
                    market_type=0,
                    close_time=1_700_000_000,
                    suggested_probability=0.5,
                ),
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
async def test_oracle_resolved_requires_operator(client):
    payload = {"conditionId": PRIMARY, "outcome": 0}
    anon = await client.post("/api/v1/oracle/resolved", json=payload)
    assert anon.status_code == 401
    nonop = await client.post(
        "/api/v1/oracle/resolved",
        headers={"Authorization": f"Bearer {_issue(NONOP, False)}"},
        json=payload,
    )
    assert nonop.status_code == 403


@pytest.mark.asyncio
async def test_oracle_resolved_flips_flags(client):
    r = await client.post(
        "/api/v1/oracle/resolved",
        headers=_op_headers(),
        json={"conditionId": PRIMARY, "outcome": 0},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["resolved"] is True
    assert body["payoutYes"] == 1
    assert body["payoutNo"] == 0
    detail = await client.get(f"/api/v1/markets/{PRIMARY}")
    assert detail.status_code == 200
    assert detail.json()["resolved"] is True
    assert detail.json()["payoutYes"] == 1
