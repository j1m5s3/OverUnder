---
title: Phase 2 remaining vision
status: MIXED
area: roadmap
summary: MIXED spec. OU-T001–T013 are done in code (relayer, static pm-AMM with close gate, loosely gated user listing included; the Base Sepolia AMM + Factory redeploy is a post-merge ops step). Open are OU-T014 invalid outcome, OU-T015 dynamic L_t and OU-T016 smart-account CLOB makers.
last_verified: 2026-09-23
pointers:
  - "[mobile/lib/models/deployments.dart : L77-90]"
  - "[web/src/app/providers.tsx : L21-45]"
  - "[backend/app/auth/router.py : L144-173]"
  - "[backend/app/cdp.py : L116-134]"
  - "[backend/app/ramps/router.py : L140-163]"
  - "[contracts/src/RevenueToken.vy : L13-32]"
  - "[contracts/src/FeeVault.vy : L44-50]"
  - "[contracts/src/OverUnderPaymaster.vy : L322-355]"
  - "[contracts/src/MarketAMM.vy : L1-12]"
  - "[contracts/src/MarketFactory.vy : L177-199]"
  - "[backend/app/relayer/worker.py : L1-21]"
  - "[backend/app/markets/trading.py : L47-67]"
  - "[oracles/resolve/fallback.py : L48-156]"
---

# Phase 2

This file is MIXED. [SHIPPED] Flutter (OU-T006), emissions (OU-T004), Cursor-runtime agents (OU-T005), score scout (OU-T011), dual-gate resolve (OU-T012), week-roll listing (OU-T013), MoonPay/KYC (OU-T007), leftover OverUnderPaymaster (OU-T001), CDP wallets (OU-T002), leftover CLOB production relayer (OU-T003), uniform-LVR spike and MarketAMM v2 (OU-T008/T009) and loosely gated user listing (OU-T010). MarketAMM v2 and MarketFactory v2 are code-complete; Base Sepolia keeps the v1 pair until the post-merge redeploy (section 9). Remaining open: invalid/refund outcome (OU-T014), dynamic liquidity L_t (OU-T015), smart-account CLOB makers and on-chain cancel (OU-T016). CLOB UX is an optional overlay, not a required depth-chart milestone. The app path is CDP Paymaster, not OverUnderPaymaster.

## 1. Flutter

Do not start from a blank app. Mirror web feature modules 1:1 as documented in [mobile/README.md](../../mobile/README.md).

### Module map

| Web today | Flutter target |
| --- | --- |
| `web/src/features/markets` | `lib/features/markets` |
| `web/src/features/trade` | `lib/features/trade` |
| `web/src/features/wallet` | `lib/features/wallet` |
| `web/src/features/oracle` | `lib/features/oracle` |
| `web/src/features/listing` | none (web only) |

Shared inputs (already in repo, do not fork):

- [SHIPPED] Theme: `shared/design-tokens/tokens.json` (color, spacing, type) feeds `lib/theme/app_theme.dart`; no hardcoded hex in widgets.
- [SHIPPED] API: `shared/openapi.json` is regenerated from `create_app().openapi()` whenever FastAPI routes change. Do not hand-write DTO field names.
- [SHIPPED] Chain: trade targets come from `GET /api/v1/chain/addresses`, else the bundled `assets/deployments/<CHAIN_ID>.json`, which `scripts/sync_mobile_deployments.py` regenerates after every AMM/Factory redeploy. [mobile/lib/models/deployments.dart : L77-90]

### Screens (required v1 mobile)

1. [SHIPPED] **Market list**: primaries (marketType 0 and 2) as event cards with wildcard rows. `GET /api/v1/markets` returns EventCard[]; `?parentId=` is a flat child filter.
2. [SHIPPED] **Market detail**: AMM buy/sell with `quoteBuy`/`quoteSell`, then `buyWithUSDC` / `sellToUSDC` through `/aa/cdp-send`. Closed banner and disabled swap once trading halts. No CLOB ticket.
3. [SHIPPED] **Wallet**: CDP email OTP via the API. Onramp destination is the smart account (`GET /api/v1/ramps/onramp-url`).
4. [SHIPPED] **Oracle**: attestations, votes and unanimity. [PHASE2] Countdown to `closeTime + 86400` and a vote CTA.

### Mobile-specific rules

- [SHIPPED] Same Base USDC and contract addresses as web. No parallel “mobile chain”.
- [PHASE2] Deep links: `overunder://markets/<conditionId>`.
- [PHASE2] Offline: cache last market list; never cache signed orders as submitted until the API 200.
- [PHASE2] QA: golden screenshots against tokens.json; the same e2e economic scenario as `scripts/e2e_local.py` against a shared Anvil.

## 2. Optional CLOB overlay (not required)

