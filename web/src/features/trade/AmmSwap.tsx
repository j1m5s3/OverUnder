"use client";

import { useState, useEffect, useRef } from "react";
import { api } from "@/shared/api/client";
import { useCurrentUser, useIsSignedIn, useSendUserOperation } from "@coinbase/cdp-hooks";
import { encodeFunctionData, parseAbi } from "viem";

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

function formatMultiplier(probability: number): string {
  if (!Number.isFinite(probability) || probability <= 0 || probability >= 1) return "—";
  const multiplier = 1 / probability;
  return multiplier < 10 ? multiplier.toFixed(2) : multiplier.toFixed(1);
}

function calculateImpliedProbability(quote: any, mode: "buy" | "sell", outcome: "yes" | "no", amountNum: number): number | null {
  if (!quote || amountNum <= 0) return null;
  
  if (mode === "buy" && quote.tokensOut) {
    const tokensOut = quote.tokensOut / 1_000_000;
    return tokensOut > 0 ? amountNum / tokensOut : null;
  }
  
  return null;
}

function smartAccountOf(
  user: { evmSmartAccounts?: Array<string | { address?: string }> } | null | undefined,
): `0x${string}` | undefined {
  const account = user?.evmSmartAccounts?.[0];
  const address = typeof account === "string" ? account : account?.address;
  return address as `0x${string}` | undefined;
}

