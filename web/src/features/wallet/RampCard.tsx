"use client";

import { useEffect, useState } from "react";
import { api, apiErrorDetail } from "@/shared/api/client";
import { parseRampAmount } from "./rampAmount";

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
  const [kycStatus, setKycStatus] = useState<KycStatus | null>(null);
  const [kycCheck, setKycCheck] = useState<KycCheckResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [amount, setAmount] = useState("100");

  const humanizeStatus = (status: string) => {
    return status
      .split("_")
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(" ");
  };

  useEffect(() => {
    if (!address) return;

    api(`/api/v1/kyc/status`)
      .then((r) => setKycStatus(r))
      .catch(() => setKycStatus(null));
  }, [address]);

  const handleBuyUSDC = async () => {
    // The API 400/422s a non-finite, zero or oversized amount; say so up front.
    const usd = parseRampAmount(amount);
    if (usd === null) {
      setError("Enter an amount between $1 and $1,000,000.");
      return;
    }
    setLoading(true);
    setError("");

    try {
      const checkResponse = await api(`/api/v1/kyc/check`, {
        method: "POST",
        body: JSON.stringify({ notional_usdc: usd }),
      });

      setKycCheck(checkResponse);

      if (!checkResponse.allowed) {
        setError(
          `Verification required: ${checkResponse.reason}. Please complete identity verification.`
        );
        setLoading(false);
        return;
      }

      try {
        const moonpayResponse = await api(`/api/v1/ramps/moonpay/session`, {
          method: "POST",
          body: JSON.stringify({ usdc_amount: String(usd) }),
        });

        if (moonpayResponse.url) {
          window.open(moonpayResponse.url, "_blank");
          setLoading(false);
          return;
        }
      } catch (moonpayError) {
        console.warn("Primary payment provider not available, trying alternative");
      }

      try {
        const query = new URLSearchParams({ address, usdc_amount: String(usd) });
        const coinbaseResponse = await api(`/api/v1/ramps/onramp-url?${query.toString()}`);
        if (coinbaseResponse.url) {
          window.open(coinbaseResponse.url, "_blank");
        } else {
          setError("No ramp providers available");
        }
      } catch {
        setError("No ramp providers available");
      }
    } catch (err) {
      setError(`Error: ${apiErrorDetail(err) || "Unknown error"}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="card">
      <h3>Add Money</h3>
      <p className="muted">
        Add funds to your wallet to place bets.
      </p>

      {address ? (
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <div>
            <label htmlFor="amount" style={{ display: "block", marginBottom: "0.5rem" }}>
              Amount ($):
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
              Verification Status: {humanizeStatus(kycStatus.status)}
              {kycStatus.jurisdiction && ` (${kycStatus.jurisdiction})`}
            </p>
          )}

          {error && <p style={{ color: "red" }}>{error}</p>}

          <button
            className="btn"
            onClick={handleBuyUSDC}
            disabled={loading || !address}
          >
            {loading ? "Processing..." : "Add Money"}
          </button>
        </div>
      ) : (
        <p className="muted">Sign in to add money to your smart account.</p>
      )}
    </div>
  );
}
