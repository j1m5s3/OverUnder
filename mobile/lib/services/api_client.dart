/// API client generated from shared/openapi.json
/// Simplified implementation for AMM-first MVP
library;

import 'dart:convert';
import 'package:http/http.dart' as http;
import '../models/models.dart';

/// The API refused a quote or trade because the market's trading window has
/// closed or it has resolved (HTTP 409 from the AMM quote and cdp-send routes).
class TradingClosedException implements Exception {
  final String reason;

  TradingClosedException(this.reason);

  /// Reads a FastAPI error body such as `{"detail": "market closed"}`.
  factory TradingClosedException.fromBody(String body) =>
      TradingClosedException(_errorDetail(body) ?? 'trading closed');

  @override
  String toString() => 'Trading closed: $reason';
}

String? _errorDetail(String body) {
  try {
    final decoded = json.decode(body);
    if (decoded is Map && decoded['detail'] is String) return decoded['detail'] as String;
  } on FormatException {
    // Not JSON; fall through to the raw body.
  }
  final trimmed = body.trim();
  if (trimmed.isEmpty) return null;
  return trimmed.length > 200 ? '${trimmed.substring(0, 200)}...' : trimmed;
}

/// "Failed to get quote (422: price bound)".
String _failure(String what, http.Response response) {
  final detail = _errorDetail(response.body);
  return '$what (${response.statusCode}${detail == null ? '' : ': $detail'})';
}

class ApiClient {
  final String baseUrl;
  final http.Client _http;
  String? _jwt;

  ApiClient({required this.baseUrl, http.Client? httpClient})
      : _http = httpClient ?? http.Client();

  void setJwt(String? jwt) {
    _jwt = (jwt == null || jwt.isEmpty) ? null : jwt;
  }

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (_jwt != null) 'Authorization': 'Bearer $_jwt',
      };

  /// `GET /api/v1/markets`: one card per primary (marketType 0 or 2) with its
  /// wildcards.
  Future<List<EventCard>> getEventCards() async {
    final response = await _http.get(Uri.parse('$baseUrl/api/v1/markets'));

    if (response.statusCode == 200) {
      return EventCard.listFromJson(json.decode(response.body));
    }
    throw Exception('Failed to load markets');
  }

  /// Flat market list. With [parentId], the wildcards under that primary;
  /// otherwise every card flattened, each primary followed by its wildcards.
  Future<List<Market>> getMarkets({String? parentId}) async {
    if (parentId == null) return flattenEventCards(await getEventCards());

    final uri = Uri.parse('$baseUrl/api/v1/markets').replace(queryParameters: {'parentId': parentId});
    final response = await _http.get(uri);

    if (response.statusCode == 200) {
      return flattenEventCards(EventCard.listFromJson(json.decode(response.body)));
    }
    throw Exception('Failed to load markets');
  }

  Future<Market> getMarket(String conditionId) async {
    final response = await _http.get(
      Uri.parse('$baseUrl/api/v1/markets/$conditionId'),
    );

    if (response.statusCode == 200) {
      return Market.fromJson(json.decode(response.body));
    }
    throw Exception('Failed to load market');
  }

  /// Quotes a buy of [amount] micro-USDC or a sell of [amount] outcome-token
  /// base units on the YES ([isYes]) or NO side. Throws
  /// [TradingClosedException] once the market has stopped trading.
  Future<AmmQuote> getAmmQuote({
    required String marketId,
    required bool isBuy,
    required bool isYes,
    required int amount,
  }) async {
    final uri = Uri.parse('$baseUrl/api/v1/amm/$marketId/quote').replace(
      queryParameters: isBuy
          ? {'buy_yes': '$isYes', 'usdc_in': '$amount'}
          : {'sell_yes': '$isYes', 'token_amount': '$amount'},
    );

    final response = await _http.get(uri);

    if (response.statusCode == 200) {
      return AmmQuote.fromJson(json.decode(response.body));
    }
    if (response.statusCode == 409) {
      throw TradingClosedException.fromBody(response.body);
    }
    throw Exception(_failure('Failed to get quote', response));
  }

  /// `GET /api/v1/chain/addresses`: the trade targets (USDC, CTF, AMM,
  /// Factory) and chainId the backend quotes against and cdp-send allowlists.
  /// Returns null when this API does not serve the route (404/405).
  Future<Map<String, dynamic>?> getChainAddresses() async {
    final response = await _http.get(Uri.parse('$baseUrl/api/v1/chain/addresses'));

    if (response.statusCode == 200) {
      final decoded = json.decode(response.body);
      return decoded is Map ? Map<String, dynamic>.from(decoded) : null;
    }
    if (response.statusCode == 404 || response.statusCode == 405) return null;
    throw Exception(_failure('Failed to load chain addresses', response));
  }

  Future<Map<String, dynamic>> getRampUrl(String address, {String amount = '100'}) async {
    final uri = Uri.parse('$baseUrl/api/v1/ramps/onramp-url').replace(
      queryParameters: {'address': address, 'usdc_amount': amount},
    );

    final response = await _http.get(uri);

    if (response.statusCode == 200) {
      return json.decode(response.body);
    }
    throw Exception('Failed to get ramp URL');
  }

  Future<OracleStatus> getOracleStatus(String marketId) async {
    final response = await _http.get(
      Uri.parse('$baseUrl/api/v1/oracle/$marketId/status'),
    );

    if (response.statusCode == 200) {
      return OracleStatus.fromJson(json.decode(response.body));
    }
    throw Exception('Failed to get oracle status');
  }

  Future<Map<String, dynamic>> getNonce(String address) async {
    final response = await _http.get(
      Uri.parse('$baseUrl/api/v1/auth/nonce/$address'),
    );

    if (response.statusCode == 200) {
      return json.decode(response.body);
    }
    throw Exception('Failed to get nonce');
  }

  Future<String> authCdpEmail(String email) async {
    final response = await _http.post(
      Uri.parse('$baseUrl/api/v1/auth/cdp/email'),
      headers: _headers,
      body: json.encode({'email': email}),
    );

    if (response.statusCode == 200) {
      final data = json.decode(response.body);
      return data['flowId'] as String;
    }
    throw Exception('Failed to send sign-in code');
  }

  Future<Map<String, dynamic>> authCdpVerify(String flowId, String otp) async {
    final response = await _http.post(
      Uri.parse('$baseUrl/api/v1/auth/cdp/verify'),
      headers: _headers,
      body: json.encode({'flowId': flowId, 'otp': otp}),
    );

    if (response.statusCode == 200) {
      return json.decode(response.body) as Map<String, dynamic>;
    }
    throw Exception('Failed to authenticate');
  }

  /// Relays a sponsored batch through the backend. Throws
  /// [TradingClosedException] when the API refuses an AMM trade after close.
  Future<Map<String, dynamic>> cdpSend(List<Map<String, dynamic>> calls) async {
    final response = await _http.post(
      Uri.parse('$baseUrl/api/v1/aa/cdp-send'),
      headers: _headers,
      body: json.encode({'calls': calls}),
    );

    if (response.statusCode == 200) {
      return json.decode(response.body) as Map<String, dynamic>;
    }
    if (response.statusCode == 409) {
      throw TradingClosedException.fromBody(response.body);
    }
    throw Exception(_failure('Failed to send trade', response));
  }

  Future<double> getNav() async {
    final response = await _http.get(
      Uri.parse('$baseUrl/api/v1/fee-vault/nav'),
    );

    if (response.statusCode == 200) {
      final data = json.decode(response.body);
      return (data['nav'] ?? 0).toDouble();
    }
    throw Exception('Failed to get NAV');
  }
}
