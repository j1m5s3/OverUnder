---
title: Loosely gated user listing
status: SHIPPED
area: cross
summary: MarketFactory v2 adds createPermissionlessMarket (type 2, sender-namespaced question id, criteria hash, seed, fee, close window, cooldown, permissionless flag or lister allowlist). The backend prepares and confirms listings and hides type 2 until confirmed. Web lists through a sponsored CDP batch. The general resolver settles user markets without arbitration.
last_verified: 2026-09-23
pointers:
  - "[contracts/src/MarketFactory.vy : L82-108]"
  - "[contracts/src/MarketFactory.vy : L161-199]"
  - "[contracts/src/MarketFactory.vy : L201-227]"
  - "[contracts/script/deploy_ci.py : L82-85]"
  - "[.github/workflows/deploy-contracts.yml : L20-35]"
  - "[backend/app/markets/listing.py : L237-362]"
  - "[backend/app/markets/listing.py : L376-489]"
  - "[backend/app/markets/listing.py : L492-526]"
  - "[backend/app/markets/listing_gates.py : L54-167]"
  - "[backend/app/markets/visibility.py : L28-107]"
  - "[backend/app/indexer/listener.py : L384-453]"
  - "[backend/app/aa/router.py : L75-160]"
  - "[web/src/features/listing/ListMarketForm.tsx : L318-344]"
  - "[web/src/features/listing/listing.ts : L276-349]"
  - "[oracles/resolve/general.py : L211-409]"
  - "[oracles/listing/questions.py : L21-26]"
  - "[contracts/tests/test_factory_listing.py : L75-89]"
---

## Status

Accepted 2026-09-23. Implements OU-T010, the listing bullet of [ADR-0007](0007-amm-first-uniform-lvr.md). It relaxes the operator-only listing of [ADR-0004](0004-trusted-mvp-keys.md) and widens the `/aa/cdp-send` allowlist of [ADR-0010](0010-cdp-embedded-wallets.md); both carry dated notes. On-chain behaviour depends on MarketFactory v2, which reaches Base Sepolia through the redeploy in [ADR-0011](0011-pm-amm-v2-close-gate.md).

## Context

- ADR-0007 wants an AMM seed, not operator-recruited makers, to bootstrap long-tail markets.
- The v1 factory only let the operator create primaries and the generator create wildcards.
- Open listing invites four problems:
  - spam;
  - wording that no oracle can settle;
  - clients editing calldata after the off-chain checks;
  - squatting, because `ConditionalTokens.prepareCondition` is callable by anyone for any question id. [contracts/src/ConditionalTokens.vy : L53-60]
- The backend must not sign listings for users. CDP smart accounts send them (ADR-0010).

## Decision

On chain: MarketFactory v2
- [SHIPPED] `createPermissionlessMarket(salt, closeTime, question, criteriaHash, seedUsdc)` creates a market of type 2. The question id is `keccak256(abi.encode(sender, salt))`, so a lister can never collide with an operator question id or with another lister. `userQuestionId` and `userConditionId` expose the formula. A reused salt reverts in the CTF (`already prepared`). [contracts/src/MarketFactory.vy : L161-199]
- [SHIPPED] The gates run in this order:
  - `listing closed`: the `permissionless` flag or `listerAllowed[sender]` must hold;
  - `seed below min`;
  - `close too soon` / `close too far`: `minLeadTime` / `maxHorizon`;
  - `question too short`: at least 10 bytes; `String[256]` caps it at 256;
  - `criteria required`: a non-zero `criteriaHash`;
  - `cooldown`: `lastListedAt + listingCooldown`, per creator.
  [contracts/src/MarketFactory.vy : L177-186]
