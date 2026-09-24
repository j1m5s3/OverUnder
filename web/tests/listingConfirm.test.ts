import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  CONFIRM_MAX_ATTEMPTS,
  CONFIRM_MAX_SERVER_ERRORS,
  DEFAULT_MAX_PENDING,
  ListingSendGuard,
  classifyConfirmError,
  confirmBackoffMs,
  confirmWithRetry,
  eligibilityGate,
  normalizeEligibility,
  rejectedMessage,
  serverErrorDetail,
  type ConfirmErrorInfo,
  type ListingConfig,
} from "../src/features/listing/listing.ts";

const config: ListingConfig = { enabled: true, permissionless: true };

describe("classifyConfirmError", () => {
  const info = (status: number | null, detail: string): ConfirmErrorInfo => ({ status, detail });

  it("treats a 422 'listing rejected' as a terminal rejection with its reason", () => {
    assert.deepEqual(classifyConfirmError(info(422, "listing rejected: a similar market is already open")), {
      kind: "rejected",
      reason: "a similar market is already open",
    });
    assert.deepEqual(
      classifyConfirmError(info(422, "listing rejected: on-chain question differs from the prepared question")),
      { kind: "rejected", reason: "on-chain question differs from the prepared question" },
    );
    assert.deepEqual(classifyConfirmError(info(422, "listing rejected:")), {
      kind: "rejected",
      reason: "the listing did not pass review",
    });
  });

  it("does not treat a FastAPI validation 422 as a rejection", () => {
    assert.equal(classifyConfirmError(info(422, "Field required")).kind, "failed");
  });

  it("retries 'not on chain yet', 5xx and network errors", () => {
    assert.deepEqual(classifyConfirmError(info(409, "not on chain yet")), { kind: "retry", cause: "not-on-chain" });
    assert.deepEqual(classifyConfirmError(info(503, "chain unavailable")), { kind: "retry", cause: "server" });
    assert.deepEqual(classifyConfirmError(info(500, "Internal Server Error")), { kind: "retry", cause: "server" });
    assert.deepEqual(classifyConfirmError(info(null, "Failed to fetch")), { kind: "retry", cause: "server" });
  });

  it("does not auto-retry other client errors", () => {
    assert.deepEqual(classifyConfirmError(info(409, "criteria hash mismatch")), {
      kind: "failed",
      detail: "criteria hash mismatch",
    });
    assert.deepEqual(classifyConfirmError(info(403, "not the market creator")), {
      kind: "failed",
      detail: "not the market creator",
    });
    assert.deepEqual(classifyConfirmError(info(400, "")), { kind: "failed", detail: "HTTP 400" });
    const expired = classifyConfirmError(info(401, "invalid token"));
    assert.equal(expired.kind, "failed");
    assert.match(expired.kind === "failed" ? expired.detail : "", /session expired/);
  });
});

describe("serverErrorDetail", () => {
  it("keeps short details and hides proxy HTML", () => {
    assert.equal(serverErrorDetail({ status: 503, detail: "chain unavailable" }), "chain unavailable (503)");
    assert.equal(
      serverErrorDetail({ status: 502, detail: "<html><body>Bad Gateway</body></html>" }),
      "server error (502)",
    );
    assert.equal(serverErrorDetail({ status: 500, detail: "x".repeat(400) }), "server error (500)");
    assert.equal(serverErrorDetail({ status: null, detail: "Failed to fetch" }), "couldn't reach the API");
  });
});

type Step = { ok: string } | { status: number | null; detail: string };

function scripted(steps: Step[]) {
  const calls: number[] = [];
  const sleeps: number[] = [];
  const retries: Array<[number, string]> = [];
  let index = 0;
  const deps = {
    confirm: async () => {
      calls.push(index);
      const step = steps[Math.min(index, steps.length - 1)];
      index += 1;
      if ("ok" in step) return step.ok;
      throw Object.assign(new Error(`${step.status} ${step.detail}`), step);
    },
    errorInfo: (err: unknown) => {
      const e = err as { status: number | null; detail: string };
      return { status: e.status, detail: e.detail };
    },
    sleep: async (ms: number) => {
      sleeps.push(ms);
    },
    cancelled: () => false,
    onRetry: (attempt: number, cause: string) => {
      retries.push([attempt, cause]);
    },
  };
  return { deps, calls, sleeps, retries };
}

