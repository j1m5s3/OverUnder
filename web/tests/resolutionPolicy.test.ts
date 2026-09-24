import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { RESOLUTION_POLICY, RESOLUTION_POLICY_SHORT } from "../src/features/oracle/resolutionPolicy.ts";

describe("resolution policy copy", () => {
  it("describes the attest fallback, not a 24h vote", () => {
    for (const text of [RESOLUTION_POLICY, RESOLUTION_POLICY_SHORT]) {
      assert.doesNotMatch(text, /24h vote/i);
      assert.match(text, /three ai agents must agree/i);
      assert.match(text, /24h/);
      assert.match(text, /attest/);
      assert.match(text, /2-of-3/);
      assert.match(text, /votes can block/i);
      assert.match(text, /arbitration/);
    }
  });
});
