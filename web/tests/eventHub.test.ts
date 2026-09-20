import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  resolveActiveConditionId,
  selectHubs,
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