The MVP proves AMM settlement. A CLOB overlay may be scheduled later; it is **not** a required Phase 2 destination and there is **no** required depth-chart milestone. The web OrderTicket was deleted.

### Discovery

- [PHASE2] Category tabs (sports, politics, crypto) stored on Market as metadata.
- [PHASE2] Search by question substring.
- [SHIPPED] “Related wildcards” on primary detail (the event hub, filterable via `parentId`).

### CLOB UX (optional leftover overlay)

- [PHASE2] Depth chart and last-trade tape from `GET /orderbook/{id}` + `Trade` rows.
- [PHASE2] Price as cents with a toggle to implied %; size in shares (6 decimals).
- [PHASE2] Click-to-join best bid/ask.
- [SHIPPED] `DELETE /orders/{hash}` cancels off-chain and returns `onchainCancelRequired` plus `cancelOrderArgs` for `Exchange.cancelOrder`. [backend/app/orderbook/router.py : L167-213]
- [SHIPPED] EIP-712 `Order` digest and signer checked at the API edge; unsigned `0x` or a wrong signer is 400. [backend/app/orderbook/router.py : L94-164]
- [STUB] Smart-account makers need EIP-1271 in a new Exchange (OU-T016).

### AMM UX

- [SHIPPED] Live quote on every keystroke; 1% fee shown (50/50 vault/LP).
- [SHIPPED] Slippage field bound to `minOut` / `minUsdc`.
- [PHASE2] Add/remove-liquidity panel (`addLiquidity`, `removeLiquidity`; withdrawable = `lpBalance - lpLocked`).
- [SHIPPED] Implied YES price is the pm-AMM mid `Φ((noReserve - yesReserve) / liquidity)` (`priceYes`); lists show the latest indexed price. [contracts/src/MarketAMM.vy : L263-269]

### Resolution UX

- [SHIPPED] Criteria text from `resolution_criteria` (always shown for user-listed markets).
- [SHIPPED] Resolution policy copy matches `OU_FALLBACK_POLICY=attest`.
- [PHASE2] Agent evidence URLs in the UI.
- [PHASE2] On-chain `castVote` button. The API vote route is authenticated and weights by the caller's CTF balance.

### Operations parity

- [SHIPPED] Operator API: create primary (idempotent), pause, archive orphaned rows, review hidden user listings.
- [SHIPPED] Relayer status (`GET /relayer/status`: next nonce, balance, job counts, last tick). [PHASE2] A status page UI and indexer lag view.

## 3. Uniform-LVR AMM (OU-T008 / OU-T009)

[SHIPPED] MarketAMM v2 is a static uniform-LVR pool (pm-AMM, Moallemi–Robinson): NO = L·g(u), YES = L·g(−u), YES price Φ(u). It keeps the v1 ABI and adds LP exit, `priceYes` and an on-chain closeTime gate. Decision: [ADR-0011](../adr/0011-pm-amm-v2-close-gate.md). Spike write-up: [explore/uniform-lvr-spike.md](../explore/uniform-lvr-spike.md). [contracts/src/MarketAMM.vy : L1-12]

- [SHIPPED] Fixed-point normal math (expWad, lnWad, Hart 5666 Φ, verified solver) in `contracts/src/lib/NormalMath.vy`. [contracts/src/lib/NormalMath.vy : L179-229]
- [SHIPPED] `buyWithUSDC` / `sellToUSDC` share `_buy` / `_sell` with the quotes, so quote equals execution. Fees stay `AMM_FEE_BPS = 100` (50 vault, 50 LP accumulator). [contracts/src/MarketAMM.vy : L159-238]
- [SHIPPED] Close gate: buys, sells and adds revert `"market closed"` at or after closeTime while `closeGate` is on (default true). [contracts/src/MarketAMM.vy : L115-119]
- [SHIPPED] Seed LP goes to the real provider (`seedPoolFor`), locked until close or resolution and `MIN_LP` forever. [contracts/src/MarketAMM.vy : L354-414]
- [PHASE2] Dynamic liquidity L_t shrinking toward close (OU-T015).

## 4. User listing (OU-T010)

[SHIPPED] Listing is loosely gated: anyone allowlisted, or anyone once the operator sets `permissionless`, can list a seeded type-2 market. Decision: [ADR-0012](../adr/0012-loosely-gated-user-listing.md). [contracts/src/MarketFactory.vy : L177-199]

