---
title: Phase 2 remaining vision
status: MIXED
area: roadmap
summary: MIXED spec — Flutter/emissions/KYC/Cursor-runtime agents/dual-gate resolve/CDP wallets shipped; remaining leftover CLOB relayer, uniform-LVR, permissionless listing.
last_verified: 2026-09-21
pointers:
  - "[mobile/README.md : L1-25]"
  - "[web/src/app/providers.tsx : L21-36]"
  - "[backend/app/auth/router.py : L144-173]"
  - "[backend/app/cdp.py : L116-134]"
  - "[backend/app/ramps/router.py : L9-32]"
  - "[contracts/src/RevenueToken.vy : L13-32]"
  - "[contracts/src/FeeVault.vy : L44-50]"
  - "[contracts/src/OverUnderPaymaster.vy : L322-355]"
  - "[docs/adr/0010-cdp-embedded-wallets.md : L28-39]"
  - "[docs/adr/0007-amm-first-uniform-lvr.md : L29-36]"
  - "[contracts/src/MarketAMM.vy : L40]"
---

# Phase 2

This file is MIXED. [SHIPPED] Flutter (OU-T006), emissions (OU-T004), Cursor-runtime agents (OU-T005), score scout (OU-T011), dual-gate resolve (OU-T012), week-roll listing (OU-T013), MoonPay/KYC (OU-T007), leftover OverUnderPaymaster (OU-T001), CDP wallets (OU-T002). Remaining open: leftover CLOB production relayer (OU-T003), uniform-LVR (OU-T008/T009), permissionless listing (OU-T010). CLOB UX is an optional overlay, not a required depth-chart milestone. Do not invent uniform-LVR as shipped. The app path is CDP Paymaster, not OverUnderPaymaster.

## 1. Flutter

Do not start from a blank app. Mirror web feature modules 1:1 as documented in [mobile/README.md](../../mobile/README.md).

### Module map

| Web today | Flutter target |
| --- | --- |
| `web/src/features/markets` | `lib/features/markets` |
| `web/src/features/trade` | `lib/features/trade` |
| `web/src/features/wallet` | `lib/features/wallet` |
| `web/src/features/oracle` | `lib/features/oracle` |

Shared inputs (already in repo, do not fork):

- Theme: `shared/design-tokens/tokens.json` (color, spacing, type). Generate a Dart `AppTheme` at build time; no hardcoded hex in widgets.
- API: regenerate a Dart client from `shared/openapi.json` whenever FastAPI routes change. Do not hand-write DTO field names.
- Chain: read `contracts/deployments/<chainId>.json` the same way web/backend do.

### Screens (required v1 mobile)

1. **Market list** — primaries as full-width cards; wildcard children as chips under the parent. Pull `GET /api/v1/markets` (EventCard[] of primaries with nested children; orphan wildcards listed alone) and `?parentId=` for a flat child filter.
2. **Market detail** — type 0 and type 1 render AMM buy/sell with `quoteBuy`/`quoteSell` then a wallet `buyWithUSDC` / `sellToUSDC`. CLOB ticket is leftover overlay, not required.
3. **Wallet** — CDP email OTP. Show USDC, YES/NO balances for open markets, OU + NAV. Onramp destination is the smart account (`GET /api/v1/ramps/onramp-url`).
4. **Oracle** — attestations, unanimity flag, countdown to `closeTime + 86400`, vote CTA calling `castVote` through the AA wallet.

### Mobile-specific rules

- Same Base USDC and contract addresses as web. No parallel “mobile chain.”
- Deep links: `overunder://markets/<conditionId>`.
- Offline: cache last market list; never cache signed orders as submitted until the API 200.
- QA: golden screenshots against tokens.json; run the same e2e economic scenario as `scripts/e2e_local.py` against a shared Anvil.

## 2. Optional CLOB overlay (not required)

The MVP proves AMM settlement. A CLOB overlay may be scheduled later; it is **not** a required Phase 2 destination and there is **no** required depth-chart milestone.

### Discovery

- Category tabs (sports, politics, crypto) stored on Market as metadata (new SQLite column + factory event field or off-chain registry).
- Search by question substring.
- “Related wildcards” rail on primary detail (already filterable via `parentId`).

### CLOB UX (optional leftover overlay)

