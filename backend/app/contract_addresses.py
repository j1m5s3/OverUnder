"""Contract address and ABI resolution for both local dev and Cloud Run."""

import json
from pathlib import Path
from typing import Any

from app.config import get_settings


def get_contract_addresses(chain_id: int | None = None) -> dict[str, str]:
    """
    Resolve contract addresses for the given chain.
    
    - If contracts/deployments/{chain_id}.json exists (local dev with anvil),
      load addresses from that file.
    - Otherwise (Cloud Run), build addresses dict from Settings env vars.
    
    Returns dict: {"MarketFactory": "0x...", "MockUSDC": "0x...", ...}
    """
    settings = get_settings()
    if chain_id is None:
        chain_id = settings.chain_id
    
    # Try local deployment JSON first (for local anvil dev)
    root = Path(__file__).resolve().parents[2]
    deploy_path = root / "contracts" / "deployments" / f"{chain_id}.json"
    
    if deploy_path.exists():
        return json.loads(deploy_path.read_text())
    
    # Fall back to env-configured addresses (Cloud Run)
    addresses = {}
    if settings.factory_address:
        addresses["MarketFactory"] = settings.factory_address
    if settings.usdc_address:
        addresses["MockUSDC"] = settings.usdc_address
    if settings.ctf_address:
        addresses["ConditionalTokens"] = settings.ctf_address
    if settings.amm_address:
        addresses["MarketAMM"] = settings.amm_address
    if settings.exchange_address:
        addresses["Exchange"] = settings.exchange_address
    if settings.oracle_address:
        addresses["ConsensusOracle"] = settings.oracle_address
    if settings.fee_vault_address:
        addresses["FeeVault"] = settings.fee_vault_address
    if settings.ou_token_address:
        addresses["RevenueToken"] = settings.ou_token_address
    
    return addresses


def get_abi_path() -> Path:
    """
    Resolve the path to the ABI directory.
    
    - In local dev: backend/app/abi
    - In Docker container: /app/app/abi
    
    Returns the first path that exists.
    """
    candidates = [
        Path(__file__).resolve().parent / "abi",  # /app/app/abi in container
        Path(__file__).resolve().parents[2] / "backend" / "app" / "abi",  # local dev
    ]
    
    for candidate in candidates:
        if candidate.exists():
            return candidate
    
    # Default to relative path if none found
    return Path(__file__).resolve().parent / "abi"


def load_abi(contract_name: str) -> list[dict[str, Any]]:
    """Load the ABI for the given contract name (e.g. 'MarketFactory')."""
    abi_dir = get_abi_path()
    abi_file = abi_dir / f"{contract_name}.json"
    return json.loads(abi_file.read_text())
