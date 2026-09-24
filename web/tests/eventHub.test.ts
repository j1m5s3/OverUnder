import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  applyActiveMarketQuery,
  hubRoster,
  isUserListed,
  resolveActiveConditionId,
  selectHubs,
  yesProbability,
  type EventCard,
  type Market,
  type MarketDetailData,
} from "../src/features/markets/eventHub.ts";

function market(partial: Partial<Market> & Pick<Market, "conditionId" | "question" | "marketType">): Market {
  return {
    closeTime: 2_000_000_000,
    paused: false,
    resolved: false,
    suggestedProbability: 0.5,
    ...partial,
  };
}

const electionPrimary = market({
  conditionId: "0xprimary",
  question: "Will the bill pass the Senate?",
  marketType: 0,
});

const chiefsChild = market({
  conditionId: "0xchild",
  parentConditionId: "0xprimary",
  question: "Chiefs to score a touchdown in Q1?",
  marketType: 1,
});

const sportsPrimary = market({
  conditionId: "0xsports",
  question: "Chiefs vs Broncos: Chiefs win?",
  marketType: 0,
});

const otherChild = market({
  conditionId: "0xotherchild",
  parentConditionId: "0xsports",
  question: "Will kickoff be delayed by weather?",
  marketType: 1,
});

const orphanWildcard = market({
  conditionId: "0xorphan",
  parentConditionId: "0xmissing",
  question: "Yankees win the series?",
  marketType: 1,
});

const cards: EventCard[] = [
  { primary: electionPrimary, children: [chiefsChild] },
  { primary: sportsPrimary, children: [otherChild] },
  { primary: orphanWildcard, children: [] },
];

function topLevelIds(hubs: EventCard[]): string[] {
  return hubs.map((card) => card.primary.conditionId);
}

describe("selectHubs", () => {
  it("does not put an election hub or its Chiefs prop under sports", () => {
    const hubs = selectHubs(cards, { searchQuery: "", category: "sports" });
    assert.deepEqual(topLevelIds(hubs), ["0xsports", "0xorphan"]);
    assert.deepEqual(
      hubs.flatMap((card) => card.children.map((row) => row.conditionId)),
      ["0xotherchild"],
    );
  });

  it("does not lift a Chiefs prop into sports when search hits the child of an election primary", () => {
    const hubs = selectHubs(cards, { searchQuery: "touchdown", category: "sports" });
    assert.deepEqual(topLevelIds(hubs), []);
  });

  it("keeps a search hit on a child nested under its parent on all", () => {
    const hubs = selectHubs(cards, { searchQuery: "touchdown", category: "all" });
    assert.deepEqual(topLevelIds(hubs), ["0xprimary"]);
    assert.equal(hubs[0].children.length, 1);
    assert.equal(hubs[0].children[0].conditionId, "0xchild");
  });

  it("keeps a sports hub when the primary matches the tab and a child hits search", () => {
    const hubs = selectHubs(cards, { searchQuery: "weather", category: "sports" });
    assert.deepEqual(topLevelIds(hubs), ["0xsports"]);
    assert.equal(hubs[0].children.length, 1);
  });

  it("lists an API orphan wildcard alone under sports when it is itself sports", () => {
    const hubs = selectHubs(cards, { searchQuery: "", category: "sports" });
    const orphan = hubs.find((card) => card.primary.conditionId === "0xorphan");
    assert.equal(orphan?.primary.conditionId, "0xorphan");
    assert.deepEqual(orphan?.children, []);
  });
});

const electionDetail: MarketDetailData = { ...electionPrimary, children: [chiefsChild] };
const emptyDetail: MarketDetailData = { ...sportsPrimary, children: [] };

describe("resolveActiveConditionId", () => {
  it("defaults to the primary when no id is requested", () => {
    assert.equal(resolveActiveConditionId(electionDetail, null), "0xprimary");
    assert.equal(resolveActiveConditionId(electionDetail, undefined), "0xprimary");
  });

  it("returns the primary when the requested id is the primary", () => {
    assert.equal(resolveActiveConditionId(electionDetail, "0xprimary"), "0xprimary");
  });

  it("returns a nested child id when the requested id is a child", () => {
    assert.equal(resolveActiveConditionId(electionDetail, "0xchild"), "0xchild");
  });

  it("falls back to the primary when the requested id is unknown", () => {
    assert.equal(resolveActiveConditionId(electionDetail, "0xmissing"), "0xprimary");
  });

  it("stays on the page market when children are empty", () => {
    assert.equal(resolveActiveConditionId(emptyDetail, null), "0xsports");
    assert.equal(resolveActiveConditionId(emptyDetail, "0xotherchild"), "0xsports");
  });
});

describe("hubRoster", () => {
  it("counts the primary plus each nested child", () => {
    assert.deepEqual(
      hubRoster(electionDetail).map((row) => row.conditionId),
      ["0xprimary", "0xchild"],
    );
    assert.deepEqual(
      hubRoster(emptyDetail).map((row) => row.conditionId),
      ["0xsports"],
    );
  });
});

describe("applyActiveMarketQuery", () => {
  it("sets m to the child and keeps side", () => {
    const next = applyActiveMarketQuery(new URLSearchParams("side=no"), "0xprimary", "0xchild");
    assert.equal(next.get("m"), "0xchild");
    assert.equal(next.get("side"), "no");
  });

  it("clears m when the primary is active", () => {
    const next = applyActiveMarketQuery(new URLSearchParams("m=0xchild&side=yes"), "0xprimary", "0xprimary");
    assert.equal(next.get("m"), null);
    assert.equal(next.get("side"), "yes");
  });
});

describe("yesProbability", () => {
  it("prefers the live AMM price over the static hint", () => {
    assert.equal(yesProbability({ yesPriceMicros: 640_000, suggestedProbability: 0.5 }), 0.64);
  });

  it("falls back to suggestedProbability without a usable price", () => {
    assert.equal(yesProbability({ suggestedProbability: 0.3 }), 0.3);
    assert.equal(yesProbability({ yesPriceMicros: null, suggestedProbability: 0.3 }), 0.3);
    assert.equal(yesProbability({ yesPriceMicros: 0, suggestedProbability: 0.3 }), 0.3);
    assert.equal(yesProbability({ yesPriceMicros: 1_000_000, suggestedProbability: 0.3 }), 0.3);
    assert.equal(yesProbability({ yesPriceMicros: Number.NaN, suggestedProbability: 0.3 }), 0.3);
  });

  it("defaults to even odds when neither is set", () => {
    assert.equal(yesProbability({ suggestedProbability: 0 }), 0.5);
  });
});

describe("user-listed markets", () => {
  it("flags marketType 2 only", () => {
    assert.equal(isUserListed({ marketType: 2 }), true);
    assert.equal(isUserListed({ marketType: 0 }), false);
    assert.equal(isUserListed({ marketType: 1 }), false);
  });

  it("keeps a user-listed primary card under its category with search", () => {
    const userCard: EventCard = {
      primary: market({ conditionId: "0xuser", question: "Will the stadium budget pass?", marketType: 2 }),
      children: [],
    };
    const hubs = selectHubs([userCard], { searchQuery: "stadium", category: "other" });
    assert.deepEqual(hubs.map((card) => card.primary.conditionId), ["0xuser"]);
  });
});
