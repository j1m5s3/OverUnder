"""Public chain metadata: the contract addresses this API trades against.

Built from Settings only (the same fields /aa/cdp-send allowlists), never from a local
contracts/deployments file, so on Cloud Run the answer is exactly what the env configures and
clients (mobile Deployments.resolve) cannot drift from the calls the API will sponsor.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from web3 import Web3

from app.config import get_settings

router = APIRouter(prefix="/chain", tags=["chain"])


class ChainAddresses(BaseModel):
    chainId: int
    MockUSDC: str | None = None
    ConditionalTokens: str | None = None
    MarketAMM: str | None = None
    MarketFactory: str | None = None
    ConsensusOracle: str | None = None
    FeeVault: str | None = None
    Exchange: str | None = None


def _checksum(value: str | None) -> str | None:
    """EIP-55 form of a configured address; None when unset or not an address."""
    raw = (value or "").strip()
    if not raw or not Web3.is_address(raw):
        return None
    return Web3.to_checksum_address(raw)


@router.get("/addresses", response_model=ChainAddresses)
async def chain_addresses() -> ChainAddresses:
    s = get_settings()
    return ChainAddresses(
        chainId=s.chain_id,
        MockUSDC=_checksum(s.usdc_address),
        ConditionalTokens=_checksum(s.ctf_address),
        MarketAMM=_checksum(s.amm_address),
        MarketFactory=_checksum(s.factory_address),
        ConsensusOracle=_checksum(s.oracle_address),
        FeeVault=_checksum(s.fee_vault_address),
        Exchange=_checksum(s.exchange_address),
    )
