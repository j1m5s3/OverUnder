/// Build-time configuration, passed with `--dart-define`:
///
///   flutter run --dart-define=API_BASE_URL=https://api.example.com \
///               --dart-define=CHAIN_ID=84532
class AppConfig {
  AppConfig._();

  /// Local anvil chain, used when `CHAIN_ID` is not defined.
  static const int defaultChainId = 31337;

  static const String apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://127.0.0.1:8000',
  );

  static const int _chainId = int.fromEnvironment('CHAIN_ID', defaultValue: defaultChainId);

  /// Chain whose `assets/deployments/<chainId>.json` holds the contract addresses.
  static int get chainId => _chainId > 0 ? _chainId : defaultChainId;

  /// Mirrors the backend `TRADING_HALT_AT_CLOSE` flag for an API that does not
  /// send `tradingHaltsAt` yet. The server field always wins when present.
  static const bool tradingHaltAtClose = bool.fromEnvironment('TRADING_HALT_AT_CLOSE');
}
