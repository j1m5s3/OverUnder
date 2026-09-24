// Pure helpers for the "List a market" flow. No runtime imports so node tests
// load it directly. All USDC amounts crossing the API are micros (6 decimals),
// matching the on-chain uint256 values the backend encodes into calldata.

export type ListingConfig = {
  enabled: boolean;
  permissionless?: boolean;
  factory?: string | null;
  usdc?: string | null;
  oracle?: string | null;
  chainId?: number | null;
  minSeedUsdc?: number | null;
  listingFeeUsdc?: number | null;
  minLeadSeconds?: number | null;
  maxHorizonSeconds?: number | null;
  cooldownSeconds?: number | null;
};

// GET /markets/listing/eligibility (EligibilityPublic), normalized by normalizeEligibility.
export type ListingEligibility = {
  enabled: boolean;
  allowed: boolean;
  permissionless: boolean;
  cooldownRemaining: number;
  // Unconfirmed ("prepared") listings by this account in the last 24h.
  pending: number;
  maxPending: number;
  reason: string | null;
};

export type ListingInput = {
  question: string;
  resolutionCriteria: string;
  closeTime: number | null;
  seedUsdc: number | null;
};

export type PreparedCall = { to: string; data: string; value?: string | number | null };

// POST /markets/listing/prepare (ListingPreparePublic).
export type PreparedListing = {
  conditionId: string;
  questionId?: string;
  salt: string;
  criteriaHash: string;
  seedUsdc?: number;
  listingFeeUsdc?: number;
  approveAmount?: number;
  calls: PreparedCall[];
};

export type CdpCall = { to: `0x${string}`; data: `0x${string}`; value: bigint };

// Mirrors the backend listing gates; the contract stores the question as String[256] (bytes).
export const QUESTION_MIN_BYTES = 10;
export const QUESTION_MAX_BYTES = 256;
export const CRITERIA_MIN_CHARS = 20;
export const CRITERIA_MAX_CHARS = 2000;
export const DEFAULT_MIN_LEAD_SECONDS = 3600;
// Headroom between filling the form and the create landing on chain.
export const SUBMIT_MARGIN_SECONDS = 300;
export const BASE_SEPOLIA_CHAIN_ID = 84532;
// Mirrors listing_gates.MAX_PENDING_PER_DAY when eligibility omits maxPending.
export const DEFAULT_MAX_PENDING = 5;
// Mirrors listing_gates.BANNED_WORDS: subjective wording the agents cannot resolve.
export const BANNED_QUESTION_WORDS = [
  "feel",
  "feels",
  "should",
  "best",
  "worst",
  "underrated",
  "overrated",
  "deserve",
  "deserves",
] as const;
// Total /confirm calls in one run. "not on chain yet" can last until the user
// op is mined and the API's RPC sees it (~90 s with confirmBackoffMs).
export const CONFIRM_MAX_ATTEMPTS = 12;
// 5xx / network failures tolerated in one run before showing confirm-failed.
export const CONFIRM_MAX_SERVER_ERRORS = 4;
// /confirm answers 422 with this prefix when the on-chain listing failed review.
export const LISTING_REJECTED_PREFIX = "listing rejected:";

const DAY = 86_400;
// Python's \b on str is Unicode-aware; JS \b is ASCII-only, so spell the word boundary out.
const BANNED_RE = new RegExp(
  `(?<![\\p{L}\\p{N}_])(?:${BANNED_QUESTION_WORDS.join("|")})(?![\\p{L}\\p{N}_])`,
  "u",
);
const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/;
const HEX_RE = /^0x(?:[0-9a-fA-F]{2})*$/;
const DATETIME_LOCAL_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/;

export function utf8Bytes(text: string): number {
  return new TextEncoder().encode(text).length;
}

// Code points, which is what the backend's len() counts.
export function charCount(text: string): number {
  return Array.from(text).length;
}

