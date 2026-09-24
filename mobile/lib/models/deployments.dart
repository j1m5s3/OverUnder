import 'dart:convert';
import 'package:flutter/services.dart';
import '../config/app_config.dart';

class Deployments {
  final int chainId;
  final String? mockUsdc;
  final String? revenueToken;
  final String? conditionalTokens;
  final String? feeVault;
  final String? consensusOracle;
  final String? exchange;
  final String? marketAmm;
  final String? marketFactory;
  final String? emissionsDistributor;

  Deployments({
    required this.chainId,
    this.mockUsdc,
    this.revenueToken,
    this.conditionalTokens,
    this.feeVault,
    this.consensusOracle,
    this.exchange,
    this.marketAmm,
    this.marketFactory,
    this.emissionsDistributor,
  });

  factory Deployments.fromJson(Map<String, dynamic> json) {
    return Deployments(
      chainId: json['chainId'] ?? 31337,
      mockUsdc: json['MockUSDC'],
      revenueToken: json['RevenueToken'],
      conditionalTokens: json['ConditionalTokens'],
      feeVault: json['FeeVault'],
      consensusOracle: json['ConsensusOracle'],
      exchange: json['Exchange'],
      marketAmm: json['MarketAMM'],
      marketFactory: json['MarketFactory'],
      emissionsDistributor: json['EmissionsDistributor'],
    );
  }

  /// Loads `assets/deployments/<chainId>.json` for [chainId], defaulting to
  /// [AppConfig.chainId] (31337 when `CHAIN_ID` is not defined).
  static Future<Deployments> load([int? chainId, AssetBundle? bundle]) async {
    final id = chainId ?? AppConfig.chainId;
    return Deployments.fromJson({...await _loadAsset(id, bundle), 'chainId': id});
  }

  // Fail-closed: throws if the deployment file is missing, invalid or for
  // another chain, rather than trading against the wrong addresses.
  static Future<Map<String, dynamic>> _loadAsset(int id, AssetBundle? bundle) async {
    final jsonString = await (bundle ?? rootBundle).loadString('assets/deployments/$id.json');
    final decoded = json.decode(jsonString);
    if (decoded is! Map) {
      throw StateError('assets/deployments/$id.json is not a JSON object');
    }
    final data = Map<String, dynamic>.from(decoded);
    final declared = _chainIdOf(data['chainId']);
    if (data['chainId'] != null && declared != id) {
      throw StateError('assets/deployments/$id.json declares chainId ${data['chainId']}');
    }
    return data;
  }

  /// Resolves the trade targets for [chainId] (default [AppConfig.chainId]).
  ///
  /// The API's `GET /api/v1/chain/addresses` wins for the trade targets
  /// ([tradeKeys]): the backend quotes against and cdp-send allowlists those
  /// same addresses, so a stale bundled asset cannot send trades elsewhere.
  /// The bundled asset fills the other keys, and is the whole answer when the
  /// API does not serve addresses (404, unreachable, or no trade keys). An API
  /// that reports another chain fails closed, as does a missing asset with no
  /// API addresses.
  static Future<ResolvedDeployments> resolve({
    Future<Map<String, dynamic>?> Function()? fetchApiAddresses,
    int? chainId,
    AssetBundle? bundle,
  }) async {
    final id = chainId ?? AppConfig.chainId;

    Map<String, dynamic>? asset;
    Object? assetError;
    try {
      asset = await _loadAsset(id, bundle);
    } catch (e) {
      assetError = e;
    }

    Map<String, String> api = const {};
    Object? apiChainId;
    if (fetchApiAddresses != null) {
      try {
        final body = await fetchApiAddresses();
        if (body != null) {
          final addresses = body['addresses'] is Map ? Map<String, dynamic>.from(body['addresses']) : body;
          api = _tradeTargets(addresses);
          apiChainId = body['chainId'] ?? addresses['chainId'];
        }
      } catch (_) {
        // API unavailable: the bundled asset is the answer (or the error).
      }
    }

    if (api.isEmpty) {
      if (asset == null) throw assetError ?? StateError('no deployments for chain $id');
      return ResolvedDeployments(
        Deployments.fromJson({...asset, 'chainId': id}),
        source: DeploymentsSource.bundle,
      );
    }

    if (apiChainId != null && _chainIdOf(apiChainId) != id) {
      throw StateError('API serves chainId $apiChainId, app is built for $id');
    }
    final drift = <String>[
      for (final entry in api.entries)
        if (asset != null &&
            asset[entry.key] is String &&
            (asset[entry.key] as String).toLowerCase() != entry.value.toLowerCase())
          entry.key,
    ];
    return ResolvedDeployments(
      Deployments.fromJson({...?asset, ...api, 'chainId': id}),
      source: DeploymentsSource.api,
      drift: drift,
    );
  }

  /// Deployment keys the app sends trades to (cdp-send allowlists these).
  static const tradeKeys = ['MockUSDC', 'ConditionalTokens', 'MarketAMM', 'MarketFactory'];

  // Short aliases a chain-addresses payload may use instead of deployment keys.
  static const _aliases = {
    'MockUSDC': ['usdc', 'usdcAddress'],
    'ConditionalTokens': ['ctf', 'ctfAddress', 'conditionalTokens'],
    'MarketAMM': ['amm', 'ammAddress', 'marketAmm'],
    'MarketFactory': ['factory', 'factoryAddress', 'marketFactory'],
  };

  static final _address = RegExp(r'^0x[0-9a-fA-F]{40}$');

  static Map<String, String> _tradeTargets(Map<String, dynamic> json) {
    final out = <String, String>{};
    for (final key in tradeKeys) {
      for (final name in [key, ..._aliases[key]!]) {
        final value = json[name];
        if (value is String && _address.hasMatch(value)) {
          out[key] = value;
          break;
        }
      }
    }
    return out;
  }

  static int? _chainIdOf(dynamic value) {
    if (value is int) return value;
    if (value is num) return value.toInt();
    if (value is String) return int.tryParse(value);
    return null;
  }
}

enum DeploymentsSource { api, bundle }

/// [Deployments] plus where its trade targets came from. [drift] lists the
/// trade keys whose bundled address differs from the API's (the bundled
/// asset is stale; regenerate it with scripts/sync_mobile_deployments.py).
class ResolvedDeployments {
  final Deployments deployments;
  final DeploymentsSource source;
  final List<String> drift;

  const ResolvedDeployments(this.deployments, {required this.source, this.drift = const []});
}
