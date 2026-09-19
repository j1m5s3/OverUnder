"use client";

import { useState } from "react";
import { api } from "@/shared/api/client";
import { useAccount, useConnect, useWriteContract, usePublicClient } from "wagmi";
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
  const [amount, setAmount] = useState("1.00");
  const [quote, setQuote] = useState<any>(null);
  const [status, setStatus] = useState("");
  const [slippage, setSlippage] = useState("0.5");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [quoting, setQuoting] = useState(false);
  
  const { address } = useAccount();
  const { connect, connectors } = useConnect();
  const { writeContractAsync } = useWriteContract();
  const publicClient = usePublicClient();

  const ammAddress = process.env.NEXT_PUBLIC_AMM_ADDRESS as `0x${string}` | undefined;
  const usdcAddress = process.env.NEXT_PUBLIC_USDC_ADDRESS as `0x${string}` | undefined;
  const ctfAddress = process.env.NEXT_PUBLIC_CTF_ADDRESS as `0x${string}` | undefined;
  
  const envConfigured = ammAddress && usdcAddress && ctfAddress;

  async function refresh() {
    if (!envConfigured) {
      setStatus("trading isn't configured on this deploy");
      return;
    }
    
    setQuoting(true);
    setStatus("quoting...");
    
    try {
      const amountNum = parseFloat(amount);
      if (isNaN(amountNum) || amountNum <= 0) {
        setStatus("enter a valid amount");
        setQuoting(false);
        return;
      }
      
      const usdcAmount = Math.floor(amountNum * 1_000_000);
      
      let q;
      if (mode === "buy") {
        q = await api(
          `/api/v1/amm/${encodeURIComponent(conditionId)}/quote?buy_yes=${outcome === "yes"}&usdc_in=${usdcAmount}`,
        );
      } else {
        q = await api(
          `/api/v1/amm/${encodeURIComponent(conditionId)}/quote?sell_yes=${outcome === "yes"}&token_amount=${usdcAmount}`,
        );
      }
      setQuote(q);
      setStatus("");
    } catch (e: any) {
      setStatus(e.message);
    } finally {
      setQuoting(false);
    }
  }

  function handleExecute() {
    if (!address) {
      if (connectors[0]) {
        connect({ connector: connectors[0] });
      }
      return;
    }

    if (!envConfigured) {
      setStatus("trading isn't configured on this deploy");
      return;
    }

    swap();
  }

  async function swap() {
    if (mode === "buy") {
      await executeBuy(ammAddress!, usdcAddress!);
    } else {
      await executeSell(ammAddress!, ctfAddress!);
    }
  }

  async function executeBuy(ammAddress: `0x${string}`, usdcAddress: `0x${string}`) {
    if (!quote || !quote.tokensOut || quote.tokensOut <= 0) {
      setStatus("get a valid quote first");
      return;
    }
    
    try {
      setStatus("checking usdc approval...");

      const allowance = await publicClient?.readContract({
        address: usdcAddress,
        abi: USDC_ABI,
        functionName: "allowance",
        args: [address!, ammAddress],
      });

      const amountNum = parseFloat(amount);
      const usdcAmount = BigInt(Math.floor(amountNum * 1_000_000));
      
      if (!allowance || allowance < usdcAmount) {
        setStatus("approving usdc...");
        const approveTx = await writeContractAsync({
          address: usdcAddress,
          abi: USDC_ABI,
          functionName: "approve",
          args: [ammAddress, usdcAmount],
        });
        setStatus(`approval submitted`);
        await publicClient?.waitForTransactionReceipt({ hash: approveTx });
      }

      setStatus("buying...");
      const slippagePercent = parseFloat(slippage);
      const minOut = Math.floor((quote.tokensOut * (100 - slippagePercent)) / 100);
      
      if (minOut <= 0) {
        setStatus("quote too small or slippage too high");
        return;
      }
      
      const swapTx = await writeContractAsync({
        address: ammAddress,
        abi: AMM_ABI,
        functionName: "buyWithUSDC",
        args: [conditionId as `0x${string}`, outcome === "yes", usdcAmount, BigInt(minOut)],
      });
      
      setStatus(`buy submitted`);
      await publicClient?.waitForTransactionReceipt({ hash: swapTx });
      setStatus("success! tokens received");
      
    } catch (e: any) {
      setStatus(e.message || "transaction failed");
    }
  }

  async function executeSell(ammAddress: `0x${string}`, ctfAddress: `0x${string}`) {
    if (!quote || !quote.usdcOut || quote.usdcOut <= 0) {
      setStatus("get a valid quote first");
      return;
    }
    
    try {
      setStatus("checking token approval...");

      const isApproved = await publicClient?.readContract({
        address: ctfAddress,
        abi: CTF_ABI,
        functionName: "isApprovedForAll",
        args: [address!, ammAddress],
      });

      if (!isApproved) {
        setStatus("approving outcome tokens...");
        const approveTx = await writeContractAsync({
          address: ctfAddress,
          abi: CTF_ABI,
          functionName: "setApprovalForAll",
          args: [ammAddress, true],
        });
        setStatus(`approval submitted`);
        await publicClient?.waitForTransactionReceipt({ hash: approveTx });
      }

      setStatus("selling...");
      const slippagePercent = parseFloat(slippage);
      const minUsdc = Math.floor((quote.usdcOut * (100 - slippagePercent)) / 100);
      
      if (minUsdc <= 0) {
        setStatus("quote too small or slippage too high");
        return;
      }

      const amountNum = parseFloat(amount);
      const tokenAmount = BigInt(Math.floor(amountNum * 1_000_000));
      
      const swapTx = await writeContractAsync({
        address: ammAddress,
        abi: AMM_ABI,
        functionName: "sellToUSDC",
        args: [conditionId as `0x${string}`, outcome === "yes", tokenAmount, BigInt(minUsdc)],
      });
      
      setStatus(`sell submitted`);
      await publicClient?.waitForTransactionReceipt({ hash: swapTx });
      setStatus("success! usdc received");
      
    } catch (e: any) {
      setStatus(e.message || "transaction failed");
    }
  }

  function formatQuote() {
    if (!quote) return null;
    
    const action = mode === "buy" ? "buy" : "sell";
    const outcomeLabel = outcome;
    const slippagePercent = parseFloat(slippage);
    
    if (mode === "buy" && quote.tokensOut) {
      const tokensOut = (quote.tokensOut / 1_000_000).toFixed(2);
      const minOut = ((quote.tokensOut * (100 - slippagePercent)) / 100 / 1_000_000).toFixed(2);
      return `${action} ${outcomeLabel} · you get ~${tokensOut} ${outcomeLabel} / fee 1% / min after slip ~${minOut}`;
    } else if (mode === "sell" && quote.usdcOut) {
      const usdcOut = (quote.usdcOut / 1_000_000).toFixed(2);
      const minUsdc = ((quote.usdcOut * (100 - slippagePercent)) / 100 / 1_000_000).toFixed(2);
      return `${action} ${outcomeLabel} · you get ~${usdcOut} usdc / fee 1% / min after slip ~${minUsdc}`;
    }
    
    return null;
  }

  const executeLabel = !address 
    ? `connect to ${mode}` 
    : `${mode} ${outcome}`;

  const amountLabel = mode === "buy" 
    ? "amount (usdc)" 
    : `amount (${outcome})`;

  return (
    <div className="card">
      <h3>Trade</h3>
      <p className="muted">1% fee</p>
      
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

      <label className="muted">{amountLabel}</label>
      <input value={amount} onChange={(e) => { setAmount(e.target.value); setQuote(null); }} />
      
      <button className="btn" style={{ marginTop: 12 }} onClick={refresh} disabled={quoting}>
        get quote
      </button>
      
      {quote && formatQuote() ? (
        <>
          <p className="muted" style={{ marginTop: 12 }}>{formatQuote()}</p>
          <button className="btn" onClick={handleExecute} disabled={!quote || !envConfigured}>
            {executeLabel}
          </button>
        </>
      ) : null}
      
      <div style={{ marginTop: 16 }}>
        <button 
          className="btn ghost" 
          onClick={() => setShowAdvanced(!showAdvanced)}
          style={{ fontSize: 13, padding: "6px 10px" }}
        >
          {showAdvanced ? "Hide" : "Show"} Advanced
        </button>
        {showAdvanced && (
          <div style={{ marginTop: 8 }}>
            <label className="muted">slippage tolerance (%)</label>
            <input value={slippage} onChange={(e) => setSlippage(e.target.value)} />
          </div>
        )}
      </div>
      
      {status && <p className="muted" style={{ marginTop: 12 }}>{status}</p>}
    </div>
  );
}