- Depth chart and last-trade tape from `GET /orderbook/{id}` + `Trade` rows — only if an overlay is scheduled.
- Price displayed as cents (divide 1e6, show ¢) with a toggle to implied %.
- Size in shares (CTF units, 6 decimals matching USDC).
- Click-to-join best bid/ask into the ticket.
- Cancel open orders (`DELETE /orders/{hash}` plus `Exchange.cancelOrder`).
- **EIP-712** `signTypedData` for `Order` using the Exchange domain (`OverUnder` name/version must match `Exchange.vy` typehashes). Reject unsigned `0x` at the API.

### AMM UX

- Live quote on every keystroke; show vault fee vs LP fee (50/50 of 100 bps).
- Slippage field bound to `minOut` / `minUsdc`.
- Add-liquidity panel for `addLiquidity`.
- After swap, show new implied YES price `noReserve / (yesReserve+noReserve)`.

### Resolution UX

- Criteria text from `resolution_criteria`.
- Agent evidence URLs (phase 2 agents must populate real links).
- Vote with on-chain `castVote` when the user holds YES or NO.

### Operations parity

- Operator console: create primary (factory tx), pause, seed wildcard inventory.
- Status page: matcher lag, relayer nonce, last indexed block.

## 3. Uniform-LVR AMM (OU-T008 / OU-T009)

[PHASE2] Book of record for every market type becomes a uniform-LVR AMM (pm-AMM / Moallemi–Robinson–Zhu 2026). Default pool: static uniform invariant. Time-based liquidity and dynamic spreads are LP/fee policy, not a second venue. [docs/adr/0007-amm-first-uniform-lvr.md : L29-36]

- [SHIPPED] `MarketAMM` stays CPMM (`AMM_FEE_BPS = 100`) until these TODOs land. [contracts/src/MarketAMM.vy : L40]
- OU-T008: Vyper formula spike, gas, Gaussian vs jump-event fit.
- OU-T009: migrate `buyWithUSDC` / `sellToUSDC` to the uniform-LVR default pool.
- Do not mark uniform-LVR as shipped in this cycle.

## 4. Permissionless listing (OU-T010)

[PHASE2] Listing becomes permissionless or loosely gated so AMM seed—not operator-recruited makers—bootstraps a market. Factory stays operator/generator gated until this lands. [contracts/src/MarketFactory.vy : L82-98]

## 5. Paymaster (CDP app path; OverUnderPaymaster leftover)

[SHIPPED] User-facing gasless ops use Coinbase CDP Paymaster (`useCdpPaymaster: true`) from CDP smart accounts. `matchOrders` stays relayer-only and is 403 on `/aa/cdp-send`. See [ADR-0010](../adr/0010-cdp-embedded-wallets.md).

### App path

- [SHIPPED] Web: `useSendUserOperation` on `base-sepolia` with `useCdpPaymaster: true`. Never a paymaster URL in client code. [web/src/features/trade/AmmSwap.tsx : L187-211]
- [SHIPPED] Flutter: `POST /aa/cdp-send`; backend `POST /v2/embedded-wallet-api/end-users/{userId}/evm/smart-accounts/{address}/send` with `useCdpPaymaster: true`. [backend/app/cdp.py : L168-179]
- [SHIPPED] Allowlist: USDC approve spender=MarketAMM, CTF setApprovalForAll operator=MarketAMM, buyWithUSDC, sellToUSDC; `value` 0. [backend/app/aa/router.py : L70-94]
- [SHIPPED] `POST /aa/userop` returns 410. [backend/app/aa/router.py : L97-99]

### Leftover contracts

- [SHIPPED] `OverUnderPaymaster` and `SimpleAccount` stay in-tree. Do not redeploy them for the app path. The app does not call them. [contracts/src/OverUnderPaymaster.vy : L322-355] [contracts/src/SimpleAccount.vy : L40-49]
- Leftover CLOB **fills** stay relayer-submitted `matchOrders`.

## 6. CDP wallets (OU-T002)

[SHIPPED] Replace Privy. Session JWT stays HS256. `sub` is the smart-account address from `validateAccessToken`. Ignore any client-supplied address. CDP users are not operators.

### Verify

