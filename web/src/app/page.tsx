import { MarketList } from "@/features/markets/MarketList";

export default function HomePage() {
  return (
    <div>
      <h1>Markets</h1>
      <p className="muted">Primary CLOB books plus AI-generated wildcard AMM markets.</p>
      <MarketList />
    </div>
  );
}
