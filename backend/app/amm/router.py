import asyncio
import logging
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.markets.chain import hex32
from app.markets.trading import trading_halt_reason

router = APIRouter(prefix="/amm", tags=["amm"])
logger = logging.getLogger(__name__)

UINT256_BOUND = 2**256


def revert_reason(exc: Exception) -> str:
    """Human revert string from a web3 ContractLogicError ("price bound", "no pool", "dust", ...)."""
    message = getattr(exc, "message", None) or (exc.args[0] if exc.args else "")
    text = str(message or "").strip()
    for prefix in ("execution reverted:", "execution reverted"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return text or "execution reverted"


@lru_cache(maxsize=4)
def _amm_contract(rpc_url: str, address: str, timeout: float):
    """MarketAMM contract on a cached Web3 with a short timeout and no web3 retries.

    web3's defaults (30 s timeout, several eth_call attempts with blocking
    backoff) would hold a worker thread for minutes on a stalled RPC; clients
    re-quote on the next input change anyway, so fail fast.
    """
    from web3 import Web3

    from app.contract_addresses import load_abi

    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout}, exception_retry_configuration=None))
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=load_abi("MarketAMM"))


@router.get("/{market_id}/quote")
async def quote(
    market_id: str,
    buy_yes: bool = True,
    usdc_in: int = Query(default=1_000_000, ge=0, lt=UINT256_BOUND),
    sell_yes: bool | None = None,
    token_amount: int | None = Query(default=None, ge=0, lt=UINT256_BOUND),
    db: AsyncSession = Depends(get_db),
):
    from web3.exceptions import ContractLogicError, MismatchedABI, Web3ValidationError

    from app.contract_addresses import get_contract_addresses

    settings = get_settings()
    # The halt is checked before any RPC so closed/resolved markets never cost a round trip.
    reason = await trading_halt_reason(db, market_id, settings)
    if reason:
        raise HTTPException(409, reason)

    cid = hex32(market_id, "market_id")
    addresses = get_contract_addresses()
    if "MarketAMM" not in addresses:
        raise HTTPException(503, "MarketAMM address not configured")

    try:
        c = _amm_contract(settings.anvil_rpc_url, addresses["MarketAMM"], settings.amm_quote_rpc_timeout_seconds)
        # eth_call runs in a worker thread: a slow RPC must never stall the event loop.
        if sell_yes is not None and token_amount is not None:
            usdc_out = await asyncio.to_thread(c.functions.quoteSell(cid, sell_yes, token_amount).call)
            return {"conditionId": market_id, "sellYes": sell_yes, "tokenAmount": token_amount, "usdcOut": usdc_out}
        out = await asyncio.to_thread(c.functions.quoteBuy(cid, buy_yes, usdc_in).call)
        return {"conditionId": market_id, "buyYes": buy_yes, "usdcIn": usdc_in, "tokensOut": out}
    except ContractLogicError as exc:
        raise HTTPException(422, revert_reason(exc))
    except (MismatchedABI, Web3ValidationError):
        # Argument encoding failed before any RPC: a client error, not an outage.
        raise HTTPException(400, "invalid quote arguments")
    except Exception as exc:
        # Never echo transport errors: RPC URLs can embed provider keys.
        logger.warning("AMM quote RPC failed: %s", type(exc).__name__)
        raise HTTPException(503, "RPC unavailable")