- [SHIPPED] The factory stores `creatorOf`, `seedOf` and `criteriaHashOf` and emits `UserMarketListed`. It pulls seed + `listingFeeUsdc` from the lister and sends the fee to `feeRecipient` (FeeVault, so it accrues to OU NAV). The seed goes through `amm.seedPoolFor(cid, seed, lister)`: the lister owns the LP, which is seed-locked until close or resolution with `MIN_LP` locked forever (ADR-0011). [contracts/src/MarketFactory.vy : L187-199] [contracts/src/MarketAMM.vy : L354-368]
- [SHIPPED] Operator admin:
  - `setListingConfig` enforces bounds: min seed > 0; a fee needs a recipient; `minLeadTime` ≥ 600 s; `minLeadTime` < `maxHorizon` ≤ 31,622,400 s.
  - `setPermissionless` and `setLister` control access; `setPaused` is unchanged.
  - Constructor defaults are closed (allowlist only): 10 USDC minimum seed, no fee, 3,600 s lead, 90-day horizon, 3,600 s cooldown.
  [contracts/src/MarketFactory.vy : L96-108] [contracts/src/MarketFactory.vy : L201-227]
- [SHIPPED] Base Sepolia v2 values come from `deploy-contracts.yml` inputs through `deploy_ci.py`:
  - `permissionless` true;
  - `min_seed_usdc` 10,000,000 (10 USDC);
  - fee 0, with FeeVault as recipient;
  - lead 3,600 s, horizon 7,776,000 s;
  - `listing_cooldown` 3,600 s;
  - `close_gate` true.
  Local chain 31337 (`deploy.py`) opens listing with cooldown 0. [.github/workflows/deploy-contracts.yml : L20-35] [contracts/script/deploy_ci.py : L82-85]

Backend listing API
- [SHIPPED] `GET /api/v1/markets/listing/config` reads the chain config (disabled on a v1 factory). `GET /eligibility` reports allowlist state, cooldown left and pending count. [backend/app/markets/listing.py : L237-285]
- [SHIPPED] `POST /prepare` requires a session. It returns 403 when the lister is not allowed and 429 during cooldown, then runs the off-chain gates:
  - question of 10–256 bytes, ending in `?`, with no subjective words;
  - criteria of 20–2,000 characters;
  - close window and minimum seed from the chain config;
  - at most 5 unconfirmed prepares per creator per 24 h;
  - no duplicate of an open public market (same normalized text, same meaningful tokens, or Jaccard ≥ 0.9 with identical numbers).

  It then draws a random 32-byte salt, checks its condition id against `factory.userConditionId`, stores a `prepared` MarketListing, and returns calldata for `USDC.approve(factory, seed + fee)` plus `createPermissionlessMarket`. The backend never sends the listing. [backend/app/markets/listing.py : L288-362] [backend/app/markets/listing_gates.py : L54-167]
- [SHIPPED] `POST /confirm` and the indexer (on a type-2 `MarketCreated`) run one shared review:
  - the criteria text must hash to `criteriaHashOf`;
  - a prepared listing's on-chain question, closeTime and seed must equal the prepared values, because the question is committed in neither the condition id nor the hash;
  - the wording, whitespace and duplicate gates re-run on the on-chain question.

  A pass sets `confirmed`. A failure sets `rejected` with `reject_reason`, and `/confirm` answers 422 `listing rejected: <reason>`. Before the tx lands it answers 409 `not on chain yet`. [backend/app/markets/listing.py : L376-489] [backend/app/markets/visibility.py : L73-107] [backend/app/indexer/listener.py : L384-453]
- [SHIPPED] Visibility rule: a type-2 market is public only once its listing is `confirmed`. `indexed`, `prepared` and `rejected` rows stay out of lists, detail, quotes (409 `listing not confirmed`) and sponsored trades. `Market.paused` is not reused, so an unpause cannot re-publish a rejected market. [backend/app/markets/visibility.py : L28-49] [backend/app/markets/trading.py : L50-54]
- [SHIPPED] The operator-only `GET /api/v1/markets/listing/review` lists type-2 markets that are not public. Hidden markets can still be traded directly on the AMM, and the general resolver never sees them. The operator pauses them (`POST /markets/{id}/pause`; the flag hides the row, and MarketAMM does not read it) and resolves them by hand. [backend/app/markets/listing.py : L492-526]

