"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api } from "@/shared/api/client";
import { AmmSwap } from "@/features/trade/AmmSwap";
import { OraclePanel } from "@/features/oracle/OraclePanel";
import { MatchupHero } from "./MatchupHero";
import { MarketInfo } from "./MarketInfo";
import { isSportsMarket } from "@/shared/utils/categorize";
import type { Market } from "./MarketList";

export function MarketDetail({ conditionId }: { conditionId: string }) {
  const [market, setMarket] = useState<Market | null>(null);
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
      <div className="market-main">
        {showMatchup ? (
          <>
            <MatchupHero question={market.question} />
            <h1 style={{ fontSize: "24px", marginBottom: "16px" }}>{market.question}</h1>
          </>
        ) : (
          <h1>{market.question}</h1>
        )}
        <MarketInfo market={market} />
        <OraclePanel conditionId={market.conditionId} />
      </div>
      <div className="market-sidebar">
        <div className="sticky-ticket">
          <AmmSwap key={initialSide ?? "yes"} conditionId={market.conditionId} initialSide={initialSide} />
        </div>
      </div>
      <style jsx>{`
        .market-detail-layout {
          display: grid;
          grid-template-columns: 1fr;
          gap: 16px;
        }
        
        @media (min-width: 768px) {
          .market-detail-layout {
            grid-template-columns: 1fr 400px;
            gap: 24px;
          }
          
          .sticky-ticket {
            position: sticky;
            top: 24px;
          }
        }
        
        @media (max-width: 767px) {
          .market-detail-layout {
            display: flex;
            flex-direction: column;
          }
          
          .market-sidebar {
            order: -1;
          }
        }
      `}</style>
    </div>
  );
}
