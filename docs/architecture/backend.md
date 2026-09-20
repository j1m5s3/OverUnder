---
title: Backend
status: MIXED
area: backend
summary: FastAPI routers for auth, markets, CLOB, AMM quotes, oracle records, ramps, KYC, emissions, and portfolio.
last_verified: 2026-09-20
pointers:
  - "[backend/app/main.py : L22-38]"
  - "[backend/app/auth/router.py : L61-102]"
  - "[backend/app/orderbook/matcher.py : L37-86]"
  - "[backend/app/orderbook/matcher.py : L88-136]"
  - "[backend/app/orderbook/router.py : L28-58]"
  - "[backend/app/markets/router.py : L59-90]"
  - "[backend/app/markets/router.py : L137-145]"
  - "[backend/app/markets/router.py : L149-188]"
  - "[backend/app/markets/sports.py : L58-65]"
  - "[backend/app/markets/router.py : L191-334]"
  - "[backend/app/amm/router.py : L9-28]"
  - "[backend/app/oracle/router.py : L29-66]"
  - "[backend/app/ramps/router.py : L28-129]"
  - "[backend/app/kyc/router.py : L29-168]"
  - "[backend/app/emissions/router.py : L16-79]"
  - "[backend/app/portfolio/router.py : L13-61]"
  - "[backend/app/config.py : L9-40]"
  - "[backend/app/models.py : L96-103]"
  - "[backend/app/models.py : L106-115]"
  - "[backend/app/markets/router.py : L117-135]"
  - "[backend/app/indexer/listener.py : L20-49]"
  - "[backend/app/indexer/listener.py : L175-237]"
---

# Backend

- [SHIPPED] FastAPI app `create_app` mounts auth, markets, orderbook, amm, ramps, oracle, portfolio under `/api/v1`, plus `/health`. [backend/app/main.py : L41-48]
- [SHIPPED] Settings from `.env` via pydantic: RPC, chain id, JWT, fee bps, optional Privy/Coinbase/relayer keys. [backend/app/config.py : L9-33]
- [SHIPPED] SQLite aiosqlite (`overunder.db`) created on lifespan. Gitignored.

## Auth

- [SHIPPED] `GET /auth/nonce/{address}` stores a hex nonce. [backend/app/auth/router.py : L61-70]
- [STUB] `POST /auth/siwe` checks address+nonce appear in `message`; it does not recover an ECDSA signature. [backend/app/auth/router.py : L73-87]
- [STUB] `POST /auth/privy` accepts any non-empty token and issues HS256 JWT. Comment marks JWKS as a later seam. [backend/app/auth/router.py : L90-102]
- [SHIPPED] Bearer JWT identifies `User`; `require_operator` gates market create/pause.
- [PHASE2] Verify Privy access tokens against Privy JWKS (`PRIVY_APP_ID` / `PRIVY_APP_SECRET`), SIWE EIP-4361 + `ecrecover`, session rotation, and device binding.

## Markets

- [SHIPPED] `GET /markets` returns EventCard[] of unpaused primaries with nested unpaused wildcard children. Wildcards whose parent is missing or paused list alone. `?parentId=` stays a flat MarketPublic[] filter. [backend/app/markets/router.py : L59-90]
- [SHIPPED] `GET /markets/{id}` returns MarketDetail with the same child filter; `children` is always present and may be `[]`. [backend/app/markets/router.py : L137-145]
- [SHIPPED] Operator-fed LiveScore on sports primaries only: `POST /markets/{id}/score` (`require_operator`) rejects wildcards and non-sports questions server-side via the sports allowlist, rejects invented `scheduled` 0–0, and upserts labels, nullable scores, status, and display-only period; `MarketDetail.score` embeds it, `null` when absent, always `null` on wildcards — nulls are never coerced to 0. [backend/app/markets/router.py : L149-188]
- [SHIPPED] Operator `POST /markets` submits factory `createPrimaryMarket` / `createWildcardMarket` (fail-closed on missing key, RPC, or `seed_usdc <= 0`), then upserts SQLite from `MarketCreated`. [backend/app/markets/router.py : L191-334]
- [PHASE2] Permissionless listing so AMM seed—not the operator—bootstraps a market (OU-T010).

## Orderbook

- [SHIPPED] Authenticated `POST /orders` persists then `try_match`. [backend/app/orderbook/router.py : L28-58]
- [SHIPPED] Crossing: opposite side, same condition/outcome, price cross, remaining size. Buys match cheapest ask first. [backend/app/orderbook/matcher.py : L23-51]
- [SHIPPED] Fill records `Trade` with volume `qty * maker.price // 1e6` and taker fee `fee_bps_taker`. [backend/app/orderbook/matcher.py : L53-72]
- [STUB] `_submit_match` is best-effort: empty tx hash if no relayer key, no deployment file, RPC down, or any web3 exception. [backend/app/orderbook/matcher.py : L88-136]
- [STUB] Web clients may post `signature: "0x"`; matcher will fail on-chain recover but still record off-chain fills.
- [PHASE2] Dedicated relayer service: queued `matchOrders`, gas/nonce manager, revert handling, maker-allowance checks before accept, and EIP-712 signature verification at the API edge.

