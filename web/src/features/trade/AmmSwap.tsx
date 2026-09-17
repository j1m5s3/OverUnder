"use client";

import { useState } from "react";
import { api } from "@/shared/api/client";

export function AmmSwap({ conditionId }: { conditionId: string }) {
  const [usdcIn, setUsdcIn] = useState("1000000");
  const [quote, setQuote] = useState<any>(null);
  const [buyYes, setBuyYes] = useState(true);

  async function refresh() {
    const q = await api(
      `/api/v1/amm/${encodeURIComponent(conditionId)}/quote?buy_yes=${buyYes}&usdc_in=${usdcIn}`,
    );
    setQuote(q);
  }

  return (
    <div className="card">
      <h3>AMM swap</h3>
      <p className="muted">CPMM on YES/NO. 100 bps fee (50 vault / 50 LPs).</p>
      <div className="row">
        <button className={buyYes ? "btn yes" : "btn ghost"} onClick={() => setBuyYes(true)}>
          Buy Yes
        </button>
        <button className={!buyYes ? "btn no" : "btn ghost"} onClick={() => setBuyYes(false)}>
          Buy No
        </button>
      </div>
      <label className="muted">USDC in (6 decimals)</label>
      <input value={usdcIn} onChange={(e) => setUsdcIn(e.target.value)} />
      <button className="btn" style={{ marginTop: 12 }} onClick={refresh}>
        Quote
      </button>
      {quote ? <pre className="muted">{JSON.stringify(quote, null, 2)}</pre> : null}
    </div>
  );
}
