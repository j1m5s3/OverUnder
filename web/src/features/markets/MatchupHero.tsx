"use client";

import { parseMatchup } from "@/shared/utils/categorize";

export function MatchupHero({ question }: { question: string }) {
  const matchup = parseMatchup(question);
  
  if (!matchup) return null;
  
  return (
    <div className="matchup-hero" style={{ 
      background: "var(--bg-elevated)", 
      border: "1px solid var(--border)", 
      borderRadius: "12px",
      padding: "20px",
      marginBottom: "16px",
      textAlign: "center"
    }}>
      <div style={{ 
        display: "flex", 
        alignItems: "center", 
        justifyContent: "center", 
        gap: "24px",
        flexWrap: "wrap"
      }}>
        <div style={{ flex: "1", minWidth: "120px", maxWidth: "200px" }}>
          <div style={{ fontSize: "20px", fontWeight: 600 }}>{matchup.teamA}</div>
        </div>
        <div style={{ 
          fontSize: "14px", 
          fontWeight: 700, 
          color: "var(--muted)",
          textTransform: "uppercase",
          letterSpacing: "0.1em"
        }}>
          VS
        </div>
        <div style={{ flex: "1", minWidth: "120px", maxWidth: "200px" }}>
          <div style={{ fontSize: "20px", fontWeight: 600 }}>{matchup.teamB}</div>
        </div>
      </div>
      {matchup.time && (
        <div className="muted" style={{ marginTop: "12px", fontSize: "13px" }}>
          {new Date(matchup.time * 1000).toLocaleString()}
        </div>
      )}
    </div>
  );
}
