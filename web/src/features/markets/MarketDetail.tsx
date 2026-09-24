"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { api, apiErrorStatus } from "@/shared/api/client";
import { AmmSwap } from "@/features/trade/AmmSwap";
import { OraclePanel } from "@/features/oracle/OraclePanel";
import { MatchupHero } from "./MatchupHero";
import { MarketInfo } from "./MarketInfo";
import { PriceChart } from "./PriceChart";
import { isSportsMarket } from "@/shared/utils/categorize";
import {
  applyActiveMarketQuery,
  detailFailureKind,
  hubRoster,
  resolveActiveConditionId,
  yesProbability,
  type DetailFailure,
  type Market,
  type MarketDetailData,
} from "./eventHub";

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

function yesPct(market: Market): string {
  return `${Math.round(yesProbability(market) * 100)}%`;
}

export function MarketDetail({ conditionId }: { conditionId: string }) {
  const [market, setMarket] = useState<MarketDetailData | null>(null);
  const [failure, setFailure] = useState<Exclude<DetailFailure, "demo"> | null>(null);
  const [activeConditionId, setActiveConditionId] = useState(conditionId);
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const sideParam = searchParams.get("side");
  const mParam = searchParams.get("m");
  const initialSide = (sideParam === "yes" || sideParam === "no") ? sideParam : undefined;

  useEffect(() => {
    let live = true;
    setMarket(null);
    setFailure(null);
    api(`/api/v1/markets/${encodeURIComponent(conditionId)}`)
      .then((detail: MarketDetailData) => {
        if (live) setMarket(detail);
      })
      .catch((err) => {
        if (!live) return;
        const kind = detailFailureKind(apiErrorStatus(err));
        if (kind === "demo") setMarket(demoDetail(conditionId));
        else setFailure(kind);
      });
    return () => {
      live = false;
    };
  }, [conditionId]);

  useEffect(() => {
    if (!market || market.conditionId !== conditionId) return;
    setActiveConditionId(resolveActiveConditionId(market, mParam));
  }, [market, mParam, conditionId]);

  if (failure) {
    return (
      <div className="card" role="status">
        <h2 style={{ marginTop: 0 }}>{failure === "not-found" ? "Market not found" : "Couldn't load this market"}</h2>
        <p className="muted" style={{ marginBottom: 0 }}>
          {failure === "not-found"
            ? "If you just listed it, it shows up here once its listing is confirmed. Rejected listings are not shown."
            : "The API had a problem. Try again in a moment."}
        </p>
      </div>
    );
  }

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
  const hubRows = hubRoster(market);
  const activeMarket = hubRows.find((row) => row.conditionId === activeConditionId) ?? market;

  function selectRow(id: string) {
    if (!market) return;
    setActiveConditionId(id);
    const next = applyActiveMarketQuery(searchParams, market.conditionId, id);
    const query = next.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }

  return (
    <div className="market-detail-layout">
      <div className="market-header">
        {showMatchup ? (
          <>
            <MatchupHero question={market.question} score={market.score} />
            <h2 style={{ fontSize: "20px", fontWeight: 600, marginBottom: "16px" }}>{market.question}</h2>
          </>
        ) : (
          <h1>{market.question}</h1>
        )}
      </div>
      <div className="market-sidebar">
        <div className="sticky-ticket">
          <AmmSwap
            key={`${activeConditionId}-${initialSide ?? "yes"}`}
            conditionId={activeConditionId}
            initialSide={initialSide}
            question={activeMarket.question}
            closeTime={activeMarket.closeTime}
            resolved={activeMarket.resolved}
            tradingHaltsAt={activeMarket.tradingHaltsAt}
            tradingOpen={activeMarket.tradingOpen}
          />
        </div>
      </div>
      <div className="market-content">
        {market.children.length > 0 && (
          <div className="hub-board">
            <div className="muted">
              {hubRows.length} market{hubRows.length === 1 ? "" : "s"}
            </div>
            {hubRows.map((row) => {
              const selected = row.conditionId === activeConditionId;
              return (
                <button
                  key={row.conditionId}
                  type="button"
                  className={selected ? "hub-row selected" : "hub-row"}
                  onClick={() => selectRow(row.conditionId)}
                >
                  <span>{row.question}</span>
                  <span className="yes">{yesPct(row)}</span>
                </button>
              );
            })}
          </div>
        )}
        <PriceChart key={`chart-${activeConditionId}`} conditionId={activeConditionId} />
        <MarketInfo market={market} facts={market.facts} />
        <OraclePanel conditionId={market.conditionId} />
      </div>
    </div>
  );
}
