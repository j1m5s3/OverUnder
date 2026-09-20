export type Category = "all" | "sports" | "other";

export function categorizeMarket(input: string | { question: string }): Category {
  const q = typeof input === "string" ? input.toLowerCase() : input.question.toLowerCase();
  
  const sportsTokens = [
    "nfl", "nba", "mlb", "nhl", "mls", "ncaa", "epl", "uefa",
    "soccer", "football", "basketball", "baseball", "hockey", "tennis",
    "golf", "boxing", "ufc", "mma", "nascar", "f1", "formula 1",
    "chiefs", "broncos", "yankees", "red sox", "dodgers", "mets",
    "lakers", "celtics", "warriors", "knicks",
    "cowboys", "patriots", "packers", "seahawks", "chargers",
    "steelers",
    "touchdown", "fumble", "home run", "strikeout", "grand slam",
    "three-pointer", "slam dunk", "hat trick"
  ];
  
  const shortTokens = ["f1", "ufc", "mma", "nfl", "nba", "mlb", "nhl", "mls", "epl"];
  
  for (const token of sportsTokens) {
    const pattern = shortTokens.includes(token)
      ? new RegExp(`\\b${token}\\b`, "i")
      : new RegExp(`\\b${token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i");
    if (pattern.test(q)) {
      return "sports";
    }
  }
  
  return "other";
}

export function isSportsMarket(question: string): boolean {
  return categorizeMarket(question) === "sports";
}

export type MatchupData = {
  teamA: string;
  teamB: string;
  time?: number;
} | null;

export function parseMatchup(question: string): MatchupData {
  if (!isSportsMarket(question)) return null;
  
  const vsPattern = /^([^:?]+?)\s+vs\.?\s+([^:?]+?):\s*/i;
  const match = question.match(vsPattern);
  
  if (!match) return null;
  
  const teamA = match[1].trim();
  const teamB = match[2].trim();
  
  return { teamA, teamB };
}
