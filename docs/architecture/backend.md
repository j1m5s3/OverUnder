---
title: Backend
status: MIXED
area: backend
summary: FastAPI routers for auth, chain addresses, markets (idempotent create, user listing, trading halt, archive), AMM quotes, the leftover-CLOB relayer, oracle records, ramps, KYC, emissions, portfolio and the chain indexer.
last_verified: 2026-09-23
pointers:
  - "[backend/app/main.py : L27-61]"
  - "[backend/app/main.py : L64-96]"
  - "[backend/app/db.py : L12-14]"
  - "[backend/app/db.py : L158-216]"
  - "[backend/app/db.py : L250-272]"
  - "[backend/app/config.py : L46-130]"
  - "[backend/app/auth/router.py : L86-141]"
  - "[backend/app/auth/router.py : L144-173]"
  - "[backend/app/cdp.py : L116-134]"
  - "[backend/app/chain/router.py : L36-48]"
  - "[backend/app/aa/router.py : L75-104]"
  - "[backend/app/aa/router.py : L168-222]"
  - "[backend/app/markets/router.py : L39-72]"
  - "[backend/app/markets/router.py : L177-229]"
  - "[backend/app/markets/router.py : L248-296]"
  - "[backend/app/markets/router.py : L385-554]"
  - "[backend/app/markets/router.py : L623-660]"
  - "[backend/app/markets/trading.py : L27-67]"
  - "[backend/app/markets/visibility.py : L28-42]"
  - "[backend/app/markets/visibility.py : L73-107]"
  - "[backend/app/markets/listing.py : L237-362]"
  - "[backend/app/markets/listing.py : L376-424]"
  - "[backend/app/markets/listing.py : L492-526]"
  - "[backend/app/markets/listing_gates.py : L58-167]"
  - "[backend/app/amm/router.py : L46-86]"
  - "[backend/app/orderbook/router.py : L94-233]"
  - "[backend/app/orderbook/matcher.py : L71-149]"
  - "[backend/app/orderbook/eip712.py : L174-196]"
  - "[backend/app/relayer/queue.py : L30-62]"
  - "[backend/app/relayer/worker.py : L1-21]"
  - "[backend/app/relayer/worker.py : L916-929]"
  - "[backend/app/relayer/router.py : L24-106]"
  - "[backend/app/oracle/router.py : L78-97]"
  - "[backend/app/oracle/router.py : L124-151]"
  - "[backend/app/oracle/router.py : L180-221]"
  - "[backend/app/ramps/router.py : L24-88]"
  - "[backend/app/kyc/router.py : L18-85]"
  - "[backend/app/kyc/router.py : L88-159]"
  - "[backend/app/emissions/router.py : L121-158]"
  - "[backend/app/emissions/router.py : L203-386]"
  - "[backend/app/portfolio/router.py : L21-158]"
  - "[backend/app/indexer/listener.py : L40-118]"
  - "[backend/app/indexer/listener.py : L121-185]"
  - "[backend/app/indexer/listener.py : L188-202]"
  - "[backend/app/indexer/listener.py : L262-388]"
  - "[backend/app/indexer/listener.py : L454-523]"
  - "[backend/app/models.py : L90-122]"
  - "[backend/app/models.py : L174-276]"
---

# Backend

- [SHIPPED] `create_app` mounts auth, chain, listing (before markets, so `/markets/{id}` does not capture `/markets/listing/*`), markets, orderbook, relayer, amm, ramps, kyc, emissions, oracle, portfolio and aa under `/api/v1`, plus `/health`. [backend/app/main.py : L64-96]
- [SHIPPED] Lifespan runs `run_migrations` once, starts the indexer loop (`INDEXER_ENABLED`) and the relayer worker (only with `RELAYER_ENABLED` and `RELAYER_WORKER_ENABLED`), and releases the indexer lock on shutdown. [backend/app/main.py : L27-61]
- [SHIPPED] `run_migrations` is the only schema entry point: on Postgres it takes `pg_advisory_xact_lock(0x4F554D47)` and runs `create_all` plus every idempotent `ensure_*` helper (relayer columns, BIGINT widening, attestation `created_at`, listing `reject_reason`, vote weight, price-point unique key, the one-vote-per-voter key after a dedup, the live-emission partial key) in one transaction, so concurrent Cloud Run starts queue instead of racing DDL. The live-emission key is skipped with a warning while live duplicates exist, because those rows are real signed txs and are never deleted. [backend/app/db.py : L250-272] [backend/app/db.py : L158-216]
- [SHIPPED] Database: SQLite `aiosqlite` locally, Postgres `asyncpg` in production (`DATABASE_URL`). [backend/app/db.py : L12-14]
- [SHIPPED] Settings from `.env` via pydantic: RPC, chain id, JWT, CDP, contract addresses, `TRADING_HALT_AT_CLOSE` (default true), bounded RPC timeouts (`AMM_QUOTE_RPC_TIMEOUT_SECONDS` 5, `PORTFOLIO_RPC_TIMEOUT_SECONDS` 10, `OPERATOR_RPC_TIMEOUT_SECONDS` 15, `OPERATOR_TX_TIMEOUT_SECONDS` 60), `INDEXER_*` and `RELAYER_*`. [backend/app/config.py : L46-130]

