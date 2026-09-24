const BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

// Fired on window whenever ConnectBar stores or clears the API session token.
export const AUTH_EVENT = "ou-auth";

export type ApiSession = { token: string | null; address: string | null };

// Keeps the historical `${status} ${body}` message so existing callers that
// match on e.message keep working; status/detail are for new code.
export class ApiError extends Error {
  status: number;
  body: string;

  constructor(status: number, body: string) {
    super(`${status} ${body}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export async function api(path: string, init?: RequestInit) {
  const token = typeof window !== "undefined" ? localStorage.getItem("ou_token") : null;
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.json();
}

// FastAPI errors arrive as {"detail": "..."} or {"detail": [{msg}, ...]}.
export function apiErrorDetail(e: unknown): string {
  const raw = e instanceof ApiError ? e.body : e instanceof Error ? e.message.replace(/^\d{3}\s/, "") : String(e ?? "");
  try {
    const parsed = JSON.parse(raw);
    const detail = parsed?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((d: { msg?: string }) => d?.msg ?? JSON.stringify(d)).join("; ");
    }
  } catch {
    // not JSON; fall through to the raw text
  }
  return raw;
}

export function apiErrorStatus(e: unknown): number | null {
  if (e instanceof ApiError) return e.status;
  const m = e instanceof Error ? /^(\d{3})\s/.exec(e.message) : null;
  return m ? Number(m[1]) : null;
}

export function readSession(): ApiSession {
  if (typeof window === "undefined") return { token: null, address: null };
  try {
    return { token: localStorage.getItem("ou_token"), address: localStorage.getItem("ou_address") };
  } catch {
    return { token: null, address: null };
  }
}

export function notifyAuthChanged() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(AUTH_EVENT));
}
