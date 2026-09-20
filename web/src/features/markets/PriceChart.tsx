"use client";

import { useEffect, useState } from "react";
import { api } from "@/shared/api/client";

type PricePoint = {
  conditionId: string;
  ts: number;
  yesPriceMicros: number;
};

function pct(micros: number): string {
  return `${((micros / 1_000_000) * 100).toFixed(1)}%`;
}

export function PriceChart({ conditionId }: { conditionId: string }) {
  const [points, setPoints] = useState<PricePoint[] | null>(null);
  const [spotText, setSpotText] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setPoints(null);
    setSpotText(null);
    api(`/api/v1/markets/${encodeURIComponent(conditionId)}/history`)
      .then((rows: PricePoint[]) => {
        if (live) setPoints(rows);
      })
      .catch(() => {
        if (live) setPoints([]);
      });
    // Optional single live-quote marker only; never appended to history.
    api(`/api/v1/amm/${encodeURIComponent(conditionId)}/quote?buy_yes=true&usdc_in=1000000`)
      .then((q: any) => {
        if (!live) return;
        const out = Number(q?.tokensOut);
        if (Number.isFinite(out) && out > 0) {
          setSpotText(`live quote implies ~${pct(Math.round((1_000_000 / out) * 1_000_000))} yes (marker only)`);
        }
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [conditionId]);

  if (points === null) {
    return (
      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginBottom: 8 }}>Price history</h3>
        <div className="skeleton" style={{ height: 120 }}></div>
      </div>
    );
  }

  if (points.length === 0) {
    return (
      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginBottom: 8 }}>Price history</h3>
        <p className="muted" style={{ margin: 0 }}>
          No price history yet — prices appear after the first swap.
        </p>
        {spotText ? <p className="muted" style={{ marginTop: 8 }}>{spotText}</p> : null}
      </div>
    );
  }

  const W = 300;
  const H = 100;
  const PAD = 8;
  const n = points.length;
  const xs = (i: number) => (n === 1 ? W / 2 : PAD + (i * (W - PAD * 2)) / (n - 1));
  const ys = (micros: number) => H - PAD - (micros / 1_000_000) * (H - PAD * 2);
  const line = points.map((p, i) => `${xs(i).toFixed(1)},${ys(p.yesPriceMicros).toFixed(1)}`).join(" ");
  const last = points[n - 1];

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
        <h3 style={{ marginBottom: 8 }}>Price history</h3>
        <span className="yes" style={{ fontWeight: 600 }}>
          {pct(last.yesPriceMicros)} yes
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: 120 }} role="img" aria-label="yes price history">
        {n === 1 ? (
          <circle cx={xs(0)} cy={ys(points[0].yesPriceMicros)} r={3.5} fill="var(--yes)" />
        ) : (
          <polyline points={line} fill="none" stroke="var(--yes)" strokeWidth={2} strokeLinejoin="round" />
        )}
      </svg>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="muted">{new Date(points[0].ts * 1000).toLocaleDateString()}</span>
        <span className="muted">
          {n} point{n === 1 ? "" : "s"}
        </span>
        <span className="muted">{new Date(last.ts * 1000).toLocaleDateString()}</span>
      </div>
      {spotText ? <p className="muted" style={{ marginTop: 8 }}>{spotText}</p> : null}
    </div>
  );
}
