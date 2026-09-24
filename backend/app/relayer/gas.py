"""EIP-1559 fee and gas-limit policy with hard caps."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings

BPS = 10_000


@dataclass(frozen=True)
class FeeQuote:
    max_fee: int
    tip: int
    legacy_gas_price: int | None = None


def _ceil_bps(x: int, bps: int) -> int:
    return (x * bps + BPS - 1) // BPS


def quote_fees(base_fee: int | None, suggested_tip: int, gas_price: int | None, s: Settings) -> FeeQuote | None:
    """Fees for a fresh send, or None when the cap cannot cover the current base fee."""
    cap = s.relayer_max_fee_per_gas_wei
    if base_fee is None:
        # Pre-London chain or RPC without baseFeePerGas: legacy gasPrice.
        if gas_price is None or gas_price <= 0 or gas_price > cap:
            return None
        return FeeQuote(max_fee=gas_price, tip=gas_price, legacy_gas_price=gas_price)
    tip = max(int(suggested_tip or 0), s.relayer_priority_fee_wei)
    if base_fee + tip > cap:
        return None
    return FeeQuote(max_fee=min(2 * base_fee + tip, cap), tip=tip)


def bump_fees(q: FeeQuote, s: Settings, base_fee: int | None = None) -> FeeQuote | None:
    """Replacement fees for the same nonce: both tip and max fee rise by at least
    RELAYER_FEE_BUMP_BPS (nodes require >= 10%). None when the cap blocks the bump."""
    cap = s.relayer_max_fee_per_gas_wei
    factor = BPS + max(s.relayer_fee_bump_bps, 1000)
    if q.legacy_gas_price is not None:
        gp = _ceil_bps(q.legacy_gas_price, factor)
        if gp > cap:
            return None
        return FeeQuote(max_fee=gp, tip=gp, legacy_gas_price=gp)
    min_tip = _ceil_bps(q.tip, factor)
    min_max = _ceil_bps(q.max_fee, factor)
    max_fee = min_max
    if base_fee is not None:
        max_fee = max(max_fee, 2 * base_fee + min_tip)
    max_fee = min(max_fee, cap)
    if max_fee < min_max or min_tip > max_fee:
        return None
    return FeeQuote(max_fee=max_fee, tip=min_tip)


def gas_limit(estimate: int, s: Settings) -> int | None:
    """Estimate plus RELAYER_GAS_BUFFER_BPS, capped. None when the raw estimate is already over the cap."""
    cap = s.relayer_gas_limit_cap
    if estimate <= 0 or estimate > cap:
        return None
    return min(_ceil_bps(estimate, BPS + s.relayer_gas_buffer_bps), cap)
