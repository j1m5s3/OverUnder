"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getUserOperation } from "@coinbase/cdp-core";
import type { EvmAddress } from "./account";
import {
  classifyWaitResult,
  repollUserOperation,
  type UserOpOutcome,
  type WaitResult,
} from "./userOpOutcome";

export type { UserOpOutcome } from "./userOpOutcome";

type Network = Parameters<typeof getUserOperation>[0]["network"];

export type TrackTarget = { evmSmartAccount: EvmAddress; network: Network };

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Turns the status triple of useSendUserOperation / useWaitForUserOperation into
// a per-hash outcome. The hooks keep the previous operation's status for one
// render after a new hash is set, so an outcome only settles after this hash has
// been seen pending and, when data is present, only for a matching userOpHash.
//
// A hook "error" is only a failure when the op itself failed or was dropped. A
// failed poll becomes "unknown": we keep watching the hash (the hook may still
// report success) and re-poll getUserOperation ourselves with bounded backoff.
export function useUserOpOutcome(wait: WaitResult) {
  const [hash, setHash] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<UserOpOutcome>({ state: "idle" });
  const sawPending = useRef(false);
  const targetRef = useRef<TrackTarget | null>(null);
  const outcomeRef = useRef<UserOpOutcome>(outcome);
  const repollRun = useRef(0);
  const { status, data, error } = wait;

  const settle = useCallback((next: UserOpOutcome) => {
    outcomeRef.current = next;
    setOutcome(next);
  }, []);

  const cancelRepoll = useCallback(() => {
    repollRun.current += 1;
  }, []);

  useEffect(() => cancelRepoll, [cancelRepoll]);

  const startRepoll = useCallback(
    (opHash: string) => {
      const target = targetRef.current;
      if (!target) {
        settle({ ...(outcomeRef.current as Extract<UserOpOutcome, { state: "unknown" }>), gaveUp: true });
        return;
      }
      const run = ++repollRun.current;
      const cancelled = () => repollRun.current !== run;
      void repollUserOperation(opHash, {
        fetch: () =>
          getUserOperation({
            userOperationHash: opHash as `0x${string}`,
            evmSmartAccount: target.evmSmartAccount,
            network: target.network,
          }),
        sleep,
        cancelled,
      }).then((result) => {
        if (cancelled()) return;
        const current = outcomeRef.current;
        if (current.state !== "unknown" || current.hash !== opHash) return;
        if (result) {
          settle(result);
          setHash(null);
        } else {
          settle({ ...current, gaveUp: true });
        }
      });
    },
    [settle],
  );

  useEffect(() => {
    if (!hash) return;
    if (status === "pending") {
      sawPending.current = true;
      return;
    }
    if (!sawPending.current) return;
    const next = classifyWaitResult(outcomeRef.current, hash, { status, data, error });
    if (!next) return;
    if (next.state === "unknown") {
      settle(next);
      startRepoll(hash);
      return;
    }
    cancelRepoll();
    settle(next);
    setHash(null);
  }, [hash, status, data, error, settle, startRepoll, cancelRepoll]);

  const track = useCallback(
    (userOperationHash: string, target?: TrackTarget) => {
      cancelRepoll();
      sawPending.current = false;
      targetRef.current = target ?? null;
      setHash(userOperationHash);
      settle({ state: "pending", hash: userOperationHash });
    },
    [cancelRepoll, settle],
  );

  const reset = useCallback(() => {
    cancelRepoll();
    sawPending.current = false;
    targetRef.current = null;
    setHash(null);
    settle({ state: "idle" });
  }, [cancelRepoll, settle]);

  return { outcome, track, reset };
}
