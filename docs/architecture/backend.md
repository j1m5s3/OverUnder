---
title: Backend
status: MIXED
area: backend
summary: FastAPI routers for auth, markets, CLOB, AMM quotes, oracle records, ramps, KYC, emissions, and portfolio.
last_verified: 2026-09-19
pointers:
  - "[backend/app/main.py : L23-48]"
  - "[backend/app/auth/router.py : L61-102]"
  - "[backend/app/orderbook/matcher.py : L37-86]"
  - "[backend/app/orderbook/matcher.py : L88-136]"
  - "[backend/app/orderbook/router.py : L28-58]"
  - "[backend/app/markets/router.py : L44-62]"
  - "[backend/app/amm/router.py : L9-32]"
  - "[backend/app/oracle/router.py : L29-66]"
  - "[backend/app/ramps/router.py : L28-129]"
  - "[backend/app/kyc/router.py : L29-168]"
  - "[backend/app/emissions/router.py : L16-79]"
  - "[backend/app/portfolio/router.py : L13-61]"
  - "[backend/app/config.py : L9-40]"
  - "[backend/app/models.py : L98-115]"
  - "[backend/app/indexer/listener.py : L20-40]"
---

# Backend

- [SHIPPED] FastAPI app `create_app` mounts auth, markets, orderbook, amm, ramps, oracle, portfolio under `/api/v1`, plus `/health`. [backend/app/main.py : L23-48]
- [SHIPPED] Settings from `.env` via pydantic: RPC, chain id, JWT, fee bps, optional Privy/Coinbase/relayer keys. [backend/app/config.py : L9-33]
- [SHIPPED] SQLite aiosqlite (`overunder.db`) created on lifespan. Gitignored.

## Auth

- [SHIPPED] `GET /auth/nonce/{address}` stores a hex nonce. [backend/app/auth/router.py : L61-70]
- [STUB] `POST /auth/siwe` checks address+nonce appear in `message`; it does not recover an ECDSA signature. [backend/app/auth/router.py : L73-87]
- [STUB] `POST /auth/privy` accepts any non-empty token and issues HS256 JWT. Comment marks JWKS as a later seam. [backend/app/auth/router.py : L90-102]
- [SHIPPED] Bearer JWT identifies `User`; `require_operator` gates market create/pause.
- [PHASE2] Verify Privy access tokens against Privy JWKS (`PRIVY_APP_ID` / `PRIVY_APP_SECRET`), SIWE EIP-4361 + `ecrecover`, session rotation, and device binding.

## Markets

- [SHIPPED] List/get unpaused markets; operator `POST /markets` and pause. [backend/app/markets/router.py : L24-76]
- [STUB] Create writes SQLite only. It does not call `MarketFactory.createPrimaryMarket` / `createWildcardMarket`.
- [PHASE2] Atomic create: operator/relayer submits the factory tx, indexer confirms `MarketCreated`, API returns the on-chain `conditionId`.

## Orderbook

- [SHIPPED] Authenticated `POST /orders` persists then `try_match`. [backend/app/orderbook/router.py : L28-58]
- [SHIPPED] Crossing: opposite side, same condition/outcome, price cross, remaining size. Buys match cheapest ask first. [backend/app/orderbook/matcher.py : L23-51]
- [SHIPPED] Fill records `Trade` with volume `qty * maker.price // 1e6` and taker fee `fee_bps_taker`. [backend/app/orderbook/matcher.py : L53-72]
- [STUB] `_submit_match` is best-effort: empty tx hash if no relayer key, no deployment file, RPC down, or any web3 exception. [backend/app/orderbook/matcher.py : L88-136]
- [STUB] Web clients may post `signature: "0x"`; matcher will fail on-chain recover but still record off-chain fills.
- [PHASE2] Dedicated relayer service: queued `matchOrders`, gas/nonce manager, revert handling, maker-allowance checks before accept, and EIP-712 signature verification at the API edge.

## AMM proxy

- [SHIPPED] `GET /amm/{id}/quote` calls `quoteBuy` when deployment+RPC exist. [backend/app/amm/router.py : L9-23]
- [STUB] On any failure, returns `tokensOut = usdc_in - amm_fee` with `simulated: true`. [backend/app/amm/router.py : L24-32]
- [PHASE2] Server-side `buyWithUSDC` / `sellToUSDC` relay for AA wallets, plus pool seed from protocol inventory.

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

- [SHIPPED] Open orders and trades by address. [backend/app/portfolio/router.py : L13-44]
- [STUB] `GET /fee-vault/nav` returns `nav: 0, simulated: true` if RPC/deploy missing. [backend/app/portfolio/router.py : L47-61]
- [STUB] `index_once` polls factory logs when connected; not started as a background task from `main.py`. [backend/app/indexer/listener.py : L20-40]
- [PHASE2] Always-on indexer, CTF balance snapshots, and OU NAV history.

## Emissions

- [SHIPPED] `POST /emissions/distribute` (operator JWT): Calls EmissionsDistributor.distribute to transfer OU from treasury to recipients. [backend/app/emissions/router.py : L16-79]
- [SHIPPED] Program IDs: 0=LP, 1=maker, 2=agent, 3=quest. Max 100 recipients per batch.
- [SHIPPED] Requires treasury approval for EmissionsDistributor; never mints. See [docs/emissions/schedule.yaml] for allocation schedules.
- [PHASE2] Off-chain accounting service that tracks vested amounts and calls distribute endpoint in batches.
