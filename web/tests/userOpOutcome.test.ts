import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  REPOLL_BUDGET_MS,
  REPOLL_MAX_MS,
  classifyPolledOperation,
  classifyWaitResult,
  isDefinitiveFailure,
  repollDelayMs,
  repollUserOperation,
  type UserOpOutcome,
  type WaitData,
} from "../src/features/wallet/userOpOutcome.ts";

const HASH = "0x" + "ab".repeat(32);
const OTHER = "0x" + "cd".repeat(32);
const pending: UserOpOutcome = { state: "pending", hash: HASH };

describe("classifyWaitResult", () => {
  it("treats a transport error with a non-final status as unknown, not failed", () => {
    for (const data of [undefined, { userOpHash: HASH, status: "broadcast" }, { status: "pending" }]) {
      const next = classifyWaitResult(pending, HASH, { status: "error", data, error: new Error("Failed to fetch") });
      assert.deepEqual(next, { state: "unknown", hash: HASH, message: "Failed to fetch", gaveUp: false });
    }
  });

  it("reports a failed op as an error carrying the revert message", () => {
    const next = classifyWaitResult(pending, HASH, {
      status: "error",
      data: { userOpHash: HASH, status: "failed", receipts: [{ revert: { message: "market closed" } }] },
      error: new Error("market closed"),
    });
    assert.deepEqual(next, { state: "error", hash: HASH, message: "market closed" });
  });

  it("falls back to the receipt revert message when the hook error is missing", () => {
    const next = classifyWaitResult(pending, HASH, {
      status: "error",
      data: { status: "failed", receipts: [{ revert: { message: "slippage" } }] },
    });
    assert.deepEqual(next, { state: "error", hash: HASH, message: "slippage" });
  });

  it("reports a dropped op as an error", () => {
    const next = classifyWaitResult(pending, HASH, {
      status: "error",
      data: { userOpHash: HASH, status: "dropped" },
      error: new Error('User operation failed with status: "dropped"'),
    });
    assert.equal(next?.state, "error");
    assert.match((next as { message: string }).message, /dropped/);
  });

  it("accepts a later success for the same hash while unknown", () => {
    const unknown = classifyWaitResult(pending, HASH, { status: "error", error: new Error("503") })!;
    assert.equal(unknown.state, "unknown");
    const next = classifyWaitResult(unknown, HASH, {
      status: "success",
      data: { userOpHash: HASH, status: "complete", transactionHash: "0xtx" },
    });
    assert.deepEqual(next, { state: "success", hash: HASH, transactionHash: "0xtx" });
  });

  it("keeps an existing unknown (and its gaveUp flag) on repeated transport errors", () => {
    const current: UserOpOutcome = { state: "unknown", hash: HASH, message: "x", gaveUp: true };
    assert.equal(classifyWaitResult(current, HASH, { status: "error", error: new Error("again") }), null);
  });

  it("promotes unknown to error when a later poll shows the op failed", () => {
    const current: UserOpOutcome = { state: "unknown", hash: HASH, message: "x", gaveUp: false };
    const next = classifyWaitResult(current, HASH, { status: "error", data: { status: "failed" } });
    assert.deepEqual(next, { state: "error", hash: HASH, message: "user operation failed" });
  });

  it("ignores results for another hash and non-settled statuses", () => {
    assert.equal(classifyWaitResult(pending, HASH, { status: "success", data: { userOpHash: OTHER } }), null);
    assert.equal(classifyWaitResult(pending, HASH, { status: "idle" }), null);
    assert.equal(classifyWaitResult(pending, HASH, { status: "pending" }), null);
  });

  it("only failed and dropped are definitive", () => {
    assert.equal(isDefinitiveFailure({ status: "failed" }), true);
    assert.equal(isDefinitiveFailure({ status: "dropped" }), true);
    for (const status of [undefined, "pending", "signed", "broadcast", "complete"]) {
      assert.equal(isDefinitiveFailure({ status }), false);
    }
    assert.equal(isDefinitiveFailure(undefined), false);
  });
});

describe("classifyPolledOperation", () => {
  it("maps complete, failed, dropped and keeps others open", () => {
    assert.deepEqual(classifyPolledOperation(HASH, { status: "complete", transactionHash: "0xtx" }), {
      state: "success",
      hash: HASH,
      transactionHash: "0xtx",
    });
    assert.deepEqual(
      classifyPolledOperation(HASH, { status: "failed", receipts: [{ revert: { message: "market closed" } }] }),
      { state: "error", hash: HASH, message: "market closed" },
    );
    assert.deepEqual(classifyPolledOperation(HASH, { status: "dropped" }), {
      state: "error",
      hash: HASH,
      message: "user operation dropped",
    });
    assert.equal(classifyPolledOperation(HASH, { status: "broadcast" }), null);
    assert.equal(classifyPolledOperation(HASH, undefined), null);
    assert.equal(classifyPolledOperation(HASH, { userOpHash: OTHER, status: "complete" }), null);
  });
});

describe("repollUserOperation", () => {
  const noSleep = async () => {};

  it("backs off and caps the delay", () => {
    assert.equal(repollDelayMs(0), 2_000);
    assert.equal(repollDelayMs(1), 4_000);
    assert.equal(repollDelayMs(2), 8_000);
    assert.equal(repollDelayMs(10), REPOLL_MAX_MS);
  });

  it("keeps polling through thrown fetches until the op completes", async () => {
    const replies: Array<WaitData | Error> = [new Error("Failed to fetch"), { status: "broadcast" }, { status: "complete", transactionHash: "0xtx" }];
    let calls = 0;
    const result = await repollUserOperation(HASH, {
      fetch: async () => {
        const reply = replies[calls++];
        if (reply instanceof Error) throw reply;
        return reply;
      },
      sleep: noSleep,
      cancelled: () => false,
    });
    assert.equal(calls, 3);
    assert.deepEqual(result, { state: "success", hash: HASH, transactionHash: "0xtx" });
  });

  it("gives up (null) once the budget is spent", async () => {
    let slept = 0;
    let calls = 0;
    const result = await repollUserOperation(HASH, {
      fetch: async () => {
        calls++;
        throw new Error("503");
      },
      sleep: async (ms) => {
        slept += ms;
      },
      cancelled: () => false,
    });
    assert.equal(result, null);
    assert.equal(slept, REPOLL_BUDGET_MS);
    assert.ok(calls > 3);
  });

  it("stops when cancelled", async () => {
    let cancelled = false;
    let calls = 0;
    const result = await repollUserOperation(HASH, {
      fetch: async () => {
        calls++;
        cancelled = true;
        return { status: "complete" };
      },
      sleep: noSleep,
      cancelled: () => cancelled,
    });
    assert.equal(result, null);
    assert.equal(calls, 1);
  });
});
