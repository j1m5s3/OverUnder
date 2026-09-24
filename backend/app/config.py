from functools import lru_cache
import json
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


def _cb_file_values() -> dict[str, str]:
    path = ROOT / ".secrets" / "cb_keys.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    project_id = raw.get("PROJECT_ID")
    key_id = raw.get("API_KEY_ID")
    secret = raw.get("API_SECRET")
    if isinstance(project_id, str) and project_id:
        out["cdp_project_id"] = project_id
    if isinstance(key_id, str) and key_id:
        out["cdp_api_key_id"] = key_id
    if isinstance(secret, str) and secret:
        out["cdp_api_key_secret"] = secret
    return out


class CbKeysFileSource(PydanticBaseSettingsSource):
    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        data = _cb_file_values()
        if field_name in data:
            return data[field_name], field_name, False
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return _cb_file_values()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    anvil_rpc_url: str = "http://127.0.0.1:8545"
    chain_id: int = 31337
    database_url: str = "sqlite+aiosqlite:///./overunder.db"
    jwt_secret: str = "dev-jwt-secret-change-me-please-32b"
    cdp_project_id: str = ""
    cdp_api_key_id: str = ""
    cdp_api_key_secret: str = ""
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

    # Trading halt (API side of the closeTime gate; the chain gate lives in MarketAMM v2).
    trading_halt_at_close: bool = True

    # Public AMM quote: short RPC timeout, no web3 retries (clients re-quote on every change).
    amm_quote_rpc_timeout_seconds: float = 5.0
    # Public portfolio positions and fee-vault NAV: bounded RPC timeout, no web3 retries.
    portfolio_rpc_timeout_seconds: float = 10.0
    # Operator transactions (POST /markets, pause): bounded RPC calls and receipt waits.
    operator_rpc_timeout_seconds: float = 15.0
    operator_tx_timeout_seconds: float = 60.0

    # Chain indexer (backend/app/indexer/listener.py).
    indexer_enabled: bool = True
    indexer_interval_seconds: float = 5.0
    indexer_start_block: int = 0
    indexer_lookback_blocks: int = 43_200
    indexer_max_block_range: int = 2_000
    indexer_min_block_range: int = 10
    indexer_max_chunks_per_tick: int = 10
    indexer_max_backoff_seconds: float = 300.0
    indexer_rpc_timeout_seconds: float = 10.0
    # Postgres session advisory lock so one API instance indexes at a time ("OUIN").
    indexer_leader_lock_key: int = 0x4F55494E

    # --- OU-T003 CLOB relayer (backend/app/relayer, track B2) ---------------
    # Off by default. Enablement is never inferred from RELAYER_PRIVATE_KEY.
    relayer_enabled: bool = False
    relayer_worker_enabled: bool = False
    relayer_poll_seconds: float = 2.0
    relayer_batch_size: int = 10
    relayer_max_attempts: int = 5
    relayer_retry_backoff_seconds: int = 15
    relayer_resubmit_after_seconds: int = 60
    relayer_fee_bump_bps: int = 1250
    relayer_max_fee_per_gas_wei: int = 5_000_000_000
    relayer_priority_fee_wei: int = 1_000_000
    relayer_gas_buffer_bps: int = 2000
    relayer_gas_limit_cap: int = 600_000
    relayer_confirmations: int = 1
    relayer_min_expiry_seconds: int = 60
    relayer_preflight_on_post: bool = True
    relayer_leader_lock_key: int = 0x4F55524C
    relayer_rpc_timeout_seconds: float = 15.0
    # OrderFilled log search window when a job has no first_sent_block on record.
    relayer_log_lookback_blocks: int = 5_000
    # POST /orders: open (unfilled, uncancelled, unexpired) orders one maker may hold.
    relayer_max_open_orders_per_maker: int = 100
    # --- end relayer ---------------------------------------------------------

    @field_validator("indexer_start_block", mode="before")
    @classmethod
    def _blank_start_block(cls, value: Any) -> Any:
        # Cloud Run passes INDEXER_START_BLOCK= when the repo var is unset.
        if isinstance(value, str) and not value.strip():
            return 0
        return value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            CbKeysFileSource(settings_cls),
            file_secret_settings,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
