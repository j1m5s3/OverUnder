import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { isResearch, outcomeLabel, summarizeOracleStatus } from "../src/features/oracle/oracleStatus.ts";

const row = (agent: string, outcome: number, summary = "", kind?: string) => ({
  agent,
  outcome,
  summary,
  evidenceHash: "0x",
  createdAt: "2026-09-23T00:00:00Z",
  ...(kind === undefined ? {} : { kind }),
});
const research = (agent: string, outcome: number, summary = "research") => row(agent, outcome, summary, "research");

describe("outcomeLabel", () => {
  it("labels YES, NO and undetermined", () => {
    assert.equal(outcomeLabel(0), "YES");
    assert.equal(outcomeLabel(1), "NO");
    assert.equal(outcomeLabel(2), "undetermined");
  });
});

describe("summarizeOracleStatus", () => {
  it("handles a missing or empty status", () => {
    for (const status of [null, undefined, {}, { attestations: null }]) {
      assert.deepEqual(summarizeOracleStatus(status), {
        latest: [],
        unanimous: false,
        consensusOutcome: null,
        voteCount: 0,
      });
    }
  });

  it("keeps each agent's latest row so repeated reports do not duplicate agents", () => {
    const summary = summarizeOracleStatus({
      attestations: [row("0xA", 2, "research: no source yet"), row("0xb", 0), row("0xa", 0, "final")],
    });
    assert.deepEqual(
      summary.latest.map((a) => [a.agent, a.outcome]),
      [
        ["0xb", 0],
        ["0xa", 0],
      ],
    );
    assert.equal(summary.unanimous, false);
  });

  it("is unanimous only when three agents' latest rows agree on YES or NO", () => {
    const yes = summarizeOracleStatus({ attestations: [row("a", 1), row("a", 0), row("b", 0), row("c", 0)] });
    assert.equal(yes.unanimous, true);
    assert.equal(yes.consensusOutcome, 0);

    const split = summarizeOracleStatus({ attestations: [row("a", 0), row("b", 0), row("c", 1)] });
    assert.equal(split.unanimous, false);

    const undetermined = summarizeOracleStatus({ attestations: [row("a", 2), row("b", 2), row("c", 2)] });
    assert.equal(undetermined.unanimous, false);
    assert.equal(undetermined.consensusOutcome, null);

    // The panel recomputes unanimity per agent instead of trusting the server's flag alone.
    const stale = summarizeOracleStatus({ unanimous: true, attestations: [row("a", 1), row("a", 1), row("a", 1)] });
    assert.equal(stale.unanimous, false);
  });

  it("ignores research rows, even three that agree on YES with low confidence", () => {
    // oracles/resolve persists agreeing-but-low-confidence runs and failed consensus sends as research.
    const lowConfidence = summarizeOracleStatus({
      unanimous: false,
      attestations: [research("a", 0, "low confidence"), research("b", 0, "low confidence"), research("c", 0, "low confidence")],
    });
    assert.equal(lowConfidence.unanimous, false);
    assert.equal(lowConfidence.consensusOutcome, null);
    assert.deepEqual(lowConfidence.latest, []);

    const txError = summarizeOracleStatus({ attestations: [research("a", 1, "tx error"), research("b", 1), research("c", 1)] });
    assert.equal(txError.unanimous, false);

    // A research row after a resolution report does not replace it as the agent's latest row.
    const mixed = summarizeOracleStatus({
      attestations: [row("a", 0, "final", "resolution"), row("b", 0), row("c", 0, "", "RESOLUTION"), research("a", 1)],
    });
    assert.equal(mixed.unanimous, true);
    assert.equal(mixed.consensusOutcome, 0);
    assert.deepEqual(
      mixed.latest.map((a) => [a.agent, a.outcome]),
      [
        ["a", 0],
        ["b", 0],
        ["c", 0],
      ],
    );

    // Two resolution reports plus one research row are not three agents.
    const partial = summarizeOracleStatus({ attestations: [row("a", 1), row("b", 1), research("c", 1)] });
    assert.equal(partial.unanimous, false);
    assert.equal(partial.latest.length, 2);
  });

  it("treats a missing, null or unknown kind as a resolution report", () => {
    const legacy = summarizeOracleStatus({
      attestations: [row("a", 1), { ...row("b", 1), kind: null }, row("c", 1, "", "")],
    });
    assert.equal(legacy.unanimous, true);
    assert.equal(legacy.consensusOutcome, 1);
    assert.equal(isResearch(row("a", 0, "", " Research ")), true);
    assert.equal(isResearch(row("a", 0)), false);
  });

  it("counts participant votes", () => {
    const summary = summarizeOracleStatus({ votes: [{ voter: "0x1", outcome: 0, weight: 5 }] });
    assert.equal(summary.voteCount, 1);
  });
});
