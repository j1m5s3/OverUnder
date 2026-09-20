"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api } from "@/shared/api/client";
import { AmmSwap } from "@/features/trade/AmmSwap";
import { OraclePanel } from "@/features/oracle/OraclePanel";
import { MatchupHero } from "./MatchupHero";
import { MarketInfo } from "./MarketInfo";
import { isSportsMarket } from "@/shared/utils/categorize";
import type { MarketDetailData } from "./eventHub";

export function MarketDetail({ conditionId }: { conditionId: string }) {
  const [market, setMarket] = useState<MarketDetailData | null>(null);
  const searchParams = useSearchParams();
  const sideParam = searchParams.get("side");
  const initialSide = (sideParam === "yes" || sideParam === "no") ? sideParam : undefined;

  useEffect(() => {
    api(`/api/v1/markets/${encodeURIComponent(conditionId)}`)
      .then(setMarket)
      .catch(() =>
        setMarket({
          conditionId,
          question: conditionId.startsWith("0xdemo2")
            ? "Travis Kelce to fumble at least once?"
            : "Chiefs vs Broncos: Chiefs win?",
          marketType: conditionId.includes("demo2") ? 1 : 0,
          closeTime: Math.floor(Date.now() / 1000) + 3600,
          paused: false,
          resolved: false,
          suggestedProbability: 0.5,
          children: [],
        }),
      );
  }, [conditionId]);

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
          <AmmSwap key={initialSide ?? "yes"} conditionId={market.conditionId} initialSide={initialSide} />
        </div>
      </div>
      <div className="market-content">
        <MarketInfo market={market} />
        <OraclePanel conditionId={market.conditionId} />
      </div>
    </div>
  );
}
