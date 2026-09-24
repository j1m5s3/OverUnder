import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  closedLabel,
  closedReasonFromError,
  effectiveClosedReason,
  haltsAtFor,
  msUntilHalt,
  quoteErrorMessage,
  tradingState,
} from "../src/features/trade/tradingWindow.ts";

const NOW = 1_800_000_000;

describe("tradingState", () => {
  it("is closed with reason resolved for a resolved market", () => {
    const state = tradingState({ closeTime: NOW + 3600, resolved: true, tradingHaltsAt: null }, NOW, false);
    assert.deepEqual(state, { closed: true, reason: "resolved", haltsAt: null });
  });

  it("resolved wins even before the halt time", () => {
    const state = tradingState({ resolved: true, tradingHaltsAt: NOW + 60 }, NOW, true);
    assert.equal(state.closed, true);
    assert.equal(state.reason, "resolved");
    assert.equal(state.haltsAt, NOW + 60);
  });

  it("closes once the server tradingHaltsAt is in the past", () => {
    const state = tradingState({ closeTime: NOW - 10, resolved: false, tradingHaltsAt: NOW - 10 }, NOW, false);
    assert.deepEqual(state, { closed: true, reason: "closed", haltsAt: NOW - 10 });
  });

  it("closes exactly at tradingHaltsAt", () => {
    assert.equal(tradingState({ tradingHaltsAt: NOW }, NOW, false).closed, true);
    assert.equal(tradingState({ tradingHaltsAt: NOW }, NOW - 1, false).closed, false);
  });

  it("stays open while the server tradingHaltsAt is in the future", () => {
    const state = tradingState({ closeTime: NOW + 600, resolved: false, tradingHaltsAt: NOW + 600 }, NOW, false);
    assert.deepEqual(state, { closed: false, reason: null, haltsAt: NOW + 600 });
  });

  it("server null overrides the env fallback even after closeTime", () => {
    const state = tradingState({ closeTime: NOW - 3600, resolved: false, tradingHaltsAt: null }, NOW, true);
    assert.deepEqual(state, { closed: false, reason: null, haltsAt: null });
  });

  it("uses closeTime when the server omits tradingHaltsAt and the fallback is on", () => {
    const past = tradingState({ closeTime: NOW - 1, resolved: false }, NOW, true);
    assert.deepEqual(past, { closed: true, reason: "closed", haltsAt: NOW - 1 });
    const future = tradingState({ closeTime: NOW + 1, resolved: false }, NOW, true);
    assert.deepEqual(future, { closed: false, reason: null, haltsAt: NOW + 1 });
  });

  it("ignores closeTime when the server omits tradingHaltsAt and the fallback is off", () => {
    const state = tradingState({ closeTime: NOW - 3600, resolved: false }, NOW, false);
    assert.deepEqual(state, { closed: false, reason: null, haltsAt: null });
  });

  it("never closes on a zero closeTime", () => {
    assert.equal(tradingState({ closeTime: 0, resolved: false }, NOW, true).closed, false);
    assert.equal(tradingState({ closeTime: 0, tradingHaltsAt: 0 }, NOW, true).closed, false);
    assert.equal(haltsAtFor({ closeTime: 0 }, true), null);
  });

  it("treats tradingOpen false from the server as closed", () => {
    const state = tradingState({ closeTime: NOW + 3600, tradingHaltsAt: NOW + 3600, tradingOpen: false }, NOW, false);
    assert.equal(state.closed, true);
    assert.equal(state.reason, "closed");
  });

  it("tradingOpen true does not reopen a past halt", () => {
    assert.equal(tradingState({ tradingHaltsAt: NOW - 5, tradingOpen: true }, NOW, false).closed, true);
  });
});

describe("msUntilHalt", () => {
  it("returns the padded delay for a future halt", () => {
    assert.equal(msUntilHalt(NOW + 10, NOW * 1000), 10_250);
    assert.equal(msUntilHalt(NOW + 10, NOW * 1000, 0), 10_000);
  });

  it("returns null for a past, current or missing halt", () => {
    assert.equal(msUntilHalt(NOW, NOW * 1000), null);
    assert.equal(msUntilHalt(NOW - 1, NOW * 1000), null);
    assert.equal(msUntilHalt(null, NOW * 1000), null);
  });

  it("returns null when the delay would overflow setTimeout", () => {
    assert.equal(msUntilHalt(NOW + 30 * 86_400, NOW * 1000), null);
    assert.notEqual(msUntilHalt(NOW + 20 * 86_400, NOW * 1000), null);
  });
});

describe("closedReasonFromError", () => {
  it("maps a quote 409 for a closed market", () => {
    assert.equal(closedReasonFromError('409 {"detail":"market closed"}'), "closed");
  });

  it("maps a quote 409 for a resolved market", () => {
    assert.equal(closedReasonFromError('409 {"detail":"market resolved"}'), "resolved");
  });

  it("treats any other 409 as closed", () => {
    assert.equal(closedReasonFromError("409 conflict"), "closed");
  });

  it("maps an on-chain revert reason from the bundler", () => {
    assert.equal(
      closedReasonFromError("UserOperation reverted during simulation with reason: execution reverted: market closed"),
      "closed",
    );
  });

  it("maps a quote 409 for an unconfirmed user listing to unlisted", () => {
    assert.equal(closedReasonFromError('409 {"detail":"listing not confirmed"}'), "unlisted");
  });

  it("leaves unrelated errors alone", () => {
    assert.equal(closedReasonFromError("503 MarketAMM address not configured"), null);
    assert.equal(closedReasonFromError("execution reverted: slippage"), null);
    assert.equal(closedReasonFromError(""), null);
    assert.equal(closedReasonFromError(undefined), null);
    assert.equal(closedReasonFromError("1409 things"), null);
  });
});

describe("closedLabel", () => {
  it("labels resolved and closed markets", () => {
    assert.equal(closedLabel("resolved"), "Market resolved");
    assert.equal(closedLabel("closed"), "Trading closed");
    assert.equal(closedLabel("unlisted"), "Not open for trading");
    assert.equal(closedLabel(null), "Trading closed");
  });
});

describe("effectiveClosedReason", () => {
  it("prefers resolved, then a server unlisted verdict, then closed", () => {
    assert.equal(effectiveClosedReason("unlisted", "resolved"), "resolved");
    assert.equal(effectiveClosedReason("resolved", null), "resolved");
    assert.equal(effectiveClosedReason("unlisted", "closed"), "unlisted");
    assert.equal(effectiveClosedReason("closed", null), "closed");
    assert.equal(effectiveClosedReason(null, "closed"), "closed");
    assert.equal(effectiveClosedReason(null, null), "closed");
  });
});

describe("quoteErrorMessage", () => {
  it("turns quote failures into short copy instead of raw status text", () => {
    assert.equal(quoteErrorMessage(422, "no pool"), "can't quote this trade: no pool");
    assert.equal(quoteErrorMessage(422, ""), "can't quote this trade");
    assert.equal(quoteErrorMessage(400, "invalid quote arguments"), "enter a valid amount");
    assert.equal(quoteErrorMessage(503, "RPC unavailable"), "quotes are unavailable right now, try again shortly");
    assert.equal(quoteErrorMessage(503, "MarketAMM address not configured"), "quotes are unavailable right now, try again shortly");
    assert.equal(quoteErrorMessage(null, "Failed to fetch"), "couldn't reach the api for a quote");
    assert.equal(quoteErrorMessage(418, "teapot"), "teapot");
    assert.equal(quoteErrorMessage(418, ""), "quote failed (418)");
  });
});
