// Pure trading-window rules shared by AmmSwap and the market cards. No runtime
// imports so node tests load it directly.

// Build-time fallback for demo data or an API that predates tradingHaltsAt.
export const HALT_AT_CLOSE_FALLBACK = process.env.NEXT_PUBLIC_TRADING_HALT_AT_CLOSE === "true";

// "unlisted": a user-listed market whose listing is not confirmed (quote 409
// "listing not confirmed"); the API never quotes or sponsors trades on it.
export type ClosedReason = "resolved" | "closed" | "unlisted";

export type TradingState = {
  closed: boolean;
  reason: ClosedReason | null;
  haltsAt: number | null;
};

export type TradingWindowInput = {
  closeTime?: number | null;
  resolved?: boolean | null;
  tradingHaltsAt?: number | null;
  tradingOpen?: boolean | null;
};

// setTimeout clamps delays above 2^31-1 ms to 1 ms, so longer waits are skipped.
const MAX_TIMER_MS = 2 ** 31 - 1;

// Server tradingHaltsAt wins whenever the field is present (null means "no halt");
// the env fallback only applies when the API did not send it.
export function haltsAtFor(m: TradingWindowInput, fallback: boolean = HALT_AT_CLOSE_FALLBACK): number | null {
  if (m.tradingHaltsAt !== undefined) {
    return m.tradingHaltsAt && m.tradingHaltsAt > 0 ? m.tradingHaltsAt : null;
  }
  return fallback && m.closeTime && m.closeTime > 0 ? m.closeTime : null;
}

export function tradingState(
  m: TradingWindowInput,
  nowSec: number,
  fallback: boolean = HALT_AT_CLOSE_FALLBACK,
): TradingState {
  const haltsAt = haltsAtFor(m, fallback);
  if (m.resolved) return { closed: true, reason: "resolved", haltsAt };
  if (haltsAt !== null && nowSec >= haltsAt) return { closed: true, reason: "closed", haltsAt };
  // tradingOpen is the server's verdict at fetch time; false without resolved means halted.
  if (m.tradingOpen === false) return { closed: true, reason: "closed", haltsAt };
  return { closed: false, reason: null, haltsAt };
}

// Delay until the halt flips, or null when it is already past or too far away
// for a single timer. A small pad lands the re-render at or after haltsAt.
export function msUntilHalt(haltsAt: number | null, nowMs: number, padMs = 250): number | null {
  if (!haltsAt) return null;
  const delay = haltsAt * 1000 - nowMs + padMs;
  if (delay <= padMs || delay > MAX_TIMER_MS) return null;
  return delay;
}

// Maps a quote/cdp-send 409 or an on-chain AMM revert to a closed reason.
// api() errors read "409 {\"detail\":\"market closed\"}"; bundler reverts carry
// the Vyper reason string ("market closed"). Details come from
// backend/app/markets/trading.py (REASON_CLOSED / REASON_RESOLVED / REASON_UNLISTED).
export function closedReasonFromError(message: string | null | undefined): ClosedReason | null {
  const text = String(message ?? "");
  const lower = text.toLowerCase();
  if (lower.includes("market resolved")) return "resolved";
  if (lower.includes("market closed") || lower.includes("trading closed")) return "closed";
  if (lower.includes("listing not confirmed")) return "unlisted";
  if (/^409\b/.test(text)) return "closed";
  return null;
}

export function closedLabel(reason: ClosedReason | null): string {
  if (reason === "resolved") return "Market resolved";
  if (reason === "unlisted") return "Not open for trading";
  return "Trading closed";
}

// Resolved wins over everything; a server "unlisted" wins over a plain close.
export function effectiveClosedReason(
  serverReason: ClosedReason | null,
  windowReason: ClosedReason | null,
): ClosedReason {
  if (serverReason === "resolved" || windowReason === "resolved") return "resolved";
  if (serverReason === "unlisted") return "unlisted";
  return "closed";
}

// Copy for a quote failure that is not a trading halt (those map through
// closedReasonFromError). Status codes follow backend/app/amm/router.py quote:
// 422 carries the AMM revert reason, 400 bad arguments, 503 RPC or config.
export function quoteErrorMessage(status: number | null, detail: string): string {
  const text = String(detail ?? "").trim();
  if (status === null) return "couldn't reach the api for a quote";
  if (status === 422) return text ? `can't quote this trade: ${text}` : "can't quote this trade";
  if (status === 400) return "enter a valid amount";
  if (status === 404) return "market not found";
  if (status >= 500) return "quotes are unavailable right now, try again shortly";
  return text || `quote failed (${status})`;
}
