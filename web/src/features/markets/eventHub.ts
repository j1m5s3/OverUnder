import { categorizeMarket, type Category } from "../../shared/utils/categorize";

export type Market = {
  conditionId: string;
  parentConditionId?: string | null;
  question: string;
  marketType: number;
  closeTime: number;
  paused: boolean;
  resolved: boolean;
  suggestedProbability: number;
};

export type EventCard = {
  primary: Market;
  children: Market[];
};

export type MarketDetailData = Market & { children: Market[] };

export function matchesSearch(market: Market, searchQuery: string): boolean {
  if (!searchQuery) return true;
  return market.question.toLowerCase().includes(searchQuery.toLowerCase());
}

export function selectHubs(
  cards: EventCard[],
  opts: { searchQuery: string; category: Category },
): { hubs: EventCard[]; orphans: Market[] } {
  const { searchQuery, category } = opts;
  const hubs: EventCard[] = [];
  const orphans: Market[] = [];

  for (const card of cards) {
    const primaryCat = categorizeMarket(card.primary);
    const categoryOk = category === "all" || primaryCat === category;
    const searchHitsPrimary = matchesSearch(card.primary, searchQuery);
    const searchHitsChild = card.children.some((child) => matchesSearch(child, searchQuery));
    const searchOk = !searchQuery || searchHitsPrimary || searchHitsChild;

    if (categoryOk && searchOk) {
      hubs.push(card);
      continue;
    }

    for (const child of card.children) {
      const childCat = categorizeMarket(child);
      const childCatOk = category === "all" || childCat === category;
      if (childCatOk && matchesSearch(child, searchQuery)) {
        orphans.push(child);
      }
    }
  }

  return { hubs, orphans };
}
