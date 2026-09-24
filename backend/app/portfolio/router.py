import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models import Market, Order, Trade

router = APIRouter(tags=["portfolio"])
settings = get_settings()
logger = logging.getLogger(__name__)


class _RpcNotConnected(Exception):
    pass


def _web3(rpc_url: str, timeout: float):
    """Web3 with a bounded request timeout and no web3 retries (see app/amm/router.py):
    web3's defaults would hold a worker thread for minutes on a stalled RPC."""
    from web3 import Web3

    return Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout}, exception_retry_configuration=None))


def _ctf_positions(
    rpc_url: str, timeout: float, ctf_address: str, abi, owner: str, markets: list[tuple[str, str]]
) -> list[dict]:
    """Non-zero YES/NO CTF balances of `owner` for (conditionId, question) pairs. Blocking: run in a thread."""
    from web3 import Web3

    w3 = _web3(rpc_url, timeout)
    if not w3.is_connected():
        raise _RpcNotConnected()
    ctf = w3.eth.contract(address=Web3.to_checksum_address(ctf_address), abi=abi)
    holder = Web3.to_checksum_address(owner)
    positions = []
    for condition_id, question in markets:
        condition_id_bytes = bytes.fromhex(condition_id[2:])
        # Check YES (outcome 0) and NO (outcome 1) positions
        for outcome in [0, 1]:
            position_id = ctf.functions.positionId(condition_id_bytes, outcome).call()
            balance = ctf.functions.balanceOf(holder, position_id).call()
            # Only include non-zero positions
            if balance > 0:
                positions.append({
                    "question": question,
                    "side": "YES" if outcome == 0 else "NO",
                    "sizeMicros": balance,
                    "conditionId": condition_id,
                    "outcome": outcome,
                })
    return positions


def _vault_nav(rpc_url: str, timeout: float, vault_address: str, abi) -> int:
    from web3 import Web3

    vault = _web3(rpc_url, timeout).eth.contract(address=Web3.to_checksum_address(vault_address), abi=abi)
    return vault.functions.nav().call()


@router.get("/portfolio/{address}")
async def portfolio(address: str, db: AsyncSession = Depends(get_db)):
    addr = address.lower()
    orders = (await db.execute(select(Order).where(Order.maker == addr))).scalars().all()
    trades = (
        await db.execute(select(Trade).where((Trade.taker == addr) | (Trade.maker == addr)))
    ).scalars().all()

    # Query CTF balances for positions - fail request if we can't read holdings
    from app.contract_addresses import get_contract_addresses, load_abi

    try:
        addresses = get_contract_addresses()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Cannot load contract addresses: {e}")

    if "ConditionalTokens" not in addresses:
        raise HTTPException(status_code=503, detail="ConditionalTokens address not configured")

    try:
        ctf_abi = load_abi("ConditionalTokens")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Cannot load CTF ABI: {e}")

    # Get all markets to check their positions
    markets = (await db.execute(select(Market))).scalars().all()
    pairs = [(m.condition_id, m.question) for m in markets]

    try:
        # eth_calls run in a worker thread: a slow RPC must never stall the event loop.
        positions = await asyncio.to_thread(
            _ctf_positions,
            settings.anvil_rpc_url,
            settings.portfolio_rpc_timeout_seconds,
            addresses["ConditionalTokens"],
            ctf_abi,
            addr,
            pairs,
        )
    except _RpcNotConnected:
        raise HTTPException(status_code=503, detail="RPC not connected")
    except Exception as exc:
        # Never echo transport errors: RPC URLs can embed provider keys.
        logger.warning("portfolio CTF RPC failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Failed to query CTF balances")

    return {
        "address": addr,
        "positions": positions,
        "openOrders": [
            {
                "orderHash": o.order_hash,
                "conditionId": o.condition_id,
                "isBuy": o.is_buy,
                "price": o.price,
                "remaining": o.amount - o.filled,
            }
            for o in orders
            if not o.cancelled and o.filled < o.amount
        ],
        "trades": [
            {
                "id": t.id,
                "conditionId": t.condition_id,
                "fillAmount": t.fill_amount,
                "volume": t.volume,
                "fee": t.fee,
                "txHash": t.tx_hash,
            }
            for t in trades
        ],
    }


@router.get("/fee-vault/nav")
async def nav():
    simulated = {"nav": 0, "chainId": settings.chain_id, "simulated": True}
    try:
        from app.contract_addresses import get_contract_addresses, load_abi

        addresses = get_contract_addresses()
        if "FeeVault" not in addresses:
            return simulated
        abi = load_abi("FeeVault")
        # Worker thread + bounded timeout, as in portfolio(); a failed read keeps the documented
        # simulated fallback rather than a 503.
        value = await asyncio.to_thread(
            _vault_nav, settings.anvil_rpc_url, settings.portfolio_rpc_timeout_seconds, addresses["FeeVault"], abi
        )
    except Exception as exc:
        logger.warning("fee-vault NAV read failed: %s", type(exc).__name__)
        return simulated
    return {"nav": value, "chainId": settings.chain_id}
