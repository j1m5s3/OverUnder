"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/shared/api/client";
import { MarketCard } from "./MarketCard";
import { selectHubs, type EventCard } from "./eventHub";
import type { Category } from "@/shared/utils/categorize";

const DEMO_CARDS: EventCard[] = [
  {
    primary: {
      conditionId: "0xdemo1",
      question: "Chiefs vs Broncos: Chiefs win?",
      marketType: 0,
      closeTime: Math.floor(Date.now() / 1000) + 86400,
      paused: false,
      resolved: false,
      suggestedProbability: 0.58,
    },
    children: [
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
    ],
  },
];

export function MarketList() {
  const [cards, setCards] = useState<EventCard[] | null>(null);
  const [error, setError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [activeCategory, setActiveCategory] = useState<Category>("all");

  useEffect(() => {
    api("/api/v1/markets")
      .then(setCards)
      .catch(() => {
        setError("couldn't reach the api — showing demo markets.");
        setCards(DEMO_CARDS);
      });
  }, []);

  if (cards === null) {
    return (
      <div className="grid">
        <div className="card skeleton" style={{ height: 140 }}></div>
        <div className="card skeleton" style={{ height: 140 }}></div>
        <div className="card skeleton" style={{ height: 140 }}></div>
      </div>
    );
  }

  if (cards.length === 0) {
    return <p className="muted">no markets yet</p>;
  }

  const hubs = selectHubs(cards, { searchQuery, category: activeCategory });
  const allView = selectHubs(cards, { searchQuery, category: "all" });
  const sportsView = selectHubs(cards, { searchQuery, category: "sports" });
  const otherView = selectHubs(cards, { searchQuery, category: "other" });

  const categoryCounts = {
    all: allView.length,
    sports: sportsView.length,
    other: otherView.length,
  };

  const availableCategories: Category[] = ["all"];
  if (categoryCounts.sports > 0) availableCategories.push("sports");
  if (categoryCounts.other > 0) availableCategories.push("other");

  const hasResults = hubs.length > 0;
  const gridStyle = hubs.length === 1 ? { maxWidth: 560 } : {};
  const q = searchQuery.toLowerCase();

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
            {hasResults
              ? `${hubs.length} result${hubs.length === 1 ? "" : "s"}`
              : "0 results"}
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
          {hubs.map((card) => {
            const chipChildren = q
              ? card.children.filter((child) => child.question.toLowerCase().includes(q))
              : card.children;
            return (
              <div key={card.primary.conditionId}>
                <MarketCard market={card.primary} childCount={card.children.length} />
                {chipChildren.length > 0 && (
                  <div className="muted" style={{ marginTop: 8 }}>
                    props:{" "}
                    {chipChildren.map((child) => (
                      <Link
                        key={child.conditionId}
                        href={`/markets/${encodeURIComponent(card.primary.conditionId)}?m=${encodeURIComponent(child.conditionId)}`}
                      >
                        {child.question}{" "}
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
