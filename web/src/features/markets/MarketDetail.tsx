"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api } from "@/shared/api/client";
import { AmmSwap } from "@/features/trade/AmmSwap";
import { OraclePanel } from "@/features/oracle/OraclePanel";
import { MatchupHero } from "./MatchupHero";
import { MarketInfo } from "./MarketInfo";
import { isSportsMarket } from "@/shared/utils/categorize";
import { resolveActiveConditionId, type Market, type MarketDetailData } from "./eventHub";

const DEMO_KELCE: Market = {
  conditionId: "0xdemo2",
  parentConditionId: "0xdemo1",
  question: "Travis Kelce to fumble at least once?",
  marketType: 1,
  closeTime: Math.floor(Date.now() / 1000) + 86400,
  paused: false,
  resolved: false,
  suggestedProbability: 0.22,
};

function demoDetail(conditionId: string): MarketDetailData {
  const isKelce = conditionId.startsWith("0xdemo2") || conditionId.includes("demo2");
  return {
    conditionId,
    question: isKelce ? DEMO_KELCE.question : "Chiefs vs Broncos: Chiefs win?",
    marketType: isKelce ? 1 : 0,
    closeTime: Math.floor(Date.now() / 1000) + 3600,
    paused: false,
    resolved: false,
    suggestedProbability: isKelce ? 0.22 : 0.58,
    children: conditionId === "0xdemo1" ? [{ ...DEMO_KELCE }] : [],
  };
}

function yesPct(probability: number): string {
  return `${Math.round((probability || 0.5) * 100)}%`;
}

export function MarketDetail({ conditionId }: { conditionId: string }) {
  const [market, setMarket] = useState<MarketDetailData | null>(null);
  const [activeConditionId, setActiveConditionId] = useState(conditionId);
  const searchParams = useSearchParams();
  const sideParam = searchParams.get("side");
  const mParam = searchParams.get("m");
  const initialSide = (sideParam === "yes" || sideParam === "no") ? sideParam : undefined;

  useEffect(() => {
    api(`/api/v1/markets/${encodeURIComponent(conditionId)}`)
      .then(setMarket)
      .catch(() => setMarket(demoDetail(conditionId)));
  }, [conditionId]);

  useEffect(() => {
    if (!market || market.conditionId !== conditionId) return;
    setActiveConditionId(resolveActiveConditionId(market, mParam));
  }, [market, mParam, conditionId]);

  if (!market) {
    return (
      <div>
        <div className="skeleton" style={{ width: 120, height: 16, marginBottom: 8 }}></div>
        <div className="skeleton" style={{ width: "80%", height: 32, marginBottom: 24 }}></div>
        <div className="card skeleton" style={{ height: 200 }}></div>
      </div>
    );
  }

  const showMatchup = isSportsMarket(market.question);
  const hubRows: Market[] = [market, ...market.children];

  return (
    <div className="market-detail-layout">
      <div className="market-header">
        {showMatchup ? (
          <>
            <MatchupHero question={market.question} />
            <h2 style={{ fontSize: "20px", fontWeight: 600, marginBottom: "16px" }}>{market.question}</h2>
          </>
        ) : (
          <h1>{market.question}</h1>
        )}
      </div>
      <div className="market-sidebar">
        <div className="sticky-ticket">
          <AmmSwap key={activeConditionId} conditionId={activeConditionId} initialSide={initialSide} />
        </div>
      </div>
      <div className="market-content">
        {market.children.length > 0 && (
          <div className="hub-board">
            <div className="muted">
              {market.children.length} market{market.children.length === 1 ? "" : "s"}
            </div>
            {hubRows.map((row) => {
              const selected = row.conditionId === activeConditionId;
              return (
                <button
                  key={row.conditionId}
                  type="button"
                  className={selected ? "hub-row selected" : "hub-row"}
                  onClick={() => setActiveConditionId(row.conditionId)}
                >
                  <span>{row.question}</span>
                  <span className="yes">{yesPct(row.suggestedProbability)}</span>
                </button>
              );
            })}
          </div>
        )}
        <MarketInfo market={market} />
        <OraclePanel conditionId={market.conditionId} />
      </div>
    </div>
  );
}
