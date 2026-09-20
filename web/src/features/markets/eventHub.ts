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
): EventCard[] {
  const { searchQuery, category } = opts;
  const hubs: EventCard[] = [];

  for (const card of cards) {
    const primaryCat = categorizeMarket(card.primary);
    if (category !== "all" && primaryCat !== category) continue;

    const searchHitsPrimary = matchesSearch(card.primary, searchQuery);
    const searchHitsChild = card.children.some((child) => matchesSearch(child, searchQuery));
    if (!searchQuery || searchHitsPrimary || searchHitsChild) {
      hubs.push(card);
    }
  }

  return hubs;
}