// "12.5" -> 12500000. Rejects more than 6 decimals, signs, exponents and separators.
export function usdcToMicros(value: string | number): number | null {
  const text = typeof value === "number" ? (Number.isFinite(value) ? String(value) : "") : value.trim();
  const m = /^(\d*)(?:\.(\d{0,6}))?$/.exec(text);
  if (!m || !/\d/.test(text)) return null;
  const whole = Number(m[1] || "0");
  const frac = Number((m[2] || "").padEnd(6, "0"));
  const micros = whole * 1_000_000 + frac;
  return Number.isSafeInteger(micros) ? micros : null;
}

export function microsToUsdc(micros: number): string {
  const value = Math.max(0, Math.trunc(micros));
  const whole = Math.floor(value / 1_000_000);
  const frac = value % 1_000_000;
  if (!frac) return String(whole);
  return `${whole}.${String(frac).padStart(6, "0").replace(/0+$/, "")}`;
}

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return "under a minute";
  const parts: string[] = [];
  const d = Math.floor(s / DAY);
  const h = Math.floor((s % DAY) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) parts.push(`${d}d`);
  if (h) parts.push(`${h}h`);
  if (m) parts.push(`${m}m`);
  return parts.slice(0, 2).join(" ");
}

export function listingTotals(config: ListingConfig, seedMicros: number | null) {
  const seed = seedMicros && seedMicros > 0 ? seedMicros : 0;
  const fee = Math.max(0, config.listingFeeUsdc ?? 0);
  return { seed, fee, total: seed + fee };
}

export function closeTimeBounds(config: ListingConfig, nowSec: number): { earliest: number; latest: number | null } {
  const lead = config.minLeadSeconds ?? DEFAULT_MIN_LEAD_SECONDS;
  const horizon = config.maxHorizonSeconds ?? 0;
  return {
    earliest: nowSec + Math.max(0, lead) + SUBMIT_MARGIN_SECONDS,
    latest: horizon > 0 ? nowSec + horizon : null,
  };
}

// A week out, kept inside the allowed window and on a whole hour when possible.
export function defaultCloseTime(config: ListingConfig, nowSec: number): number {
  const { earliest, latest } = closeTimeBounds(config, nowSec);
  const target = Math.max(nowSec + 7 * DAY, earliest);
  const bounded = latest !== null ? Math.min(target, latest) : target;
  const hour = Math.floor(bounded / 3600) * 3600;
  if (hour >= earliest) return hour;
  const minute = Math.ceil(bounded / 60) * 60;
  return latest !== null && minute > latest ? bounded : minute;
}

export function hasSubjectiveWording(question: string): boolean {
  return BANNED_RE.test(question.toLowerCase());
}

