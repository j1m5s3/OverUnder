"use client";

import { CDPContext, CDPHooksProvider, type CDPContextValue } from "@coinbase/cdp-hooks";
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

// Without a project id CDPHooksProvider takes the whole tree down: initialize()
// rejects and the provider's onOAuthStateChange then throws "SDK not initialized".
// An inert signed-out context keeps every CDP hook usable, so the app renders and
// ConnectBar shows "sign-in isn't configured".
const signedOutContext: CDPContextValue = {
  isInitialized: false,
  currentUser: null,
  isSignedIn: false,
  config: cdpConfig,
  oauthState: null,
};

function WalletProvider({ children }: { children: ReactNode }) {
  if (!cdpConfig.projectId) {
    return <CDPContext.Provider value={signedOutContext}>{children}</CDPContext.Provider>;
  }
  return <CDPHooksProvider config={cdpConfig}>{children}</CDPHooksProvider>;
}

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient());
  return (
    <WalletProvider>
      <WagmiProvider config={config}>
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      </WagmiProvider>
    </WalletProvider>
  );
}
