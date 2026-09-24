---
title: Contracts
status: MIXED
area: contracts
summary: Vyper contract graph for CTF, leftover CLOB, MarketAMM v2 (static pm-AMM with close gate), MarketFactory v2 (user listing), oracle, OU NAV, emissions, and the leftover ERC-4337 paymaster.
last_verified: 2026-09-24
pointers:
  - "[contracts/src/ConditionalTokens.vy : L52-60]"
  - "[contracts/src/ConditionalTokens.vy : L62-72]"
  - "[contracts/src/ConditionalTokens.vy : L85-95]"
  - "[contracts/src/ConditionalTokens.vy : L97-105]"
  - "[contracts/src/MarketFactory.vy : L96-108]"
  - "[contracts/src/MarketFactory.vy : L115-141]"
  - "[contracts/src/MarketFactory.vy : L143-159]"
  - "[contracts/src/MarketFactory.vy : L161-199]"
  - "[contracts/src/MarketFactory.vy : L201-248]"
  - "[contracts/src/Exchange.vy : L22-35]"
  - "[contracts/src/Exchange.vy : L94-121]"
  - "[contracts/src/Exchange.vy : L128-159]"
  - "[contracts/src/lib/NormalMath.vy : L1-8]"
  - "[contracts/src/lib/NormalMath.vy : L140-229]"
  - "[contracts/src/MarketAMM.vy : L1-12]"
  - "[contracts/src/MarketAMM.vy : L60-88]"
  - "[contracts/src/MarketAMM.vy : L105-157]"
  - "[contracts/src/MarketAMM.vy : L159-269]"
  - "[contracts/src/MarketAMM.vy : L271-317]"
  - "[contracts/src/MarketAMM.vy : L319-414]"
  - "[contracts/src/ConsensusOracle.vy : L37-38]"
  - "[contracts/src/ConsensusOracle.vy : L120-133]"
  - "[contracts/src/ConsensusOracle.vy : L144-161]"
  - "[contracts/src/ConsensusOracle.vy : L197-226]"
  - "[contracts/src/FeeVault.vy : L44-83]"
  - "[contracts/src/RevenueToken.vy : L13-32]"
  - "[contracts/src/EmissionsDistributor.vy : L23-49]"
  - "[contracts/src/OverUnderPaymaster.vy : L322-355]"
  - "[contracts/src/OverUnderPaymaster.vy : L358-368]"
  - "[contracts/src/SimpleAccount.vy : L40-49]"
  - "[contracts/src/SimpleAccountFactory.vy : L46-53]"
  - "[contracts/src/MockEntryPoint.vy : L94-120]"
  - "[contracts/script/deploy.py : L160-242]"
  - "[contracts/script/deploy_v2.py : L178-268]"
---

# Contracts

All production logic is Vyper 0.4.3 under `contracts/src/`. Tests live in `contracts/tests/`. Full-graph deploy: [contracts/script/deploy.py : L160-242]. AMM + Factory swap on an existing stack: [contracts/script/deploy_v2.py : L178-268].

## ConditionalTokens

Binary Gnosis-style CTF. `prepareCondition` binds an oracle and questionId to a `conditionId`. Positions are ERC-1155 ids from `keccak256(conditionId, outcome)`.

- [SHIPPED] Split: lock USDC, mint equal YES and NO. [contracts/src/ConditionalTokens.vy : L62-72]
- [SHIPPED] Merge: burn equal YES and NO, return USDC. Inverse of split.
- [SHIPPED] Redeem: after `reportPayouts`, winning outcome pays `amount * numerator / denom`. [contracts/src/ConditionalTokens.vy : L85-95]
- [SHIPPED] Only the registered oracle may `reportPayouts`; any non-zero denominator is accepted, so a [1,1] refund would settle here (OU-T014). [contracts/src/ConditionalTokens.vy : L97-105]

## MarketFactory (v2)

Operator primaries (`marketType=0`), generator or operator wildcards (`marketType=1`, close ≤ parent) and user listings (`marketType=2`). The v1 constructor, create/pause ABI, `Market` struct and events are unchanged; everything else is additive. Listing decision: [ADR-0012](../adr/0012-loosely-gated-user-listing.md).

