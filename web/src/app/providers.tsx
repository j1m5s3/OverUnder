"use client";

import { CDPHooksProvider } from "@coinbase/cdp-hooks";
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

const cdpConfig = {
  projectId: process.env.NEXT_PUBLIC_CDP_PROJECT_ID || "",
  ethereum: {
    createOnLogin: "smart" as const,
  },
};

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient());
  return (
    <CDPHooksProvider config={cdpConfig}>
      <WagmiProvider config={config}>
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      </WagmiProvider>
    </CDPHooksProvider>
  );
}
