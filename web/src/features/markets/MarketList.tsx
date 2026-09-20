"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/shared/api/client";
import { MarketCard } from "./MarketCard";
import { categorizeMarket, type Category } from "@/shared/utils/categorize";

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

  const matchesFilter = (m: Market) => {
    const matchesSearch = m.question.toLowerCase().includes(searchQuery.toLowerCase());
    if (!matchesSearch) return false;
    
    if (activeCategory === "all") return true;
    return categorizeMarket(m) === activeCategory;
  };

  const allPrimaries = markets.filter((m) => m.marketType === 0);
  const allWildcards = markets.filter((m) => m.marketType === 1);

  const visiblePrimaries = allPrimaries.filter((primary) => {
    if (matchesFilter(primary)) return true;
    const childWildcards = allWildcards.filter((w) => w.parentConditionId === primary.conditionId);
    return childWildcards.some(matchesFilter);
  });

  const searchFilteredMarkets = markets.filter((m) =>
    m.question.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const categoryCounts = {
    all: searchFilteredMarkets.length,
    sports: searchFilteredMarkets.filter((m) => categorizeMarket(m) === "sports").length,
    other: searchFilteredMarkets.filter((m) => categorizeMarket(m) === "other").length,
  };

  const availableCategories: Category[] = ["all"];
  if (categoryCounts.sports > 0) availableCategories.push("sports");
  if (categoryCounts.other > 0) availableCategories.push("other");

  const orphanWildcards = allWildcards.filter(
    (w) => matchesFilter(w) && !visiblePrimaries.some((p) => p.conditionId === w.parentConditionId)
  );

  const hasResults = visiblePrimaries.length > 0 || orphanWildcards.length > 0;
  const gridStyle = visiblePrimaries.length === 1 ? { maxWidth: 560 } : {};

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

        {searchQuery && (
          <div className="muted" style={{ marginTop: 12, fontSize: 12 }}>
            {hasResults ? `${visiblePrimaries.length + orphanWildcards.length} result${visiblePrimaries.length + orphanWildcards.length === 1 ? "" : "s"}` : "0 results"}
          </div>
        )}
      </div>

      {!hasResults ? (
        <p className="muted">
          {searchQuery
            ? "no markets match your search"
            : "no markets in this category"}
        </p>
      ) : (
        <div className="grid" style={gridStyle}>
          {visiblePrimaries.map((primary) => {
            const wildcards = allWildcards.filter(
              (w) => w.parentConditionId === primary.conditionId
            );
            const visibleWildcards = wildcards.filter(matchesFilter);
            return (
              <div key={primary.conditionId}>
                <MarketCard market={primary} childCount={wildcards.length} />
                {visibleWildcards.length > 0 && (
                  <div className="muted" style={{ marginTop: 8 }}>
                    Wildcards:{" "}
                    {visibleWildcards.map((c) => (
                      <Link key={c.conditionId} href={`/markets/${c.conditionId}`}>
                        {c.question}{" "}
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
          {orphanWildcards.map((m) => (
            <MarketCard key={m.conditionId} market={m} />
          ))}
        </div>
      )}
    </div>
  );
}
