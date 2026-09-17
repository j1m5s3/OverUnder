"use client";

import { useEffect, useState } from "react";
import { api } from "@/shared/api/client";

export default function PortfolioPage() {
  const [address, setAddress] = useState("");
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    const stored = localStorage.getItem("ou_address") || "";
    setAddress(stored);
    if (stored) {
      api(`/api/v1/portfolio/${stored}`).then(setData).catch(() => setData(null));
    }
  }, []);

  return (
    <div>
      <h1>Portfolio</h1>
      <p className="muted">{address || "Connect a wallet to load positions."}</p>
      {data ? (
        <pre className="card">{JSON.stringify(data, null, 2)}</pre>
      ) : (
        <p className="muted">No positions yet.</p>
      )}
    </div>
  );
}