Web and sponsored gas
- [SHIPPED] The web `/list` page (`ListMarketForm`) works in this order:
  1. Load config and eligibility and mirror the gates locally.
  2. Call `/prepare`.
  3. Send both calls as one CDP user operation with `useCdpPaymaster: true`, straight to CDP.
  4. Confirm with bounded retries: 409 `not on chain yet` and 5xx retry with 1.5–10 s backoff, at most 12 calls. A 422 rejection is terminal.

  A per-mount guard never re-sends a batch once it has a user-operation hash. [web/src/features/listing/ListMarketForm.tsx : L318-344] [web/src/features/listing/listing.ts : L276-349] [web/src/features/listing/listing.ts : L398-428]
- [SHIPPED] `/aa/cdp-send` (the mobile send path) sponsors a listing only if its calls exactly match a `prepared` row of the same user (see the ADR-0010 note). Mobile has no listing UI. [backend/app/aa/router.py : L117-160]

Resolution
- [SHIPPED] User markets resolve through the general resolver, never the sports dual gate, and it only sees public (confirmed) markets:
  - Order: oldest closeTime first, at most 2 researched per tick.
  - Ungated markets wait until closeTime + 24 h (`OU_GENERAL_RESOLVE_DELAY_SECONDS`). The resolver would honour a `resolveAfter` field, but the API does not send one today.
  - The question and criteria reach the agents as sanitized untrusted text.
  - `submitConsensus` needs 3/3 agreement with every confidence ≥ 0.8.
  - Any `undetermined` (2) answer blocks both consensus and fallback.
  - After oracle closeTime + 24 h, under the default `attest` policy, a confident 2/3 majority attests and `resolveFallback` runs. `resolveArbitrated` is never called.
  - A failed research attempt waits 6 h before retrying.

  [oracles/resolve/general.py : L1-19] [oracles/resolve/general.py : L168-179] [oracles/resolve/general.py : L211-409] [oracles/resolve/cooldown.py : L19-82]

Squatting
- [SHIPPED] Operator NFL question ids are derived from the public schedule. With the `OU_QUESTION_ID_KEY` secret set, the listing job makes them HMAC-SHA256 under that key, so nobody can precompute them and prepare the condition first. Without the key they stay the legacy public sha256. The key must stay stable, because changing it changes every future id. A squatted create gets 409 `condition prepared outside this factory` and is reported under `squatted`, not as a stage error. [oracles/listing/questions.py : L21-26] [backend/app/markets/router.py : L386-396] [oracles/listing/run.py : L250-257]

## Consequences

- A lister with a seed can open a market without an operator. A non-zero fee would accrue to OU holders.
- Negative: spam. On Base Sepolia the MockUSDC faucet makes the 10 USDC minimum seed free, so the real controls are the 1 h per-creator cooldown, a non-zero listing fee, turning `permissionless` off in favour of the lister allowlist, and operator review and pause. Fresh addresses evade the per-creator cooldown.
- Negative: the off-chain gates (wording, duplicates, 5 per day) only shape what the app shows. A direct factory call skips `/prepare`; if it fails review it stays hidden but can still be traded on the AMM.
- Negative: ambiguous or cancelled user markets can stay unresolved. Agents answer `undetermined`, which blocks consensus and fallback, and ConsensusOracle only pays [1,0] or [0,1], so there is no refund outcome (OU-T014). Hidden markets need operator pause plus arbitration by hand, and after 24 h the operator can still arbitrate any market (ADR-0002).
- Negative: listing gas is about 720k execution gas in boa (120-byte question, seed included). The CDP Portal paymaster policy needs a per-op cap of at least about 750k, plus the v2 factory's `createPermissionlessMarket` and a USDC approve to the factory. That is a manual portal step.
- Negative: the gates live in three places (contract, `listing_gates.py`, web `listing.ts`), and the copies must stay in sync.