// Returns human-readable problems; an empty list means the form can be prepared.
export function validateListing(input: ListingInput, config: ListingConfig, nowSec: number): string[] {
  if (!config.enabled) return ["Listing isn't open on this deploy."];
  const errors: string[] = [];

  const question = input.question.trim();
  const qBytes = utf8Bytes(question);
  // Same order as listing_gates.check_question, one question problem at a time.
  if (qBytes < QUESTION_MIN_BYTES) {
    errors.push(`Question must be at least ${QUESTION_MIN_BYTES} bytes.`);
  } else if (qBytes > QUESTION_MAX_BYTES) {
    errors.push(`Question is ${qBytes} bytes; the limit is ${QUESTION_MAX_BYTES}.`);
  } else if (!question.endsWith("?")) {
    errors.push("Question must be a yes/no question ending in '?'.");
  } else if (hasSubjectiveWording(question)) {
    errors.push(
      `Question uses subjective wording the agents can't resolve (avoid: ${BANNED_QUESTION_WORDS.join(", ")}).`,
    );
  }

  const criteria = charCount(input.resolutionCriteria.trim());
  if (criteria < CRITERIA_MIN_CHARS) {
    errors.push(`Resolution criteria must be at least ${CRITERIA_MIN_CHARS} characters.`);
  } else if (criteria > CRITERIA_MAX_CHARS) {
    errors.push(`Resolution criteria is ${criteria} characters; the limit is ${CRITERIA_MAX_CHARS}.`);
  }

  const close = input.closeTime;
  if (close === null || !Number.isInteger(close) || close <= 0) {
    errors.push("Pick a close date and time.");
  } else {
    const { earliest, latest } = closeTimeBounds(config, nowSec);
    const lead = config.minLeadSeconds ?? DEFAULT_MIN_LEAD_SECONDS;
    if (close < earliest) {
      errors.push(`Close time must be at least ${formatDuration(lead)} from now, plus a few minutes to confirm.`);
    } else if (latest !== null && close > latest) {
      errors.push(`Close time must be within ${formatDuration(config.maxHorizonSeconds ?? 0)} from now.`);
    }
  }

  const seed = input.seedUsdc;
  const minSeed = Math.max(0, config.minSeedUsdc ?? 0);
  if (seed === null || !Number.isSafeInteger(seed) || seed <= 0) {
    errors.push("Enter a seed amount in USDC (up to 6 decimals).");
  } else if (seed < minSeed) {
    errors.push(`Seed must be at least ${microsToUsdc(minSeed)} USDC.`);
  }

  if (config.chainId && config.chainId !== BASE_SEPOLIA_CHAIN_ID) {
    errors.push(`Listing is configured for chain ${config.chainId}; this app sends on Base Sepolia.`);
  }
  return errors;
}

function callValue(value: PreparedCall["value"], index: number): bigint {
  if (value === null || value === undefined || value === "") return 0n;
  if (typeof value === "number" && !Number.isSafeInteger(value)) {
    throw new Error(`call ${index}: value must be an integer`);
  }
  let parsed: bigint;
  try {
    parsed = BigInt(value);
  } catch {
    throw new Error(`call ${index}: value must be an integer`);
  }
  if (parsed < 0n) throw new Error(`call ${index}: value must not be negative`);
  return parsed;
}

// Converts the backend's prepared calls into CDP EvmCall objects. When
// allowedTargets is non-empty, every call must go to one of them (USDC and the
// factory from the listing config), so a bad prepare response cannot make the
// user sign a call to anything else.
export function toCdpCalls(calls: PreparedCall[], allowedTargets: Array<string | null | undefined> = []): CdpCall[] {
  if (!Array.isArray(calls) || calls.length === 0) throw new Error("prepare returned no calls");
  const allowed = new Set(allowedTargets.filter((t): t is string => Boolean(t)).map((t) => t.toLowerCase()));
  return calls.map((call, index) => {
    const to = String(call?.to ?? "");
    const data = String(call?.data ?? "");
    if (!ADDRESS_RE.test(to)) throw new Error(`call ${index}: bad target address`);
    if (!HEX_RE.test(data)) throw new Error(`call ${index}: bad calldata`);
    if (allowed.size > 0 && !allowed.has(to.toLowerCase())) {
      throw new Error(`call ${index}: unexpected target ${to}`);
    }
    return { to: to as `0x${string}`, data: data as `0x${string}`, value: callValue(call.value, index) };
  });
}

// confirm answers 409 "not on chain yet" until the create is readable over RPC.
export function isNotOnChainYet(message: string | null | undefined): boolean {
  const text = String(message ?? "");
  return /^409\b/.test(text) && /not on chain/i.test(text);
}

export function confirmBackoffMs(attempt: number): number {
  return Math.min(1500 * 2 ** Math.max(0, attempt), 10_000);
}

// status null = no HTTP response (network error). detail = the FastAPI detail text.
export type ConfirmErrorInfo = { status: number | null; detail: string };