## Auth

- [SHIPPED] `GET /auth/nonce/{address}` stores a hex nonce. [backend/app/auth/router.py : L74-83]
- [SHIPPED] `POST /auth/siwe` recovers ECDSA, matches address and nonce, issues HS256. Operator flag only if the recovered address is `OPERATOR_PRIVATE_KEY`; the oracle job bootstraps its operator user this way. [backend/app/auth/router.py : L86-141]
- [SHIPPED] `POST /auth/cdp` / `/auth/cdp/email` / `/auth/cdp/verify`: `validateAccessToken`; JWT `sub` is the smart account; client address ignored; CDP users are not operators. [backend/app/auth/router.py : L144-173] [backend/app/cdp.py : L116-134]
- [SHIPPED] Bearer JWT identifies `User`; `require_operator` gates operator routes. [backend/app/auth/router.py : L50-71]
- [SHIPPED] Fail closed on CDP routes when `CDP_PROJECT_ID` or `CDP_API_KEY_SECRET` is missing. Tests mock CDP. [backend/app/cdp.py : L21-25]

## Chain metadata

- [SHIPPED] Public `GET /chain/addresses` returns `{chainId, MockUSDC, ConditionalTokens, MarketAMM, MarketFactory, ConsensusOracle, FeeVault, Exchange}` from Settings only (EIP-55, null when unset), never from a local deployments file. Mobile prefers it over its bundled asset. [backend/app/chain/router.py : L36-48]

## Account abstraction

- [SHIPPED] `POST /aa/userop` returns 410. [backend/app/aa/router.py : L163-165]
- [SHIPPED] `POST /aa/cdp-send` (JWT) allowlist: USDC approve with spender MarketAMM or MarketFactory, CTF `setApprovalForAll` for MarketAMM, `buyWithUSDC`, `sellToUSDC`, and factory `createPermissionlessMarket`; `value` must be 0; `matchOrders`, `createPrimaryMarket` and anything else 403. [backend/app/aa/router.py : L75-104]
- [SHIPPED] Before sending: AMM trades on a halted market get 409 (`market closed`, `market resolved`, `listing not confirmed`), and a listing call must match a `prepared` row for the same user and salt (403 `listing not prepared` / `listing differs from the prepared listing`). Sender is JWT `sub`. [backend/app/aa/router.py : L168-222]
- [SHIPPED] Backend REST send uses `useCdpPaymaster: true` on Base Sepolia. [backend/app/cdp.py : L169-180]

## Markets

