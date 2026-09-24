// Pure outcome logic for a CDP user operation, kept free of React and the CDP
// SDK so node tests can import it. useUserOpOutcome wires it to the hooks.
//
// The CDP wait hook reports status "error" for two different things: the op
// finished badly (data.status "failed"/"dropped"), or one getUserOperation poll
// threw (network blip, CDP 5xx, rate limit) and the hook stopped watching. Only
// the first is a failed transaction. The second is "unknown": the op may still
// be included, so callers must not let the user resubmit as if it failed.

export type UserOpOutcome =
  | { state: "idle" }
  | { state: "pending"; hash: string }
  | { state: "success"; hash: string; transactionHash?: string }
  | { state: "error"; hash: string; message: string }
  // gaveUp: our own re-poll budget ran out without a final status.
  | { state: "unknown"; hash: string; message: string; gaveUp: boolean };

export type WaitStatus = "idle" | "pending" | "success" | "error";

export type WaitData = {
  userOpHash?: string;
  transactionHash?: string;
  status?: string;
  receipts?: Array<{ revert?: { message?: string } }>;
};

export type WaitResult = {
  status: WaitStatus;
  data?: WaitData;
  error?: Error;
};

const FINAL_FAILURES = new Set(["failed", "dropped"]);

export function isDefinitiveFailure(data: WaitData | undefined): boolean {
  return FINAL_FAILURES.has(String(data?.status ?? ""));
}

function revertMessage(data: WaitData | undefined): string | undefined {
  return data?.receipts?.[0]?.revert?.message || undefined;
}

// Maps one settled (non-pending) wait result for `hash` to the next outcome, or
// null when nothing changes. `current` is the outcome shown now.
export function classifyWaitResult(
  current: UserOpOutcome,
  hash: string,
  wait: WaitResult,
): UserOpOutcome | null {
  const { status, data, error } = wait;
  if (data?.userOpHash && data.userOpHash.toLowerCase() !== hash.toLowerCase()) return null;
  if (status === "success") {
    return { state: "success", hash, transactionHash: data?.transactionHash };
  }
  if (status !== "error") return null;
  if (isDefinitiveFailure(data)) {
    return {
      state: "error",
      hash,
      message: error?.message || revertMessage(data) || `user operation ${data?.status}`,
    };
  }
  if (current.state === "unknown" && current.hash === hash) return null;
  return {
    state: "unknown",
    hash,
    message: error?.message || "lost track of the transaction",
    gaveUp: false,
  };
}

// Maps a getUserOperation result from our own re-poll. null = not final yet.
export function classifyPolledOperation(hash: string, op: WaitData | null | undefined): UserOpOutcome | null {
  if (!op) return null;
  if (op.userOpHash && op.userOpHash.toLowerCase() !== hash.toLowerCase()) return null;
  if (op.status === "complete") {
    return { state: "success", hash, transactionHash: op.transactionHash || undefined };
  }
  if (isDefinitiveFailure(op)) {
    return {
      state: "error",
      hash,
      message: revertMessage(op) || `user operation ${op.status}`,
    };
  }
  return null;
}

export const REPOLL_BASE_MS = 2_000;
export const REPOLL_MAX_MS = 15_000;
export const REPOLL_BUDGET_MS = 150_000;

export function repollDelayMs(attempt: number): number {
  return Math.min(REPOLL_BASE_MS * 2 ** Math.max(0, attempt), REPOLL_MAX_MS);
}

export type RepollDeps = {
  fetch: () => Promise<WaitData>;
  sleep: (ms: number) => Promise<void>;
  cancelled: () => boolean;
  budgetMs?: number;
};

// Re-polls an op whose watcher lost track, with bounded backoff. Resolves to a
// final outcome, to null when the budget ran out, or to null once cancelled.
// Thrown fetches and non-final statuses keep polling.
export async function repollUserOperation(hash: string, deps: RepollDeps): Promise<UserOpOutcome | null> {
  const budget = deps.budgetMs ?? REPOLL_BUDGET_MS;
  let waited = 0;
  for (let attempt = 0; waited < budget; attempt++) {
    const delay = Math.min(repollDelayMs(attempt), budget - waited);
    await deps.sleep(delay);
    waited += delay;
    if (deps.cancelled()) return null;
    let op: WaitData | undefined;
    try {
      op = await deps.fetch();
    } catch {
      op = undefined;
    }
    if (deps.cancelled()) return null;
    const next = classifyPolledOperation(hash, op);
    if (next) return next;
  }
  return null;
}
