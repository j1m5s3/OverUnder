"use client";

import { parseMatchup } from "@/shared/utils/categorize";
import type { LiveScore } from "./eventHub";

export function MatchupHero({ question, score }: { question: string; score?: LiveScore | null }) {
  const matchup = parseMatchup(question);

  if (!matchup) return null;

  const home = score?.homeLabel || matchup.teamA;
  const away = score?.awayLabel || matchup.teamB;
  const showScore =
    score != null &&
    score.status !== "scheduled" &&
    (score.homeScore != null || score.awayScore != null);

  return (
    <div
      className="matchup-hero"
      style={{
        background: "var(--bg-elevated)",
        border: "1px solid var(--border)",
        borderRadius: "12px",
        padding: "20px",
        marginBottom: "16px",
        textAlign: "center",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: "24px",
          flexWrap: "wrap",
        }}
      >
        <div style={{ flex: "1", minWidth: "120px", maxWidth: "200px" }}>
          <div style={{ fontSize: "20px", fontWeight: 600 }}>{home}</div>
          {showScore && (
            <div style={{ fontSize: "32px", fontWeight: 700, marginTop: 4 }}>{score.homeScore ?? "—"}</div>
          )}
        </div>
        <div
          style={{
            fontSize: "14px",
            fontWeight: 700,
            color: "var(--muted)",
            textTransform: "uppercase",
            letterSpacing: "0.1em",
          }}
        >
          {showScore ? score.status.replace("_", " ") : "VS"}
        </div>
        <div style={{ flex: "1", minWidth: "120px", maxWidth: "200px" }}>
          <div style={{ fontSize: "20px", fontWeight: 600 }}>{away}</div>
          {showScore && (
            <div style={{ fontSize: "32px", fontWeight: 700, marginTop: 4 }}>{score.awayScore ?? "—"}</div>
          )}
        </div>
      </div>
      {score?.periodLabel && (
        <div className="muted" style={{ marginTop: "8px", fontSize: "13px" }}>
          {score.periodLabel}
        </div>
      )}
      {matchup.time && (
        <div className="muted" style={{ marginTop: "12px", fontSize: "13px" }}>
          {new Date(matchup.time * 1000).toLocaleString()}
        </div>
      )}
    </div>
  );
}