- [SHIPPED] `GET /markets` returns EventCard[]: primaries are types 0 and 2 with nested wildcard children; orphan wildcards list alone; `?parentId=` stays a flat filter. Only visible markets: unpaused, and for type 2 a confirmed listing. `?includePaused=1` also lists paused and archived rows, only with an operator bearer JWT (401 without a token, 403 for a non-operator), so the oracle job can resolve a paused registered market; the public list is unchanged. [backend/app/markets/router.py : L177-229] [backend/app/markets/visibility.py : L28-42]
- [SHIPPED] `MarketPublic` adds `tradingHaltsAt` (closeTime when the halt flag is on, else null), `tradingOpen` and `yesPriceMicros` (latest indexed PricePoint, one window query for the list). `MarketDetail` adds `creator` and `listing` for type 2. [backend/app/markets/router.py : L86-130]
- [SHIPPED] `GET /markets/schedule` is public; operator `POST /markets/schedule` upserts NFL week rows (int fields bounded to int4). `listedConditionId` omitted keeps the stored link, a cid sets it, and an explicit `""` clears it so an orphaned game can be relisted. [backend/app/markets/router.py : L248-296]
- [SHIPPED] `GET /markets/{id}` (children always present, maybe `[]`) and `/history` return 404 for type-2 markets without a confirmed listing; paused markets still resolve by id. [backend/app/markets/router.py : L299-338]
- [SHIPPED] Operator `POST /markets/{id}/score` on sports primaries only (LiveScore with facts; null scores never become 0). [backend/app/markets/router.py : L341-382]
- [SHIPPED] Operator `POST /markets` is idempotent: it derives the cid from `factory.oracle()` and the questionId and mirrors an existing factory market without a tx. Seed below `MIN_LP` (10,000 base units) is 422, `market_type` other than 0/1 is 400 ("user markets use /markets/listing"), and a condition prepared outside this factory is 409. Chain work runs in a worker thread under one operator-tx lock (pending nonce, receipt wait bounded by `OPERATOR_TX_TIMEOUT_SECONDS`); errors carry only the exception type. Creates are serialized per process (asyncio lock) and, on Postgres, by `pg_advisory_xact_lock` keyed by the questionId; `marketExists` is re-read under the operator lock, so a racing replay mirrors the first create instead of sending a second tx. The DB mirror inserts `ON CONFLICT DO NOTHING`, so a row the indexer inserted first is updated instead of causing a 500. [backend/app/markets/router.py : L385-554] [backend/app/markets/router.py : L39-72]
- [SHIPPED] Operator `POST /markets/{id}/pause` calls factory `setPaused` off the event loop with `OPERATOR_RPC_TIMEOUT_SECONDS`. [backend/app/markets/router.py : L557-602]
- [SHIPPED] Operator `POST /markets/{id}/archive` hides an orphaned row (DB only) when the configured ConsensusOracle has no closeTime for it; a registered market is 409 ("pause it on the factory instead"). Use it for the legacy markets from an older deployment. It also clears `listedConditionId` on schedule rows that point at the orphan. [backend/app/markets/router.py : L623-660]

## User listing (OU-T010)

Decision: [ADR-0012](../adr/0012-loosely-gated-user-listing.md).

- [SHIPPED] `GET /markets/listing/config` (public) reads the factory's listing config; it never errors and returns `enabled: false` with a reason for a legacy factory, a chain outage or a missing address. `GET /eligibility` (JWT) returns allowed, cooldown and pending count. [backend/app/markets/listing.py : L237-285]
- [SHIPPED] `POST /prepare` (JWT) runs the off-chain gates, mints a random salt, checks the cid against `factory.userConditionId`, stores a `prepared` MarketListing and returns two calls: USDC `approve(factory, seed + fee)` and `createPermissionlessMarket`. The backend never sends the listing tx. Errors: 400 gates, 403 invite-only, 409 similar market open, 429 cooldown or 5 pending per 24 h, 503 chain. [backend/app/markets/listing.py : L288-362]
- [SHIPPED] Gates: question 10–256 bytes ending in "?", no subjective words, no leading or trailing whitespace; criteria 20–2000 chars; seed below 2^63; duplicate when meaningful tokens match or Jaccard ≥ 0.9 with identical numbers. [backend/app/markets/listing_gates.py : L58-167]
- [SHIPPED] `POST /confirm` (JWT, idempotent) needs the on-chain market (409 "not on chain yet"), the creator (403) and a criteria text whose keccak matches `criteriaHashOf` (409), then re-runs the gates on the on-chain question, closeTime and seed. Failure stores `rejected` with `reject_reason` and returns 422 "listing rejected: …". Rows are inserted `ON CONFLICT DO NOTHING`, so a racing indexer is harmless. [backend/app/markets/listing.py : L376-424] [backend/app/markets/visibility.py : L73-107]
- [SHIPPED] Operator `GET /markets/listing/review` lists type-2 markets that exist on chain but are not public, so the operator can pause and arbitrate them (the general resolver never sees them). [backend/app/markets/listing.py : L492-526]
- [SHIPPED] `MarketListing` table (`prepared | indexed | confirmed | rejected`); `markets` is never altered for it. [backend/app/models.py : L182-200]

