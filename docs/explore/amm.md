---
title: Prediction AMM literature
status: MIXED
area: contracts
summary: Stance box plus literature on uniform-LVR AMMs. Literature is not spec.
last_verified: 2026-09-23
pointers:
  - "[contracts/src/MarketAMM.vy : L1-12]"
  - "[contracts/src/MarketAMM.vy : L105-119]"
  - "[contracts/src/lib/NormalMath.vy : L140-159]"
---

> OverUnder stance ([ADR-0007](../adr/0007-amm-first-uniform-lvr.md)): **adopt** the uniform-LVR family (pm-AMM 2024 as the Gaussian-score case; Moallemi–Robinson–Zhu 2026 as the general theory). Default pool is a static uniform invariant; time-based liquidity and dynamic spreads are LP/fee policy, not a second book. Implemented by [ADR-0011](../adr/0011-pm-amm-v2-close-gate.md): `MarketAMM` v2 is the static pm-AMM (YES price Φ((no − yes)/L)) with an on-chain closeTime halt; dynamic L_t is OU-T015. Spike numbers: [uniform-lvr-spike.md](uniform-lvr-spike.md). Literature below is not spec.

Yes, automated market maker (AMM) technology and methods have improved dramatically since Polymarket abandoned its original design.
When Polymarket transitioned away from AMMs, the broader industry realized that standard DeFi formulas (like the ones used for swapping tokens) and traditional prediction-market rules (like the standard Logarithmic Market Scoring Rule, or LMSR) were structurally flawed for event contracts.
In a traditional AMM, liquidity providers (LPs) face massive "impermanent loss" because prediction market shares eventually expire at a fixed value of either $1.00 or $0.00. If you provide liquidity to a "YES" pool and the outcome becomes 100% certain, arbitrageurs drain the pool, leaving LPs with worthless "NO" shares—meaning LPs were essentially guaranteed to lose all their money at market expiration. [1, 2, 3] 
To solve this, major crypto research firms and academic institutions have designed next-generation AMMs built specifically for prediction markets.
------------------------------
## 📊 Major Innovations in Prediction AMMs

| Innovation | How it Works | Problem it Solves |
|---|---|---|
| The pm-AMM Framework (Paradigm, late 2024) | Automatically adjusts liquidity and yields based on the exact time left until market expiration and the current mathematical probability. | Prevents the uneven, inconsistent liquidity that plagued older AMMs as a market neared its deadline. |
| Uniform-Loss / LVR AMMs (Moallemi, Robinson, & Zhu, 2026) | Uses a Loss-Versus-Rebalancing (LVR) framework to distribute risk evenly across all price points instead of heavily penalizing the edges. | Eliminates the fatal flaw where LPs were guaranteed to lose 100% of their principal at settlement. |
| Dynamic Bid-Ask Spreads | Algorithmic formulas that mimic human market makers by dynamically shrinking the spread as trading volume surges. | Minimizes the extreme price slippage that used to ruin large trades in older AMM setups. |

------------------------------
## 💡 Why Polymarket Still Hasn't Switched Back
Despite these brilliant upgrades, Polymarket continues to stick with its Central Limit Order Book (CLOB) infrastructure. [2] 
The primary reason is institutional and algorithmic trading compatibility. Advanced high-frequency traders, Wall Street trading desks, and quantitative firms use traditional trading software that relies entirely on order books. By maintaining a CLOB, Polymarket can smoothly plug into institutional trading systems, which helped drive the platform to process tens of billions in volume. [3, 4, 5] 
However, these new, improved AMM methods are widely used today by decentralized "Polymarket clones," smaller on-chain event platforms, and niche permissionless markets where attracting professional, human market makers isn't a viable option. [2, 6] 
Would you like to dive deeper into the mathematics behind how pm-AMM protects liquidity providers, or are you curious about how new prediction platforms are utilizing these updated models?

[1] [https://www.paradigm.xyz](https://www.paradigm.xyz/writing/pm-amm)
[2] [https://www.dwellir.com](https://www.dwellir.com/blog/what-is-a-prediction-market)
[3] [https://www.softwareseni.com](https://www.softwareseni.com/kalshi-vs-polymarket-platform-architecture-comparison-for-developers-building-prediction-market-integrations/)
[4] [https://www.linkedin.com](https://www.linkedin.com/pulse/from-opinions-odds-emerging-trends-prediction-market-landscape-n6lce)
[5] [https://www.marketsmedia.com](https://www.marketsmedia.com/institutional-access-increases-in-prediction-markets/)
[6] [https://www.linkedin.com](https://www.linkedin.com/pulse/how-do-automated-market-maker-amm-algorithms-work-inside-li9fc)