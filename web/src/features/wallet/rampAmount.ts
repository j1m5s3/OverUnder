// Pure amount check for RampCard. Mirrors the API: /kyc/check wants a finite
// notional_usdc > 0 and /ramps parses usdc_amount as a Decimal in (0, 1,000,000].

export const RAMP_MIN_USD = 1;
export const RAMP_MAX_USD = 1_000_000;

// Returns the dollar amount (at most 2 decimals) or null when it is not sendable.
export function parseRampAmount(text: string): number | null {
  const trimmed = String(text ?? "").trim();
  if (!/^\d+(?:\.\d{1,2})?$/.test(trimmed)) return null;
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value < RAMP_MIN_USD || value > RAMP_MAX_USD) return null;
  return value;
}