## Trading halt

Decision: [ADR-0011](../adr/0011-pm-amm-v2-close-gate.md) (on-chain gate plus this API gate).

- [SHIPPED] `trading_halt_reason`: `market resolved` always; `listing not confirmed` for hidden type-2 markets; `market closed` once now ≥ closeTime when `TRADING_HALT_AT_CLOSE` is on; then `market paused` for a paused or archived row (whatever the flag), whose `tradingOpen` is false. Unknown markets pass (the chain decides). Used by quotes, `/aa/cdp-send`, `POST /orders`, the matcher and the relayer worker. MarketAMM does not read the factory's paused flag, so direct AMM calls on a paused market stay possible. [backend/app/markets/trading.py : L27-67]

## AMM proxy

- [SHIPPED] `GET /amm/{id}/quote` checks the halt before any RPC (409), then calls `quoteBuy` or `quoteSell` in a worker thread on a cached Web3 with a 5 s timeout and no retries. Bad id or arguments 400; a revert 422 with its reason (`price bound`, `no pool`, `dust`); transport errors 503 "RPC unavailable" without the URL. [backend/app/amm/router.py : L46-86]
- [STUB] The proxy only knows `AMM_ADDRESS`. After the v2 swap, still-open legacy markets whose pools stay on `MarketAMMLegacy` quote `no pool`.

## Orderbook and relayer (OU-T003, leftover CLOB)

- [SHIPPED] `POST /orders` (JWT) checks in order: maker equals session, field ranges, halt (409), Exchange configured (503), recomputed EIP-712 `orderHash`, signature recovers to the maker (unsigned `0x` or bad signature 400, nothing stored), expiry margin, duplicate (409); with the relayer enabled also readiness (503 "relayer misconfigured"), an open-order cap and an optional on-chain preflight (400 with `reasons`). [backend/app/orderbook/router.py : L94-164] [backend/app/orderbook/eip712.py : L174-196]
- [SHIPPED] Matching skips expired orders and self-matches, applies fills with guarded `filled + q ≤ amount` updates, returns nothing once the market halts, and records a `Trade` with status `pending` (relay job) or `offchain` (relayer off). [backend/app/orderbook/matcher.py : L71-149]
- [SHIPPED] `DELETE /orders/{hash}` rolls back pending jobs, lists in-flight ones, and returns `offchainOnly`, `onchainCancelRequired` and `cancelOrderArgs` once the signature may be public (the maker must call `Exchange.cancelOrder`). `GET /orderbook/{cid}` adds `tradingOpen` and `haltReason`. [backend/app/orderbook/router.py : L167-233]
- [SHIPPED] Queue: `relayer_ready` needs `RELAYER_ENABLED`, a parseable `RELAYER_PRIVATE_KEY` and a configured Exchange; the key alone never enables anything. Each match inserts a new `RelayJob`. With the relayer off, orders still verify and match, and fills stay `offchain` with no tx. [backend/app/relayer/queue.py : L30-62]
- [SHIPPED] Worker state machine `pending → sending → sent → confirmed | failed`: the signed tx, nonce and fees are committed before broadcast and the same bytes are re-broadcast after a crash; EIP-1559 fees capped by `RELAYER_MAX_FEE_PER_GAS_WEI` with ≥10% bumps; receipts need the matching `OrderFilled` log; a failed job rolls back its optimistic fills and retires the side that failed preflight. [backend/app/relayer/worker.py : L1-21]
- [SHIPPED] Invariants: a job that signed a tx is never re-signed on a fresh nonce on one ambiguous "nonce too low"; preflight counts funds other in-flight jobs will spend (`busy:` defers); jobs matched at or after closeTime roll back; stored and logged errors are redacted (RPC URLs carry keys).
- [SHIPPED] A Postgres advisory lock (`RELAYER_LEADER_LOCK_KEY`) elects one sending instance; the background loop starts only with both flags and a ready relayer. On Cloud Run it needs `--no-cpu-throttling --min-instances=1`, or an operator calls `POST /relayer/tick`. The relayer EOA needs Base Sepolia ETH. [backend/app/relayer/worker.py : L916-929]
- [SHIPPED] Tunables (code defaults; deploy-gcp passes only the two flags): `RELAYER_POLL_SECONDS`, `_BATCH_SIZE`, `_MAX_ATTEMPTS`, `_RETRY_BACKOFF_SECONDS`, `_RESUBMIT_AFTER_SECONDS`, `_FEE_BUMP_BPS`, `_MAX_FEE_PER_GAS_WEI`, `_PRIORITY_FEE_WEI`, `_GAS_BUFFER_BPS`, `_GAS_LIMIT_CAP`, `_CONFIRMATIONS`, `_MIN_EXPIRY_SECONDS`, `_PREFLIGHT_ON_POST`, `_LEADER_LOCK_KEY`, `_RPC_TIMEOUT_SECONDS`, `_LOG_LOOKBACK_BLOCKS`, `_MAX_OPEN_ORDERS_PER_MAKER`. `RELAYER_PRIVATE_KEY` is shared with `/emissions/distribute` through one nonce manager. [backend/app/config.py : L107-130]
- [SHIPPED] Operator `GET /relayer/status` (enabled, ready, next nonce, balance, counts, last error and tick), `GET /relayer/jobs`, `POST /relayer/tick` (409 when not ready); `GET /relayer/jobs/{id}` for either maker or an operator, else 404. Job JSON never includes the raw tx. [backend/app/relayer/router.py : L24-106]
- [STUB] Makers must be EOAs (SIWE): Exchange has no EIP-1271, and there is no relayed on-chain cancel (OU-T016).

