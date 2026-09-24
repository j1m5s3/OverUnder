export type EvmAddress = `0x${string}`;

// CDP users carry their smart account in evmSmartAccounts (string or object form).
export function smartAccountOf(
  user: { evmSmartAccounts?: Array<string | { address?: string }> } | null | undefined,
): EvmAddress | undefined {
  const account = user?.evmSmartAccounts?.[0];
  const address = typeof account === "string" ? account : account?.address;
  return address as EvmAddress | undefined;
}
