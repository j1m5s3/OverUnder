"use client";

import Link from "next/link";
import type { Market } from "./MarketList";

export function MarketCard({ market }: { market: Market }) {
  const pct = Math.round((market.suggestedProbability || 0.5) * 100);
  return (
    <Link href={`/markets/${encodeURIComponent(market.conditionId)}`} className="card" style={{ display: "block" }}>
      <div className="muted">{market.marketType === 0 ? "PRIMARY · CLOB" : "WILDCARD · AMM"}</div>
      <h3>{market.question}</h3>
      <div className="row">
        <span className="yes">Yes {pct}%</span>
        <span className="no">No {100 - pct}%</span>
      </div>
    </Link>
  );
}