## Oracle records

- [SHIPPED] `POST /oracle/attest` is operator-only (the oracle job sends its operator JWT); outcome 0–255, so research records may carry outcome 2. [backend/app/oracle/router.py : L78-97]
- [SHIPPED] `GET /oracle/{id}/status` returns attestations with `createdAt` (ISO UTC, null for legacy rows) and `kind` (`research` when evidenceJson carries the research marker, else `resolution`); `unanimous` counts resolution rows only. The research cooldown reads `createdAt`. [backend/app/oracle/router.py : L124-151]
- [SHIPPED] `POST /oracle/vote` needs a user JWT: voter is the caller and weight is their CTF YES+NO balance read on chain (403 for zero, 409 `already voted` for a second vote, 503 on RPC failure). The unique index `uq_vote_condition_voter` with `ON CONFLICT DO NOTHING` enforces one vote per voter, so concurrent requests record one row. [backend/app/oracle/router.py : L180-221]
- [SHIPPED] Operator `POST /oracle/resolved` mirrors `Market.resolved` and payouts after the job resolves on chain. [backend/app/oracle/router.py : L100-121]
- [STUB] Attest and vote endpoints do not submit `submitAttestation` / `castVote` on chain; the oracle job sends its own txs.

## Ramps

- [SHIPPED] Coinbase Pay URLs from `coinbase_onramp_app_id` or `"demo"`; destination is the smart account. Coinbase stays as fallback. [backend/app/ramps/router.py : L140-163]
- [SHIPPED] `POST /ramps/moonpay/session` (JWT): amount must be finite, positive and ≤ 1,000,000 (else 400), KYC gate, then a signed widget URL with `lockAmount=true`, destination = session address. [backend/app/ramps/router.py : L24-88]
- [SHIPPED] `POST /ramps/moonpay/webhook`: verify signature on raw body; fields longer than their `ramp_txs` columns are 400; upsert `RampTx`. [backend/app/ramps/router.py : L91-137]
- [PHASE2] Additional payment providers and webhook retry logic.

## KYC

- [SHIPPED] Routes read `user.address` from the ORM `User` (previously every authenticated call returned 500). `enforce_kyc_gate` rejects non-finite or non-positive amounts with 400. [backend/app/kyc/router.py : L18-85]
- [SHIPPED] `POST /kyc/session`, `GET /kyc/status`, `POST /kyc/check` (notional must be > 0 and finite, else 422). Gate: notional ≥ threshold (default $500/day) or restricted jurisdiction requires KYC; 403 unless status is `pass` or `not_required`. [backend/app/kyc/router.py : L114-262]
- [SHIPPED] `jurisdiction` must be empty or an ISO 3166-1 alpha-2 code, two uppercase letters (else 422). It is write-once: a different code for an address that already has one is 409. It is still self-reported. [backend/app/kyc/router.py : L88-159]
- [SHIPPED] `KycRecord{address, status, jurisdiction, updated_at}`: status only, never documents. [backend/app/models.py : L174-179]
- [PHASE2] Third-party KYC provider integration with webhook callbacks.

