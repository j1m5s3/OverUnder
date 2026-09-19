"use client";

import { useEffect, useState } from "react";
import { RampCard } from "@/features/wallet/RampCard";

export default function WalletPage() {
  const [address, setAddress] = useState("");
  useEffect(() => {
    setAddress(localStorage.getItem("ou_address") || "");
  }, []);
  return (
    <div>
      <h1>Wallet</h1>
      <p className="muted">Add money to place bets on prediction markets.</p>
      <RampCard address={address} />
    </div>
  );
}
