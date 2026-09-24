"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  useCurrentUser,
  useIsSignedIn,
  useSendUserOperation,
  useWaitForUserOperation,
} from "@coinbase/cdp-hooks";
import { api, apiErrorDetail, apiErrorStatus } from "@/shared/api/client";
import { useApiSession } from "@/shared/api/session";
import { smartAccountOf } from "@/features/wallet/account";
import { useUserOpOutcome } from "@/features/wallet/useUserOpOutcome";
import { RESOLUTION_POLICY } from "@/features/oracle/resolutionPolicy";
import {
  CRITERIA_MAX_CHARS,
  ListingSendGuard,
  QUESTION_MAX_BYTES,
  charCount,
  closeTimeBounds,
  confirmFailedMessage,
  confirmWithRetry,
  datetimeLocalToUnix,
  defaultCloseTime,
  eligibilityGate,
  formatDuration,
  listingTotals,
  microsToUsdc,
  normalizeEligibility,
  rejectedMessage,
  toCdpCalls,
  unixToDatetimeLocal,
  usdcToMicros,
  utf8Bytes,
  validateListing,
  type ListingConfig,
  type ListingEligibility,
  type PreparedListing,
} from "./listing";

type Phase =
  | "form"
  | "preparing"
  | "sending"
  | "mining"
  | "confirming"
  | "confirm-failed"
  | "rejected"
  | "done";

type Submitted = { prepared: PreparedListing; criteria: string };

const MAX_TIMER_MS = 2 ** 31 - 1;

