"use client";

import { useAccount, useConnect } from "wagmi";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/shared/api/client";

interface Position {
  question: string;
  side: "YES" | "NO";
  sizeMicros: number;
  conditionId: string;
  outcome: number;
}

interface PortfolioData {
  address: string;
  positions: Position[];
  openOrders: any[];
  trades: any[];
}

export default function PortfolioPage() {
  const { address, isConnected } = useAccount();
  const { connect, connectors } = useConnect();
  const [data, setData] = useState<PortfolioData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (address) {
      setLoading(true);
      setError(false);
      api(`/api/v1/portfolio/${address.toLowerCase()}`)
        .then((response: PortfolioData) => {
          setData(response);
          setLoading(false);
        })
        .catch(() => {
          setError(true);
          setLoading(false);
        });
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

  if (error) {
    return (
      <div>
        <h1>Portfolio</h1>
        <p className="muted">Failed to load portfolio. Please try again.</p>
      </div>
    );
  }

  // Filter out zero/dust positions
  const activePositions = (data?.positions || []).filter((p) => p.sizeMicros > 0);

  if (activePositions.length === 0) {
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
      <div>
        <h2>Open Bets</h2>
        <ul>
          {activePositions.map((position, idx) => (
            <li key={`${position.conditionId}-${position.outcome}-${idx}`}>
              <strong>{position.question}</strong> — {position.side} — {(position.sizeMicros / 1_000_000).toFixed(2)} tokens
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