- [SHIPPED] On-chain gates: seed ≥ `minSeedUsdc` (10 USDC default), closeTime within [now + minLeadTime, now + maxHorizon], question ≥ 10 bytes, non-zero criteria hash, per-creator cooldown; optional listing fee to `feeRecipient`. [contracts/src/MarketFactory.vy : L201-227]
- [SHIPPED] Off-chain gates on prepare and again on confirm: question ends in "?", no subjective words, criteria 20–2000 chars, duplicate check against open markets, 5 unconfirmed listings per 24 h. [backend/app/markets/listing_gates.py : L58-167]
- [SHIPPED] A type-2 market is public only when its listing is `confirmed`; indexed, prepared and rejected ones are hidden and not tradable through the API. [backend/app/markets/visibility.py : L28-42]
- [SHIPPED] Web `/list` sends USDC approve + `createPermissionlessMarket` as one CDP-sponsored user op, then confirms. [web/src/features/listing/ListMarketForm.tsx : L283-344]
- [SHIPPED] Operator pause stays; the operator reviews hidden listings via `GET /markets/listing/review`.

## 5. Paymaster (CDP app path; OverUnderPaymaster leftover)

[SHIPPED] User-facing gasless ops use Coinbase CDP Paymaster (`useCdpPaymaster: true`) from CDP smart accounts. `matchOrders` stays relayer-only and is 403 on `/aa/cdp-send`. See [ADR-0010](../adr/0010-cdp-embedded-wallets.md).

### App path

- [SHIPPED] Web: `useSendUserOperation` on `base-sepolia` with `useCdpPaymaster: true`. Never a paymaster URL in client code. [web/src/features/trade/AmmSwap.tsx : L244-299]
- [SHIPPED] Flutter: `POST /aa/cdp-send`; backend `POST /v2/embedded-wallet-api/end-users/{userId}/evm/smart-accounts/{address}/send` with `useCdpPaymaster: true`. [backend/app/cdp.py : L169-180]
- [SHIPPED] Allowlist: USDC approve with spender MarketAMM or MarketFactory, CTF setApprovalForAll for MarketAMM, buyWithUSDC, sellToUSDC, factory `createPermissionlessMarket` (must match a prepared listing); `value` 0. Trades on halted markets are 409. [backend/app/aa/router.py : L75-104]
- [SHIPPED] `POST /aa/userop` returns 410. [backend/app/aa/router.py : L163-165]
- [PHASE2] Ops: the CDP Portal paymaster policy must allowlist the v2 AMM and Factory addresses and `createPermissionlessMarket` (about 800k gas per op) after the redeploy.

### Leftover contracts

- [SHIPPED] `OverUnderPaymaster` and `SimpleAccount` stay in-tree. Do not redeploy them for the app path. The app does not call them. [contracts/src/OverUnderPaymaster.vy : L322-355] [contracts/src/SimpleAccount.vy : L40-49]
- [SHIPPED] Leftover CLOB **fills** are relayer-submitted `matchOrders` (OU-T003).

## 6. CDP wallets (OU-T002)

[SHIPPED] Replace Privy. Session JWT stays HS256. `sub` is the smart-account address from `validateAccessToken`. Ignore any client-supplied address. CDP users are not operators.

### Verify

- [SHIPPED] `cdp.end_user.validate_access_token`. Fail closed if `CDP_PROJECT_ID` or `CDP_API_KEY_SECRET` is missing. Tests mock the client. [backend/app/cdp.py : L21-25] [backend/app/cdp.py : L116-134]
- [SHIPPED] `POST /auth/cdp` upserts `User.address` = smart account, `cdp_user_id`, `is_operator=False`. [backend/app/auth/router.py : L144-173]

### SIWE

- [SHIPPED] `POST /auth/siwe` `ecrecover`s, matches nonce, consumes nonce. Operator flag only for `OPERATOR_PRIVATE_KEY`. [backend/app/auth/router.py : L86-141]
- [SHIPPED] Web `ConnectBar` is email OTP, not SIWE. The oracle job bootstraps its operator user through SIWE.

### Email AA

- [SHIPPED] Web `@coinbase/cdp-hooks` with `ethereum.createOnLogin: "smart"`; without a project id the app renders signed out. [web/src/app/providers.tsx : L21-45]
- [SHIPPED] Flutter has no CDP SDK; email/OTP and trades go through OverUnder API only.

### Config

- [SHIPPED] Env: `CDP_PROJECT_ID`, `CDP_API_KEY_ID`, `CDP_API_KEY_SECRET`, `NEXT_PUBLIC_CDP_PROJECT_ID`.
- [SHIPPED] Local file `.secrets/cb_keys.json` keys `PROJECT_ID`, `API_KEY_ID`, `API_SECRET` only if env unset. Never log values.

## 7. MoonPay / KYC

[SHIPPED] Coinbase URL builder remains as a fallback. [backend/app/ramps/router.py : L140-163]

### On-ramp

