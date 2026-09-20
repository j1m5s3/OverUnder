"use client";

import { useAccount, useConnect } from "wagmi";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/shared/api/client";

export default function PortfolioPage() {
  const { address, isConnected } = useAccount();
  const { connect, connectors } = useConnect();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (address) {
      setLoading(true);
      api(`/api/v1/portfolio/${address.toLowerCase()}`)
        .then(setData)
        .catch(() => setData(null))
        .finally(() => setLoading(false));
    }
  }, [address]);

  if (!isConnected) {
    return (
      <div>
        <h1>Portfolio</h1>
        <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
          <p className="muted" style={{ marginBottom: "16px" }}>
            Connect to see your bets.
          </p>
          <button className="btn" onClick={() => connect({ connector: connectors[0] })}>
            connect to see your bets
          </button>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div>
        <h1>Portfolio</h1>
        <p className="muted">Loading positions...</p>
      </div>
    );
  }

  const positions = data?.positions || [];

  if (positions.length === 0) {
    return (
      <div>
        <h1>Portfolio</h1>
        <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
          <p className="muted" style={{ marginBottom: "16px" }}>
            No open bets yet.
          </p>
          <Link className="btn" href="/">
            Browse markets
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h1>Portfolio</h1>
      <p className="muted">{address}</p>
      <pre className="card">{JSON.stringify(data, null, 2)}</pre>
    </div>
  );
}
