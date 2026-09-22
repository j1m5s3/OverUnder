"use client";

import { useCurrentUser, useIsSignedIn } from "@coinbase/cdp-hooks";
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
  const { isSignedIn } = useIsSignedIn();
  const { currentUser } = useCurrentUser();
  const address = currentUser?.evmSmartAccounts?.[0];
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

  if (!isSignedIn || !address) {
    return (
      <div>
        <h1>My Bets</h1>
        <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
          <p className="muted" style={{ marginBottom: "16px" }}>
            Sign in to see your bets.
          </p>
        </div>
      </div>
    );
  }

  if (loading || (!data && !error)) {
    return (
      <div>
        <h1>My Bets</h1>
        <p className="muted">Loading your bets...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <h1>My Bets</h1>
        <p className="muted">Failed to load your bets. Please try again.</p>
      </div>
    );
  }

  const activePositions = (data?.positions || []).filter((p) => p.sizeMicros > 0);

  if (activePositions.length === 0) {
    return (
      <div>
        <h1>My Bets</h1>
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
      <h1>My Bets</h1>
      <p className="muted">{address}</p>
      <div>
        <h2>Open Bets</h2>
        <ul>
          {activePositions.map((position, idx) => (
            <li key={`${position.conditionId}-${position.outcome}-${idx}`}>
              <strong>{position.question}</strong> — {position.side} — {(position.sizeMicros / 1_000_000).toFixed(2)} shares
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
