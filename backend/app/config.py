from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    anvil_rpc_url: str = "http://127.0.0.1:8545"
    chain_id: int = 31337
    database_url: str = "sqlite+aiosqlite:///./overunder.db"
    jwt_secret: str = "dev-jwt-secret-change-me-please-32b"
    privy_app_id: str = ""
    privy_app_secret: str = ""
    coinbase_onramp_app_id: str = ""
    moonpay_api_key: str = ""
    moonpay_secret: str = ""
    kyc_threshold_usdc: float = 500.0
    kyc_restricted_jurisdictions: str = ""
    operator_private_key: str = ""
    relayer_private_key: str = ""
    usdc_address: str = ""
    ctf_address: str = ""
    factory_address: str = ""
    exchange_address: str = ""
    amm_address: str = ""
    oracle_address: str = ""
    fee_vault_address: str = ""
    ou_token_address: str = ""
    entrypoint_address: str = ""
    paymaster_address: str = ""
    account_factory_address: str = ""
    bundler_url: str = ""
    emissions_distributor_address: str = ""
    fee_bps_taker: int = 75
    fee_bps_amm: int = 100
    consensus_window_seconds: int = 86400
    wildcard_seed_usdc: int = 200
    jwt_ttl_seconds: int = 60 * 60 * 24 * 7
    auth_anvil_bypass: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
