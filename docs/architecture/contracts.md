---
title: Contracts
status: SHIPPED
area: contracts
summary: Vyper contract graph for CTF, CLOB, AMM, oracle, OU NAV, emissions, and ERC-4337 paymaster.
last_verified: 2026-09-21
pointers:
  - "[contracts/src/ConditionalTokens.vy : L52-60]"
  - "[contracts/src/ConditionalTokens.vy : L63-72]"
  - "[contracts/src/ConditionalTokens.vy : L86-95]"
  - "[contracts/src/ConditionalTokens.vy : L97-105]"
  - "[contracts/src/MarketFactory.vy : L61-79]"
  - "[contracts/src/MarketFactory.vy : L82-89]"
  - "[contracts/src/MarketFactory.vy : L92-99]"
  - "[contracts/src/Exchange.vy : L22-35]"
  - "[contracts/src/Exchange.vy : L128-159]"
  - "[contracts/src/MarketAMM.vy : L40-48]"
  - "[contracts/src/MarketAMM.vy : L73-82]"
  - "[contracts/src/MarketAMM.vy : L104-119]"
  - "[contracts/src/MarketAMM.vy : L122-159]"
  - "[contracts/src/MarketAMM.vy : L162-201]"
  - "[contracts/src/ConsensusOracle.vy : L37-38]"
  - "[contracts/src/ConsensusOracle.vy : L144-161]"
  - "[contracts/src/ConsensusOracle.vy : L197-217]"
  - "[contracts/src/FeeVault.vy : L44-83]"
  - "[contracts/src/RevenueToken.vy : L13-32]"
  - "[contracts/src/EmissionsDistributor.vy : L22-50]"
  - "[contracts/src/OverUnderPaymaster.vy : L322-355]"
  - "[contracts/src/OverUnderPaymaster.vy : L358-368]"
  - "[contracts/src/SimpleAccount.vy : L40-49]"
  - "[contracts/src/SimpleAccountFactory.vy : L46-53]"
  - "[contracts/src/MockEntryPoint.vy : L94-120]"
---

# Contracts

All production logic is Vyper 0.4.3 under `contracts/src/`. Tests live in `contracts/tests/`. Deploy script: [contracts/script/deploy.py : L31-74].

## ConditionalTokens

Binary Gnosis-style CTF. `prepareCondition` binds an oracle and questionId to a `conditionId`. Positions are ERC-1155 ids from `keccak256(conditionId, outcome)`.

- Split: lock USDC, mint equal YES and NO. [contracts/src/ConditionalTokens.vy : L63-72]
- Merge: burn equal YES and NO, return USDC. Inverse of split.
- Redeem: after `reportPayouts`, winning outcome pays `amount * numerator / denom`. [contracts/src/ConditionalTokens.vy : L86-95]
- Only the registered oracle may `reportPayouts`. [contracts/src/ConditionalTokens.vy : L97-105]

## MarketFactory

Permissioned factory. Operator creates primaries (`marketType=0`). Wildcard generator or operator creates children (`marketType=1`) that must close no later than the parent.

- `_create` prepares the CTF condition and registers closeTime on the oracle. [contracts/src/MarketFactory.vy : L61-79]
- [SHIPPED] Primary required `seedUsdc` pulls USDC then `seedPool`. [contracts/src/MarketFactory.vy : L82-89]
- [SHIPPED] Wildcard optional `seedUsdc` pulls USDC then `seedPool`. [contracts/src/MarketFactory.vy : L92-99]
- Operator pause is a factory flag; Exchange/AMM still check CTF resolution, not this flag, on every trade.

## Exchange (leftover CLOB overlay)

EIP-712 `Order` struct. Price is USDC per token with `PRICE_SCALE = 1e6` (1.00 USD = 1_000_000). Deployed leftover; not the product path. See [ADR-0007](../adr/0007-amm-first-uniform-lvr.md).

- `TAKER_FEE_BPS = 75`. [contracts/src/Exchange.vy : L33]
- `matchOrders` requires opposite sides, same condition/outcome, price cross, valid maker signatures, unused fill. [contracts/src/Exchange.vy : L128-159]
- Buy taker pays `volume` USDC to maker plus `fee` to FeeVault, receives outcome tokens.
- Sell taker sends tokens; maker pays `volume - fee` USDC to taker and `fee` to vault.
- Operator or maker may `cancelOrder`. Makers can `incrementNonce` to invalidate the nonce class.

## MarketAMM

Constant-product pool of YES and NO reserves. Seed splits USDC 50/50 into both tokens. Remains CPMM until OU-T008/OU-T009; do not invent uniform-LVR as shipped.

