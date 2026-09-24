import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { detailFailureKind, listedBy } from "../src/features/markets/eventHub.ts";

describe("detailFailureKind", () => {
  it("falls back to demo data only when the API is unreachable", () => {
    assert.equal(detailFailureKind(null), "demo");
  });

  it("shows not-found for a 404 (unknown id, or an unconfirmed or rejected listing)", () => {
    assert.equal(detailFailureKind(404), "not-found");
  });

  it("shows an error for any other HTTP failure instead of demo markets", () => {
    assert.equal(detailFailureKind(500), "error");
    assert.equal(detailFailureKind(503), "error");
    assert.equal(detailFailureKind(400), "error");
  });
});

describe("listedBy", () => {
  it("uses the detail's creator, then the listing's", () => {
    assert.equal(listedBy({ creator: "0xabc", listing: null }), "0xabc");
    assert.equal(
      listedBy({ creator: null, listing: { creator: "0xdef", status: "confirmed", seedUsdc: 1, criteriaHash: "0x" } }),
      "0xdef",
    );
    assert.equal(listedBy({}), null);
  });
});
