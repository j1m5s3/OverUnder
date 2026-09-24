"""Pin backend Settings for tests so the repo-root .env, .secrets/cb_keys.json and
contracts/deployments/*.json never leak into the suite, and give every test a
throwaway SQLite file instead of backend/overunder.db."""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="ou-backend-tests-"))

_PINNED = {
    "DATABASE_URL": f"sqlite+aiosqlite:///{(_TMP / 'test.db').as_posix()}",
    "CHAIN_ID": "999999",
    "ANVIL_RPC_URL": "http://127.0.0.1:1",
    "JWT_SECRET": "test-jwt-secret-0123456789abcdef0123",
    "OPERATOR_PRIVATE_KEY": "",
    "RELAYER_PRIVATE_KEY": "",
    "CDP_PROJECT_ID": "",
    "CDP_API_KEY_ID": "",
    "CDP_API_KEY_SECRET": "",
    "COINBASE_ONRAMP_APP_ID": "",
    "MOONPAY_API_KEY": "",
    "MOONPAY_SECRET": "",
    "KYC_THRESHOLD_USDC": "500",
    "KYC_RESTRICTED_JURISDICTIONS": "",
    "AUTH_ANVIL_BYPASS": "false",
    "BUNDLER_URL": "",
}
for _name in (
    "USDC_ADDRESS", "CTF_ADDRESS", "FACTORY_ADDRESS", "EXCHANGE_ADDRESS", "AMM_ADDRESS",
    "ORACLE_ADDRESS", "FEE_VAULT_ADDRESS", "OU_TOKEN_ADDRESS", "ENTRYPOINT_ADDRESS",
    "PAYMASTER_ADDRESS", "ACCOUNT_FACTORY_ADDRESS", "EMISSIONS_DISTRIBUTOR_ADDRESS",
):
    _PINNED[_name] = ""
os.environ.update(_PINNED)

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.db import Base, engine, ensure_live_score_facts, ensure_user_cdp_user_id  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(ensure_live_score_facts)
        await conn.run_sync(ensure_user_cdp_user_id)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
