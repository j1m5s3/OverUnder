from sqlalchemy import inspect, text
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


async def get_db():
    async with SessionLocal() as session:
        yield session
