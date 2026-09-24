import pytest
from httpx import ASGITransport, AsyncClient

from app.db import Base, SessionLocal, engine
from app.main import create_app
from app.models import Market, MarketListing

PRIMARY = "0x" + "a1" * 32
CHILD = "0x" + "a2" * 32
PAUSED_CHILD = "0x" + "a3" * 32
RESOLVED_CHILD = "0x" + "a4" * 32
PAUSED_PARENT = "0x" + "b1" * 32
CHILD_OF_PAUSED = "0x" + "b2" * 32
MISSING_PARENT = "0x" + "c1" * 32
ORPHAN = "0x" + "c2" * 32
LONELY = "0x" + "d1" * 32
USER_LISTED = "0x" + "e7" * 32
CHILD_OF_USER = "0x" + "e8" * 32
USER_INDEXED = "0x" + "e9" * 32
USER_REJECTED = "0x" + "ea" * 32
USER_NO_LISTING = "0x" + "eb" * 32
SEED_IDS = (
    PRIMARY,
    CHILD,
    PAUSED_CHILD,
    RESOLVED_CHILD,
    PAUSED_PARENT,
    CHILD_OF_PAUSED,
    ORPHAN,
    LONELY,
    USER_LISTED,
    CHILD_OF_USER,
    USER_INDEXED,
    USER_REJECTED,
    USER_NO_LISTING,
)
LISTING_STATUS = {USER_LISTED: "confirmed", USER_INDEXED: "indexed", USER_REJECTED: "rejected"}


async def _clean(session) -> None:
    for existing in SEED_IDS:
        for model in (Market, MarketListing):
            row = await session.get(model, existing)
            if row is not None:
                await session.delete(row)


def _row(
    condition_id: str,
    *,
    question: str,
    market_type: int = 0,
    parent_condition_id: str = "",
    paused: bool = False,
    resolved: bool = False,
) -> Market:
    return Market(
        condition_id=condition_id,
        parent_condition_id=parent_condition_id,
        question=question,
        resolution_criteria="",
        market_type=market_type,
        close_time=2_000_000_000,
        paused=paused,
        resolved=resolved,
        suggested_probability=0.5,
    )


@pytest.fixture
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        await _clean(session)
        session.add_all(
            [
                _row(PRIMARY, question="Will the bill pass the Senate?"),
                _row(
                    CHILD,
                    question="Chiefs to score a touchdown in Q1?",
                    market_type=1,
                    parent_condition_id=PRIMARY,
                ),
                _row(
                    PAUSED_CHILD,
                    question="Paused wildcard",
                    market_type=1,
                    parent_condition_id=PRIMARY,
                    paused=True,
                ),
                _row(
                    RESOLVED_CHILD,
                    question="Resolved wildcard",
                    market_type=1,
                    parent_condition_id=PRIMARY,
                    resolved=True,
                ),
                _row(PAUSED_PARENT, question="Paused hub", paused=True),
                _row(
                    CHILD_OF_PAUSED,
                    question="Child of paused hub",
                    market_type=1,
                    parent_condition_id=PAUSED_PARENT,
                ),
                _row(
                    ORPHAN,
                    question="Yankees win the series?",
                    market_type=1,
                    parent_condition_id=MISSING_PARENT,
                ),
                _row(LONELY, question="Lonely primary with no children"),
                _row(USER_LISTED, question="Will the Artemis II crew launch in 2026?", market_type=2),
                _row(
                    CHILD_OF_USER,
                    question="Artemis II launch in September?",
                    market_type=1,
                    parent_condition_id=USER_LISTED,
                ),
                _row(USER_INDEXED, question="Will an unconfirmed listing stay hidden?", market_type=2),
                _row(USER_REJECTED, question="Who is the best QB and should he feel underrated", market_type=2),
                _row(USER_NO_LISTING, question="Will a listing with no row stay hidden?", market_type=2),
                *[
                    MarketListing(condition_id=cid, creator="0x" + "4a" * 20, status=status)
                    for cid, status in LISTING_STATUS.items()
                ],
            ]
        )
        await session.commit()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    async with SessionLocal() as session:
        await _clean(session)
        await session.commit()


def _cards_by_primary(payload: list[dict]) -> dict[str, dict]:
    return {card["primary"]["conditionId"]: card for card in payload}


