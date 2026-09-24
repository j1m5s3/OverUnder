"use client";

import { useCurrentUser } from "@coinbase/cdp-hooks";
import { RampCard } from "@/features/wallet/RampCard";
import { smartAccountOf } from "@/features/wallet/account";

export default function WalletPage() {
  const { currentUser } = useCurrentUser();
  const address = smartAccountOf(currentUser) ?? "";
  return (
    <div>
      <h1>Wallet</h1>
      <p className="muted">Add money to place bets on prediction markets.</p>
      <RampCard address={address} />
    </div>
  );
}
