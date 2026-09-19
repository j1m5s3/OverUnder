"use client";

import { useAccount, useConnect } from "wagmi";
import Link from "next/link";

export default function PortfolioPage() {
  const { isConnected } = useAccount();
  const { connect, connectors } = useConnect();

  if (!isConnected) {
    return (
      <div>
        <h1>Portfolio</h1>
        <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
          <p className="muted" style={{ marginBottom: "16px" }}>
            Connect to see your bets.
          </p>
          <button className="btn" onClick={() => connect({ connector: connectors[0] })}>
            Connect Wallet
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h1>Portfolio</h1>
      <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
        <p className="muted" style={{ marginBottom: "16px" }}>
          No open bets.
        </p>
        <Link href="/">
          <button className="btn">Browse Markets</button>
        </Link>
      </div>
    </div>
  );
}
