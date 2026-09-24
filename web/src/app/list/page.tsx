import { ListMarketForm } from "@/features/listing/ListMarketForm";

export default function ListMarketPage() {
  return (
    <div style={{ maxWidth: 640 }}>
      <h1>List a market</h1>
      <p className="muted">Ask a yes/no question, seed its pool in USDC and let everyone trade it.</p>
      <ListMarketForm />
    </div>
  );
}
