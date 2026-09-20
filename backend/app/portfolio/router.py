from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models import Market, Order, Trade

router = APIRouter(tags=["portfolio"])
settings = get_settings()


@router.get("/portfolio/{address}")
async def portfolio(address: str, db: AsyncSession = Depends(get_db)):
    addr = address.lower()
    orders = (await db.execute(select(Order).where(Order.maker == addr))).scalars().all()
    trades = (
        await db.execute(select(Trade).where((Trade.taker == addr) | (Trade.maker == addr)))
    ).scalars().all()
    
    # Query CTF balances for positions - fail request if we can't read holdings
    from web3 import Web3
    from fastapi import HTTPException
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
    
    w3 = Web3(Web3.HTTPProvider(settings.anvil_rpc_url))
    if not w3.is_connected():
        raise HTTPException(status_code=503, detail="RPC not connected")
    
    ctf = w3.eth.contract(
        address=Web3.to_checksum_address(addresses["ConditionalTokens"]),
        abi=ctf_abi
    )
    
    # Get all markets to check their positions
    markets = (await db.execute(select(Market))).scalars().all()
    
    positions = []
    try:
        for market in markets:
            condition_id_bytes = bytes.fromhex(market.condition_id[2:])
            
            # Check YES (outcome 0) and NO (outcome 1) positions
            for outcome in [0, 1]:
                position_id = ctf.functions.positionId(condition_id_bytes, outcome).call()
                balance = ctf.functions.balanceOf(
                    Web3.to_checksum_address(addr),
                    position_id
                ).call()
                
                # Only include non-zero positions
                if balance > 0:
                    positions.append({
                        "question": market.question,
                        "side": "YES" if outcome == 0 else "NO",
                        "sizeMicros": balance,
                        "conditionId": market.condition_id,
                        "outcome": outcome,
                    })
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to query CTF balances: {e}")
    
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
    try:
        from web3 import Web3
        from app.contract_addresses import get_contract_addresses, load_abi

        addresses = get_contract_addresses()
        if "FeeVault" not in addresses:
            return {"nav": 0, "chainId": settings.chain_id, "simulated": True}
        
        abi = load_abi("FeeVault")
        w3 = Web3(Web3.HTTPProvider(settings.anvil_rpc_url))
        c = w3.eth.contract(address=Web3.to_checksum_address(addresses["FeeVault"]), abi=abi)
        return {"nav": c.functions.nav().call(), "chainId": settings.chain_id}
    except Exception:
        return {"nav": 0, "chainId": settings.chain_id, "simulated": True}
