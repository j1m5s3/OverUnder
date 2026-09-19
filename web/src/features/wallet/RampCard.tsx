"use client";

import { useEffect, useState } from "react";
import { api } from "@/shared/api/client";

interface KycStatus {
  status: string;
  jurisdiction: string;
  updated_at: string;
}

interface KycCheckResponse {
  allowed: boolean;
  reason: string;
  kyc_status: string;
}

export function RampCard({ address }: { address: string }) {
  const [moonpayUrl, setMoonpayUrl] = useState("");
  const [coinbaseUrl, setCoinbaseUrl] = useState("");
  const [kycStatus, setKycStatus] = useState<KycStatus | null>(null);
  const [kycCheck, setKycCheck] = useState<KycCheckResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [amount, setAmount] = useState("100");

  useEffect(() => {
    if (!address) return;

    // Get Coinbase fallback URL
    api(`/api/v1/ramps/onramp-url?address=${address}&usdc_amount=${amount}`)
      .then((r) => setCoinbaseUrl(r.url))
      .catch(() => setCoinbaseUrl(""));

    // Get KYC status
    api(`/api/v1/kyc/status`, {
      headers: { Authorization: `Bearer ${localStorage.getItem("jwt")}` },
    })
      .then((r) => setKycStatus(r))
      .catch(() => setKycStatus(null));
  }, [address, amount]);

  const handleBuyUSDC = async () => {
    setLoading(true);
    setError("");

    try {
      // Check KYC requirements
      const checkResponse = await api(`/api/v1/kyc/check`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${localStorage.getItem("jwt")}`,
        },
        body: JSON.stringify({ notional_usdc: parseFloat(amount) }),
      });

      setKycCheck(checkResponse);

      if (!checkResponse.allowed) {
        setError(
          `KYC required: ${checkResponse.reason}. Please complete KYC verification.`
        );
        setLoading(false);
        return;
      }

      // Try MoonPay first
      try {
        const moonpayResponse = await api(`/api/v1/ramps/moonpay/session`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${localStorage.getItem("jwt")}`,
          },
          body: JSON.stringify({ usdc_amount: amount }),
        });

        if (moonpayResponse.url) {
          window.open(moonpayResponse.url, "_blank");
          setLoading(false);
          return;
        }
      } catch (moonpayError) {
        console.warn("MoonPay not available, falling back to Coinbase");
      }

      // Fallback to Coinbase
      if (coinbaseUrl) {
        window.open(coinbaseUrl, "_blank");
      } else {
        setError("No ramp providers available");
      }
    } catch (err) {
      setError(`Error: ${err instanceof Error ? err.message : "Unknown error"}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="card">
      <h3>Buy USDC</h3>
      <p className="muted">
        Purchase USDC on Base with MoonPay or Coinbase Onramp.
      </p>

      {address ? (
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <div>
            <label htmlFor="amount" style={{ display: "block", marginBottom: "0.5rem" }}>
              Amount (USD):
            </label>
            <input
              id="amount"
              type="number"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              min="1"
              step="1"
              style={{ width: "100%", padding: "0.5rem" }}
            />
          </div>

          {kycStatus && kycStatus.status !== "not_started" && (
            <p className="muted">
              KYC Status: {kycStatus.status}
              {kycStatus.jurisdiction && ` (${kycStatus.jurisdiction})`}
            </p>
          )}

          {error && <p style={{ color: "red" }}>{error}</p>}

          <button
            className="btn"
            onClick={handleBuyUSDC}
            disabled={loading || !address}
          >
            {loading ? "Processing..." : "Buy USDC"}
          </button>

          {coinbaseUrl && (
            <a
              className="btn"
              href={coinbaseUrl}
              target="_blank"
              rel="noreferrer"
              style={{ opacity: 0.7 }}
            >
              Coinbase Onramp (Fallback)
            </a>
          )}
        </div>
      ) : (
        <p className="muted">Connect wallet to buy USDC.</p>
      )}
    </div>
  );
}
