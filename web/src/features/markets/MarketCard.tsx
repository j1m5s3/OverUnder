"use client";

import Link from "next/link";
import { parseMatchup } from "@/shared/utils/categorize";
import type { Market } from "./eventHub";

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

function formatMultiplier(probability: number): string {
  if (!Number.isFinite(probability) || probability <= 0 || probability >= 1) return "—";
  const multiplier = 1 / probability;
  return multiplier < 10 ? multiplier.toFixed(2) : multiplier.toFixed(1);
}

export function MarketCard({ market, childCount }: { market: Market; childCount?: number }) {
  const yesPct = Math.round((market.suggestedProbability || 0.5) * 100);
  const noPct = 100 - yesPct;
  const isLeading = (side: "yes" | "no") => side === "yes" ? yesPct > noPct : noPct > yesPct;
  
  const yesProb = market.suggestedProbability || 0.5;
  const noProb = 1 - yesProb;
  const yesMultiplier = formatMultiplier(yesProb);
  const noMultiplier = formatMultiplier(noProb);
  
  const matchup = parseMatchup(market.question);
  
  return (
    <div className="card">
      <Link href={`/markets/${encodeURIComponent(market.conditionId)}`} style={{ display: "block", marginBottom: 8 }}>
        {matchup && (
          <div className="muted" style={{ fontSize: 10, marginBottom: 3, textTransform: "uppercase", letterSpacing: "0.05em" }}>
            {matchup.teamA} vs {matchup.teamB}
          </div>
        )}
        <h3 style={{ marginBottom: 4, fontSize: 16, lineHeight: 1.3 }}>{market.question}</h3>
        <div className="muted" style={{ fontSize: 11 }}>{formatCloseTime(market.closeTime)}</div>
      </Link>
      
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
        <div style={{ textAlign: "center", flex: 1 }}>
          <div className="muted" style={{ fontSize: 10, marginBottom: 2, textTransform: "uppercase", letterSpacing: "0.05em" }}>Yes</div>
          <div style={{ fontSize: 28, fontWeight: 600, opacity: isLeading("yes") ? 1 : 0.7 }} className="yes">{yesPct}%</div>
          <div className="muted" style={{ fontSize: 10, marginTop: 1 }}>{yesMultiplier}×</div>
        </div>
        <div style={{ textAlign: "center", flex: 1 }}>
          <div className="muted" style={{ fontSize: 10, marginBottom: 2, textTransform: "uppercase", letterSpacing: "0.05em" }}>No</div>
          <div style={{ fontSize: 28, fontWeight: 600, opacity: isLeading("no") ? 1 : 0.7 }} className="no">{noPct}%</div>
          <div className="muted" style={{ fontSize: 10, marginTop: 1 }}>{noMultiplier}×</div>
        </div>
      </div>
      
      {childCount !== undefined && childCount > 0 && (
        <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
          {childCount} market{childCount === 1 ? "" : "s"}
        </div>
      )}
      
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
