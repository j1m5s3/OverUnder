"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { WagmiProvider, createConfig, http, injected } from "wagmi";
import { anvil, base, baseSepolia } from "wagmi/chains";

const rpc = process.env.NEXT_PUBLIC_RPC_URL || "http://127.0.0.1:8545";

const config = createConfig({
  chains: [anvil, baseSepolia, base],
  connectors: [injected({ shimDisconnect: true })],
  transports: {
    [anvil.id]: http(rpc),
    [baseSepolia.id]: http(),
    [base.id]: http(),
  },
});

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient());
  return (
    <WagmiProvider config={config}>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </WagmiProvider>
  );
}
