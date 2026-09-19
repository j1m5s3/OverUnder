from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.aa.router import router as aa_router
from app.amm.router import router as amm_router
from app.auth.router import router as auth_router
from app.db import Base, engine
from app.emissions.router import router as emissions_router
from app.kyc.router import router as kyc_router
from app.markets.router import router as markets_router
from app.oracle.router import router as oracle_router
from app.orderbook.router import router as orderbook_router
from app.portfolio.router import router as portfolio_router
from app.ramps.router import router as ramps_router
from app.indexer.listener import run_indexer_loop
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    settings = get_settings()
    interval = getattr(settings, 'indexer_interval_seconds', 5.0)
    indexer_task = asyncio.create_task(run_indexer_loop(interval))
    
    try:
        yield
    finally:
        indexer_task.cancel()
        try:
            await indexer_task
        except asyncio.CancelledError:
            pass


def create_app() -> FastAPI:
    app = FastAPI(title="OverUnder API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(markets_router, prefix="/api/v1")
    app.include_router(orderbook_router, prefix="/api/v1")
    app.include_router(amm_router, prefix="/api/v1")
    app.include_router(ramps_router, prefix="/api/v1")
    app.include_router(kyc_router, prefix="/api/v1")
    app.include_router(emissions_router, prefix="/api/v1")
    app.include_router(oracle_router, prefix="/api/v1")
    app.include_router(portfolio_router, prefix="/api/v1")
    app.include_router(aa_router, prefix="/api/v1")

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/openapi-export")
    async def openapi_export():
        return app.openapi()

    return app


app = create_app()
