"use client";

import { useEffect, useState } from "react";
import { api } from "@/shared/api/client";

export function OrderTicket({ conditionId }: { conditionId: string }) {
  const [book, setBook] = useState<{ bids: any[]; asks: any[] }>({ bids: [], asks: [] });
  const [price, setPrice] = useState("500000");
  const [amount, setAmount] = useState("1000000");
  const [status, setStatus] = useState("");

  useEffect(() => {
    api(`/api/v1/orderbook/${encodeURIComponent(conditionId)}`)
      .then(setBook)
      .catch(() => setBook({ bids: [], asks: [] }));
  }, [conditionId]);

  async function place(isBuy: boolean) {
    const maker = localStorage.getItem("ou_address");
    if (!maker) {
      setStatus("Connect wallet first.");
      return;
    }
    try {
      const body = {
        maker,
        isBuy,
        conditionId,
        outcome: 0,
        price: Number(price),
        amount: Number(amount),
        salt: Date.now(),
        nonce: 0,
        expiry: Math.floor(Date.now() / 1000) + 86400,
        signature: "0x",
        orderHash: "0x" + Date.now().toString(16).padStart(64, "0").slice(-64),
      };
      const res = await api("/api/v1/orders", { method: "POST", body: JSON.stringify(body) });
      setStatus(`Posted. fills=${res.fills?.length ?? 0}`);
    } catch (e: any) {
      setStatus(e.message);
    }
  }

  return (
    <div className="card">
      <h3>Limit order</h3>
      <p className="muted">Price is USDC per token (1e6 = $1.00). Taker fee 75 bps on fill.</p>
      <label className="muted">Price</label>
      <input value={price} onChange={(e) => setPrice(e.target.value)} />
      <label className="muted">Amount</label>
      <input value={amount} onChange={(e) => setAmount(e.target.value)} />
      <div className="row" style={{ marginTop: 12 }}>
        <button className="btn yes" onClick={() => place(true)}>
          Buy Yes
        </button>
        <button className="btn no" onClick={() => place(false)}>
          Sell Yes
        </button>
      </div>
      <p className="muted">{status}</p>
      <div className="row">
        <div>
          <div className="yes">Bids</div>
          {book.bids.map((o) => (
            <div key={o.orderHash} className="muted">
              {o.price} × {o.remaining}
            </div>
          ))}
        </div>
        <div>
          <div className="no">Asks</div>
          {book.asks.map((o) => (
            <div key={o.orderHash} className="muted">
              {o.price} × {o.remaining}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
