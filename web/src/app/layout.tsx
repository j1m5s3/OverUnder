"use client";

import type { Metadata } from "next";
import Link from "next/link";
import { useState } from "react";
import "./globals.css";
import { Providers } from "./providers";
import { ConnectBar } from "@/features/wallet/ConnectBar";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);
  
  return (
    <html lang="en">
      <body>
        <Providers>
          <header className="nav">
            <div className="nav-row">
              <Link className="brand" href="/" onClick={() => setMenuOpen(false)}>
                OVERUNDER
              </Link>
              <button className="menu-toggle btn ghost" onClick={() => setMenuOpen(!menuOpen)}>
                {menuOpen ? "✕" : "☰"}
              </button>
              <nav className={`nav-links ${menuOpen ? "open" : ""}`}>
                <Link href="/" onClick={() => setMenuOpen(false)}>Markets</Link>
                <Link href="/portfolio" onClick={() => setMenuOpen(false)}>Portfolio</Link>
                <Link href="/wallet" onClick={() => setMenuOpen(false)}>Wallet</Link>
                <ConnectBar />
              </nav>
            </div>
          </header>
          <main className="wrap">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
