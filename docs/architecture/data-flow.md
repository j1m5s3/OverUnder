---
title: Data flow
status: MIXED
area: cross
summary: End-to-end traces for seeded AMM swaps and the trading halt, user listing, oracle resolution with the 24h fallback, week-roll listing, the leftover CLOB relayer queue, and OU redeem.
last_verified: 2026-09-23
pointers:
  - "[contracts/src/MarketFactory.vy : L143-199]"
  - "[contracts/src/MarketAMM.vy : L126-157]"
  - "[contracts/src/MarketAMM.vy : L240-317]"
  - "[backend/app/markets/router.py : L329-452]"
  - "[backend/app/markets/listing.py : L288-424]"
  - "[backend/app/markets/trading.py : L42-58]"
  - "[backend/app/amm/router.py : L46-86]"
  - "[backend/app/aa/router.py : L168-222]"
  - "[backend/app/cdp.py : L169-179]"
  - "[backend/app/indexer/listener.py : L106-140]"
  - "[backend/app/indexer/listener.py : L384-453]"
  - "[backend/app/orderbook/router.py : L94-164]"
  - "[backend/app/orderbook/matcher.py : L71-149]"
  - "[backend/app/relayer/worker.py : L1-24]"
  - "[web/src/features/trade/AmmSwap.tsx : L264-365]"
  - "[web/src/features/listing/ListMarketForm.tsx : L283-344]"
  - "[oracles/resolve/run.py : L118-298]"
  - "[oracles/resolve/fallback.py : L47-151]"
  - "[oracles/resolve/general.py : L211-409]"
  - "[oracles/schedule/scout.py : L147-202]"
  - "[oracles/listing/run.py : L74-86]"
  - "[oracles/listing/run.py : L139-294]"
  - "[contracts/src/ConsensusOracle.vy : L136-161]"
  - "[contracts/src/ConsensusOracle.vy : L197-226]"
  - "[contracts/src/ConditionalTokens.vy : L85-105]"
  - "[contracts/src/FeeVault.vy : L61-83]"
---

# Data flow

## Primary AMM swap

1. [SHIPPED] Operator `POST /markets` (idempotent) submits `createPrimaryMarket` with a seed ≥ 10,000 base units; the factory registers closeTime on the oracle and seeds the pool with LP to the operator. [backend/app/markets/router.py : L329-452] [contracts/src/MarketFactory.vy : L143-199]
2. [SHIPPED] MarketAMM v2 splits the seed 50/50, sets L = S/φ(0) and caches closeTime. [contracts/src/MarketAMM.vy : L126-157]
3. [SHIPPED] Trader calls `GET /amm/{id}/quote`: 409 if halted, else `quoteBuy` or `quoteSell` (422 carries a revert reason). [backend/app/amm/router.py : L46-86]
4. [SHIPPED] Web AmmSwap buy: batched CDP user op, USDC `approve` then `buyWithUSDC`; sell: CTF `setApprovalForAll` then `sellToUSDC`; `useCdpPaymaster: true`. [web/src/features/trade/AmmSwap.tsx : L264-365]
5. [SHIPPED] Flutter encodes calldata and calls `POST /aa/cdp-send`; the backend refuses halted markets with 409, then sends with `useCdpPaymaster: true`. [backend/app/aa/router.py : L168-222] [backend/app/cdp.py : L169-179]
6. [SHIPPED] On chain, trades pay ceil 50 bps to FeeVault and 50 bps to the pool's LP accumulator; quote equals execution. [contracts/src/MarketAMM.vy : L240-317]
7. [SHIPPED] The indexer stores `PoolSeeded` at 0.5 and each `Swap` at the pool price Φ((no − yes)/L) (CPMM mid for a v1 pool); lists carry the latest point as `yesPriceMicros`. [backend/app/indexer/listener.py : L106-140]

## Trading halt at closeTime

1. [SHIPPED] At closeTime the API reports `tradingOpen: false` and refuses quotes, sponsored trades and CLOB orders with 409 `market closed` (`market resolved` once resolved, whatever the flag). [backend/app/markets/trading.py : L42-58]
2. [SHIPPED] Web and mobile flip the ticket to closed on a timer, a 409 or a `"market closed"` revert.
3. [SHIPPED] MarketAMM v2 reverts direct `buyWithUSDC` / `sellToUSDC` / `addLiquidity` with `"market closed"` while `closeGate` is on; quotes and `removeLiquidity` keep working.
4. [STUB] Until the Base Sepolia redeploy, the live v1 AMM has no gate, so direct contract calls can still trade after close.

## Wildcard AMM swap

1. [SHIPPED] Generator or operator `createWildcardMarket` with optional seed (LP to the caller). [contracts/src/MarketFactory.vy : L143-199]
2. [SHIPPED] Same quote, halt and AmmSwap path as primaries; the general resolver settles it after the parent game is final.

## User listing (OU-T010)

