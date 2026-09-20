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
  const openOrders = data?.openOrders || [];
  const trades = data?.trades || [];
  const hasActivity = positions.length > 0 || openOrders.length > 0 || trades.length > 0;

  if (!hasActivity) {
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
      
      {positions.length > 0 && (
        <div className="card">
          <h3>Positions</h3>
          <div style={{ marginTop: "12px" }}>
            {positions.map((pos: any, i: number) => (
              <div key={i} style={{ padding: "8px 0", borderBottom: i < positions.length - 1 ? "1px solid var(--border)" : "none" }}>
                <div>{pos.question || pos.marketId}</div>
                <div className="row" style={{ marginTop: "4px" }}>
                  <span className={pos.side === "YES" ? "yes" : "no"}>{pos.side}</span>
                  <span className="muted" style={{ marginLeft: "8px" }}>{pos.amount} shares</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {openOrders.length > 0 && (
        <div className="card" style={{ marginTop: "16px" }}>
          <h3>Open Orders</h3>
          <p className="muted" style={{ marginTop: "8px" }}>
            You have {openOrders.length} open {openOrders.length === 1 ? "order" : "orders"}
          </p>
        </div>
      )}

      {trades.length > 0 && (
        <div className="card" style={{ marginTop: "16px" }}>
          <h3>Trade History</h3>
          <p className="muted" style={{ marginTop: "8px" }}>
            {trades.length} {trades.length === 1 ? "trade" : "trades"}
          </p>
        </div>
      )}
    </div>
  );
}