export type ConfirmDecision =
  // The on-chain listing failed the gates or differs from the prepared one. Terminal.
  | { kind: "rejected"; reason: string }
  // Transient: retry the (idempotent) confirm after a backoff.
  | { kind: "retry"; cause: "not-on-chain" | "server" }
  // Retrying automatically will not help; the user may still retry by hand.
  | { kind: "failed"; detail: string };

// Status codes follow backend/app/markets/listing.py listing_confirm.
export function classifyConfirmError(info: ConfirmErrorInfo): ConfirmDecision {
  const { status } = info;
  const detail = String(info.detail ?? "").trim();
  if (status === 422 && detail.toLowerCase().startsWith(LISTING_REJECTED_PREFIX)) {
    const reason = detail.slice(LISTING_REJECTED_PREFIX.length).trim();
    return { kind: "rejected", reason: reason || "the listing did not pass review" };
  }
  if (status === 409 && /not on chain/i.test(detail)) return { kind: "retry", cause: "not-on-chain" };
  if (status === null || (status >= 500 && status <= 599)) return { kind: "retry", cause: "server" };
  if (status === 401) return { kind: "failed", detail: "your session expired; sign out, sign in again and retry" };
  return { kind: "failed", detail: detail || `HTTP ${status}` };
}

// 5xx bodies can be proxy HTML or long traces; keep the copy short.
export function serverErrorDetail(info: ConfirmErrorInfo): string {
  if (info.status === null) return "couldn't reach the API";
  const detail = String(info.detail ?? "").trim();
  if (!detail || detail.length > 160 || /<[a-z!]/i.test(detail)) return `server error (${info.status})`;
  return `${detail} (${info.status})`;
}

export type ConfirmResult<T> =
  | { state: "confirmed"; market: T }
  | { state: "rejected"; reason: string }
  // detail null: the listing never showed up on chain within the attempts.
  | { state: "failed"; detail: string | null }
  | { state: "cancelled" };

export type ConfirmDeps<T> = {
  confirm: () => Promise<T>;
  errorInfo: (err: unknown) => ConfirmErrorInfo;
  sleep: (ms: number) => Promise<void>;
  cancelled: () => boolean;
  onRetry?: (attempt: number, cause: "not-on-chain" | "server") => void;
  maxAttempts?: number;
  maxServerErrors?: number;
  backoffMs?: (attempt: number) => number;
};

// Runs POST /markets/listing/confirm with bounded retries. Only the confirm is
// retried; nothing here can re-send the listing transaction.
export async function confirmWithRetry<T>(deps: ConfirmDeps<T>): Promise<ConfirmResult<T>> {
  const maxAttempts = Math.max(1, deps.maxAttempts ?? CONFIRM_MAX_ATTEMPTS);
  const maxServerErrors = Math.max(1, deps.maxServerErrors ?? CONFIRM_MAX_SERVER_ERRORS);
  const backoff = deps.backoffMs ?? confirmBackoffMs;
  let serverErrors = 0;
  let lastDetail: string | null = null;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    if (deps.cancelled()) return { state: "cancelled" };
    try {
      const market = await deps.confirm();
      if (deps.cancelled()) return { state: "cancelled" };
      return { state: "confirmed", market };
    } catch (err) {
      if (deps.cancelled()) return { state: "cancelled" };
      const info = deps.errorInfo(err);
      const decision = classifyConfirmError(info);
      if (decision.kind === "rejected") return { state: "rejected", reason: decision.reason };
      if (decision.kind === "failed") return { state: "failed", detail: decision.detail };
      if (decision.cause === "server") {
        serverErrors += 1;
        lastDetail = serverErrorDetail(info);
        if (serverErrors >= maxServerErrors) break;
      } else {
        lastDetail = null;
      }
      if (attempt + 1 >= maxAttempts) break;
      deps.onRetry?.(attempt + 1, decision.cause);
      await deps.sleep(backoff(attempt));
    }
  }
  if (deps.cancelled()) return { state: "cancelled" };
  return { state: "failed", detail: lastDetail };
}

