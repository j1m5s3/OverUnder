"use client";

import { useAccount } from "wagmi";
import { RampCard } from "@/features/wallet/RampCard";

export default function WalletPage() {
  const { address } = useAccount();
  return (
    <div>
      <h1>Wallet</h1>
      <p className="muted">Add money to place bets on prediction markets.</p>
      <RampCard address={address || ""} />
    </div>
  );
}
