// Pure view model for GET /oracle/{id}/status. No runtime imports so node tests
// load it directly.
//
// The API returns every attestation row in insertion order:
// [{agent, outcome, summary, evidenceHash, createdAt, kind}]. Rows with
// kind "research" are working notes (retries, low-confidence agreement, a failed
// consensus send); they can carry real 0/1 outcomes but never settle anything,
// so they are dropped here, as the server does for its own `unanimous` flag and
// as the mobile app does. A missing kind means a resolution report (rows written
// before the field existed). An agent can still have several resolution rows,
// and outcome 2 means the agent could not determine the result, so the panel
// shows each agent's latest resolution row and only calls it unanimous when
// three agents' latest rows agree on YES (0) or NO (1).

export type Attestation = {
  agent: string;
  outcome: number;
  summary?: string | null;
  evidenceHash?: string | null;
  createdAt?: string | null;
  kind?: string | null;
};

export type OracleVote = { voter: string; outcome: number; weight: number };

export type OracleStatus = {
  conditionId?: string;
  attestations?: Attestation[] | null;
  votes?: OracleVote[] | null;
  unanimous?: boolean;
};

export type OracleSummary = {
  latest: Attestation[];
  unanimous: boolean;
  consensusOutcome: 0 | 1 | null;
  voteCount: number;
};

export const REQUIRED_AGENTS = 3;

export function outcomeLabel(outcome: number): string {
  if (outcome === 0) return "YES";
  if (outcome === 1) return "NO";
  return "undetermined";
}

/** True for research rows; a missing or empty kind counts as a resolution report. */
export function isResearch(row: Attestation): boolean {
  return typeof row.kind === "string" && row.kind.trim().toLowerCase() === "research";
}

export function summarizeOracleStatus(status: OracleStatus | null | undefined): OracleSummary {
  const rows = Array.isArray(status?.attestations) ? status!.attestations! : [];
  const byAgent = new Map<string, Attestation>();
  for (const row of rows) {
    if (!row || typeof row.agent !== "string" || !row.agent) continue;
    if (isResearch(row)) continue;
    const key = row.agent.toLowerCase();
    // Insertion order: a later row replaces the agent's earlier one.
    byAgent.delete(key);
    byAgent.set(key, row);
  }
  const latest = Array.from(byAgent.values());
  const outcomes = new Set(latest.map((a) => a.outcome));
  const [only] = Array.from(outcomes);
  const unanimous = latest.length >= REQUIRED_AGENTS && outcomes.size === 1 && (only === 0 || only === 1);
  return {
    latest,
    unanimous,
    consensusOutcome: unanimous ? (only as 0 | 1) : null,
    voteCount: Array.isArray(status?.votes) ? status!.votes!.length : 0,
  };
}
