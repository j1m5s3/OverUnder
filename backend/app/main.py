from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.aa.router import router as aa_router
from app.amm.router import router as amm_router
from app.auth.router import router as auth_router
from app.chain.router import router as chain_router
from app.db import engine, run_migrations
from app.emissions.router import router as emissions_router
from app.kyc.router import router as kyc_router
from app.markets.listing import router as listing_router
from app.markets.router import router as markets_router
from app.oracle.router import router as oracle_router
from app.orderbook.router import router as orderbook_router
from app.portfolio.router import router as portfolio_router
from app.ramps.router import router as ramps_router
from app.indexer.listener import release_indexer_leadership, run_indexer_loop
from app.config import get_settings
# OU-T003 relayer (track B2)
from app.relayer.router import router as relayer_router
from app.relayer.worker import maybe_start_relayer, stop_relayer_state


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Every schema step (relayer ones included) lives in run_migrations: one transaction, and on
    # Postgres an advisory xact lock serializes concurrent instances. Add no DDL here.
    async with engine.begin() as conn:
        await conn.run_sync(run_migrations)

    settings = get_settings()
    indexer_task = None
    if settings.indexer_enabled:
        indexer_task = asyncio.create_task(
            run_indexer_loop(settings.indexer_interval_seconds, settings.indexer_max_backoff_seconds)
        )
    # Off unless RELAYER_ENABLED and RELAYER_WORKER_ENABLED; None otherwise.
    relayer_task = maybe_start_relayer(settings)
    app.state.relayer_task = relayer_task
    
    try:
        yield
    finally:
        if indexer_task is not None:
            indexer_task.cancel()
            try:
                await indexer_task
            except asyncio.CancelledError:
                pass
            # Postgres: free the indexer advisory lock so another instance takes over at once.
            await release_indexer_leadership()
        if relayer_task is not None:
            relayer_task.cancel()
            try:
                await relayer_task
            except asyncio.CancelledError:
                pass
            stop_relayer_state()


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
    app.include_router(chain_router, prefix="/api/v1")
    # Listing before markets: /markets/{condition_id} would otherwise capture /markets/listing/*.
    app.include_router(listing_router, prefix="/api/v1")
    app.include_router(markets_router, prefix="/api/v1")
    app.include_router(orderbook_router, prefix="/api/v1")
    app.include_router(relayer_router, prefix="/api/v1")
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
