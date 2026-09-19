"use client";

import { useState } from "react";
import { api } from "@/shared/api/client";
import { useAccount, useWriteContract, usePublicClient } from "wagmi";
import { parseAbi } from "viem";

const AMM_ABI = parseAbi([
  "function buyWithUSDC(bytes32 conditionId, bool buyYes, uint256 usdcIn, uint256 minOut) external returns (uint256)",
  "function quoteBuy(bytes32 conditionId, bool buyYes, uint256 usdcIn) external view returns (uint256)",
]);

const USDC_ABI = parseAbi([
  "function approve(address spender, uint256 amount) external returns (bool)",
  "function allowance(address owner, address spender) external view returns (uint256)",
]);

export function AmmSwap({ conditionId }: { conditionId: string }) {
  const [usdcIn, setUsdcIn] = useState("1000000");
  const [quote, setQuote] = useState<any>(null);
  const [buyYes, setBuyYes] = useState(true);
  const [status, setStatus] = useState("");
  const [slippage, setSlippage] = useState("0.5");
  
  const { address } = useAccount();
  const { writeContractAsync } = useWriteContract();
  const publicClient = usePublicClient();

  async function refresh() {
    try {
      const q = await api(
        `/api/v1/amm/${encodeURIComponent(conditionId)}/quote?buy_yes=${buyYes}&usdc_in=${usdcIn}`,
      );
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
    if (!quote || !quote.tokensOut || quote.tokensOut <= 0) {
      setStatus("Get a valid quote first.");
      return;
    }
    
    try {
      setStatus("Checking approval...");
      
      const ammAddress = process.env.NEXT_PUBLIC_AMM_ADDRESS as `0x${string}`;
      const usdcAddress = process.env.NEXT_PUBLIC_USDC_ADDRESS as `0x${string}`;
      
      if (!ammAddress || !usdcAddress) {
        setStatus("Missing contract addresses in env");
        return;
      }

      const allowance = await publicClient?.readContract({
        address: usdcAddress,
        abi: USDC_ABI,
        functionName: "allowance",
        args: [address, ammAddress],
      });

      const usdcAmount = BigInt(usdcIn);
      
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

      setStatus("Swapping...");
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
        args: [conditionId as `0x${string}`, buyYes, usdcAmount, BigInt(minOut)],
      });
      
      setStatus(`Swap sent: ${swapTx}`);
      await publicClient?.waitForTransactionReceipt({ hash: swapTx });
      setStatus("Success! Tokens received.");
      
    } catch (e: any) {
      setStatus(e.message || "Transaction failed");
    }
  }

  return (
    <div className="card">
      <h3>AMM swap</h3>
      <p className="muted">CPMM on YES/NO. 100 bps fee (50 vault / 50 LPs).</p>
      <div className="row">
        <button className={buyYes ? "btn yes" : "btn ghost"} onClick={() => setBuyYes(true)}>
          Buy Yes
        </button>
        <button className={!buyYes ? "btn no" : "btn ghost"} onClick={() => setBuyYes(false)}>
          Buy No
        </button>
      </div>
      <label className="muted">USDC in (6 decimals)</label>
      <input value={usdcIn} onChange={(e) => setUsdcIn(e.target.value)} />
      <label className="muted">Slippage tolerance (%)</label>
      <input value={slippage} onChange={(e) => setSlippage(e.target.value)} />
      <button className="btn" style={{ marginTop: 12 }} onClick={refresh}>
        Get Quote
      </button>
      {quote ? (
        <>
          <pre className="muted">{JSON.stringify(quote, null, 2)}</pre>
          <button className="btn" onClick={swap}>
            Execute Swap
          </button>
        </>
      ) : null}
      {status && <p className="muted">{status}</p>}
    </div>
  );
}
