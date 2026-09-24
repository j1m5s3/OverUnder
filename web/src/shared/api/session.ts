"use client";

import { useEffect, useState } from "react";
import { AUTH_EVENT, readSession, type ApiSession } from "./client";

// The backend JWT lands in localStorage a moment after CDP sign-in (ConnectBar
// exchanges the CDP access token). Callers that need an authenticated API call
// wait for `ready`, which also rejects a leftover token for another account.
export function useApiSession(expectedAddress?: string | null): { ready: boolean; session: ApiSession } {
  const [session, setSession] = useState<ApiSession>({ token: null, address: null });

  useEffect(() => {
    const update = () => setSession(readSession());
    update();
    window.addEventListener(AUTH_EVENT, update);
    window.addEventListener("storage", update);
    return () => {
      window.removeEventListener(AUTH_EVENT, update);
      window.removeEventListener("storage", update);
    };
  }, []);

  const addressMatches =
    !expectedAddress || (session.address ?? "").toLowerCase() === expectedAddress.toLowerCase();
  return { ready: Boolean(session.token) && addressMatches, session };
}
