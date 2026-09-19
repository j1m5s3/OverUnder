# OverUnder Flutter

Flutter mobile app for OverUnder prediction markets on Base.

## Architecture

Mirrors web AMM-first architecture:
- `lib/features/markets` - Market list and detail screens
- `lib/features/trade` - AMM swap widget (no CLOB OrderTicket)
- `lib/features/wallet` - Wallet connection, balances, ramps
- `lib/features/oracle` - Oracle status and attestations

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
3. **Wallet**: Connect wallet, view balances, buy USDC via MoonPay/Coinbase
4. **Oracle Status**: View attestations, unanimity, and resolution status

## Features Implemented

- ✅ Market list with primary/wildcard chips
- ✅ Market detail with AMM swap (buy/sell tokens)
- ✅ Quote fetching from `/api/v1/amm/{id}/quote`
- ✅ Wallet screen with NAV display
- ✅ Ramp integration (MoonPay/Coinbase fallback)
- ✅ Oracle status widget with agent attestations
- ✅ Theme generated from design tokens
- ✅ API client from OpenAPI spec

## Not Implemented (AMM-First)

- ❌ CLOB OrderTicket (no `POST /orders` in mobile)
- ❌ Orderbook depth view
- ❌ Limit order management

## Phase 2

- [ ] Privy wallet integration
- [ ] WalletConnect support
- [ ] Transaction signing
- [ ] Push notifications for market resolution
- [ ] Portfolio tracking
