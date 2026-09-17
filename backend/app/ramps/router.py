from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(prefix="/ramps", tags=["ramps"])
settings = get_settings()


@router.get("/onramp-url")
async def onramp_url(address: str, usdc_amount: str = "100"):
    app_id = settings.coinbase_onramp_app_id or "demo"
    url = (
        "https://pay.coinbase.com/buy/select-asset"
        f"?appId={app_id}"
        f"&addresses={{\" {address} \":[\"base\"]}}"
        "&assets=[\"USDC\"]"
        f"&presetFiatAmount={usdc_amount}"
    )
    # Compact Coinbase Onramp session-style URL used by Polymarket-class flows.
    safe = (
        f"https://pay.coinbase.com/buy?"
        f"appId={app_id}&destinationWallets="
        f"[{{%22address%22:%22{address}%22,%22blockchains%22:[%22base%22],%22assets%22:[%22USDC%22]}}]"
    )
    return {"url": safe, "provider": "coinbase", "asset": "USDC", "chain": "base"}


@router.get("/offramp-url")
async def offramp_url(address: str):
    app_id = settings.coinbase_onramp_app_id or "demo"
    url = f"https://pay.coinbase.com/offramp?appId={app_id}&address={address}&asset=USDC"
    return {"url": url, "provider": "coinbase", "asset": "USDC", "chain": "base"}