const NOT_ON_CHAIN: Step = { status: 409, detail: "not on chain yet" };
const UNAVAILABLE: Step = { status: 503, detail: "chain unavailable" };

describe("confirmWithRetry", () => {
  it("returns the market on the first success", async () => {
    const { deps, calls, sleeps } = scripted([{ ok: "market" }]);
    assert.deepEqual(await confirmWithRetry(deps), { state: "confirmed", market: "market" });
    assert.equal(calls.length, 1);
    assert.deepEqual(sleeps, []);
  });

  it("retries 'not on chain yet' with backoff until the market appears", async () => {
    const { deps, calls, sleeps, retries } = scripted([NOT_ON_CHAIN, NOT_ON_CHAIN, { ok: "market" }]);
    assert.deepEqual(await confirmWithRetry(deps), { state: "confirmed", market: "market" });
    assert.equal(calls.length, 3);
    assert.deepEqual(sleeps, [confirmBackoffMs(0), confirmBackoffMs(1)]);
    assert.deepEqual(retries, [
      [1, "not-on-chain"],
      [2, "not-on-chain"],
    ]);
  });

  it("stops at a 422 rejection without retrying", async () => {
    const { deps, calls, sleeps } = scripted([
      NOT_ON_CHAIN,
      { status: 422, detail: "listing rejected: question uses subjective wording that cannot be resolved" },
      { ok: "never" },
    ]);
    assert.deepEqual(await confirmWithRetry(deps), {
      state: "rejected",
      reason: "question uses subjective wording that cannot be resolved",
    });
    assert.equal(calls.length, 2);
    assert.equal(sleeps.length, 1);
  });

  it("recovers from a transient 5xx", async () => {
    const { deps, calls, retries } = scripted([UNAVAILABLE, { ok: "market" }]);
    assert.deepEqual(await confirmWithRetry(deps), { state: "confirmed", market: "market" });
    assert.equal(calls.length, 2);
    assert.deepEqual(retries, [[1, "server"]]);
  });

  it("gives up after a bounded number of 5xx and reports the last one", async () => {
    const { deps, calls, sleeps } = scripted([UNAVAILABLE]);
    assert.deepEqual(await confirmWithRetry(deps), { state: "failed", detail: "chain unavailable (503)" });
    assert.equal(calls.length, CONFIRM_MAX_SERVER_ERRORS);
    assert.equal(sleeps.length, CONFIRM_MAX_SERVER_ERRORS - 1);
  });

  it("gives up after the attempt budget when the listing never shows up", async () => {
    const { deps, calls, sleeps } = scripted([NOT_ON_CHAIN]);
    assert.deepEqual(await confirmWithRetry(deps), { state: "failed", detail: null });
    assert.equal(calls.length, CONFIRM_MAX_ATTEMPTS);
    // No sleep after the final attempt.
    assert.equal(sleeps.length, CONFIRM_MAX_ATTEMPTS - 1);
  });

  it("clears a stale 5xx detail once the API answers 'not on chain yet' again", async () => {
    const { deps } = scripted([UNAVAILABLE, NOT_ON_CHAIN]);
    assert.deepEqual(await confirmWithRetry({ ...deps, maxAttempts: 3 }), { state: "failed", detail: null });
  });

  it("fails immediately on a non-retryable error", async () => {
    const { deps, calls, sleeps } = scripted([{ status: 409, detail: "criteria hash mismatch" }]);
    assert.deepEqual(await confirmWithRetry(deps), { state: "failed", detail: "criteria hash mismatch" });
    assert.equal(calls.length, 1);
    assert.deepEqual(sleeps, []);
  });

  it("stops quietly once cancelled", async () => {
    const { deps, calls } = scripted([NOT_ON_CHAIN, { ok: "market" }]);
    let cancelled = false;
    const result = await confirmWithRetry({
      ...deps,
      cancelled: () => cancelled,
      sleep: async () => {
        cancelled = true;
      },
    });
    assert.deepEqual(result, { state: "cancelled" });
    assert.equal(calls.length, 1);
  });

  it("drops a result that lands after cancellation", async () => {
    let cancelled = false;
    const result = await confirmWithRetry({
      confirm: async () => {
        cancelled = true;
        return "market";
      },
      errorInfo: () => ({ status: null, detail: "" }),
      sleep: async () => {},
      cancelled: () => cancelled,
    });
    assert.deepEqual(result, { state: "cancelled" });
  });
});

