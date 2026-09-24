import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { parseRampAmount } from "../src/features/wallet/rampAmount.ts";

describe("parseRampAmount", () => {
  it("accepts dollar amounts the ramp API accepts", () => {
    assert.equal(parseRampAmount("100"), 100);
    assert.equal(parseRampAmount(" 25.5 "), 25.5);
    assert.equal(parseRampAmount("1"), 1);
    assert.equal(parseRampAmount("1000000"), 1_000_000);
  });

  it("rejects what /kyc/check or /ramps would 4xx", () => {
    for (const bad of ["", "0", "0.5", "-5", "abc", "1e3", "NaN", "Infinity", "1,000", "10.123", "1000000.01"]) {
      assert.equal(parseRampAmount(bad), null, bad);
    }
  });
});