- `AMM_FEE_BPS = 100`, applied as 50 vault + 50 LP on buy and sell. [contracts/src/MarketAMM.vy : L40]
- `quoteSell` shares sell math with `sellToUSDC`. [contracts/src/MarketAMM.vy : L104-119]
- `buyWithUSDC` splits trade+LP fee into tokens, swaps against k, sends the bought outcome to trader. [contracts/src/MarketAMM.vy : L122-159]
- `sellToUSDC` solves for merge amount `x`, takes fees from `x`, returns net USDC. [contracts/src/MarketAMM.vy : L162-201]
- `addLiquidity` mints LP shares proportional to the thin reserve.

## ConsensusOracle

Three agent addresses. `WINDOW = 86400`, `ARBITRATION_GRACE = 172800` (grace stored; arbitration is operator-gated after WINDOW, not a separate timed role).

- `submitAttestation` recovers one agent EIP-712 signature after closeTime.
- `submitConsensus` requires three distinct agent signatures on the same outcome, then `reportPayouts`. [contracts/src/ConsensusOracle.vy : L144-161]
- `castVote` weights by YES+NO token balance.
- `resolveFallback` after WINDOW needs 2/3 agent majority; if ≥2/3 of voted weight opposes agents, revert `"arbitration required"`. [contracts/src/ConsensusOracle.vy : L197-217]
- `resolveArbitrated` is operator-only after WINDOW.

## FeeVault + RevenueToken

- OU: name OverUnder, 18 decimals, `INITIAL_SUPPLY = 100_000_000e18` minted to treasury. [contracts/src/RevenueToken.vy : L13-32]
- `burn` is public on the token; FeeVault is the intended burner after a redeem claim. [contracts/src/RevenueToken.vy : L57-61]
- NAV view: `usdc_balance * 1e18 / totalSupply`. Integer division can be 0 when fees are tiny versus 100M supply. [contracts/src/FeeVault.vy : L44-50]
- Redeem: `requestRedeem` locks OU for `cooldown` (deploy uses 24h), then `claim` burns OU and pays pro-rata USDC. [contracts/src/FeeVault.vy : L61-83]

## EmissionsDistributor

Treasury-gated OU distribution. Never mints; only `transferFrom` treasury to recipients. Operator-only.

- `distribute(program, recipients[], amounts[])`: pulls OU from treasury (requires treasury approval), transfers to recipients. [contracts/src/EmissionsDistributor.vy : L22-50]
- Asserts `totalSupply` unchanged before/after batch. [contracts/src/EmissionsDistributor.vy : L47-49]
- Program IDs: 0=LP, 1=maker, 2=agent, 3=quest. See [docs/emissions/schedule.yaml] for allocation schedules.
- NAV stays accurate: transfers don't affect USDC backing or totalSupply, so `nav = usdc * 1e18 / supply` unchanged.
- FeeVault never receives minted OU; only fees from Exchange/AMM.

## OverUnderPaymaster

[SHIPPED] ERC-4337 v0.7 paymaster. Deny-by-default allowlist. `matchOrders` and `executeBatch` stay denied. Canonical EntryPoint `0x0000000071727De22E5E9d8BAf0edAc6f37da032` on 84532; Anvil uses MockEntryPoint.

- Operator-gated deposit, factories, `weiPerUsdc`, `feeRecipient`, daily cap.
- Sponsor iff `allowedSenders[sender]` or `initCode` from `allowedFactories`. Non-empty `initCode` must use an allowed factory even if the sender is already listed.
- USDC `approve` spenders ∈ {ctf, exchange, amm, feeVault, paymaster}. `execute` with `value != 0` denied.
- Fee: `usdcFee = ceil(maxCost * 1e6 / weiPerUsdc)` at validation; `postOp` charges actual gas capped at that fee. Fail closed on balance/allowance/cap before sponsorship. [contracts/src/OverUnderPaymaster.vy : L322-355] [contracts/src/OverUnderPaymaster.vy : L358-368]
- Operator ECDSA over the UserOp fields (not the account sig) so only this bundler can drain the tank.

## SimpleAccount

[SHIPPED] EIP-1167 proxy. `initialize(entryPoint, owner)` once. `execute(address,uint256,bytes)` selector `0xb61d27f6` (EntryPoint or owner). `validateUserOp` recovers `toEthSignedMessageHash(userOpHash)`. Factory `createAccount` / `getAddress` with canonical salt 0. [contracts/src/SimpleAccount.vy : L40-49] [contracts/src/SimpleAccountFactory.vy : L46-53]

## MockEntryPoint

Anvil EntryPoint v0.7 stand-in: `depositTo` / `getDepositInfo`, `getUserOpHash`, `handleOps` (initCode deploy, validate, call, `postOp`, subtract deposit). [contracts/src/MockEntryPoint.vy : L94-120]

## MockUSDC

Local 6-decimal ERC-20 with operator mint. Not for Base mainnet; production uses native USDC.
