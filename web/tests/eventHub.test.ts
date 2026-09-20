import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { selectHubs, type EventCard, type Market } from "../src/features/markets/eventHub.ts";

function market(partial: Partial<Market> & Pick<Market, "conditionId" | "question" | "marketType">): Market {
  return {
    closeTime: 2_000_000_000,
    paused: false,
    resolved: false,
    suggestedProbability: 0.5,
    ...partial,
  };
}

const otherPrimary = market({
  conditionId: "0xprimary",
  question: "Will the bill pass the Senate?",
  marketType: 0,
});

const sportsChild = market({
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
  { primary: otherPrimary, children: [sportsChild] },
  { primary: sportsPrimary, children: [otherChild] },
  { primary: orphanWildcard, children: [] },
];

describe("selectHubs", () => {
  it("categorizes on the primary only, so a sports child does not put an other event in sports", () => {
    const { hubs, orphans } = selectHubs(cards, { searchQuery: "", category: "sports" });
    assert.deepEqual(
      hubs.map((card) => card.primary.conditionId),
      ["0xsports", "0xorphan"],
    );
    assert.equal(hubs.find((card) => card.primary.conditionId === "0xsports")?.children.length, 1);
    assert.deepEqual(
      orphans.map((row) => row.conditionId),
      ["0xchild"],
    );
  });

  it("lifts matching children when the parent hub is filtered out, and does not drop orphans", () => {
    const { hubs, orphans } = selectHubs(cards, { searchQuery: "touchdown", category: "sports" });
    assert.deepEqual(
      hubs.map((card) => card.primary.conditionId),
      [],
    );
    assert.deepEqual(
      orphans.map((row) => row.conditionId),
      ["0xchild"],
    );
  });

  it("keeps a search hit on a child nested under its parent on all, without inventing extra hubs", () => {
    const { hubs, orphans } = selectHubs(cards, { searchQuery: "touchdown", category: "all" });
    assert.deepEqual(
      hubs.map((card) => card.primary.conditionId),
      ["0xprimary"],
    );
    assert.equal(hubs[0].children.length, 1);
    assert.deepEqual(orphans, []);
  });

  it("lists an API orphan wildcard alone under sports when it is itself sports", () => {
    const { hubs, orphans } = selectHubs(cards, { searchQuery: "", category: "sports" });
    const standalone = [...hubs.map((card) => card.primary), ...orphans];
    assert.ok(standalone.some((row) => row.conditionId === "0xorphan"));
    assert.equal(
      hubs.find((card) => card.primary.conditionId === "0xorphan")?.children.length ?? 0,
      0,
    );
  });
});
