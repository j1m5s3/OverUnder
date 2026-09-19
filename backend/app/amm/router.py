from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(prefix="/amm", tags=["amm"])
settings = get_settings()


@router.get("/{market_id}/quote")
async def quote(market_id: str, buy_yes: bool = True, usdc_in: int = 1_000_000, sell_yes: bool | None = None, token_amount: int | None = None):
    try:
        from web3 import Web3
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        deploy = json.loads((root / "contracts" / "deployments" / f"{settings.chain_id}.json").read_text())
        abi = json.loads((root / "backend" / "app" / "abi" / "MarketAMM.json").read_text())
        w3 = Web3(Web3.HTTPProvider(settings.anvil_rpc_url))
        c = w3.eth.contract(address=Web3.to_checksum_address(deploy["MarketAMM"]), abi=abi)
        cid = bytes.fromhex(market_id[2:] if market_id.startswith("0x") else market_id)
        
        if sell_yes is not None and token_amount is not None:
            usdc_out = c.functions.quoteSell(cid, sell_yes, token_amount).call()
            return {"conditionId": market_id, "sellYes": sell_yes, "tokenAmount": token_amount, "usdcOut": usdc_out}
        else:
            out = c.functions.quoteBuy(cid, buy_yes, usdc_in).call()
            return {"conditionId": market_id, "buyYes": buy_yes, "usdcIn": usdc_in, "tokensOut": out}
    except Exception:
        if sell_yes is not None and token_amount is not None:
            fee = token_amount * settings.fee_bps_amm // 10_000
            return {
                "conditionId": market_id,
                "sellYes": sell_yes,
                "tokenAmount": token_amount,
                "usdcOut": token_amount - fee,
                "simulated": True,
            }
        else:
            fee = usdc_in * settings.fee_bps_amm // 10_000
            return {
                "conditionId": market_id,
                "buyYes": buy_yes,
                "usdcIn": usdc_in,
                "tokensOut": usdc_in - fee,
                "simulated": True,
            }