- [SHIPPED] `_create` prepares the CTF condition and registers closeTime on the oracle; `_seed` approves the AMM and calls `seedPoolFor(cid, seed, msg.sender)`, so the caller owns the seed LP. [contracts/src/MarketFactory.vy : L115-141]
- [SHIPPED] Primary requires a seed; wildcard seed is optional. [contracts/src/MarketFactory.vy : L143-159]
- [SHIPPED] `createPermissionlessMarket(salt, closeTime, question, criteriaHash, seedUsdc)` checks, in order: allowlist or `permissionless`, `seed ≥ minSeedUsdc`, lead time, horizon, question ≥ 10 bytes, non-zero criteria hash, per-creator cooldown. The questionId is `keccak(abi.encode(sender, salt))`, so listers cannot squat operator ids. It pulls seed + fee and forwards the fee to `feeRecipient`. [contracts/src/MarketFactory.vy : L161-199]
- [SHIPPED] Constructor defaults: listing closed (allowlist only), min seed 10 USDC, lead 3600 s, horizon 90 days, cooldown 3600 s. [contracts/src/MarketFactory.vy : L96-108]
- [SHIPPED] Operator admin: `setListingConfig` (bounded), `setPermissionless`, `setLister`, `importLegacyMarkets` (copies v1 rows and the paused flag, 50 per call), `setPaused`. [contracts/src/MarketFactory.vy : L201-248]
- [SHIPPED] Operator pause is a factory flag; the AMM and Exchange check CTF resolution and the close gate, not this flag.

## Exchange (leftover CLOB overlay)

EIP-712 `Order` struct. Price is USDC per token with `PRICE_SCALE = 1e6` (1.00 USD = 1_000_000). Deployed leftover; not the product path. See [ADR-0007](../adr/0007-amm-first-uniform-lvr.md).

- [SHIPPED] `TAKER_FEE_BPS = 75`. [contracts/src/Exchange.vy : L33]
- [SHIPPED] `matchOrders` requires opposite sides, same condition/outcome, price cross, valid maker signatures, unused fill. [contracts/src/Exchange.vy : L128-159]
- [SHIPPED] Buy taker pays `volume` USDC to maker plus `fee` to FeeVault, receives outcome tokens. Sell taker sends tokens; maker pays `volume - fee` USDC to taker and `fee` to vault.
- [SHIPPED] Signatures recover with `ecrecover` only (no EIP-1271), so smart accounts cannot be makers. Operator or maker may `cancelOrder`; makers can `incrementNonce`. [contracts/src/Exchange.vy : L94-121]
- [STUB] EIP-1271 makers and a relayed cancel path (OU-T016).

## MarketAMM (v2, static pm-AMM)

Static uniform-LVR pool (Moallemi & Robinson): x = YES reserve, y = NO reserve, liquidity L, u = (y − x)/L, with y = L·g(u), x = L·g(−u) and YES price Φ(u). Every rounding keeps the pool on or above the curve. Decision: [ADR-0011](../adr/0011-pm-amm-v2-close-gate.md). Spike and measurements: [explore/uniform-lvr-spike.md](../explore/uniform-lvr-spike.md). [contracts/src/MarketAMM.vy : L1-12]

- [SHIPPED] Every v1 selector and event is kept. `pools(cid)` appends `liquidity`, `lpFees`, `closeTime` after `(yesReserve, noReserve, lpSupply, exists)`; new storage `closeGate` and `seedLocked`. [contracts/src/MarketAMM.vy : L60-88]
- [SHIPPED] `seedPoolFor` (factory or operator) credits LP to the provider; seed must be ≥ `MIN_LP`, sets L = S/φ(0) and caches closeTime from `factory.oracle()`, reverting `"not registered"` when it is 0. `seedPool` is `seedPoolFor` with the caller as provider. [contracts/src/MarketAMM.vy : L126-157]
- [SHIPPED] `_buy` / `_sell` price trades off the curve point, not raw reserves, and are shared by `quoteBuy`/`quoteSell`, so quote equals execution. Trades keep the price in [1e-4, 1 − 1e-4] (`"price bound"`); amounts at or below the two fees revert `"dust"`. `priceYes` returns Φ(u) in WAD. [contracts/src/MarketAMM.vy : L159-269]
- [SHIPPED] Fees: ceil 50 bps to FeeVault plus ceil 50 bps into the pool's `lpFees` accumulator (`AMM_FEE_BPS` stays 100). [contracts/src/MarketAMM.vy : L271-317]
- [SHIPPED] Close gate: with `closeGate` on (true at deploy; operator `setCloseGate`), `buyWithUSDC`, `sellToUSDC` and `addLiquidity` revert `"market closed"` at or after closeTime. Quotes, `priceYes` and `removeLiquidity` stay open. Resolved markets revert trades with a bare assert, as in v1. [contracts/src/MarketAMM.vy : L105-119]
- [SHIPPED] `addLiquidity` is proportional at constant price with a short-side token refund. `removeLiquidity` pays pro rata (merging complete sets before resolution, tokens after). Seed shares are locked until closeTime or resolution and `MIN_LP` of them forever (`"seed locked"`); `lpLocked(cid, account)` shows the current lock. [contracts/src/MarketAMM.vy : L319-414]
- [SHIPPED] Fixed-point math lives in `lib/NormalMath.vy`: Solady expWad/lnWad ports, Hart 5666 Φ tail, `g_cdf` with one exp, and `solve_g` (Newton, result verified at or below the root). [contracts/src/lib/NormalMath.vy : L1-8] [contracts/src/lib/NormalMath.vy : L140-229]
- [PHASE2] Dynamic liquidity L_t shrinking toward closeTime (OU-T015).
- [SHIPPED] Migration (`deploy_v2.migrate_v2`): deploy AMM v2 and Factory v2, wire them, set listing config, then `oracle.setFactory(new)` and import legacy rows. Legacy pools stay on the old AMM (`MarketAMMLegacy`) and still trade and resolve there. [contracts/script/deploy_v2.py : L178-268]