- [SHIPPED] `cdp.end_user.validate_access_token`. Fail closed if `CDP_PROJECT_ID` or `CDP_API_KEY_SECRET` is missing. Tests mock the client. [backend/app/cdp.py : L21-25] [backend/app/cdp.py : L116-134]
- [SHIPPED] `POST /auth/cdp` upserts `User.address` = smart account, `cdp_user_id`, `is_operator=False`. [backend/app/auth/router.py : L144-173]

### SIWE

- [SHIPPED] `POST /auth/siwe` `ecrecover`s, matches nonce, consumes nonce. Operator flag only for `OPERATOR_PRIVATE_KEY`. [backend/app/auth/router.py : L86-141]
- Web `ConnectBar` is email OTP, not SIWE.

### Email AA

- [SHIPPED] Web `@coinbase/cdp-hooks` with `ethereum.createOnLogin: "smart"`. [web/src/app/providers.tsx : L21-36]
- [SHIPPED] Flutter has no CDP SDK; email/OTP and trades go through OverUnder API only.

### Config

- Env: `CDP_PROJECT_ID`, `CDP_API_KEY_ID`, `CDP_API_KEY_SECRET`, `NEXT_PUBLIC_CDP_PROJECT_ID`.
- Local file `.secrets/cb_keys.json` keys `PROJECT_ID`, `API_KEY_ID`, `API_SECRET` only if env unset. Never log values. No wallet secret unless an SDK call fails without it.

## 7. MoonPay / KYC

Coinbase URL builder remains as a fallback ([backend/app/ramps/router.py : L9-32]). Phase 2 adds a second provider and identity.

### On-ramp

- MoonPay widget signed server-side (`MOONPAY_SECRET`). Currency USDC, network Base, destination = session address.
- Webhook `POST /api/v1/ramps/moonpay/webhook` (raw body + signature header) records `RampTx{address, amount, provider_id, kyc_status}`.
- UI: Wallet screen shows “Buy USDC” → MoonPay if `kyc_status in {pass, not_required}`, else KYC CTA.

### KYC

- Provider: MoonPay identity or a dedicated vendor (Persona/Sumsub) behind `POST /kyc/session`.
- Store `KycRecord{address, status, jurisdiction, updated_at}`. Do not store government IDs in SQLite.
- Geo policy table: deny OFAC regions; require KYC above a USDC notional (default $500/day).
- Operator cannot bypass KYC via JWT `op` flag for fiat; they still can mint MockUSDC on Anvil only.

### Off-ramp

- Keep Coinbase offramp URL; add MoonPay sell URL with the same KYC gate.
- Never send users to a dApp that asks for a wallet recovery key.

## 8. OU emissions

FeeVault must stay a **fee sink**, not a minter ([ADR-0003](../adr/0003-ou-nav-token-not-savings-vault.md)).

### Supply rules

- Circulating OU starts at 100M in treasury. [contracts/src/RevenueToken.vy : L13-32]
- Emissions **transfer** from treasury (or a Timelock) — they do not `mint`. If a future minter is required, it is a new contract with a hard cap; FeeVault still only burns on redeem.
- NAV formula stays `usdc_balance * 1e18 / totalSupply`. Transfers do not change supply; they change who can redeem. New mint (if ever) **dilutes** NAV and must be called out in the UI.

### Programs (spec)

1. **LP incentives** — weekly OU from treasury to addresses that minted MarketAMM LP shares, pro-rata `lpBalance` snapshots (indexer).
2. **Maker rebates** — OU to CLOB makers based on `Trade` volume where they were maker, funded from treasury not from FeeVault USDC (do not drain trader redeem backing).
3. **Agent stipend** — fixed OU/month to the three oracle EOAs, clawback if an agent misses attestations.
4. **User quests** — capped OU for first verified KYC + first fill; sybil-checked via KYC id, not demo hex addresses.

### Safety

- Emission schedule published as YAML in `docs/` (amounts, start, cliff). Changing it is an ADR.
- `previewRedeem` on a 1e18 unit should be the displayed “USDC per OU”; do not rely on `nav()` while supply is 100M and fees are small (integer zero). [contracts/src/FeeVault.vy : L44-50]
- Treasury multisig (phase 2 ops) replaces Anvil key 0x8b3a… for production.

### Explicit non-goals

- No auto-compounding vault.
- No “deposit USDC mint OU.”
- No fee-switch that pays OU instead of USDC into FeeVault (that would break NAV backing).
