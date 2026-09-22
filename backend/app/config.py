from functools import lru_cache
import json
from pathlib import Path
from typing import Any

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
