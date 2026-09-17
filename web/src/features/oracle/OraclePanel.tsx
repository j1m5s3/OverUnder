"use client";

import { useEffect, useState } from "react";
import { api } from "@/shared/api/client";

export function OraclePanel({ conditionId }: { conditionId: string }) {
  const [status, setStatus] = useState<any>(null);
  useEffect(() => {
    api(`/api/v1/oracle/${encodeURIComponent(conditionId)}/status`)
      .then(setStatus)
      .catch(() => setStatus({ unanimous: false, attestations: [] }));
  }, [conditionId]);
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h3>Oracle</h3>
      <p className="muted">Three AI agents. Unanimous settlement, else 24h majority + votes.</p>
      <div className="muted">{status?.unanimous ? "Unanimous — settling" : "Awaiting consensus"}</div>
      <ul>
        {(status?.attestations || []).map((a: any) => (
          <li key={a.agent}>
            {a.agent}: {a.outcome === 0 ? "YES" : "NO"} — {a.summary}
          </li>
        ))}
      </ul>
    </div>
  );
}