export function AmmSwap({
  conditionId,
  initialSide,
  question,
}: {
  conditionId: string;
  initialSide?: "yes" | "no";
  question?: string;
}) {
  const [mode, setMode] = useState<"buy" | "sell">("buy");
  const [outcome, setOutcome] = useState<"yes" | "no">(initialSide || "yes");
  const [amount, setAmount] = useState("1.00");
  const [quote, setQuote] = useState<any>(null);
  const [status, setStatus] = useState("");
  const [slippage, setSlippage] = useState("0.5");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [quoting, setQuoting] = useState(false);
  
  const { isSignedIn } = useIsSignedIn();
  const { currentUser } = useCurrentUser();
  const { sendUserOperation } = useSendUserOperation();
  const address = smartAccountOf(currentUser);
  const smartAccount = currentUser?.evmSmartAccounts?.[0];

  const ammAddress = process.env.NEXT_PUBLIC_AMM_ADDRESS as `0x${string}` | undefined;
  const usdcAddress = process.env.NEXT_PUBLIC_USDC_ADDRESS as `0x${string}` | undefined;
  const ctfAddress = process.env.NEXT_PUBLIC_CTF_ADDRESS as `0x${string}` | undefined;
  
  const envConfigured = ammAddress && usdcAddress && ctfAddress;
  
  const requestIdRef = useRef(0);

  useEffect(() => {
    requestIdRef.current++;
    setQuote(null);
    setQuoting(true);
    
    const debounceTimer = setTimeout(() => {
      refresh();
    }, 300);
    
    return () => clearTimeout(debounceTimer);
  }, [amount, outcome, mode, conditionId]);

  async function refresh() {
    if (!envConfigured) {
      setStatus("trading isn't configured on this deploy");
      setQuoting(false);
      return;
    }
    
    const requestId = requestIdRef.current;
    setStatus("");
    
    try {
      const amountNum = parseFloat(amount);
      if (isNaN(amountNum) || amountNum <= 0) {
        if (requestId === requestIdRef.current) {
          setQuoting(false);
        }
        return;
      }
      
      const microAmount = Math.round(amountNum * 1_000_000);
      
      let q;
      if (mode === "buy") {
        q = await api(
          `/api/v1/amm/${encodeURIComponent(conditionId)}/quote?buy_yes=${outcome === "yes"}&usdc_in=${microAmount}`,
        );
      } else {
        q = await api(
          `/api/v1/amm/${encodeURIComponent(conditionId)}/quote?sell_yes=${outcome === "yes"}&token_amount=${microAmount}`,
        );
      }
      
      if (requestId === requestIdRef.current) {
        setQuote(q);
        setStatus("");
      }
    } catch (e: any) {
      if (requestId === requestIdRef.current) {
        setQuote(null);
        setStatus(e.message);
      }
    } finally {
      if (requestId === requestIdRef.current) {
        setQuoting(false);
      }
    }
  }

  function handleExecute() {
    if (!isSignedIn || !address) {
      setStatus("sign in to trade");
      return;
    }

    if (!envConfigured) {
      setStatus("trading isn't configured on this deploy");
      return;
    }

    if (!quote) {
      setStatus("waiting for quote...");
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

  async function executeBuy(amm: `0x${string}`, usdc: `0x${string}`) {
    if (!quote || !quote.tokensOut || quote.tokensOut <= 0) {
      setStatus("get a valid quote first");
      return;
    }
    if (!address) {
      setStatus("sign in to trade");
      return;
    }
    
    try {
      const amountNum = parseFloat(amount);
      const usdcAmount = BigInt(Math.round(amountNum * 1_000_000));
      const slippagePercent = parseFloat(slippage);
      const minOut = Math.floor((quote.tokensOut * (100 - slippagePercent)) / 100);
      if (minOut <= 0) {
        setStatus("quote too small or slippage too high");
        return;
      }

      setStatus("buying...");
      await sendUserOperation({
        evmSmartAccount: smartAccount,
        network: "base-sepolia",
        calls: [
          {
            to: usdc,
            value: 0n,
            data: encodeFunctionData({
              abi: USDC_ABI,
              functionName: "approve",
              args: [amm, usdcAmount],
            }),
          },
          {
            to: amm,
            value: 0n,
            data: encodeFunctionData({
              abi: AMM_ABI,
              functionName: "buyWithUSDC",
              args: [conditionId as `0x${string}`, outcome === "yes", usdcAmount, BigInt(minOut)],
            }),
          },
        ],
        useCdpPaymaster: true,
      });
      setStatus("success! tokens received");
    } catch (e: any) {
      setStatus(e.message || "transaction failed");
    }
  }

  async function executeSell(amm: `0x${string}`, ctf: `0x${string}`) {
    if (!quote || !quote.usdcOut || quote.usdcOut <= 0) {
      setStatus("get a valid quote first");
      return;
    }
    if (!address) {
      setStatus("sign in to trade");
      return;
    }
    
    try {
      const slippagePercent = parseFloat(slippage);
      const minUsdc = Math.floor((quote.usdcOut * (100 - slippagePercent)) / 100);
      if (minUsdc <= 0) {
        setStatus("quote too small or slippage too high");
        return;
      }
      const amountNum = parseFloat(amount);
      const tokenAmount = BigInt(Math.round(amountNum * 1_000_000));

      setStatus("selling...");
      await sendUserOperation({
        evmSmartAccount: smartAccount,
        network: "base-sepolia",
        calls: [
          {
            to: ctf,
            value: 0n,
            data: encodeFunctionData({
              abi: CTF_ABI,
              functionName: "setApprovalForAll",
              args: [amm, true],
            }),
          },
          {
            to: amm,
            value: 0n,
            data: encodeFunctionData({
              abi: AMM_ABI,
              functionName: "sellToUSDC",
              args: [conditionId as `0x${string}`, outcome === "yes", tokenAmount, BigInt(minUsdc)],
            }),
          },
        ],
        useCdpPaymaster: true,
      });
      setStatus("success! usdc received");
    } catch (e: any) {
      setStatus(e.message || "transaction failed");
    }
  }

  function formatPayout() {
    if (!quote) return null;
    
    const stakeNum = parseFloat(amount);
    if (isNaN(stakeNum)) return null;
    
    if (mode === "buy" && quote.tokensOut) {
      const tokensOut = quote.tokensOut / 1_000_000;
      const profit = tokensOut - stakeNum;
      return `stake $${stakeNum.toFixed(2)} → to win ~$${profit.toFixed(2)}`;
    } else if (mode === "sell" && quote.usdcOut) {
      const usdcOut = (quote.usdcOut / 1_000_000).toFixed(2);
      return `receive ~$${usdcOut}`;
    }
    
    return null;
  }

  const executeLabel = !address 
    ? mode === "buy" 
      ? `sign in to buy ${outcome}`
      : `sign in to sell ${outcome}`
    : mode === "buy"
      ? `buy ${outcome}`
      : `sell ${outcome}`;

  const amountLabel = mode === "buy" 
    ? "stake ($)" 
    : `amount (${outcome})`;

  const amountNum = parseFloat(amount);
  const impliedProb = calculateImpliedProbability(quote, mode, outcome, amountNum);
  const multiplier = impliedProb ? formatMultiplier(impliedProb) : null;

  function addStake(chipAmount: number) {
    const current = parseFloat(amount) || 0;
    setAmount((current + chipAmount).toFixed(2));
  }

  return (
    <div className="card">
      <h3>Trade</h3>
      {question ? <p className="muted" style={{ marginTop: 0 }}>{question}</p> : null}
      <p className="muted">1% fee</p>

      <div className="row">
        <button 
          className={outcome === "yes" ? "btn yes" : "btn ghost"} 
          onClick={() => setOutcome("yes")}
          style={{ flex: 1, position: "relative" }}
        >
          <div>Yes</div>
          {mode === "buy" && multiplier && outcome === "yes" && (
            <div style={{ fontSize: "11px", marginTop: "2px", opacity: 0.8 }}>
              {multiplier}×
            </div>
          )}
        </button>
        <button 
          className={outcome === "no" ? "btn no" : "btn ghost"} 
          onClick={() => setOutcome("no")}
          style={{ flex: 1, position: "relative" }}
        >
          <div>No</div>
          {mode === "buy" && multiplier && outcome === "no" && (
            <div style={{ fontSize: "11px", marginTop: "2px", opacity: 0.8 }}>
              {multiplier}×
            </div>
          )}
        </button>
      </div>

      <label className="muted">{amountLabel}</label>
      <input value={amount} onChange={(e) => setAmount(e.target.value)} />
      
      {mode === "buy" && (
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn ghost" style={{ fontSize: 12, padding: "6px 10px" }} onClick={() => addStake(5)}>
            +$5
          </button>
          <button className="btn ghost" style={{ fontSize: 12, padding: "6px 10px" }} onClick={() => addStake(10)}>
            +$10
          </button>
          <button className="btn ghost" style={{ fontSize: 12, padding: "6px 10px" }} onClick={() => addStake(25)}>
            +$25
          </button>
        </div>
      )}
      
      {quoting && (
        <p className="muted" style={{ marginTop: 12 }}>quoting...</p>
      )}
      
      {!quoting && formatPayout() && (
        <>
          <p className="muted" style={{ marginTop: 12 }}>{formatPayout()}</p>
          <p className="muted" style={{ marginTop: 4, fontSize: 12 }}>includes 1% fee</p>
        </>
      )}
      
      <button 
        className="btn" 
        style={{ marginTop: 12 }} 
        onClick={handleExecute} 
        disabled={!envConfigured || (Boolean(address) && (!quote || quoting))}
      >
        {executeLabel}
      </button>
      
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
            <div className="row">
              <button className={mode === "buy" ? "btn" : "btn ghost"} onClick={() => setMode("buy")}>
                Buy
              </button>
              <button className={mode === "sell" ? "btn" : "btn ghost"} onClick={() => setMode("sell")}>
                Sell
              </button>
            </div>
            <label className="muted" style={{ marginTop: 8 }}>slippage tolerance (%)</label>
            <input value={slippage} onChange={(e) => setSlippage(e.target.value)} />
          </div>
        )}
      </div>
      
      {status && <p className="muted" style={{ marginTop: 12 }}>{status}</p>}
    </div>
  );
}
