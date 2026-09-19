import { MarketList } from "@/features/markets/MarketList";

export default function HomePage() {
  return (
    <div>
      <h1>Markets</h1>
      <p className="muted">bet yes or no in usdc.</p>
      <MarketList />
    </div>
  );
}
