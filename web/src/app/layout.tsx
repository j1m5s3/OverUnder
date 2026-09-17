import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { Providers } from "./providers";
import { ConnectBar } from "@/features/wallet/ConnectBar";

export const metadata: Metadata = {
  title: "OverUnder",
  description: "AI-oracle prediction markets on Base",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <header className="nav">
            <Link className="brand" href="/">
              OVERUNDER
            </Link>
            <nav className="row">
              <Link href="/">Markets</Link>
              <Link href="/portfolio">Portfolio</Link>
              <Link href="/wallet">Wallet</Link>
              <ConnectBar />
            </nav>
          </header>
          <main className="wrap">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
