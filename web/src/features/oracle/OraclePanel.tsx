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
  
  const attestations = status?.attestations || [];
  const emptySlots = Math.max(0, 3 - attestations.length);
  
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h3>Oracle</h3>
      <p className="muted">three ai agents. settles when they agree, or after a 24h vote.</p>
      <div className="muted">{status?.unanimous ? "unanimous — settling" : "awaiting consensus"}</div>
      {attestations.length > 0 && (
        <ul>
          {attestations.map((a: any) => (
            <li key={a.agent}>
              {a.agent}: {a.outcome === 0 ? "YES" : "NO"} — {a.summary}
            </li>
          ))}
        </ul>
      )}
      {emptySlots > 0 && (
        <div style={{ marginTop: attestations.length > 0 ? 8 : 12 }}>
          {Array.from({ length: emptySlots }).map((_, i) => (
            <div key={i} className="muted" style={{ marginBottom: 4 }}>
              agent {attestations.length + i + 1}: waiting...
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
