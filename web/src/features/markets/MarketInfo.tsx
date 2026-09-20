"use client";

import type { Market } from "./MarketList";

function formatTimestamp(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit"
  });
}

export function MarketInfo({ market }: { market: Market }) {
  const now = Math.floor(Date.now() / 1000);
  const hasClosed = market.closeTime < now;
  
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h3 style={{ marginBottom: 12 }}>Rules & Timeline</h3>
      
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div>
          <div className="muted" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 4 }}>
            Closes
          </div>
          <div style={{ fontSize: 14 }}>
            {hasClosed ? "Closed" : formatTimestamp(market.closeTime)}
          </div>
        </div>
        
        <div>
          <div className="muted" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 4 }}>
            Settlement
          </div>
          <div style={{ fontSize: 14 }}>
            After oracle consensus
          </div>
        </div>
        
        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12, marginTop: 4 }}>
          <div className="muted" style={{ fontSize: 12, lineHeight: 1.5 }}>
            Binary yes/no market. Winners pay $1 per share. 1% fee on all trades. 
            Three AI oracles must agree to resolve; otherwise 24h vote.
          </div>
        </div>
      </div>
    </div>
  );
}
