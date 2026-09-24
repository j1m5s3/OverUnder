// One source for how a market resolves (ADR-0002, OU_FALLBACK_POLICY=attest):
// three AI agents must agree; otherwise, after 24h, agents whose research
// matches the result attest and it resolves 2-of-3, unless participant votes
// block it and force arbitration.
export const RESOLUTION_POLICY =
  "Three AI agents must agree to resolve. If they don't, after 24h the agents whose research matches the " +
  "result attest and the market resolves 2-of-3; participant votes can block that and force arbitration.";

export const RESOLUTION_POLICY_SHORT =
  "three ai agents must agree. if not, after 24h the agents whose research matches attest and it resolves " +
  "2-of-3 (participant votes can block and force arbitration).";
