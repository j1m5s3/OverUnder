import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  SUBMIT_MARGIN_SECONDS,
  charCount,
  closeTimeBounds,
  confirmBackoffMs,
  confirmFailedMessage,
  datetimeLocalToUnix,
  defaultCloseTime,
  formatDuration,
  hasSubjectiveWording,
  isNotOnChainYet,
  listingTotals,
  microsToUsdc,
  toCdpCalls,
  unixToDatetimeLocal,
  usdcToMicros,
  utf8Bytes,
  validateListing,
  type ListingConfig,
  type ListingInput,
} from "../src/features/listing/listing.ts";

const NOW = 1_800_000_000;
const USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e";
const FACTORY = "0x1111111111111111111111111111111111111111";

const config: ListingConfig = {
  enabled: true,
  permissionless: true,
  factory: FACTORY,
  usdc: USDC,
  oracle: "0x2222222222222222222222222222222222222222",
  chainId: 84532,
  minSeedUsdc: 10_000_000,
  listingFeeUsdc: 1_000_000,
  minLeadSeconds: 3600,
  maxHorizonSeconds: 90 * 86_400,
  cooldownSeconds: 3600,
};

function input(partial: Partial<ListingInput> = {}): ListingInput {
  return {
    question: "Will the city approve the stadium budget by March 31?",
    resolutionCriteria: "YES if the council minutes show the budget passed by March 31; NO otherwise.",
    closeTime: NOW + 7 * 86_400,
    seedUsdc: 25_000_000,
    ...partial,
  };
}

describe("utf8Bytes / charCount", () => {
  it("counts UTF-8 bytes, not UTF-16 units", () => {
    assert.equal(utf8Bytes("abc"), 3);
    assert.equal(utf8Bytes("é"), 2);
    assert.equal(utf8Bytes("€"), 3);
    assert.equal(utf8Bytes("🏈"), 4);
  });

  it("counts code points like Python len()", () => {
    assert.equal(charCount("🏈🏈"), 2);
    assert.equal("🏈🏈".length, 4);
  });
});

describe("usdcToMicros / microsToUsdc", () => {
  it("converts decimal strings exactly", () => {
    assert.equal(usdcToMicros("10"), 10_000_000);
    assert.equal(usdcToMicros("0.1"), 100_000);
    assert.equal(usdcToMicros("12.345678"), 12_345_678);
    assert.equal(usdcToMicros(" 5.5 "), 5_500_000);
    assert.equal(usdcToMicros(".25"), 250_000);
    assert.equal(usdcToMicros("7."), 7_000_000);
    assert.equal(usdcToMicros(3), 3_000_000);
  });

  it("rejects bad input", () => {
    for (const bad of ["", ".", "abc", "-1", "1e3", "1,000", "1.2345678", "$5"]) {
      assert.equal(usdcToMicros(bad), null, bad);
    }
    assert.equal(usdcToMicros(Number.NaN), null);
    assert.equal(usdcToMicros("99999999999999"), null);
  });

  it("formats micros without trailing zeros", () => {
    assert.equal(microsToUsdc(10_000_000), "10");
    assert.equal(microsToUsdc(10_500_000), "10.5");
    assert.equal(microsToUsdc(1), "0.000001");
    assert.equal(microsToUsdc(0), "0");
    assert.equal(microsToUsdc(-5), "0");
  });

  it("round-trips", () => {
    for (const v of ["0.5", "10", "123.456789"]) {
      assert.equal(microsToUsdc(usdcToMicros(v)!), v);
    }
  });
});

describe("formatDuration", () => {
  it("uses the two largest units", () => {
    assert.equal(formatDuration(30), "under a minute");
    assert.equal(formatDuration(3600), "1h");
    assert.equal(formatDuration(5400), "1h 30m");
    assert.equal(formatDuration(90 * 86_400), "90d");
    assert.equal(formatDuration(86_400 + 3600 + 60), "1d 1h");
  });
});

describe("listingTotals", () => {
  it("adds the listing fee to the seed", () => {
    assert.deepEqual(listingTotals(config, 25_000_000), { seed: 25_000_000, fee: 1_000_000, total: 26_000_000 });
    assert.deepEqual(listingTotals({ enabled: true }, null), { seed: 0, fee: 0, total: 0 });
  });
});

