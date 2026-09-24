"use client";

import { useEffect, useState } from "react";
import { api } from "@/shared/api/client";
import { RESOLUTION_POLICY_SHORT } from "./resolutionPolicy";
import { REQUIRED_AGENTS, outcomeLabel, summarizeOracleStatus, type OracleStatus } from "./oracleStatus";

export function OraclePanel({ conditionId }: { conditionId: string }) {
  const [status, setStatus] = useState<OracleStatus | null>(null);
  useEffect(() => {
    let live = true;
    api(`/api/v1/oracle/${encodeURIComponent(conditionId)}/status`)
      .then((s: OracleStatus) => {
        if (live) setStatus(s);
      })
      .catch(() => {
        if (live) setStatus({ unanimous: false, attestations: [] });
      });
    return () => {
      live = false;
    };
  }, [conditionId]);

  const { latest, unanimous, consensusOutcome, voteCount } = summarizeOracleStatus(status);
  const emptySlots = Math.max(0, REQUIRED_AGENTS - latest.length);

  return (
    <details className="card" style={{ marginTop: 16 }}>
      <summary style={{ cursor: "pointer", userSelect: "none" }}>how this resolves</summary>
      <div style={{ marginTop: 12 }}>
        <p className="muted">{RESOLUTION_POLICY_SHORT}</p>
        <div className="muted">
          {unanimous && consensusOutcome !== null
            ? `unanimous ${outcomeLabel(consensusOutcome)} — settling`
            : "awaiting consensus"}
        </div>
        {latest.length > 0 && (
          <ul>
            {latest.map((a) => (
              <li key={a.agent.toLowerCase()}>
                {a.agent}: {outcomeLabel(a.outcome)}
                {a.summary ? ` — ${a.summary}` : ""}
              </li>
            ))}
          </ul>
        )}
        {emptySlots > 0 && (
          <div style={{ marginTop: latest.length > 0 ? 8 : 12 }}>
            {Array.from({ length: emptySlots }).map((_, i) => (
              <div key={i} className="muted" style={{ marginBottom: 4 }}>
                agent {latest.length + i + 1}: waiting...
              </div>
            ))}
          </div>
        )}
        {voteCount > 0 && (
          <div className="muted" style={{ marginTop: 8 }}>
            {voteCount} participant vote{voteCount === 1 ? "" : "s"}
          </div>
        )}
      </div>
    </details>
  );
}
