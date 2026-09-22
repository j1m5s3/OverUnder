"use client";

import { useCurrentUser } from "@coinbase/cdp-hooks";
import { RampCard } from "@/features/wallet/RampCard";

export default function WalletPage() {
  const { currentUser } = useCurrentUser();
  const address = currentUser?.evmSmartAccounts?.[0] || "";
  return (
    <div>
      <h1>Wallet</h1>
      <p className="muted">Add money to place bets on prediction markets.</p>
      <RampCard address={address} />
    </div>
  );
}