describe("rejectedMessage", () => {
  it("states the reason and offers no retry", () => {
    const text = rejectedMessage("a similar market is already open");
    assert.match(text, /^Listing rejected: a similar market is already open\./);
    assert.doesNotMatch(text, /try again|retry/i);
  });
});

describe("eligibility", () => {
  const open = normalizeEligibility({
    enabled: true,
    allowed: true,
    permissionless: true,
    cooldownRemaining: 0,
    pending: 0,
    maxPending: 5,
  });

  it("normalizes the API payload and tolerates missing fields", () => {
    assert.deepEqual(open, {
      enabled: true,
      allowed: true,
      permissionless: true,
      cooldownRemaining: 0,
      pending: 0,
      maxPending: 5,
      reason: null,
    });
    const sparse = normalizeEligibility({ allowed: true, cooldownRemaining: -3, pending: "2" });
    assert.equal(sparse.enabled, true);
    assert.equal(sparse.cooldownRemaining, 0);
    assert.equal(sparse.pending, 2);
    assert.equal(sparse.maxPending, DEFAULT_MAX_PENDING);
    assert.equal(normalizeEligibility(null).allowed, false);
  });

  it("lets an eligible account list", () => {
    assert.equal(eligibilityGate(open, config), null);
  });

  it("explains every blocking reason", () => {
    assert.match(
      eligibilityGate({ ...open, enabled: false, reason: "factory has no listing support" }, config) ?? "",
      /isn't available right now: factory has no listing support/,
    );
    assert.match(
      eligibilityGate({ ...open, allowed: false, permissionless: false }, { permissionless: false }) ?? "",
      /invite-only/,
    );
    assert.match(eligibilityGate({ ...open, cooldownRemaining: 5400 }, config) ?? "", /in 1h 30m/);
    assert.match(eligibilityGate({ ...open, pending: 5 }, config) ?? "", /5 unconfirmed listings.*limit is 5/);
    assert.equal(eligibilityGate({ ...open, pending: 4 }, config), null);
  });
});

describe("ListingSendGuard", () => {
  const CID = "0xAbC0000000000000000000000000000000000000000000000000000000000001";

  it("lets only one submit start until it is released", () => {
    const guard = new ListingSendGuard();
    assert.equal(guard.begin(), true);
    assert.equal(guard.busy, true);
    assert.equal(guard.begin(), false);
    guard.release();
    assert.equal(guard.busy, false);
    assert.equal(guard.begin(), true);
  });

  it("never allows the same batch to be sent twice once it has a user operation hash", () => {
    const guard = new ListingSendGuard();
    assert.equal(guard.canSend(CID), false, "not before begin()");
    guard.begin();
    assert.equal(guard.canSend(CID), true);
    guard.markSent(CID, "0xop1");
    assert.equal(guard.canSend(CID), false);
    assert.equal(guard.canSend(CID.toLowerCase()), false);
    assert.equal(guard.hashFor(CID.toLowerCase()), "0xop1");
    guard.release();
    guard.begin();
    assert.equal(guard.canSend(CID), false, "still blocked after release");
    assert.equal(guard.canSend(`${CID.slice(0, -1)}2`), true, "a new prepare gets a new condition id");
  });
});
