# OverUnder — JIT Plan (MVP Execute Cycle)

Greenfield implementation of the approved OverUnder MVP Architecture.

## Product slice

- Operator creates one primary binary market (CLOB).
- AI wildcard generator publishes child AMM markets with `parentMarketId`.
- Settlement: 3/3 AI oracle unanimity, else 24h agent majority + position-weighted votes.
- USDC on Anvil (MockUSDC) / Base Sepolia. OU redeems from FeeVault at NAV.
- Web: Privy + Coinbase Smart Wallet + EOA. Flutter: carryover contract only.

## Architectural decisions

See approved plan: hybrid CLOB + CPMM, binary CTF, 75 bps taker / 100 bps AMM (50/50 vault-LP), 100M OU, 24h redeem cooldown.

## Execute order

1. Monorepo skeleton, env, compose.
2. Vyper core: MockUSDC, RevenueToken, ConditionalTokens, MarketFactory.
3. ConsensusOracle, Exchange, MarketAMM, FeeVault.
4. Tests + deploy.py.
5. FastAPI + indexer + CLOB matcher.
6. Oracle agents + wildcard generator.
7. Next.js + shared tokens/OpenAPI + e2e.

## Acceptance

`moccasin test` green; e2e happy path + fallback; web CLOB + AMM; NAV matches on-chain.