describe("validateListing", () => {
  it("accepts a well-formed listing", () => {
    assert.deepEqual(validateListing(input(), config, NOW), []);
  });

  it("refuses everything when listing is disabled", () => {
    assert.deepEqual(validateListing(input(), { enabled: false }, NOW), ["Listing isn't open on this deploy."]);
  });

  it("checks question length in bytes", () => {
    assert.match(validateListing(input({ question: "short" }), config, NOW)[0], /at least 10 bytes/);
    const long = "€".repeat(86); // 258 bytes, 86 characters
    assert.match(validateListing(input({ question: long }), config, NOW)[0], /258 bytes; the limit is 256/);
    assert.deepEqual(validateListing(input({ question: `${"a".repeat(255)}?` }), config, NOW), []);
  });

  it("requires a yes/no question ending in '?' like the backend gate", () => {
    const errors = validateListing(input({ question: "Will the city approve the stadium budget" }), config, NOW);
    assert.deepEqual(errors, ["Question must be a yes/no question ending in '?'."]);
  });

  it("rejects subjective wording the backend gate bans", () => {
    for (const q of ["Is Mahomes the best quarterback?", "Should the Bills FEEL good about week 3?"]) {
      const errors = validateListing(input({ question: q }), config, NOW);
      assert.equal(errors.length, 1, q);
      assert.match(errors[0], /subjective wording/);
    }
  });

  it("matches banned words on Unicode word boundaries only", () => {
    assert.equal(hasSubjectiveWording("Will the bestseller list include it?"), false);
    assert.equal(hasSubjectiveWording("Will the fiancébest brand launch?"), false);
    assert.equal(hasSubjectiveWording("Will worst_case happen?"), false);
    assert.equal(hasSubjectiveWording("Who is the best?"), true);
    assert.equal(hasSubjectiveWording("Is it the (worst) week?"), true);
    assert.equal(hasSubjectiveWording("Does he DESERVE it?"), true);
  });

  it("reports one question problem at a time, bytes first", () => {
    const errors = validateListing(input({ question: "best?" }), config, NOW);
    assert.deepEqual(errors, ["Question must be at least 10 bytes."]);
  });

  it("trims before measuring", () => {
    assert.match(validateListing(input({ question: "   tiny    " }), config, NOW)[0], /at least 10 bytes/);
  });

  it("checks criteria length", () => {
    assert.match(validateListing(input({ resolutionCriteria: "too short" }), config, NOW)[0], /at least 20/);
    assert.match(validateListing(input({ resolutionCriteria: "x".repeat(2001) }), config, NOW)[0], /2001 characters/);
  });

  it("enforces the minimum lead plus the submit margin", () => {
    const earliest = NOW + 3600 + SUBMIT_MARGIN_SECONDS;
    assert.match(validateListing(input({ closeTime: NOW + 3600 }), config, NOW)[0], /at least 1h from now/);
    assert.deepEqual(validateListing(input({ closeTime: earliest }), config, NOW), []);
    assert.equal(validateListing(input({ closeTime: earliest - 1 }), config, NOW).length, 1);
  });

  it("enforces the maximum horizon", () => {
    assert.match(validateListing(input({ closeTime: NOW + 91 * 86_400 }), config, NOW)[0], /within 90d/);
    assert.deepEqual(validateListing(input({ closeTime: NOW + 90 * 86_400 }), config, NOW), []);
  });

  it("has no horizon when maxHorizonSeconds is 0", () => {
    const open = { ...config, maxHorizonSeconds: 0 };
    assert.deepEqual(validateListing(input({ closeTime: NOW + 400 * 86_400 }), open, NOW), []);
  });

  it("requires a close time", () => {
    assert.match(validateListing(input({ closeTime: null }), config, NOW)[0], /Pick a close/);
    assert.match(validateListing(input({ closeTime: 1.5 }), config, NOW)[0], /Pick a close/);
  });

  it("enforces the minimum seed", () => {
    assert.match(validateListing(input({ seedUsdc: 9_999_999 }), config, NOW)[0], /at least 10 USDC/);
    assert.match(validateListing(input({ seedUsdc: null }), config, NOW)[0], /Enter a seed/);
    assert.match(validateListing(input({ seedUsdc: 0 }), config, NOW)[0], /Enter a seed/);
    assert.deepEqual(validateListing(input({ seedUsdc: 10_000_000 }), config, NOW), []);
  });

  it("flags a config for another chain", () => {
    const errors = validateListing(input(), { ...config, chainId: 8453 }, NOW);
    assert.deepEqual(errors, ["Listing is configured for chain 8453; this app sends on Base Sepolia."]);
  });

  it("reports every problem at once", () => {
    const errors = validateListing(
      { question: "", resolutionCriteria: "", closeTime: null, seedUsdc: null },
      config,
      NOW,
    );
    assert.equal(errors.length, 4);
  });
});