function nowSec(): number {
  return Math.floor(Date.now() / 1000);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const labelStyle = { fontSize: 11, textTransform: "uppercase" as const, letterSpacing: "0.05em" };

export function ListMarketForm() {
  const router = useRouter();
  const { isSignedIn } = useIsSignedIn();
  const { currentUser } = useCurrentUser();
  const smart = smartAccountOf(currentUser);
  const { ready: sessionReady } = useApiSession(smart);
  const { sendUserOperation } = useSendUserOperation();
  const [opHash, setOpHash] = useState<`0x${string}` | undefined>();
  const wait = useWaitForUserOperation({
    userOperationHash: opHash,
    evmSmartAccount: smart,
    network: "base-sepolia",
    enabled: Boolean(opHash && smart),
  });
  const { outcome, track, reset } = useUserOpOutcome(wait);

  const [config, setConfig] = useState<ListingConfig | null>(null);
  const [configUnavailable, setConfigUnavailable] = useState(false);
  const [eligibility, setEligibility] = useState<ListingEligibility | null>(null);
  const [eligibilityError, setEligibilityError] = useState("");
  const [eligibilityNonce, setEligibilityNonce] = useState(0);

  const [question, setQuestion] = useState("");
  const [criteria, setCriteria] = useState("");
  const [closeLocal, setCloseLocal] = useState("");
  const [seedText, setSeedText] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [phase, setPhase] = useState<Phase>("form");
  const [status, setStatus] = useState("");
  const [submitted, setSubmitted] = useState<Submitted | null>(null);
  // True while we register a listing whose transaction we never saw mined.
  const [unverified, setUnverifiedState] = useState(false);
  const unverifiedRef = useRef(false);
  const aliveRef = useRef(true);
  const confirmRunRef = useRef(0);
  // Survives re-renders: one in-flight listing at a time, and a batch whose
  // userOperationHash exists is never sent again.
  const guardRef = useRef<ListingSendGuard | null>(null);
  if (guardRef.current === null) guardRef.current = new ListingSendGuard();
  const guard = guardRef.current;

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  useEffect(() => {
    let live = true;
    api("/api/v1/markets/listing/config")
      .then((c: ListingConfig) => {
        if (live) setConfig({ ...c, enabled: Boolean(c?.enabled) });
      })
      .catch(() => {
        if (!live) return;
        setConfigUnavailable(true);
        setConfig({ enabled: false });
      });
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    if (!config?.enabled) return;
    const now = nowSec();
    setCloseLocal((v) => v || unixToDatetimeLocal(defaultCloseTime(config, now)));
    setSeedText((v) => v || (config.minSeedUsdc ? microsToUsdc(config.minSeedUsdc) : ""));
  }, [config]);

  useEffect(() => {
    if (!config?.enabled || !isSignedIn || !smart || !sessionReady) {
      setEligibility(null);
      setEligibilityError("");
      return;
    }
    let live = true;
    api("/api/v1/markets/listing/eligibility")
      .then((e: unknown) => {
        if (!live) return;
        setEligibilityError("");
        setEligibility(normalizeEligibility(e));
      })
      .catch((err) => {
        if (!live) return;
        setEligibility(null);
        setEligibilityError(
          apiErrorStatus(err) === 401
            ? "Your session expired. Sign out and sign in again to list."
            : `Couldn't check whether you can list: ${apiErrorDetail(err)}`,
        );
      });
    return () => {
      live = false;
    };
  }, [config, isSignedIn, smart, sessionReady, eligibilityNonce]);

  // Re-check eligibility once the cooldown runs out.
  useEffect(() => {
    const remaining = eligibility?.cooldownRemaining ?? 0;
    if (remaining <= 0) return;
    const delay = remaining * 1000 + 1000;
    if (delay > MAX_TIMER_MS) return;
    const timer = setTimeout(() => setEligibilityNonce((n) => n + 1), delay);
    return () => clearTimeout(timer);
  }, [eligibility]);

  // A definitive failure returns to the form. "unknown" means the status poll
  // failed, not the transaction: the op may still land, so go straight to
  // confirm, which retries "not on chain yet" and redirects once it is mined.
  useEffect(() => {
    if (!submitted) return;
    if (phase === "mining") {
      if (outcome.state === "success") {
        void confirmListing(submitted, false);
      } else if (outcome.state === "unknown") {
        void confirmListing(submitted, true);
      } else if (outcome.state === "error") {
        fail(`The listing transaction failed: ${outcome.message}`);
      }
      return;
    }
    if (!unverified || (phase !== "confirming" && phase !== "confirm-failed")) return;
    if (outcome.state === "error") {
      // The re-poll found the op failed or dropped while we were confirming.
      fail(`The listing transaction failed: ${outcome.message}`);
    } else if (outcome.state === "success") {
      if (phase === "confirm-failed") void confirmListing(submitted, false);
      else setUnverified(false);
    }
    // confirmListing/fail only use state setters, refs and the submitted
    // snapshot, so they are intentionally left out of the dependency list.
  }, [outcome, phase, submitted]);

  function setUnverified(value: boolean) {
    unverifiedRef.current = value;
    setUnverifiedState(value);
  }

  // Back to the form. Only reached before anything was sent or after the op
  // definitively failed, so nothing landed and a fresh prepare is safe; the
  // old batch stays blocked in the guard.
  function fail(message: string) {
    confirmRunRef.current += 1;
    guard.release();
    setPhase("form");
    setStatus(message);
    setUnverified(false);
    setOpHash(undefined);
    reset();
    // The cooldown gate should reflect what actually happened on chain.
    setEligibilityNonce((n) => n + 1);
  }

  // After a terminal rejection: the user may list a different market (a new
  // prepare, a new batch). The rejected batch is never re-sent.
  function startOver() {
    confirmRunRef.current += 1;
    guard.release();
    setSubmitted(null);
    setPhase("form");
    setStatus("");
    setUnverified(false);
    setOpHash(undefined);
    reset();
    setEligibilityNonce((n) => n + 1);
  }

  // `txUnverified`: we never saw the op mined, so failure copy must not claim
  // the market is on chain. Only /confirm is retried here, never the batch.
  async function confirmListing(pending: Submitted, txUnverified: boolean) {
    const run = ++confirmRunRef.current;
    const stale = () => !aliveRef.current || confirmRunRef.current !== run;
    const { conditionId } = pending.prepared;
    setUnverified(txUnverified);
    setPhase("confirming");
    setStatus(
      txUnverified
        ? "Submitted. Still confirming the transaction and registering your market..."
        : "Transaction confirmed. Registering your market...",
    );
    const result = await confirmWithRetry({
      confirm: () =>
        api("/api/v1/markets/listing/confirm", {
          method: "POST",
          body: JSON.stringify({ conditionId, resolutionCriteria: pending.criteria }),
        }),
      errorInfo: (err) => ({ status: apiErrorStatus(err), detail: apiErrorDetail(err) }),
      sleep,
      cancelled: stale,
      onRetry: (_attempt, cause) => {
        if (stale() || cause !== "server") return;
        setStatus("The API had a problem registering your market. Retrying...");
      },
    });
    if (result.state === "cancelled" || stale()) return;
    if (result.state === "confirmed") {
      setPhase("done");
      setStatus("Market listed. Opening it...");
      router.push(`/markets/${encodeURIComponent(conditionId)}`);
      return;
    }
    if (result.state === "rejected") {
      // A 422 means the API read the market on chain, so the op did land.
      setUnverified(false);
      setOpHash(undefined);
      reset();
      setPhase("rejected");
      setStatus(rejectedMessage(result.reason));
      return;
    }
    setPhase("confirm-failed");
    setStatus(confirmFailedMessage(unverifiedRef.current, result.detail));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!config || !smart || phase !== "form" || guard.busy) return;
    const input = {
      question: question.trim(),
      resolutionCriteria: criteria.trim(),
      closeTime: datetimeLocalToUnix(closeLocal),
      seedUsdc: usdcToMicros(seedText),
    };
    const problems = validateListing(input, config, nowSec());
    setErrors(problems);
    if (problems.length > 0) return;
    // Synchronous, unlike `phase`: a second submit in the same tick stops here.
    if (!guard.begin()) return;

    setStatus("");
    setPhase("preparing");
    let prepared: PreparedListing;
    try {
      prepared = await api("/api/v1/markets/listing/prepare", {
        method: "POST",
        body: JSON.stringify(input),
      });
    } catch (err) {
      fail(`Couldn't prepare the listing: ${apiErrorDetail(err)}`);
      return;
    }

    let calls;
    try {
      calls = toCdpCalls(prepared.calls, [config.usdc, config.factory]);
    } catch (err: any) {
      fail(`The listing couldn't be prepared safely: ${err?.message || err}`);
      return;
    }
    if (!guard.canSend(prepared.conditionId)) {
      // Unreachable while prepare mints a fresh salt; never send a batch twice.
      fail("This listing was already submitted. Refresh the page to see its status.");
      return;
    }

    setSubmitted({ prepared, criteria: input.resolutionCriteria });
    setPhase("sending");
    let userOperationHash: `0x${string}`;
    try {
      ({ userOperationHash } = await sendUserOperation({
        evmSmartAccount: smart,
        network: "base-sepolia",
        calls,
        useCdpPaymaster: true,
      }));
    } catch (err: any) {
      fail(err?.message || "The transaction was not sent.");
      return;
    }
    guard.markSent(prepared.conditionId, userOperationHash);
    if (!aliveRef.current) return;
    setOpHash(userOperationHash);
    track(userOperationHash, { evmSmartAccount: smart, network: "base-sepolia" });
    setPhase("mining");
    setStatus("Submitted. Waiting for the transaction to confirm...");
  }

  if (config === null) {
    return <div className="card skeleton" style={{ height: 240 }}></div>;
  }

  if (!config.enabled) {
    return (
      <div className="card">
        <p style={{ marginTop: 0 }}>
          {configUnavailable ? "Listing isn't available right now." : "Listing isn't open on this deploy yet."}
        </p>
        <p className="muted" style={{ marginBottom: 0 }}>
          Markets are listed by the operator for now. Check back later.
        </p>
      </div>
    );
  }

  const now = nowSec();
  const seedMicros = usdcToMicros(seedText);
  const totals = listingTotals(config, seedMicros);
  const bounds = closeTimeBounds(config, now);
  const questionBytes = utf8Bytes(question.trim());
  const criteriaChars = charCount(criteria.trim());
  const signedIn = Boolean(isSignedIn && smart);
  const busy = phase !== "form";

  let gate: string | null = null;
  if (!signedIn) gate = "Sign in to list a market.";
  else if (!sessionReady) gate = "Connecting your account...";
  else if (eligibilityError) gate = eligibilityError;
  else if (!eligibility) gate = "Checking whether you can list...";
  else gate = eligibilityGate(eligibility, config);
  // The gate only guards new listings; an in-flight one keeps its status line.
  const canSubmit = signedIn && sessionReady && eligibility !== null && gate === null && acknowledged && !busy;

  const buttonLabel = !signedIn
    ? "Sign in to list"
    : phase === "preparing"
      ? "Preparing..."
      : phase === "sending"
        ? "Sending..."
        : phase === "mining"
          ? "Waiting for confirmation..."
          : phase === "confirming"
            ? "Registering market..."
            : phase === "done"
              ? "Opening market..."
              : phase === "confirm-failed"
                ? unverified
                  ? "Waiting for the transaction"
                  : "Listed on chain"
                : phase === "rejected"
                  ? "Listing rejected"
                  : `List market · ${microsToUsdc(totals.total)} USDC`;

  return (
    <form className="card" onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {gate ? (
        <div className="banner" role="status">
          {gate}
        </div>
      ) : null}
      {eligibility && eligibility.pending > 0 && !busy ? (
        <div className="muted">
          You have {eligibility.pending} listing{eligibility.pending === 1 ? "" : "s"} waiting to be confirmed.
        </div>
      ) : null}

      <div className="field">
        <label className="muted" style={labelStyle} htmlFor="listing-question">
          Question
        </label>
        <input
          id="listing-question"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Will the city approve the new stadium budget by March 31?"
          disabled={busy}
        />
        <span className="field-hint" style={questionBytes > QUESTION_MAX_BYTES ? { color: "var(--no)" } : undefined}>
          {questionBytes}/{QUESTION_MAX_BYTES} bytes. A yes/no question with a clear answer after close.
        </span>
      </div>

      <div className="field">
        <label className="muted" style={labelStyle} htmlFor="listing-criteria">
          Resolution criteria
        </label>
        <textarea
          id="listing-criteria"
          rows={5}
          value={criteria}
          onChange={(e) => setCriteria(e.target.value)}
          placeholder="Resolves YES if the council's published minutes show the budget passed on or before March 31. Resolves NO otherwise. Source: the city's official website."
          disabled={busy}
        />
        <span className="field-hint" style={criteriaChars > CRITERIA_MAX_CHARS ? { color: "var(--no)" } : undefined}>
          {criteriaChars}/{CRITERIA_MAX_CHARS} characters. Say exactly what makes it YES, what makes it NO, and the
          source the agents should check.
        </span>
      </div>

      <div className="field">
        <label className="muted" style={labelStyle} htmlFor="listing-close">
          Close time
        </label>
        <input
          id="listing-close"
          type="datetime-local"
          value={closeLocal}
          min={unixToDatetimeLocal(Math.ceil(bounds.earliest / 60) * 60)}
          max={bounds.latest !== null ? unixToDatetimeLocal(bounds.latest) : undefined}
          onChange={(e) => setCloseLocal(e.target.value)}
          disabled={busy}
        />
        <span className="field-hint">
          Trading stops at close. Between {formatDuration(config.minLeadSeconds ?? 3600)} and{" "}
          {config.maxHorizonSeconds ? formatDuration(config.maxHorizonSeconds) : "any time"} from now, in your local time.
        </span>
      </div>

      <div className="field">
        <label className="muted" style={labelStyle} htmlFor="listing-seed">
          Seed (USDC)
        </label>
        <input
          id="listing-seed"
          inputMode="decimal"
          value={seedText}
          onChange={(e) => setSeedText(e.target.value)}
          disabled={busy}
        />
        <span className="field-hint">
          Minimum {microsToUsdc(config.minSeedUsdc ?? 0)} USDC. Listing fee {microsToUsdc(totals.fee)} USDC. Total from
          your wallet: {microsToUsdc(totals.total)} USDC. Gas is sponsored.
        </span>
      </div>

      <div className="banner" style={{ fontSize: 13, lineHeight: 1.5 }}>
        Your seed funds this market&apos;s liquidity pool. You own the LP position and can withdraw it after the market
        resolves. After close: {RESOLUTION_POLICY}{" "}
        The operator may pause a market.
        <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8, color: "var(--text)" }}>
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
            disabled={busy}
            style={{ width: "auto" }}
          />
          I understand
        </label>
      </div>

      {errors.length > 0 ? (
        <ul className="form-errors">
          {errors.map((error) => (
            <li key={error}>{error}</li>
          ))}
        </ul>
      ) : null}

      <button type="submit" className="btn" disabled={!canSubmit}>
        {buttonLabel}
      </button>

      {phase === "confirm-failed" && submitted ? (
        <div className="row">
          <button type="button" className="btn ghost" onClick={() => void confirmListing(submitted, unverified)}>
            Try registering again
          </button>
          <button
            type="button"
            className="btn ghost"
            onClick={() => router.push(`/markets/${encodeURIComponent(submitted.prepared.conditionId)}`)}
          >
            Open market
          </button>
        </div>
      ) : null}

      {phase === "rejected" ? (
        <div className="banner" role="alert" style={{ borderColor: "var(--no)" }}>
          <p style={{ marginTop: 0 }}>{status}</p>
          <button type="button" className="btn ghost" onClick={startOver}>
            List a different market
          </button>
        </div>
      ) : status ? (
        <p className="muted" role="status" style={{ margin: 0 }}>
          {status}
        </p>
      ) : null}
    </form>
  );
}
