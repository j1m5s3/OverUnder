from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models import Order, Trade

router = APIRouter(tags=["portfolio"])
settings = get_settings()


@router.get("/portfolio/{address}")
async def portfolio(address: str, db: AsyncSession = Depends(get_db)):
    addr = address.lower()
    orders = (await db.execute(select(Order).where(Order.maker == addr))).scalars().all()
    trades = (
        await db.execute(select(Trade).where((Trade.taker == addr) | (Trade.maker == addr)))
    ).scalars().all()
    return {
        "address": addr,
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
    try:
        from pathlib import Path
        import json
        from web3 import Web3

        root = Path(__file__).resolve().parents[3]
        deploy = json.loads((root / "contracts" / "deployments" / f"{settings.chain_id}.json").read_text())
        abi = json.loads((root / "backend" / "app" / "abi" / "FeeVault.json").read_text())
        w3 = Web3(Web3.HTTPProvider(settings.anvil_rpc_url))
        c = w3.eth.contract(address=Web3.to_checksum_address(deploy["FeeVault"]), abi=abi)
        return {"nav": c.functions.nav().call(), "chainId": settings.chain_id}
    except Exception:
        return {"nav": 0, "chainId": settings.chain_id, "simulated": True}
