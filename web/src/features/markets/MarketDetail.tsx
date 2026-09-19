"use client";

import { useEffect, useState } from "react";
import { api } from "@/shared/api/client";
import { AmmSwap } from "@/features/trade/AmmSwap";
import { OraclePanel } from "@/features/oracle/OraclePanel";
import type { Market } from "./MarketList";

export function MarketDetail({ conditionId }: { conditionId: string }) {
  const [market, setMarket] = useState<Market | null>(null);

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

  if (!market) return <p>Loading…</p>;
  return (
    <div>
      <div className="muted">{market.marketType === 0 ? "PRIMARY · AMM" : "WILDCARD · AMM"}</div>
      <h1>{market.question}</h1>
      <AmmSwap conditionId={market.conditionId} />
      <OraclePanel conditionId={market.conditionId} />
    </div>
  );
}
