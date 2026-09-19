---
title: Contracts
status: SHIPPED
area: contracts
summary: Vyper contract graph for CTF, CLOB, AMM, oracle, OU NAV, and ERC-4337 paymaster.
last_verified: 2026-09-19
pointers:
  - "[contracts/src/ConditionalTokens.vy : L52-60]"
  - "[contracts/src/ConditionalTokens.vy : L63-72]"
  - "[contracts/src/ConditionalTokens.vy : L86-95]"
  - "[contracts/src/ConditionalTokens.vy : L97-105]"
  - "[contracts/src/MarketFactory.vy : L61-79]"
  - "[contracts/src/MarketFactory.vy : L81-94]"
  - "[contracts/src/Exchange.vy : L22-35]"
  - "[contracts/src/Exchange.vy : L128-159]"
  - "[contracts/src/MarketAMM.vy : L40-48]"
  - "[contracts/src/MarketAMM.vy : L74-82]"
  - "[contracts/src/MarketAMM.vy : L103-140]"
  - "[contracts/src/ConsensusOracle.vy : L37-38]"
  - "[contracts/src/ConsensusOracle.vy : L144-161]"
  - "[contracts/src/ConsensusOracle.vy : L197-217]"
  - "[contracts/src/FeeVault.vy : L44-83]"
  - "[contracts/src/RevenueToken.vy : L13-32]"
  - "[contracts/src/OverUnderPaymaster.vy : L235-268]"
  - "[contracts/src/OverUnderPaymaster.vy : L206-229]"
---

# Contracts

All production logic is Vyper 0.4.3 under `contracts/src/`. Tests live in `contracts/tests/`. Deploy script: [contracts/script/deploy.py : L29-40].

## ConditionalTokens

Binary Gnosis-style CTF. `prepareCondition` binds an oracle and questionId to a `conditionId`. Positions are ERC-1155 ids from `keccak256(conditionId, outcome)`.

- Split: lock USDC, mint equal YES and NO. [contracts/src/ConditionalTokens.vy : L63-72]
- Merge: burn equal YES and NO, return USDC. Inverse of split.
- Redeem: after `reportPayouts`, winning outcome pays `amount * numerator / denom`. [contracts/src/ConditionalTokens.vy : L86-95]
- Only the registered oracle may `reportPayouts`. [contracts/src/ConditionalTokens.vy : L97-105]

## MarketFactory

Permissioned factory. Operator creates primaries (`marketType=0`). Wildcard generator or operator creates children (`marketType=1`) that must close no later than the parent.

- `_create` prepares the CTF condition and registers closeTime on the oracle. [contracts/src/MarketFactory.vy : L61-79]
- Wildcard optional `seedUsdc` pulls USDC then `seedPool`. [contracts/src/MarketFactory.vy : L86-94]
- Operator pause is a factory flag; Exchange/AMM still check CTF resolution, not this flag, on every trade.

## Exchange (CLOB settlement)

EIP-712 `Order` struct. Price is USDC per token with `PRICE_SCALE = 1e6` (1.00 USD = 1_000_000).

- `TAKER_FEE_BPS = 75`. [contracts/src/Exchange.vy : L33]
- `matchOrders` requires opposite sides, same condition/outcome, price cross, valid maker signatures, unused fill. [contracts/src/Exchange.vy : L128-159]
- Buy taker pays `volume` USDC to maker plus `fee` to FeeVault, receives outcome tokens.
- Sell taker sends tokens; maker pays `volume - fee` USDC to taker and `fee` to vault.
- Operator or maker may `cancelOrder`. Makers can `incrementNonce` to invalidate the nonce class.

## MarketAMM

Constant-product pool of YES and NO reserves. Seed splits USDC 50/50 into both tokens.

- `AMM_FEE_BPS = 100`, applied as 50 vault + 50 LP on buy and sell. [contracts/src/MarketAMM.vy : L40]
- `buyWithUSDC` splits trade+LP fee into tokens, swaps against k, sends the bought outcome to trader. [contracts/src/MarketAMM.vy : L103-140]
- `sellToUSDC` solves for merge amount `x`, takes fees from `x`, returns net USDC.
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

## OverUnderPaymaster

ERC-4337 v0.7 paymaster for gasless AMM operations. Deny-by-default allowlist prevents arbitrary sponsorship.

- Operator-gated: only operator can deposit ETH, add/remove senders/factories.
- Sender allowlist: `validatePaymasterUserOp` checks `allowedSenders[userOp.sender]` before sponsoring. MVP: operator pre-registers AA accounts via `addSender()`. Production: parse `initCode` to extract factory, validate against `allowedFactories`.
- Selector allowlist: unwraps AA `execute(address,uint256,bytes)` and validates inner `(target, callData)` against protocol contract addresses + allowed selectors.
- Allowed: USDC `approve` (spender ∈ {ctf, amm, vault}), CTF `splitPosition`/`setApprovalForAll`, AMM buy/sell/addLiquidity, Oracle `castVote`, Exchange `cancelOrder`/`incrementNonce`, FeeVault `requestRedeem`/`claim`.
- Blocked: `matchOrders` (relayer-only), `executeBatch` (too complex, denied wholesale for stricter security), unknown selectors, undecodable calldata.
- Production path for `executeBatch`: decode dynamic arrays, validate each `(target, value, data)` tuple against allowlist. MVP denies entirely to simplify security surface.

## MockEntryPoint

Minimal EntryPoint v0.7 for local testing. Tracks paymaster deposits via `depositTo`, returns deposit info for validation. Production uses canonical EntryPoint deployment.

## MockUSDC

Local 6-decimal ERC-20 with operator mint. Not for Base mainnet; production uses native USDC.
