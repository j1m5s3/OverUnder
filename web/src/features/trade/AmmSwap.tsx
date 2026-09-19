"use client";

import { useState } from "react";
import { api } from "@/shared/api/client";
import { useAccount, useWriteContract, usePublicClient } from "wagmi";
import { parseAbi } from "viem";

const AMM_ABI = parseAbi([
  "function buyWithUSDC(bytes32 conditionId, bool buyYes, uint256 usdcIn, uint256 minOut) external returns (uint256)",
  "function sellToUSDC(bytes32 conditionId, bool sellYes, uint256 tokenAmount, uint256 minUsdc) external returns (uint256)",
  "function quoteBuy(bytes32 conditionId, bool buyYes, uint256 usdcIn) external view returns (uint256)",
  "function quoteSell(bytes32 conditionId, bool sellYes, uint256 tokenAmount) external view returns (uint256)",
]);

const USDC_ABI = parseAbi([
  "function approve(address spender, uint256 amount) external returns (bool)",
  "function allowance(address owner, address spender) external view returns (uint256)",
]);

const CTF_ABI = parseAbi([
  "function setApprovalForAll(address operator, bool approved) external",
  "function isApprovedForAll(address account, address operator) external view returns (bool)",
  "function positionId(bytes32 conditionId, uint8 outcome) external view returns (uint256)",
]);

