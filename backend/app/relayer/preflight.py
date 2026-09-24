"""On-chain checks that mirror Exchange.matchOrders before we spend gas.

Every function returns a list of stable reason strings (empty = OK). RPC
errors propagate so callers can fail closed (503 at POST, retry in the worker).

Chain state only shows mined txs. `Committed` carries what in-flight relay jobs
(signed, not yet reconciled) will still spend, so jobs that share a funding
address or an order cannot all pass one after another and all but one revert
at the relayer's expense. A check that passes on chain but fails once the
committed amounts are counted returns a "busy: ..." reason: the worker defers
that job (nobody is at fault yet) instead of failing it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import Settings
from app.orderbook.eip712 import OrderFields
from app.relayer.chain import ChainClient

PRICE_SCALE = 1_000_000
BPS = 10_000
# Exchange.vy TAKER_FEE_BPS (a constant on chain). Funds checks use this, not
# settings.fee_bps_taker, because the chain charges it regardless of config.
EXCHANGE_TAKER_FEE_BPS = 75


def match_amounts(fill: int, maker_price: int, fee_bps: int) -> tuple[int, int]:
    """(volume, fee) exactly as Exchange.matchOrders computes them."""
    volume = fill * maker_price // PRICE_SCALE
    return volume, volume * fee_bps // BPS


def order_usdc_need(remaining: int, price: int, fee_bps: int) -> int:
    """Upper bound a buy order can spend: volume at its limit price plus the taker fee, rounded up."""
    volume = (remaining * price + PRICE_SCALE - 1) // PRICE_SCALE
    return volume + (volume * fee_bps + BPS - 1) // BPS


@dataclass
class Committed:
    """Amounts in-flight jobs will still take: USDC per buyer, CTF per (seller, cid, outcome), fill per order."""

    usdc: dict[str, int] = field(default_factory=dict)
    ctf: dict[tuple[str, str, int], int] = field(default_factory=dict)
    filled: dict[str, int] = field(default_factory=dict)

    def add_match(self, taker: OrderFields, maker: OrderFields, taker_hash: str, maker_hash: str, fill: int) -> None:
        volume, fee = match_amounts(fill, maker.price, EXCHANGE_TAKER_FEE_BPS)
        buyer, seller = (taker, maker) if taker.is_buy else (maker, taker)
        spend = volume + fee if taker.is_buy else volume
        b = buyer.maker.lower()
        self.usdc[b] = self.usdc.get(b, 0) + spend
        k = (seller.maker.lower(), "0x" + seller.condition_id.hex(), int(seller.outcome))
        self.ctf[k] = self.ctf.get(k, 0) + fill
        for h in (taker_hash.lower(), maker_hash.lower()):
            self.filled[h] = self.filled.get(h, 0) + fill

    def usdc_of(self, addr: str) -> int:
        return self.usdc.get(addr.lower(), 0)

    def ctf_of(self, o: OrderFields) -> int:
        return self.ctf.get((o.maker.lower(), "0x" + o.condition_id.hex(), int(o.outcome)), 0)

    def filled_of(self, h: bytes) -> int:
        return self.filled.get("0x" + h.hex(), 0)


def _short(have: int, need: int, busy: int, hard: str, soft: str, reasons: list[str]) -> None:
    if have < need:
        reasons.append(hard)
    elif busy and have < need + busy:
        reasons.append("busy: " + soft)


async def preflight_order(
    chain: ChainClient, o: OrderFields, exchange: str, remaining: int | None = None,
    fee_bps: int = EXCHANGE_TAKER_FEE_BPS, reserved: int = 0,
) -> list[str]:
    """POST-time check. `reserved` is what the maker's other open orders already claim from the same
    funds (USDC for a buy, the (cid, outcome) CTF balance for a sell), so one balance cannot back many orders."""
    rem = o.amount if remaining is None else remaining
    reasons: list[str] = []
    if await chain.ex_nonce(o.maker) != o.nonce:
        reasons.append("nonce stale")
    if o.is_buy:
        need = order_usdc_need(rem, o.price, fee_bps) + max(0, reserved)
        if await chain.usdc_balance(o.maker) < need:
            reasons.append(f"usdc balance < {need}")
        if await chain.usdc_allowance(o.maker, exchange) < need:
            reasons.append(f"usdc allowance < {need}")
    else:
        need = rem + max(0, reserved)
        if await chain.ctf_balance(o.maker, o.condition_id, o.outcome) < need:
            reasons.append(f"ctf balance < {need}")
        if not await chain.ctf_approved(o.maker, exchange):
            reasons.append("ctf not approved")
    return reasons


async def _order_state(
    chain: ChainClient, role: str, o: OrderFields, h: bytes, fill: int, block_ts: int, s: Settings,
    committed: Committed | None = None,
) -> list[str]:
    reasons: list[str] = []
    if o.expiry <= block_ts + s.relayer_min_expiry_seconds:
        reasons.append(f"{role} expired")
    if await chain.ex_nonce(o.maker) != o.nonce:
        reasons.append(f"{role} nonce stale")
    if await chain.ex_cancelled(h):
        reasons.append(f"{role} cancelled onchain")
    onchain = await chain.ex_filled(h)
    if onchain + fill > o.amount:
        reasons.append(f"{role} overfill")
    elif committed is not None and onchain + committed.filled_of(h) + fill > o.amount:
        reasons.append(f"busy: {role} fill in flight")
    return reasons


async def preflight_match(
    chain: ChainClient,
    taker: OrderFields,
    maker: OrderFields,
    taker_hash: bytes,
    maker_hash: bytes,
    fill: int,
    exchange: str,
    block_ts: int,
    s: Settings,
    committed: Committed | None = None,
) -> list[str]:
    c = committed or Committed()
    reasons: list[str] = []
    if await chain.ctf_resolved(taker.condition_id):
        reasons.append("market resolved")
    reasons += await _order_state(chain, "taker", taker, taker_hash, fill, block_ts, s, committed)
    reasons += await _order_state(chain, "maker", maker, maker_hash, fill, block_ts, s, committed)

    volume, fee = match_amounts(fill, maker.price, EXCHANGE_TAKER_FEE_BPS)
    if taker.is_buy:
        # Taker pays volume + fee in USDC; maker delivers `fill` outcome tokens.
        need = volume + fee
        busy = c.usdc_of(taker.maker)
        _short(await chain.usdc_balance(taker.maker), need, busy,
               f"taker usdc balance < {need}", "taker usdc in flight", reasons)
        _short(await chain.usdc_allowance(taker.maker, exchange), need, busy,
               f"taker usdc allowance < {need}", "taker usdc allowance in flight", reasons)
        _short(await chain.ctf_balance(maker.maker, maker.condition_id, maker.outcome), fill, c.ctf_of(maker),
               f"maker ctf balance < {fill}", "maker ctf in flight", reasons)
        if not await chain.ctf_approved(maker.maker, exchange):
            reasons.append("maker ctf not approved")
    else:
        # Maker (buyer) pays volume in total (volume - fee to taker, fee to vault).
        busy = c.usdc_of(maker.maker)
        _short(await chain.usdc_balance(maker.maker), volume, busy,
               f"maker usdc balance < {volume}", "maker usdc in flight", reasons)
        _short(await chain.usdc_allowance(maker.maker, exchange), volume, busy,
               f"maker usdc allowance < {volume}", "maker usdc allowance in flight", reasons)
        _short(await chain.ctf_balance(taker.maker, taker.condition_id, taker.outcome), fill, c.ctf_of(taker),
               f"taker ctf balance < {fill}", "taker ctf in flight", reasons)
        if not await chain.ctf_approved(taker.maker, exchange):
            reasons.append("taker ctf not approved")
    return reasons
