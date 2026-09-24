import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { outcomeLabel, summarizeOracleStatus } from "../src/features/oracle/oracleStatus.ts";

const row = (agent: string, outcome: number, summary = "") => ({
  agent,
  outcome,
  summary,
  evidenceHash: "0x",
  createdAt: "2026-09-23T00:00:00Z",
});

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

  it("keeps each agent's latest row so repeated research rows do not duplicate agents", () => {
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

    // The server's flag counts every row; the panel does not trust it alone.
    const stale = summarizeOracleStatus({ unanimous: true, attestations: [row("a", 1), row("a", 1), row("a", 1)] });
    assert.equal(stale.unanimous, false);
  });

  it("counts participant votes", () => {
    const summary = summarizeOracleStatus({ votes: [{ voter: "0x1", outcome: 0, weight: 5 }] });
    assert.equal(summary.voteCount, 1);
  });
});