export function AmmSwap({ conditionId }: { conditionId: string }) {
  const [mode, setMode] = useState<"buy" | "sell">("buy");
  const [outcome, setOutcome] = useState<"yes" | "no">("yes");
  const [amount, setAmount] = useState("1000000");
  const [quote, setQuote] = useState<any>(null);
  const [status, setStatus] = useState("");
  const [slippage, setSlippage] = useState("0.5");
  
  const { address } = useAccount();
  const { writeContractAsync } = useWriteContract();
  const publicClient = usePublicClient();

  async function refresh() {
    try {
      let q;
      if (mode === "buy") {
        q = await api(
          `/api/v1/amm/${encodeURIComponent(conditionId)}/quote?buy_yes=${outcome === "yes"}&usdc_in=${amount}`,
        );
      } else {
        q = await api(
          `/api/v1/amm/${encodeURIComponent(conditionId)}/quote?sell_yes=${outcome === "yes"}&token_amount=${amount}`,
        );
      }
      setQuote(q);
      setStatus("");
    } catch (e: any) {
      setStatus(e.message);
    }
  }

  async function swap() {
    if (!address) {
      setStatus("Connect wallet first.");
      return;
    }

    const ammAddress = process.env.NEXT_PUBLIC_AMM_ADDRESS as `0x${string}`;
    const usdcAddress = process.env.NEXT_PUBLIC_USDC_ADDRESS as `0x${string}`;
    const ctfAddress = process.env.NEXT_PUBLIC_CTF_ADDRESS as `0x${string}`;
    
    if (!ammAddress || !usdcAddress || !ctfAddress) {
      setStatus("Missing contract addresses in env");
      return;
    }

    if (mode === "buy") {
      await executeBuy(ammAddress, usdcAddress);
    } else {
      await executeSell(ammAddress, ctfAddress);
    }
  }

  async function executeBuy(ammAddress: `0x${string}`, usdcAddress: `0x${string}`) {
    if (!quote || !quote.tokensOut || quote.tokensOut <= 0) {
      setStatus("Get a valid quote first.");
      return;
    }
    
    try {
      setStatus("Checking USDC approval...");

      const allowance = await publicClient?.readContract({
        address: usdcAddress,
        abi: USDC_ABI,
        functionName: "allowance",
        args: [address!, ammAddress],
      });

      const usdcAmount = BigInt(amount);
      
      if (!allowance || allowance < usdcAmount) {
        setStatus("Approving USDC...");
        const approveTx = await writeContractAsync({
          address: usdcAddress,
          abi: USDC_ABI,
          functionName: "approve",
          args: [ammAddress, usdcAmount],
        });
        setStatus(`Approval sent: ${approveTx}`);
        await publicClient?.waitForTransactionReceipt({ hash: approveTx });
      }

      setStatus("Buying...");
      const slippagePercent = parseFloat(slippage);
      const minOut = Math.floor((quote.tokensOut * (100 - slippagePercent)) / 100);
      
      if (minOut <= 0) {
        setStatus("Quote too small or slippage too high. Get a fresh quote.");
        return;
      }
      
      const swapTx = await writeContractAsync({
        address: ammAddress,
        abi: AMM_ABI,
        functionName: "buyWithUSDC",
        args: [conditionId as `0x${string}`, outcome === "yes", usdcAmount, BigInt(minOut)],
      });
      
      setStatus(`Buy sent: ${swapTx}`);
      await publicClient?.waitForTransactionReceipt({ hash: swapTx });
      setStatus("Success! Tokens received.");
      
    } catch (e: any) {
      setStatus(e.message || "Transaction failed");
    }
  }

  async function executeSell(ammAddress: `0x${string}`, ctfAddress: `0x${string}`) {
    if (!quote || !quote.usdcOut || quote.usdcOut <= 0) {
      setStatus("Get a valid quote first.");
      return;
    }
    
    try {
      setStatus("Checking token approval...");

      const isApproved = await publicClient?.readContract({
        address: ctfAddress,
        abi: CTF_ABI,
        functionName: "isApprovedForAll",
        args: [address!, ammAddress],
      });

      if (!isApproved) {
        setStatus("Approving outcome tokens...");
        const approveTx = await writeContractAsync({
          address: ctfAddress,
          abi: CTF_ABI,
          functionName: "setApprovalForAll",
          args: [ammAddress, true],
        });
        setStatus(`Approval sent: ${approveTx}`);
        await publicClient?.waitForTransactionReceipt({ hash: approveTx });
      }

      setStatus("Selling...");
      const slippagePercent = parseFloat(slippage);
      const minUsdc = Math.floor((quote.usdcOut * (100 - slippagePercent)) / 100);
      
      if (minUsdc <= 0) {
        setStatus("Quote too small or slippage too high. Get a fresh quote.");
        return;
      }

      const tokenAmount = BigInt(amount);
      
      const swapTx = await writeContractAsync({
        address: ammAddress,
        abi: AMM_ABI,
        functionName: "sellToUSDC",
        args: [conditionId as `0x${string}`, outcome === "yes", tokenAmount, BigInt(minUsdc)],
      });
      
      setStatus(`Sell sent: ${swapTx}`);
      await publicClient?.waitForTransactionReceipt({ hash: swapTx });
      setStatus("Success! USDC received.");
      
    } catch (e: any) {
      setStatus(e.message || "Transaction failed");
    }
  }

  return (
    <div className="card">
      <h3>AMM swap</h3>
      <p className="muted">CPMM on YES/NO. 100 bps fee (50 vault / 50 LPs).</p>
      
      <div className="row">
        <button className={mode === "buy" ? "btn" : "btn ghost"} onClick={() => { setMode("buy"); setQuote(null); }}>
          Buy
        </button>
        <button className={mode === "sell" ? "btn" : "btn ghost"} onClick={() => { setMode("sell"); setQuote(null); }}>
          Sell
        </button>
      </div>

      <div className="row">
        <button className={outcome === "yes" ? "btn yes" : "btn ghost"} onClick={() => { setOutcome("yes"); setQuote(null); }}>
          Yes
        </button>
        <button className={outcome === "no" ? "btn no" : "btn ghost"} onClick={() => { setOutcome("no"); setQuote(null); }}>
          No
        </button>
      </div>

      <label className="muted">{mode === "buy" ? "USDC in (6 decimals)" : "Tokens to sell"}</label>
      <input value={amount} onChange={(e) => { setAmount(e.target.value); setQuote(null); }} />
      
      <label className="muted">Slippage tolerance (%)</label>
      <input value={slippage} onChange={(e) => setSlippage(e.target.value)} />
      
      <button className="btn" style={{ marginTop: 12 }} onClick={refresh}>
        Get Quote
      </button>
      
      {quote ? (
        <>
          <pre className="muted">{JSON.stringify(quote, null, 2)}</pre>
          <button className="btn" onClick={swap}>
            Execute {mode === "buy" ? "Buy" : "Sell"}
          </button>
        </>
      ) : null}
      
      {status && <p className="muted">{status}</p>}
    </div>
  );
}