- [SHIPPED] MoonPay widget signed server-side (`MOONPAY_SECRET`), USDC on Base, destination = session address, `lockAmount=true`; amounts must be finite, positive and ≤ 1,000,000.
- [SHIPPED] Webhook `POST /api/v1/ramps/moonpay/webhook` (raw body + signature) records `RampTx`.
- [SHIPPED] Wallet screen: KYC check, then MoonPay; Coinbase fallback.

### KYC

- [SHIPPED] `POST /kyc/session`, `GET /kyc/status`, `POST /kyc/check` store `KycRecord{address, status, jurisdiction, updated_at}` only.
- [SHIPPED] Gate: KYC above a USDC notional (default $500/day) or in a restricted jurisdiction.
- [PHASE2] Third-party identity vendor (Persona/Sumsub) with webhooks.

### Off-ramp

- [SHIPPED] Coinbase offramp URL. [PHASE2] MoonPay sell URL with the same KYC gate.
- [SHIPPED] Rule: never send users to a dApp that asks for a wallet recovery key.

## 8. OU emissions

FeeVault must stay a **fee sink**, not a minter ([ADR-0003](../adr/0003-ou-nav-token-not-savings-vault.md)).

### Supply rules

- [SHIPPED] Circulating OU starts at 100M in treasury. [contracts/src/RevenueToken.vy : L13-32]
- [SHIPPED] Emissions **transfer** from treasury; they do not `mint`. A future minter would be a new capped contract; FeeVault still only burns on redeem.
- [SHIPPED] NAV formula stays `usdc_balance * 1e18 / totalSupply`.

### Programs (spec)

1. [PHASE2] **LP incentives**: weekly OU to MarketAMM LPs, pro-rata `lpBalance` snapshots.
2. [PHASE2] **Maker rebates**: OU to CLOB makers from treasury, never from FeeVault USDC.
3. [PHASE2] **Agent stipend**: fixed OU/month to the three oracle EOAs, clawback on missed attestations.
4. [PHASE2] **User quests**: capped OU for first verified KYC + first fill.

### Safety

- [SHIPPED] Emission schedule is YAML in `docs/emissions/`; changing it is an ADR.
- [SHIPPED] Show `previewRedeem` on 1e18 as “USDC per OU”; `nav()` can be integer zero. [contracts/src/FeeVault.vy : L44-50]
- [PHASE2] Treasury multisig replaces the Anvil key for production.

### Explicit non-goals

- [SHIPPED] Non-goal: no auto-compounding vault.
- [SHIPPED] Non-goal: no “deposit USDC mint OU”.
- [SHIPPED] Non-goal: no fee-switch that pays OU instead of USDC into FeeVault.

## 9. Lifecycle hardening (shipped in code 2026-09-23)

- [SHIPPED] Trading halt at closeTime: on chain in MarketAMM v2 (`closeGate`), and in the API behind `TRADING_HALT_AT_CLOSE` (default true): quotes, `/aa/cdp-send` trades and CLOB orders get 409; web and mobile disable the ticket. [backend/app/markets/trading.py : L47-67]
- [SHIPPED] ADR-0002 fallback in the oracle job: `OU_FALLBACK_POLICY=attest` (default), `arbitrate` or `manual`. [oracles/resolve/fallback.py : L48-156]
- [SHIPPED] General resolver for wildcards, user markets and non-sports primaries (3/3 plus a confidence floor; confident 2/3 fallback after 24 h). [oracles/resolve/general.py : L219-438]
- [SHIPPED] Job hardening: auth preflight with SIWE bootstrap, exit 1 on any failed stage, redacted output, single-flight lease, tick budget, research cooldown. [oracles/job.py : L136-260]
- [SHIPPED] Redeploy procedure (ops, after merge): run `deploy-contracts.yml` with target `verify`, then `v2` simulate, then `v2` broadcast (pauses the oracle scheduler); set the repo vars `AMM_ADDRESS` and `FACTORY_ADDRESS` (and `INDEXER_START_BLOCK` from `deployBlock`); regenerate the mobile asset; run `deploy-gcp.yml`; resume the scheduler; audit. Addresses are not recorded here until that run. Step by step: [runbooks/operations.md](../runbooks/operations.md).
- [STUB] Imported legacy markets keep their pools on `MarketAMMLegacy`; the API, web, mobile and indexer only know `AMM_ADDRESS`, so still-open legacy markets are not tradable from the app after the swap.

## 10. Open lifecycle gaps

- [STUB] OU-T014: invalid/refund outcome. ConsensusOracle only pays [1,0] or [0,1]; the audit and resolvers detect cancelled or undetermined markets. [contracts/src/ConsensusOracle.vy : L120-127]
- [PHASE2] OU-T015: dynamic L_t for MarketAMM.
- [STUB] OU-T016: EIP-1271 makers and a relayed on-chain cancel for the leftover CLOB. [contracts/src/Exchange.vy : L94-100]
