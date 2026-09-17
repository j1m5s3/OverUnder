"use client";

import { useEffect, useState } from "react";
import { api } from "@/shared/api/client";

export function RampCard({ address }: { address: string }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    if (!address) return;
    api(`/api/v1/ramps/onramp-url?address=${address}`)
      .then((r) => setUrl(r.url))
      .catch(() => setUrl(""));
  }, [address]);
  return (
    <div className="card">
      <h3>On / off ramps</h3>
      <p className="muted">Coinbase Onramp for USDC on Base (same pattern as Polymarket).</p>
      {url ? (
        <a className="btn" href={url} target="_blank" rel="noreferrer">
          Buy USDC
        </a>
      ) : (
        <p className="muted">Connect to generate an onramp URL.</p>
      )}
    </div>
  );
}
