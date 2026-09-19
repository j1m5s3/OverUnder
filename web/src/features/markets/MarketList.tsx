"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/shared/api/client";
import { MarketCard } from "./MarketCard";

export type Market = {
  conditionId: string;
  parentConditionId?: string | null;
  question: string;
  marketType: number;
  closeTime: number;
  paused: boolean;
  resolved: boolean;
  suggestedProbability: number;
};

export function MarketList() {
  const [markets, setMarkets] = useState<Market[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/api/v1/markets")
      .then(setMarkets)
      .catch(() => {
        setError("couldn't reach the api — showing demo markets.");
        setMarkets([
          {
            conditionId: "0xdemo1",
            question: "Chiefs vs Broncos: Chiefs win?",
            marketType: 0,
            closeTime: Math.floor(Date.now() / 1000) + 86400,
            paused: false,
            resolved: false,
            suggestedProbability: 0.58,
          },
          {
            conditionId: "0xdemo2",
            parentConditionId: "0xdemo1",
            question: "Travis Kelce to fumble at least once?",
            marketType: 1,
            closeTime: Math.floor(Date.now() / 1000) + 86400,
            paused: false,
            resolved: false,
            suggestedProbability: 0.22,
          },
        ]);
      });
  }, []);

  if (markets === null) {
    return (
      <div className="grid">
        <div className="card skeleton" style={{ height: 140 }}></div>
        <div className="card skeleton" style={{ height: 140 }}></div>
        <div className="card skeleton" style={{ height: 140 }}></div>
      </div>
    );
  }

  if (markets.length === 0) {
    return <p className="muted">no markets yet</p>;
  }

  const primaries = markets.filter((m) => m.marketType === 0);
  const gridStyle = primaries.length === 1 ? { maxWidth: 560 } : {};

  return (
    <div>
      {error ? (
        <div className="banner" style={{ marginBottom: 16 }}>
          {error}
        </div>
      ) : null}
      <div className="grid" style={gridStyle}>
        {primaries.map((m) => {
          const wildcards = markets.filter((c) => c.parentConditionId === m.conditionId);
          return (
            <div key={m.conditionId}>
              <MarketCard market={m} />
              {wildcards.length > 0 && (
                <div className="muted" style={{ marginTop: 8 }}>
                  Wildcards:{" "}
                  {wildcards.map((c) => (
                    <Link key={c.conditionId} href={`/markets/${c.conditionId}`}>
                      {c.question}{" "}
                    </Link>
                  ))}
                </div>
              )}
            </div>
          );
        })}
        {primaries.length === 0
          ? markets.map((m) => <MarketCard key={m.conditionId} market={m} />)
          : null}
      </div>
    </div>
  );
}
