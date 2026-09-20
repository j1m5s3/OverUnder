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

type Category = "all" | "sports" | "other";

function categorizeMarket(market: Market): Category {
  const q = market.question.toLowerCase();
  const sportsKeywords = [
    "win", "lose", "score", "game", "match", "championship", "playoff",
    "bowl", "series", "league", "nfl", "nba", "mlb", "nhl", "mls", "ncaa",
    "soccer", "football", "basketball", "baseball", "hockey", "tennis",
    "golf", "boxing", "ufc", "mma", "racing", "nascar", "f1",
    "chiefs", "broncos", "yankees", "lakers", "celtics", "cowboys",
    "point", "touchdown", "goal", "home run", "overtime", "fumble"
  ];
  
  if (sportsKeywords.some((kw) => q.includes(kw))) {
    return "sports";
  }
  return "other";
}

export function MarketList() {
  const [markets, setMarkets] = useState<Market[] | null>(null);
  const [error, setError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [activeCategory, setActiveCategory] = useState<Category>("all");

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

  const filtered = markets.filter((m) => {
    const matchesSearch = m.question.toLowerCase().includes(searchQuery.toLowerCase());
    if (!matchesSearch) return false;
    
    if (activeCategory === "all") return true;
    return categorizeMarket(m) === activeCategory;
  });

  const categoryCounts = {
    all: markets.length,
    sports: markets.filter((m) => categorizeMarket(m) === "sports").length,
    other: markets.filter((m) => categorizeMarket(m) === "other").length,
  };

  const availableCategories: Category[] = ["all"];
  if (categoryCounts.sports > 0) availableCategories.push("sports");
  if (categoryCounts.other > 0) availableCategories.push("other");

  const primaries = filtered.filter((m) => m.marketType === 0);
  const gridStyle = primaries.length === 1 ? { maxWidth: 560 } : {};

  return (
    <div>
      {error ? (
        <div className="banner" style={{ marginBottom: 16 }}>
          {error}
        </div>
      ) : null}

      <div style={{ marginBottom: 20 }}>
        <input
          type="search"
          placeholder="Search markets..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{ marginBottom: 16 }}
        />

        {availableCategories.length > 1 && (
          <div className="category-tabs">
            {availableCategories.map((cat) => (
              <button
                key={cat}
                className={`category-tab ${activeCategory === cat ? "active" : ""}`}
                onClick={() => setActiveCategory(cat)}
              >
                {cat.charAt(0).toUpperCase() + cat.slice(1)}
                <span className="category-count">{categoryCounts[cat]}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {filtered.length === 0 ? (
        <p className="muted">no markets match your search</p>
      ) : (
        <div className="grid" style={gridStyle}>
          {primaries.map((m) => {
            const wildcards = filtered.filter((c) => c.parentConditionId === m.conditionId);
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
            ? filtered.map((m) => <MarketCard key={m.conditionId} market={m} />)
            : null}
        </div>
      )}
    </div>
  );
}