describe("close time helpers", () => {
  it("computes bounds from the config", () => {
    assert.deepEqual(closeTimeBounds(config, NOW), {
      earliest: NOW + 3600 + SUBMIT_MARGIN_SECONDS,
      latest: NOW + 90 * 86_400,
    });
    assert.deepEqual(closeTimeBounds({ enabled: true }, NOW), {
      earliest: NOW + 3600 + SUBMIT_MARGIN_SECONDS,
      latest: null,
    });
  });

  it("defaults to a valid whole hour about a week out", () => {
    const t = defaultCloseTime(config, NOW);
    assert.equal(t % 3600, 0);
    assert.ok(t > NOW + 6 * 86_400 && t <= NOW + 7 * 86_400);
    assert.deepEqual(validateListing(input({ closeTime: t }), config, NOW), []);
  });

  it("stays inside a short horizon", () => {
    const short = { ...config, minLeadSeconds: 600, maxHorizonSeconds: 2 * 3600 };
    const t = defaultCloseTime(short, NOW);
    assert.deepEqual(validateListing(input({ closeTime: t }), short, NOW), []);
  });

  it("round-trips datetime-local values in local time", () => {
    const t = 1_800_003_600; // whole minute
    const text = unixToDatetimeLocal(t);
    assert.match(text, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
    assert.equal(datetimeLocalToUnix(text), t);
  });

  it("rejects malformed datetime-local values", () => {
    assert.equal(datetimeLocalToUnix(""), null);
    assert.equal(datetimeLocalToUnix("2026-09-24"), null);
    assert.equal(datetimeLocalToUnix("tomorrow"), null);
  });
});

describe("toCdpCalls", () => {
  const approve = `0x095ea7b3${"0".repeat(128)}`;
  const create = `0x4e7d1a32${"ab".repeat(64)}`;

  it("converts prepared calls with bigint values", () => {
    const calls = toCdpCalls(
      [
        { to: USDC, data: approve, value: 0 },
        { to: FACTORY, data: create, value: "0x0" },
      ],
      [USDC, FACTORY],
    );
    assert.deepEqual(calls, [
      { to: USDC, data: approve, value: 0n },
      { to: FACTORY, data: create, value: 0n },
    ]);
  });

  it("defaults a missing value to zero and parses decimal strings", () => {
    assert.equal(toCdpCalls([{ to: USDC, data: "0x" }])[0].value, 0n);
    assert.equal(toCdpCalls([{ to: USDC, data: "0x", value: null }])[0].value, 0n);
    assert.equal(toCdpCalls([{ to: USDC, data: "0x", value: "12" }])[0].value, 12n);
  });

  it("matches allowed targets case-insensitively", () => {
    assert.equal(toCdpCalls([{ to: USDC.toLowerCase(), data: approve }], [USDC]).length, 1);
  });

  it("rejects a target outside the allowlist", () => {
    assert.throws(
      () => toCdpCalls([{ to: "0x3333333333333333333333333333333333333333", data: approve }], [USDC, FACTORY]),
      /unexpected target/,
    );
  });

  it("ignores empty allowlist entries", () => {
    assert.equal(toCdpCalls([{ to: USDC, data: approve }], [null, undefined, ""]).length, 1);
  });

  it("rejects malformed calls", () => {
    assert.throws(() => toCdpCalls([]), /no calls/);
    assert.throws(() => toCdpCalls([{ to: "0x1234", data: "0x" }]), /bad target/);
    assert.throws(() => toCdpCalls([{ to: USDC, data: "0xabc" }]), /bad calldata/);
    assert.throws(() => toCdpCalls([{ to: USDC, data: "deadbeef" }]), /bad calldata/);
    assert.throws(() => toCdpCalls([{ to: USDC, data: "0x", value: -1 }]), /negative/);
    assert.throws(() => toCdpCalls([{ to: USDC, data: "0x", value: 1.5 }]), /integer/);
    assert.throws(() => toCdpCalls([{ to: USDC, data: "0x", value: "lots" }]), /integer/);
  });
});

describe("confirm retry helpers", () => {
  it("retries only the not-on-chain 409", () => {
    assert.equal(isNotOnChainYet('409 {"detail":"not on chain yet"}'), true);
    assert.equal(isNotOnChainYet('409 {"detail":"criteria hash mismatch"}'), false);
    assert.equal(isNotOnChainYet('403 {"detail":"not on chain yet"}'), false);
    assert.equal(isNotOnChainYet(undefined), false);
  });

  it("backs off and caps the delay", () => {
    assert.equal(confirmBackoffMs(0), 1500);
    assert.equal(confirmBackoffMs(1), 3000);
    assert.equal(confirmBackoffMs(2), 6000);
    assert.equal(confirmBackoffMs(3), 10_000);
    assert.equal(confirmBackoffMs(50), 10_000);
  });
});

describe("confirmFailedMessage", () => {
  it("does not claim the market is on chain when the transaction was never seen mined", () => {
    for (const detail of [null, "500 boom"]) {
      const text = confirmFailedMessage(true, detail);
      assert.doesNotMatch(text, /is on chain/i);
      assert.match(text, /couldn't confirm the transaction yet/i);
      assert.match(text, /will appear shortly/i);
    }
    assert.match(confirmFailedMessage(true, "500 boom"), /500 boom/);
  });

  it("keeps the on-chain wording once the transaction was confirmed", () => {
    assert.match(confirmFailedMessage(false, null), /on chain but the API hasn't seen it yet/);
    assert.match(confirmFailedMessage(false, "500 boom"), /on chain but registering it failed: 500 boom/);
  });
});