## ConsensusOracle

Three agent addresses. `WINDOW = 86400`, `ARBITRATION_GRACE = 172800` (grace stored; arbitration is operator-gated after WINDOW, not a separate timed role). [contracts/src/ConsensusOracle.vy : L37-38]

- [SHIPPED] `submitAttestation` recovers one agent EIP-712 signature after closeTime; the job relays these under the fallback policy.
- [SHIPPED] `submitConsensus` requires three distinct agent signatures on the same outcome, then `reportPayouts`. [contracts/src/ConsensusOracle.vy : L144-161]
- [SHIPPED] `castVote` weights by YES+NO token balance.
- [SHIPPED] `resolveFallback` after WINDOW needs 2/3 agent majority; if ≥2/3 of voted weight opposes agents, revert `"arbitration required"`. `resolveArbitrated` is operator-only after WINDOW. [contracts/src/ConsensusOracle.vy : L197-226]
- [SHIPPED] `_payouts` only produces [1,0] or [0,1]: there is no invalid or refund outcome. [contracts/src/ConsensusOracle.vy : L120-133]
- [STUB] Invalid/refund outcome for cancelled or unanswerable markets (OU-T014).

## FeeVault + RevenueToken

- [SHIPPED] OU: name OverUnder, 18 decimals, `INITIAL_SUPPLY = 100_000_000e18` minted to treasury. [contracts/src/RevenueToken.vy : L13-32]
- [SHIPPED] `burn` is public on the token; FeeVault is the intended burner after a redeem claim. [contracts/src/RevenueToken.vy : L57-61]
- [SHIPPED] NAV view: `usdc_balance * 1e18 / totalSupply`. Integer division can be 0 when fees are tiny versus 100M supply. [contracts/src/FeeVault.vy : L44-50]
- [SHIPPED] Redeem: `requestRedeem` locks OU for `cooldown` (deploy uses 24h), then `claim` burns OU and pays pro-rata USDC. [contracts/src/FeeVault.vy : L61-83]

## EmissionsDistributor

Treasury-gated OU distribution. Never mints; only `transferFrom` treasury to recipients. Operator-only.

- [SHIPPED] `distribute(program, recipients[], amounts[])`: pulls OU from treasury (requires treasury approval), transfers to recipients, and asserts `totalSupply` is unchanged. [contracts/src/EmissionsDistributor.vy : L23-49]
- [SHIPPED] Program IDs: 0=LP, 1=maker, 2=agent, 3=quest. See [docs/emissions/schedule.yaml](../emissions/schedule.yaml).
- [SHIPPED] FeeVault never receives minted OU; only fees from Exchange, AMM and optional listing fees.

## OverUnderPaymaster

[SHIPPED] Leftover ERC-4337 v0.7 paymaster. The app path is Coinbase CDP Paymaster (ADR-0010); `POST /aa/userop` returns 410. Contracts stay in-tree and are not redeployed for that path.

- [SHIPPED] Operator-gated deposit, factories, `weiPerUsdc`, `feeRecipient`, daily cap.
- [SHIPPED] Sponsor iff `allowedSenders[sender]` or `initCode` from `allowedFactories`.
- [SHIPPED] Fee: `usdcFee = ceil(maxCost * 1e6 / weiPerUsdc)` at validation; `postOp` charges actual gas capped at that fee. [contracts/src/OverUnderPaymaster.vy : L322-355] [contracts/src/OverUnderPaymaster.vy : L358-368]

## SimpleAccount

[SHIPPED] EIP-1167 proxy. `initialize(entryPoint, owner)` once. `execute(address,uint256,bytes)` selector `0xb61d27f6` (EntryPoint or owner). `validateUserOp` recovers `toEthSignedMessageHash(userOpHash)`. Factory `createAccount` / `getAddress` with canonical salt 0. [contracts/src/SimpleAccount.vy : L40-49] [contracts/src/SimpleAccountFactory.vy : L46-53]

## MockEntryPoint

[SHIPPED] Anvil EntryPoint v0.7 stand-in: `depositTo` / `getDepositInfo`, `getUserOpHash`, `handleOps`. [contracts/src/MockEntryPoint.vy : L94-120]

## MockUSDC

[SHIPPED] Local 6-decimal ERC-20 with operator mint and a faucet. Not for Base mainnet; on Base Sepolia the faucet makes `minSeedUsdc` free, so cooldown, fee, allowlist and pause are the real spam controls.
