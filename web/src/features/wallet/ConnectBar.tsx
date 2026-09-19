"use client";

import { useAccount, useConnect, useDisconnect, useSignMessage } from "wagmi";
import { useEffect, useState } from "react";
import { api } from "@/shared/api/client";

export function ConnectBar() {
  const { address, isConnected } = useAccount();
  const { connect, connectors } = useConnect();
  const { disconnect } = useDisconnect();
  const { signMessageAsync } = useSignMessage();
  const [authStatus, setAuthStatus] = useState("");

  useEffect(() => {
    if (address) {
      localStorage.setItem("ou_address", address.toLowerCase());
      authenticateWallet(address);
    }
  }, [address]);

  async function authenticateWallet(addr: string) {
    try {
      const nonceResp = await api(`/api/v1/auth/nonce/${addr}`);
      const message = `Sign in to OverUnder\n\nAddress: ${addr}\nNonce: ${nonceResp.nonce}`;
      
      const signature = await signMessageAsync({ message });
      
      const authResp = await api("/api/v1/auth/siwe", {
        method: "POST",
        body: JSON.stringify({
          address: addr,
          signature,
          message,
        }),
      });
      
      localStorage.setItem("ou_token", authResp.token);
      setAuthStatus("Authenticated");
    } catch (e: any) {
      setAuthStatus(`Auth failed: ${e.message}`);
      console.error("Auth error:", e);
    }
  }

  if (isConnected && address) {
    return (
      <div>
        <button className="btn ghost" onClick={() => disconnect()}>
          {address.slice(0, 6)}…{address.slice(-4)}
        </button>
        {authStatus && <span className="muted" style={{ marginLeft: 8 }}>{authStatus}</span>}
      </div>
    );
  }

  return (
    <div className="row">
      <button className="btn ghost" onClick={() => connect({ connector: connectors[0] })}>
        Connect Wallet
      </button>
      {authStatus && <span className="muted">{authStatus}</span>}
    </div>
  );
}