@pytest.mark.asyncio
async def test_list_nests_children_under_primaries_and_keeps_orphans(client):
    listed = await client.get("/api/v1/markets")
    assert listed.status_code == 200
    payload = listed.json()
    assert all("primary" in card and "children" in card for card in payload)
    cards = _cards_by_primary(payload)

    hub = cards[PRIMARY]
    child_ids = [row["conditionId"] for row in hub["children"]]
    assert child_ids == [CHILD, RESOLVED_CHILD]
    assert PAUSED_CHILD not in child_ids
    assert hub["primary"]["marketType"] == 0

    lonely = cards[LONELY]
    assert lonely["children"] == []

    assert PAUSED_PARENT not in cards
    orphan_of_paused = cards[CHILD_OF_PAUSED]
    assert orphan_of_paused["primary"]["marketType"] == 1
    assert orphan_of_paused["children"] == []

    missing_parent_orphan = cards[ORPHAN]
    assert missing_parent_orphan["primary"]["question"] == "Yankees win the series?"
    assert missing_parent_orphan["children"] == []


@pytest.mark.asyncio
async def test_parent_id_stays_flat_for_ops(client):
    listed = await client.get("/api/v1/markets", params={"parentId": PRIMARY})
    assert listed.status_code == 200
    payload = listed.json()
    assert all("conditionId" in row and "primary" not in row for row in payload)
    assert {row["conditionId"] for row in payload} == {CHILD, RESOLVED_CHILD}


@pytest.mark.asyncio
async def test_detail_children_match_list_nest(client):
    listed = await client.get("/api/v1/markets")
    cards = _cards_by_primary(listed.json())
    detail = await client.get(f"/api/v1/markets/{PRIMARY}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["conditionId"] == PRIMARY
    list_ids = [row["conditionId"] for row in cards[PRIMARY]["children"]]
    detail_ids = [row["conditionId"] for row in body["children"]]
    assert detail_ids == list_ids == [CHILD, RESOLVED_CHILD]
    assert len(cards[PRIMARY]["children"]) == len(body["children"])


@pytest.mark.asyncio
async def test_wildcard_detail_always_includes_empty_children(client):
    detail = await client.get(f"/api/v1/markets/{CHILD}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["conditionId"] == CHILD
    assert body["children"] == []


@pytest.mark.asyncio
async def test_paused_parent_detail_uses_same_child_filter(client):
    detail = await client.get(f"/api/v1/markets/{PAUSED_PARENT}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["paused"] is True
    assert [row["conditionId"] for row in body["children"]] == [CHILD_OF_PAUSED]
    listed = await client.get("/api/v1/markets")
    cards = _cards_by_primary(listed.json())
    assert PAUSED_PARENT not in cards
    assert cards[CHILD_OF_PAUSED]["primary"]["conditionId"] == CHILD_OF_PAUSED


@pytest.mark.asyncio
async def test_user_listed_market_lists_as_primary_card(client):
    listed = await client.get("/api/v1/markets")
    cards = _cards_by_primary(listed.json())
    card = cards[USER_LISTED]
    assert card["primary"]["marketType"] == 2
    assert [row["conditionId"] for row in card["children"]] == [CHILD_OF_USER]
    assert CHILD_OF_USER not in cards


@pytest.mark.asyncio
@pytest.mark.parametrize("cid", [USER_INDEXED, USER_REJECTED, USER_NO_LISTING])
async def test_unconfirmed_user_listing_is_hidden_everywhere(client, cid):
    cards = _cards_by_primary((await client.get("/api/v1/markets")).json())
    assert cid not in cards
    assert all(cid not in [c["conditionId"] for c in card["children"]] for card in cards.values())
    flat = (await client.get("/api/v1/markets", params={"parentId": ""})).json()
    assert cid not in {row["primary"]["conditionId"] for row in flat}
    assert (await client.get(f"/api/v1/markets/{cid}")).status_code == 404
    assert (await client.get(f"/api/v1/markets/{cid}/history")).status_code == 404


@pytest.mark.asyncio
async def test_confirmed_user_listing_detail_is_public(client):
    detail = await client.get(f"/api/v1/markets/{USER_LISTED}")
    assert detail.status_code == 200
    assert detail.json()["listing"]["status"] == "confirmed"
