import { MarketDetail } from "@/features/markets/MarketDetail";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <MarketDetail conditionId={id} />;
}
