# OverUnder Flutter carryover contract

Do not implement the Flutter app in this MVP. When mobile work starts, mirror the web feature modules 1:1.

## Feature map

| Web module | Flutter destination |
| --- | --- |
| `web/src/features/markets` | `lib/features/markets` |
| `web/src/features/trade` | `lib/features/trade` |
| `web/src/features/wallet` | `lib/features/wallet` |
| `web/src/features/oracle` | `lib/features/oracle` |

## Shared contracts

- Theme: consume [`shared/design-tokens/tokens.json`](../shared/design-tokens/tokens.json) (colors, spacing, type).
- API: generate a Dart client from [`shared/openapi.json`](../shared/openapi.json).
- Chain: same Base USDC + contract addresses as web (`contracts/deployments/<chainId>.json`).

## Screens

1. Market list (primaries + wildcard chips)
2. Market detail: CLOB ticket on type 0, AMM buy/sell on type 1
3. Wallet: Privy/email + EOA + Coinbase Onramp URL from `GET /api/v1/ramps/onramp-url`
4. Oracle status: attestations, unanimity, fallback votes
