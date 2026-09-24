"use client";

import {
  useCurrentUser,
  useGetAccessToken,
  useIsSignedIn,
  useSignInWithEmail,
  useSignOut,
  useVerifyEmailOTP,
} from "@coinbase/cdp-hooks";
import { useEffect, useState } from "react";
import { api, notifyAuthChanged } from "@/shared/api/client";

function smartAccountOf(user: { evmSmartAccounts?: Array<string | { address?: string }> } | null | undefined): string | null {
  const account = user?.evmSmartAccounts?.[0];
  const address = typeof account === "string" ? account : account?.address;
  return address ? address.toLowerCase() : null;
}

export function ConnectBar() {
  const { isSignedIn } = useIsSignedIn();
  const { currentUser } = useCurrentUser();
  const { getAccessToken } = useGetAccessToken();
  const { signInWithEmail } = useSignInWithEmail();
  const { verifyEmailOTP } = useVerifyEmailOTP();
  const { signOut } = useSignOut();
  const [email, setEmail] = useState("");
  const [otp, setOtp] = useState("");
  const [flowId, setFlowId] = useState<string | null>(null);
  const [authStatus, setAuthStatus] = useState("");
  const [busy, setBusy] = useState(false);

  const smart = smartAccountOf(currentUser);
  const configured = Boolean(process.env.NEXT_PUBLIC_CDP_PROJECT_ID);

  useEffect(() => {
    if (!isSignedIn) return;
    let cancelled = false;
    (async () => {
      try {
        const accessToken = await getAccessToken();
        if (!accessToken || cancelled) return;
        const authResp = await api("/api/v1/auth/cdp", {
          method: "POST",
          body: JSON.stringify({ accessToken }),
        });
        if (cancelled) return;
        localStorage.setItem("ou_token", authResp.token);
        localStorage.setItem("ou_address", authResp.address);
        notifyAuthChanged();
        setAuthStatus("Authenticated");
      } catch (e: any) {
        if (!cancelled) setAuthStatus(`Auth failed: ${e.message}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isSignedIn, getAccessToken]);

  async function sendOtp() {
    if (!configured) {
      setAuthStatus("sign-in isn't configured on this deploy");
      return;
    }
    setBusy(true);
    setAuthStatus("");
    try {
      const result = await signInWithEmail({ email: email.trim() });
      setFlowId(result.flowId);
      setAuthStatus("Check your email for a code");
    } catch (e: any) {
      setAuthStatus(e.message || "Failed to send code");
    } finally {
      setBusy(false);
    }
  }

  async function verifyOtp() {
    if (!flowId) return;
    setBusy(true);
    setAuthStatus("");
    try {
      await verifyEmailOTP({ flowId, otp: otp.trim() });
    } catch (e: any) {
      setAuthStatus(e.message || "Failed to verify code");
      setBusy(false);
    }
  }

  async function handleSignOut() {
    await signOut();
    localStorage.removeItem("ou_token");
    localStorage.removeItem("ou_address");
    notifyAuthChanged();
    setFlowId(null);
    setOtp("");
    setAuthStatus("");
  }

  if (isSignedIn && smart) {
    return (
      <div>
        <button className="btn ghost" onClick={handleSignOut}>
          {smart.slice(0, 6)}…{smart.slice(-4)}
        </button>
        {authStatus && <span className="muted" style={{ marginLeft: 8 }}>{authStatus}</span>}
      </div>
    );
  }

  if (!configured) {
    return <span className="muted">sign-in isn't configured</span>;
  }

  if (flowId) {
    return (
      <div className="row">
        <input
          value={otp}
          onChange={(e) => setOtp(e.target.value)}
          placeholder="Email code"
          style={{ width: 120 }}
        />
        <button className="btn ghost" onClick={verifyOtp} disabled={busy || otp.trim().length < 6}>
          Verify
        </button>
        {authStatus && <span className="muted">{authStatus}</span>}
      </div>
    );
  }

  return (
    <div className="row">
      <input
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="Email"
        style={{ width: 180 }}
      />
      <button className="btn ghost" onClick={sendOtp} disabled={busy || !email.trim()}>
        Sign in
      </button>
      {authStatus && <span className="muted">{authStatus}</span>}
    </div>
  );
}