## AMM proxy

- [SHIPPED] `GET /amm/{id}/quote` calls `quoteBuy` or `quoteSell` when deployment+RPC exist. [backend/app/amm/router.py : L9-28]
- [PHASE2] Server-side `buyWithUSDC` / `sellToUSDC` relay for AA wallets.

## Oracle records

- [SHIPPED] Persist attestations and votes; status computes `unanimous` when ≥3 identical outcomes. [backend/app/oracle/router.py : L29-66]
- [STUB] Attest/vote endpoints do not submit `submitAttestation` / `castVote` on-chain.
- [PHASE2] Coordinator worker watches closeTime, posts `submitConsensus` or `resolveFallback`, and mirrors logs into SQLite.

## Ramps

- [SHIPPED] Builds Coinbase Pay URLs from `coinbase_onramp_app_id` or `"demo"`. Coinbase stays as fallback. [backend/app/ramps/router.py : L107-129]
- [SHIPPED] `POST /ramps/moonpay/session` (JWT): Sign widget URL with `MOONPAY_SECRET`; destination = session address, usdc/base. [backend/app/ramps/router.py : L28-56]
- [SHIPPED] `POST /ramps/moonpay/webhook`: Verify signature on raw body; upsert `RampTx{address, amount, provider_id, status}` only. [backend/app/ramps/router.py : L59-104]
- [SHIPPED] `RampTx` model stores address, amount, provider_id, status with timestamps. No government IDs. [backend/app/models.py : L98-106]
- [PHASE2] Additional payment providers and webhook retry logic.

## KYC

- [SHIPPED] `POST /kyc/session` (JWT): Create or update KYC record with status enum and jurisdiction only. [backend/app/kyc/router.py : L29-75]
- [SHIPPED] `GET /kyc/status` (JWT): Get current KYC status for authenticated user. [backend/app/kyc/router.py : L78-98]
- [SHIPPED] `POST /kyc/check` (JWT): Gate logic - notional ≥ threshold (default $500/day) or restricted jurisdiction → require KYC. [backend/app/kyc/router.py : L101-168]
- [SHIPPED] `KycRecord{address, status, jurisdiction, updated_at}` — status enum only, NEVER document images/numbers. [backend/app/models.py : L109-115]
- [SHIPPED] Gate returns 403 if KYC required but not in {pass, not_required}. Operator JWT cannot bypass fiat KYC.
- [PHASE2] Third-party KYC provider integration (Persona, Jumio) with webhook callbacks.

## Portfolio + indexer

- [SHIPPED] `GET /portfolio/{address}` returns positions (CTF balances), open orders, and trades. [backend/app/portfolio/router.py : L13-101]
- [SHIPPED] Positions are AMM outcome holdings queried from ConditionalTokens.balanceOf for all markets. Returns 503 if CTF query fails. [backend/app/portfolio/router.py : L22-74]
- [STUB] `GET /fee-vault/nav` returns `nav: 0, simulated: true` if RPC/deploy missing. [backend/app/portfolio/router.py : L104-119]
- [SHIPPED] `index_once` polls factory logs when connected; `run_indexer_loop` is started from `main.py` lifespan. [backend/app/indexer/listener.py : L64-172] [backend/app/main.py : L22-38]
- [SHIPPED] AMM history: `PoolSeeded` stores the first `PricePoint` at 0.5; each `Swap` stores pool-mid `noReserve / (yes+no)` via `pools(conditionId)` at that block — the YES price is the NO reserve share, never trade-implied amounts. [backend/app/indexer/listener.py : L20-49]
- [SHIPPED] Failed AMM ranges never advance the checkpoint: `get_logs`/`pools().call` failures return False so `last_block` holds and the range retries instead of committing a gap. [backend/app/indexer/listener.py : L175-237]
- [SHIPPED] `PricePoint{condition_id, ts, block_number, log_index, yes_price_micros}` deduped per block+logIndex. [backend/app/models.py : L96-103]
- [SHIPPED] `GET /markets/{id}/history` returns ordered `PricePoint[]`, `[]` when empty, 404 for unknown markets. [backend/app/markets/router.py : L99-116]
- [PHASE2] Always-on indexer, CTF balance snapshots, and OU NAV history.

## Emissions

- [SHIPPED] `POST /emissions/distribute` (operator JWT): Calls EmissionsDistributor.distribute to transfer OU from treasury to recipients. [backend/app/emissions/router.py : L16-79]
- [SHIPPED] Program IDs: 0=LP, 1=maker, 2=agent, 3=quest. Max 100 recipients per batch.
- [SHIPPED] Requires treasury approval for EmissionsDistributor; never mints. See [docs/emissions/schedule.yaml] for allocation schedules.
- [PHASE2] Off-chain accounting service that tracks vested amounts and calls distribute endpoint in batches.
