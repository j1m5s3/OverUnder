# OverUnder Flutter

Flutter mobile app for OverUnder prediction markets on Base.

## Architecture

Mirrors web AMM-first architecture:
- `lib/features/markets` - Market list and detail screens
- `lib/features/trade` - AMM swap widget (no CLOB OrderTicket)
- `lib/features/wallet` - Email OTP, smart-account session, ramps
- `lib/features/oracle` - Oracle status and attestations

Flutter has no Coinbase CDP SDK. Dart calls only OverUnder API. Backend calls CDP REST with `useCdpPaymaster: true`. See [ADR-0010](../docs/adr/0010-cdp-embedded-wallets.md).

## Shared Contracts

- **Theme**: Generated from `shared/design-tokens/tokens.json` → `lib/theme/app_theme.dart`
- **API**: Client built from `shared/openapi.json` → `lib/services/api_client.dart`
- **Chain**: Uses `contracts/deployments/<chainId>.json` for contract addresses

## Local Development

```bash
# Install dependencies
flutter pub get

# Run on device/simulator against local API
flutter run

# Build for production
flutter build apk  # Android
flutter build ios  # iOS
```

## Screens

1. **Market List**: Browse primaries and wildcard markets
2. **Market Detail**: View market info + AMM swap interface
3. **Wallet**: Email OTP, smart-account address, buy USDC to that account
4. **Oracle Status**: View attestations, unanimity, and resolution status

## Features Implemented

- [SHIPPED] Market list with primary/wildcard chips
- [SHIPPED] Market detail with AMM swap (buy/sell tokens)
- [SHIPPED] Quote fetching from `/api/v1/amm/{id}/quote`
- [SHIPPED] Email OTP via `POST /api/v1/auth/cdp/email` and `/auth/cdp/verify`
- [SHIPPED] Trades via `POST /api/v1/aa/cdp-send` (no web3dart send)
- [SHIPPED] Onramp destination is the smart account
- [SHIPPED] Oracle status widget with agent attestations
- [SHIPPED] Theme generated from design tokens
- [SHIPPED] API client from OpenAPI spec

## Not Implemented (AMM-First)

- [PHASE2] CLOB OrderTicket (no `POST /orders` in mobile)
- [PHASE2] Orderbook depth view
- [PHASE2] Limit order management

## Phase 2

- [PHASE2] WalletConnect support
- [PHASE2] Push notifications for market resolution
- [PHASE2] Portfolio tracking
