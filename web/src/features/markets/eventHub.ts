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
  // Latest indexed AMM YES price in micros (1e6 = 100%); null until the pool trades or is seeded.
  yesPriceMicros?: number | null;
  // Unix seconds when trading halts; null when the deploy does not halt at close.
  tradingHaltsAt?: number | null;
  tradingOpen?: boolean;
  resolutionCriteria?: string;
  // Lister address for user-listed markets (marketType 2).
  creator?: string | null;
};

// 0 = operator primary, 1 = wildcard child, 2 = user-listed. The API lists type 2
// as a primary card; nothing here filters on type, so it renders like type 0.
export const MARKET_TYPE_USER = 2;

export function isUserListed(market: Pick<Market, "marketType">): boolean {
  return market.marketType === MARKET_TYPE_USER;
}

// Live AMM price wins over the static listing hint when it is a usable probability.
export function yesProbability(market: Pick<Market, "yesPriceMicros" | "suggestedProbability">): number {
  const micros = market.yesPriceMicros;
  if (typeof micros === "number" && Number.isFinite(micros) && micros > 0 && micros < 1_000_000) {
    return micros / 1_000_000;
  }
  return market.suggestedProbability || 0.5;
}

export type EventCard = {
  primary: Market;
  children: Market[];
};

export type ScoreStatus = "scheduled" | "in_progress" | "final" | "postponed" | "cancelled";

export type LiveScore = {
  conditionId: string;
  homeLabel: string;
  awayLabel: string;
  homeScore: number | null;
  awayScore: number | null;
  status: ScoreStatus;
  periodLabel: string | null;
  updatedAt: string;
  facts?: Record<string, unknown> | null;
};

// MarketDetail.listing (ListingPublic) for user-listed markets. Public detail
// only serves confirmed listings; unconfirmed or rejected ones are 404.
export type MarketListingInfo = {
  creator: string;
  status: string;
  seedUsdc: number;
  criteriaHash: string;
};

export type MarketDetailData = Market & {
  children: Market[];
  score?: LiveScore | null;
  facts?: Record<string, unknown> | null;
  listing?: MarketListingInfo | null;
};

// What MarketDetail shows when GET /markets/{id} fails. No HTTP status means
// the API is unreachable (local dev without a backend): keep the demo data.
// A 404 is a real answer (unknown id, or a user listing that is not confirmed
// yet or was rejected) and must never be papered over with demo markets.
export type DetailFailure = "demo" | "not-found" | "error";

export function detailFailureKind(status: number | null): DetailFailure {
  if (status === null) return "demo";
  if (status === 404) return "not-found";
  return "error";
}

export function listedBy(market: Pick<MarketDetailData, "creator" | "listing">): string | null {
  return market.creator || market.listing?.creator || null;
}

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

export function resolveActiveConditionId(
  detail: MarketDetailData,
  requestedId: string | null | undefined,
): string {
  if (!requestedId || requestedId === detail.conditionId) return detail.conditionId;
  const nested = detail.children.some((row) => row.conditionId === requestedId);
  return nested ? requestedId : detail.conditionId;
}

export function hubRoster(detail: MarketDetailData): Market[] {
  return [detail, ...detail.children];
}

export function applyActiveMarketQuery(
  params: { toString(): string },
  primaryId: string,
  activeId: string,
): URLSearchParams {
  const next = new URLSearchParams(params.toString());
  if (activeId === primaryId) next.delete("m");
  else next.set("m", activeId);
  return next;
}
