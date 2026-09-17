"""Off-chain crossing engine. Relayer submission is best-effort when RPC is up."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Order, Trade

settings = get_settings()
ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "contracts" / "deployments" / f"{settings.chain_id}.json"


def _remaining(o: Order) -> int:
    return o.amount - o.filled


def _crosses(taker: Order, maker: Order) -> bool:
    if taker.condition_id != maker.condition_id:
        return False
    if taker.outcome != maker.outcome:
        return False
    if taker.is_buy == maker.is_buy:
        return False
    if taker.cancelled or maker.cancelled:
        return False
    if taker.is_buy:
        return taker.price >= maker.price
    return maker.price >= taker.price


async def try_match(db: AsyncSession, incoming: Order) -> list[dict]:
    others = (
        await db.execute(
            select(Order).where(
                Order.condition_id == incoming.condition_id,
                Order.cancelled.is_(False),
                Order.id != incoming.id,
            )
        )
    ).scalars().all()
    makers = [o for o in others if _crosses(incoming, o) and _remaining(o) > 0]
    if incoming.is_buy:
        makers.sort(key=lambda o: o.price)
    else:
        makers.sort(key=lambda o: -o.price)

    fills = []
    for maker in makers:
        qty = min(_remaining(incoming), _remaining(maker))
        if qty <= 0:
            continue
        volume = qty * maker.price // 1_000_000
        fee = volume * settings.fee_bps_taker // 10_000
        incoming.filled += qty
        maker.filled += qty
        tx_hash = _submit_match(incoming, maker, qty)
        trade = Trade(
            condition_id=incoming.condition_id,
            taker=incoming.maker,
            maker=maker.maker,
            fill_amount=qty,
            volume=volume,
            fee=fee,
            tx_hash=tx_hash,
        )
        db.add(trade)
        fills.append(
            {
                "makerHash": maker.order_hash,
                "takerHash": incoming.order_hash,
                "fillAmount": qty,
                "volume": volume,
                "fee": fee,
                "txHash": tx_hash,
            }
        )
        if _remaining(incoming) == 0:
            break
    return fills


def _submit_match(taker: Order, maker: Order, qty: int) -> str:
    """Push matchOrders when a relayer key and RPC are configured; otherwise record off-chain."""
    if not settings.relayer_private_key or not DEPLOY.exists():
        return ""
    try:
        from web3 import Web3
        from eth_account import Account

        w3 = Web3(Web3.HTTPProvider(settings.anvil_rpc_url))
        if not w3.is_connected():
            return ""
        abi = json.loads((ROOT / "backend" / "app" / "abi" / "Exchange.json").read_text())
        addr = json.loads(DEPLOY.read_text())["Exchange"]
        acct = Account.from_key(settings.relayer_private_key)
        contract = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=abi)

        def tup(o: Order):
            return (
                Web3.to_checksum_address(o.maker),
                o.is_buy,
                bytes.fromhex(o.condition_id[2:] if o.condition_id.startswith("0x") else o.condition_id),
                o.outcome,
                o.price,
                o.amount,
                o.salt,
                o.nonce,
                o.expiry,
            )

        tx = contract.functions.matchOrders(
            tup(taker),
            tup(maker),
            qty,
            bytes.fromhex(taker.signature[2:] if taker.signature.startswith("0x") else taker.signature),
            bytes.fromhex(maker.signature[2:] if maker.signature.startswith("0x") else maker.signature),
        ).build_transaction(
            {
                "from": acct.address,
                "nonce": w3.eth.get_transaction_count(acct.address),
                "chainId": settings.chain_id,
                "gas": 800_000,
                "gasPrice": w3.eth.gas_price,
            }
        )
        signed = acct.sign_transaction(tx)
        txh = w3.eth.send_raw_transaction(signed.raw_transaction)
        return txh.hex()
    except Exception:
        return ""
