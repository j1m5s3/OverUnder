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

- **Theme**: Generated from `shared/design-tokens/tokens.json` → `lib/theme/app_theme.dart`. No generator is checked in; the file was hand-fixed to `CardThemeData` for Flutter 3.27, so a future generator must emit that
- **API**: Client built from `shared/openapi.json` → `lib/services/api_client.dart`
- **Chain**: Trade targets (USDC, CTF, MarketAMM, MarketFactory) come from `GET /api/v1/chain/addresses` when the API serves it, since `/aa/cdp-send` allowlists those same addresses. Otherwise the app uses the bundled `assets/deployments/<CHAIN_ID>.json`. Resolution fails closed when the API reports another chain, or when there are no API addresses and the asset is missing or declares another chain
- **Regenerating the asset**: the bundled file is generated, not hand-copied. Regenerate it after every contract deploy, including each AMM/Factory redeploy: `python scripts/sync_mobile_deployments.py 84532` (add `--source <upload>/84532.json` to use the deploy-contracts.yml artifact). `--check` exits 1 on drift. A stale asset makes every cdp-send trade fail with 403 when the API does not serve addresses

## Configuration

Build-time `--dart-define` values, read in `lib/config/app_config.dart`:

- `API_BASE_URL` (default `http://127.0.0.1:8000`)
- `CHAIN_ID` (default `31337`, local anvil)
- `TRADING_HALT_AT_CLOSE` (default `false`): halts at `closeTime` only when the API omits `tradingHaltsAt`; the server field wins when present

## Local Development

```bash
# Install dependencies
flutter pub get

# Run on device/simulator against local API
flutter run

# Run against Base Sepolia
flutter run --dart-define=API_BASE_URL=https://api.example.com --dart-define=CHAIN_ID=84532

# Analyze and test
flutter analyze
flutter test

# Build for production (no android/ or ios/ folders are checked in: run `flutter create .` first)
flutter build apk  # Android
flutter build ios  # iOS
```

Requires Flutter >= 3.27 (`pubspec.yaml`). `flutter analyze` reports only info-level lints; `flutter test` has no CI job, so run it before a PR.

## Screens

1. **Market List**: Browse primaries and wildcard markets
2. **Market Detail**: View market info + AMM swap interface
3. **Wallet**: Email OTP, smart-account address, buy USDC to that account
4. **Oracle Status**: View attestations, unanimity, and resolution status

## Features Implemented

- [SHIPPED] Market list of event cards (`GET /api/v1/markets`): each primary (marketType 0, or 2 for user-listed) with its wildcards
- [SHIPPED] Market detail with AMM swap (buy/sell YES or NO tokens), close time, resolution criteria and live YES price when indexed
- [SHIPPED] Quote fetching from `/api/v1/amm/{id}/quote` (`buy_yes`/`usdc_in` or `sell_yes`/`token_amount`)
- [SHIPPED] Decimal amounts converted to 6-decimal base units (micro-USDC, outcome tokens)
- [SHIPPED] Trading halt: swap disabled with a banner once resolved or past `tradingHaltsAt`, flips live on an open screen, and on any API 409 (quote or cdp-send)
- [SHIPPED] Email OTP via `POST /api/v1/auth/cdp/email` and `/auth/cdp/verify`
- [SHIPPED] Trades via `POST /api/v1/aa/cdp-send` (no web3dart send)
- [SHIPPED] Onramp destination is the smart account
- [SHIPPED] Oracle status widget with agent attestations (`attestations`, `votes`; outcome shown when unanimous). `kind: "research"` rows (unresolved research retries) are hidden; only resolution rows are shown
- [SHIPPED] Trade targets from the API when served, else the bundled asset (`Deployments.resolve`); a load failure shows on execute
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
