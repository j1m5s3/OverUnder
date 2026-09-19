"use client";

import Link from "next/link";
import type { Market } from "./MarketList";

function formatCloseTime(unixSeconds: number): string {
  const date = new Date(unixSeconds * 1000);
  const now = Date.now();
  const diff = date.getTime() - now;
  
  if (diff < 0) return "Closed";
  
  const hours = Math.floor(diff / (1000 * 60 * 60));
  const days = Math.floor(hours / 24);
  
  if (days > 1) return `Closes in ${days} days`;
  if (hours > 1) return `Closes in ${hours} hours`;
  return "Closes soon";
}

export function MarketCard({ market }: { market: Market }) {
  const yesPct = Math.round((market.suggestedProbability || 0.5) * 100);
  const noPct = 100 - yesPct;
  const isLeading = (side: "yes" | "no") => side === "yes" ? yesPct > noPct : noPct > yesPct;
  
  return (
    <div className="card">
      <Link href={`/markets/${encodeURIComponent(market.conditionId)}`} style={{ display: "block", marginBottom: 12 }}>
        <h3 style={{ marginBottom: 8 }}>{market.question}</h3>
        <div className="muted" style={{ fontSize: 12 }}>{formatCloseTime(market.closeTime)}</div>
      </Link>
      
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 12 }}>
        <div style={{ textAlign: "center" }}>
          <div style={{ fontSize: 32, fontWeight: 600, opacity: isLeading("yes") ? 1 : 0.7 }} className="yes">{yesPct}%</div>
          <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>yes</div>
        </div>
        <div style={{ textAlign: "center" }}>
          <div style={{ fontSize: 32, fontWeight: 600, opacity: isLeading("no") ? 1 : 0.7 }} className="no">{noPct}%</div>
          <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>no</div>
        </div>
      </div>
      
      <div className="row">
        <Link href={`/markets/${encodeURIComponent(market.conditionId)}?side=yes`} className="btn yes" style={{ flex: 1, textAlign: "center" }}>
          Yes
        </Link>
        <Link href={`/markets/${encodeURIComponent(market.conditionId)}?side=no`} className="btn no" style={{ flex: 1, textAlign: "center" }}>
          No
        </Link>
      </div>
    </div>
  );
}
