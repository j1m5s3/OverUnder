"use client";

import { useAccount, useConnect, useDisconnect } from "wagmi";
import { useEffect, useState } from "react";
import { api } from "@/shared/api/client";

export function ConnectBar() {
  const { address, isConnected } = useAccount();
  const { connect, connectors } = useConnect();
  const { disconnect } = useDisconnect();
  const [email, setEmail] = useState("");

  useEffect(() => {
    if (address) {
      localStorage.setItem("ou_address", address.toLowerCase());
      api(`/api/v1/auth/nonce/${address}`)
        .then((n) =>
          api("/api/v1/auth/siwe", {
            method: "POST",
            body: JSON.stringify({
              address,
              signature: "0x",
              message: `login ${address} nonce ${n.nonce}`,
            }),
          }),
        )
        .then((r) => localStorage.setItem("ou_token", r.token))
        .catch(() => undefined);
    }
  }, [address]);

  async function emailLogin() {
    const s = email || "user@overunder.local";
    let hex = "";
    for (let i = 0; i < 20; i++) {
      hex += (s.charCodeAt(i % s.length) % 256).toString(16).padStart(2, "0");
    }
    const demo = "0x" + hex.slice(0, 40);
    localStorage.setItem("ou_address", demo);
    const r = await api("/api/v1/auth/privy", {
      method: "POST",
      body: JSON.stringify({ token: "privy-demo", address: demo }),
    });
    localStorage.setItem("ou_token", r.token);
    window.location.reload();
  }

  if (isConnected && address) {
    return (
      <button className="btn ghost" onClick={() => disconnect()}>
        {address.slice(0, 6)}…{address.slice(-4)}
      </button>
    );
  }

  return (
    <div className="row">
      <button className="btn ghost" onClick={() => connect({ connector: connectors[0] })}>
        EOA / Smart Wallet
      </button>
      <input
        placeholder="email AA"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        style={{ width: 140 }}
      />
      <button className="btn" onClick={emailLogin}>
        Email
      </button>
    </div>
  );
}