1. [SHIPPED] Web `/list` reads `/markets/listing/config` and `/eligibility`.
2. [SHIPPED] `POST /markets/listing/prepare` runs the gates, stores a `prepared` row and returns USDC `approve(factory, seed + fee)` plus `createPermissionlessMarket(salt, …)`. [backend/app/markets/listing.py : L288-424]
3. [SHIPPED] The web checks both targets against the config, sends them as one CDP-sponsored user op and waits for inclusion. [web/src/features/listing/ListMarketForm.tsx : L283-344]
4. [SHIPPED] The factory checks its gates, emits `MarketCreated` (type 2) and `UserMarketListed`, and seeds with LP to the lister (locked until close). [contracts/src/MarketFactory.vy : L143-199]
5. [SHIPPED] `POST /markets/listing/confirm` matches the criteria hash and re-runs the gates on the on-chain question: `confirmed` makes the market public; a failure is 422 and the row stays `rejected` and hidden.
6. [SHIPPED] The indexer applies the same review when it sees the type-2 `MarketCreated`, so a listing whose confirm never lands is still published or rejected. [backend/app/indexer/listener.py : L384-453]
7. [SHIPPED] After close, `resolve_general` researches a confirmed listing (closeTime + 24 h by default). Unconfirmed or rejected listings are invisible to it and to the API, yet stay tradable by direct AMM calls until close; the operator finds them via `GET /markets/listing/review`, can factory-pause them (the AMM ignores that flag) and arbitrates after the window.

## Resolve market

1. [SHIPPED] Sports winners: after closeTime and LiveScore `final`, the job needs unanimous research matching the score-derived winner, then signs three EIP-712 attestations and sends `submitConsensus` → `reportPayouts`. [oracles/resolve/run.py : L118-298] [contracts/src/ConsensusOracle.vy : L136-161]
2. [SHIPPED] Other markets: `resolve_general` needs 3/3 with every confidence ≥ 0.8, after the event gate or delay. [oracles/resolve/general.py : L211-409]
3. [SHIPPED] Markets resolved on chain by anyone are mirrored into the DB from CTF payouts; markets with on-chain closeTime 0 are skipped as `not registered`.
4. [SHIPPED] Research that does not resolve is recorded, and the market is skipped for `OU_RESEARCH_RETRY_SECONDS` (6 h).
5. [SHIPPED] Winners `redeemPositions` for USDC. [contracts/src/ConditionalTokens.vy : L85-105]
6. [STUB] Cancelled or undetermined markets never settle: there is no invalid/refund outcome (OU-T014).

## 24 h fallback (OU_FALLBACK_POLICY)

1. [SHIPPED] At closeTime + 86400 with no matching unanimous research, `attest` (default) has each agent whose research matches the derived outcome (score-derived for sports, confident 2/3 majority for general markets) sign, and the operator relays `submitAttestation`. [oracles/resolve/fallback.py : L47-151]
2. [SHIPPED] Once the `resolveFallback` preflight passes (2/3 agents agree and votes do not force arbitration), the job sends it; anyone could. [contracts/src/ConsensusOracle.vy : L197-226]
3. [SHIPPED] Holders may `castVote`; ≥2/3 of voted weight against the agents reverts `"arbitration required"`.
4. [SHIPPED] `arbitrate` policy: for sports markets with no agent majority or forced arbitration the job sends operator `resolveArbitrated(derived)`; `manual` sends nothing past the window.

## Week-roll listing

1. [SHIPPED] Schedule scout publishes rows all three agents agree on for the current and next NFL week. [oracles/schedule/scout.py : L147-202]
2. [SHIPPED] Week W is done when every game is final, postponed or cancelled, or stale past 8 h. [oracles/listing/run.py : L74-86]
3. [SHIPPED] The job operator-POSTs a winner primary per week W+1 game (`close_time = kickoff`), skipping games ≤ 10 minutes from kickoff; each POST is isolated and a 409 is recorded as squatted. [oracles/listing/run.py : L139-294]

## Leftover CLOB relayer (OU-T003)

1. [SHIPPED] An EOA maker signs an EIP-712 `Order`; `POST /orders` verifies hash and signature, the halt, expiry and (relayer on) balances and approvals. [backend/app/orderbook/router.py : L94-164]
2. [SHIPPED] Matching applies guarded fills and, with the relayer ready, enqueues a `RelayJob` per match; with it off, fills stay `offchain`. [backend/app/orderbook/matcher.py : L71-149]
3. [SHIPPED] The worker (leader via Postgres advisory lock, or an operator `POST /relayer/tick`) preflights, commits the signed tx write-ahead, broadcasts `matchOrders`, bumps stuck fees and confirms on the `OrderFilled` log; a failure rolls fills back and retires the failing side. [backend/app/relayer/worker.py : L1-24]
4. [SHIPPED] `DELETE /orders` cancels off-chain; once the signature may be public the maker must call `Exchange.cancelOrder` with the returned args.
5. [STUB] Smart-account makers (EIP-1271) and a relayed on-chain cancel (OU-T016).

## OU redeem

1. [SHIPPED] Fees into FeeVault: AMM 50 bps of every trade, optional listing fees, and 75 bps on leftover Exchange fills.
2. [SHIPPED] Holder `requestRedeem` (24h cooldown at deploy), then `claim` burns OU and pays pro-rata USDC. [contracts/src/FeeVault.vy : L61-83]
3. [SHIPPED] Emissions transfer from treasury; they do not mint into this vault.
