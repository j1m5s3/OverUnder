"use client";

import { isUserListed, listedBy, type Market, type MarketListingInfo } from "./eventHub";
import { RESOLUTION_POLICY } from "@/features/oracle/resolutionPolicy";

function shortAddress(address: string): string {
  return address.length > 12 ? `${address.slice(0, 6)}…${address.slice(-4)}` : address;
}

function formatUsdc(micros: number): string {
  return (micros / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function formatTimestamp(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit"
  });
}

function formatFacts(facts: Record<string, unknown>): string {
  return Object.entries(facts)
    .filter(([, value]) => value != null && value !== "")
    .map(([key, value]) => `${key.replace(/_/g, " ")} ${String(value)}`)
    .join(" · ");
}

export function MarketInfo({
  market,
  facts,
}: {
  market: Market & { listing?: MarketListingInfo | null };
  facts?: Record<string, unknown> | null;
}) {
  const now = Math.floor(Date.now() / 1000);
  const hasClosed = market.closeTime < now;
  const factsLine = facts ? formatFacts(facts) : "";
  const userListed = isUserListed(market);
  const criteria = (market.resolutionCriteria || "").trim();
  const creator = listedBy(market);
  const seedMicros = market.listing?.seedUsdc ?? 0;

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

        {userListed && criteria ? (
          <div>
            <div className="muted" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 4 }}>
              Resolution criteria
            </div>
            <div style={{ fontSize: 14, whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{criteria}</div>
          </div>
        ) : null}

        {userListed ? (
          <div>
            <div className="muted" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 4 }}>
              Listed by
            </div>
            <div style={{ fontSize: 14 }} title={creator || undefined}>
              {creator ? shortAddress(creator) : "A community member"}
              {seedMicros > 0 ? ` · seeded ${formatUsdc(seedMicros)} USDC` : ""}
            </div>
          </div>
        ) : null}

        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12, marginTop: 4 }}>
          <div className="muted" style={{ fontSize: 12, lineHeight: 1.5 }}>
            Binary yes/no market. Winners pay $1 per share. 1% fee on all trades. {RESOLUTION_POLICY}
          </div>
          {factsLine ? (
            <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
              {factsLine}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