## Portfolio

- [SHIPPED] `GET /portfolio/{address}` returns CTF positions, open orders and trades. Chain reads run in a worker thread with `PORTFOLIO_RPC_TIMEOUT_SECONDS` and no web3 retries; 503 without echoing RPC errors. [backend/app/portfolio/router.py : L21-137]
- [STUB] `GET /fee-vault/nav` reads `nav()` the same way and returns `nav: 0, simulated: true` when RPC or the deployment is missing. [backend/app/portfolio/router.py : L140-158]

## Indexer

- [SHIPPED] Leader: on Postgres one instance holds `pg_try_advisory_lock(INDEXER_LEADER_LOCK_KEY)` on a dedicated connection; others skip ticks without backing off. A failed ping, try-lock or unlock invalidates that connection instead of returning it to the pool, so the session lock cannot leak. [backend/app/indexer/listener.py : L40-118]
- [SHIPPED] Checkpoints: `indexer_checkpoints` has one row per `(MarketFactory, MarketAMM)` address pair (lowercase). A new pair starts at `INDEXER_START_BLOCK` (the v2 deploy block) whatever the old pair's row says. With the variable at 0 it starts at block 1 on anvil, else `INDEXER_LOOKBACK_BLOCKS` behind head but never past the legacy unkeyed `checkpoints` row, which is kept and only read. [backend/app/indexer/listener.py : L262-388]
- [SHIPPED] Ranges: for a pair that has a row, `INDEXER_START_BLOCK` only fast-forwards it and never rewinds it. Ticks split into at most `INDEXER_MAX_CHUNKS_PER_TICK` windows of `INDEXER_MAX_BLOCK_RANGE`, halve the window on failure, and back off up to `INDEXER_MAX_BACKOFF_SECONDS`. [backend/app/indexer/listener.py : L188-202]
- [SHIPPED] A failed range holds the checkpoint; `advance_checkpoint` only moves forward. All RPC runs in threads with `INDEXER_RPC_TIMEOUT_SECONDS`; logs carry exception types only. [backend/app/indexer/listener.py : L262-388]
- [SHIPPED] Price history: `PoolSeeded` stores 0.5; each `Swap` stores the pool price from `pools(cid)` at that block: pm-AMM `Φ((no − yes) / L)` from index 4, or the CPMM mid `no / (yes + no)` for a v1 pool. Points are unique per (condition, block, log index). [backend/app/indexer/listener.py : L121-185]
- [SHIPPED] Type-2 `MarketCreated` upserts a MarketListing (`indexed`) and marks it `confirmed` only when the criteria hash matches and the same review as `/confirm` passes, else `rejected`. [backend/app/indexer/listener.py : L454-523]
- [SHIPPED] `GET /markets/{id}/history` returns ordered `PricePoint[]`, `[]` when empty. [backend/app/markets/router.py : L299-314]
- [STUB] Only `AMM_ADDRESS` is indexed; legacy-AMM swaps stop being indexed after the v2 swap.
- [PHASE2] CTF balance snapshots and OU NAV history.

## Emissions

- [SHIPPED] `POST /emissions/distribute` (operator) calls `EmissionsDistributor.distribute` with `RELAYER_PRIVATE_KEY` through the relayer's shared nonce manager. Each signed tx is written ahead (`EmissionDistribution`): an ambiguous broadcast returns 202 with its hash, a retry with the same payload and `idempotencyKey` returns the recorded row (409 once confirmed), and a revert estimate is 400. `GET /emissions/distributions/{id}` reads a row. [backend/app/emissions/router.py : L203-386]
- [SHIPPED] Two concurrent identical requests send one tx. The route takes the nonce lock, then (Postgres) the leader lock, and repeats the duplicate check under both, so a second identical request in the same process waits and gets the first row back (202 `duplicate`). The partial unique index `uq_emission_live_payload` on `(payload_hash, idempotency_key)` for live rows makes a cross-instance loser's write-ahead insert fail before anything is sent. [backend/app/emissions/router.py : L121-158]
- [SHIPPED] Program IDs: 0=LP, 1=maker, 2=agent, 3=quest. Max 100 recipients per batch; never mints.
- [PHASE2] Off-chain accounting service that tracks vested amounts and calls the distribute endpoint in batches.