export function rejectedMessage(reason: string): string {
  return (
    `Listing rejected: ${reason}. The market was created on chain but failed the listing checks, ` +
    "so it won't be shown or traded here. The operator reviews rejected listings."
  );
}

// Tolerates missing fields from an older API.
export function normalizeEligibility(raw: unknown): ListingEligibility {
  const e = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const count = (value: unknown, fallback: number) => {
    const n = Number(value);
    return Number.isFinite(n) && n >= 0 ? Math.floor(n) : fallback;
  };
  return {
    enabled: e.enabled === undefined ? true : Boolean(e.enabled),
    allowed: Boolean(e.allowed),
    permissionless: Boolean(e.permissionless),
    cooldownRemaining: count(e.cooldownRemaining, 0),
    pending: count(e.pending, 0),
    maxPending: count(e.maxPending, DEFAULT_MAX_PENDING) || DEFAULT_MAX_PENDING,
    reason: typeof e.reason === "string" && e.reason ? e.reason : null,
  };
}

// Why this account cannot list right now, or null when it can.
export function eligibilityGate(e: ListingEligibility, config: Pick<ListingConfig, "permissionless">): string | null {
  if (!e.enabled) return e.reason ? `Listing isn't available right now: ${e.reason}.` : "Listing isn't available right now.";
  if (!e.allowed) {
    return config.permissionless === false || !e.permissionless
      ? "Listing is invite-only right now and your account isn't on the list."
      : "Your account can't list markets right now.";
  }
  if (e.cooldownRemaining > 0) return `You can list another market in ${formatDuration(e.cooldownRemaining)}.`;
  if (e.pending >= e.maxPending) {
    return (
      `You have ${e.pending} unconfirmed listings from the last 24 hours; the limit is ${e.maxPending}. ` +
      "Try again once they are confirmed or older than a day."
    );
  }
  return null;
}

// Keeps one listing's batch from being sent twice. begin() is taken
// synchronously when a submit starts, so a double click or a re-render that
// replays the handler is ignored. Once a userOperationHash is recorded for a
// prepared conditionId that batch is never sent again, even after release().
export class ListingSendGuard {
  private active = false;
  private readonly sent = new Map<string, string>();

  begin(): boolean {
    if (this.active) return false;
    this.active = true;
    return true;
  }

  get busy(): boolean {
    return this.active;
  }

  canSend(conditionId: string): boolean {
    return this.active && !this.sent.has(conditionId.toLowerCase());
  }

  markSent(conditionId: string, userOperationHash: string): void {
    this.sent.set(conditionId.toLowerCase(), userOperationHash);
  }

  hashFor(conditionId: string): string | undefined {
    return this.sent.get(conditionId.toLowerCase());
  }

  // Only after a definitive failure (nothing landed) or a finished listing.
  release(): void {
    this.active = false;
  }
}

// Copy for the confirm-failed state. `txUnverified` means we never saw the
// listing transaction mined (the status poll was lost), so we must not claim
// the market is on chain. `detail` is a non-409 confirm error, if any.
export function confirmFailedMessage(txUnverified: boolean, detail: string | null): string {
  if (txUnverified) {
    const base = "We couldn't confirm the transaction yet; if it went through, your market will appear shortly.";
    return detail ? `${base} (${detail})` : base;
  }
  return detail
    ? `Your market is on chain but registering it failed: ${detail}`
    : "Your market is on chain but the API hasn't seen it yet. Try again in a minute.";
}

export function datetimeLocalToUnix(value: string): number | null {
  const m = DATETIME_LOCAL_RE.exec(value.trim());
  if (!m) return null;
  const [, y, mo, d, h, mi, s] = m;
  const date = new Date(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s || "0"));
  const ms = date.getTime();
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}

export function unixToDatetimeLocal(unixSeconds: number): string {
  const date = new Date(unixSeconds * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}
