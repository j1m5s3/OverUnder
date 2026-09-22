/// API client generated from shared/openapi.json
/// Simplified implementation for AMM-first MVP

import 'dart:convert';
import 'package:http/http.dart' as http;
import '../models/models.dart';

class ApiClient {
  final String baseUrl;
  String? _jwt;

  ApiClient({required this.baseUrl});

  void setJwt(String? jwt) {
    _jwt = (jwt == null || jwt.isEmpty) ? null : jwt;
  }

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (_jwt != null) 'Authorization': 'Bearer $_jwt',
      };

  Future<List<Market>> getMarkets({String? parentId}) async {
    final uri = Uri.parse('$baseUrl/api/v1/markets');
    final response = await http.get(
      parentId != null ? uri.replace(queryParameters: {'parentId': parentId}) : uri,
    );

    if (response.statusCode == 200) {
      final List<dynamic> data = json.decode(response.body);
      return data.map((m) => Market.fromJson(m)).toList();
    }
    throw Exception('Failed to load markets');
  }

  Future<Market> getMarket(String conditionId) async {
    final response = await http.get(
      Uri.parse('$baseUrl/api/v1/markets/$conditionId'),
    );

    if (response.statusCode == 200) {
      return Market.fromJson(json.decode(response.body));
    }
    throw Exception('Failed to load market');
  }

  Future<AmmQuote> getAmmQuote({
    required String marketId,
    required bool isBuy,
    required int amount,
  }) async {
    final uri = Uri.parse('$baseUrl/api/v1/amm/$marketId/quote').replace(
      queryParameters: {
        'isBuy': isBuy.toString(),
        'amount': amount.toString(),
      },
    );

    final response = await http.get(uri);

    if (response.statusCode == 200) {
      return AmmQuote.fromJson(json.decode(response.body));
    }
    throw Exception('Failed to get quote');
  }

  Future<Map<String, dynamic>> getRampUrl(String address, {String amount = '100'}) async {
    final uri = Uri.parse('$baseUrl/api/v1/ramps/onramp-url').replace(
      queryParameters: {'address': address, 'usdc_amount': amount},
    );

    final response = await http.get(uri);

    if (response.statusCode == 200) {
      return json.decode(response.body);
    }
    throw Exception('Failed to get ramp URL');
  }

  Future<OracleStatus> getOracleStatus(String marketId) async {
    final response = await http.get(
      Uri.parse('$baseUrl/api/v1/oracle/$marketId/status'),
    );

    if (response.statusCode == 200) {
      return OracleStatus.fromJson(json.decode(response.body));
    }
    throw Exception('Failed to get oracle status');
  }

  Future<Map<String, dynamic>> getNonce(String address) async {
    final response = await http.get(
      Uri.parse('$baseUrl/api/v1/auth/nonce/$address'),
    );

    if (response.statusCode == 200) {
      return json.decode(response.body);
    }
    throw Exception('Failed to get nonce');
  }

  Future<String> authCdpEmail(String email) async {
    final response = await http.post(
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
    final response = await http.post(
      Uri.parse('$baseUrl/api/v1/auth/cdp/verify'),
      headers: _headers,
      body: json.encode({'flowId': flowId, 'otp': otp}),
    );

    if (response.statusCode == 200) {
      return json.decode(response.body) as Map<String, dynamic>;
    }
    throw Exception('Failed to authenticate');
  }

  Future<Map<String, dynamic>> cdpSend(List<Map<String, dynamic>> calls) async {
    final response = await http.post(
      Uri.parse('$baseUrl/api/v1/aa/cdp-send'),
      headers: _headers,
      body: json.encode({'calls': calls}),
    );

    if (response.statusCode == 200) {
      return json.decode(response.body) as Map<String, dynamic>;
    }
    throw Exception('Failed to send trade');
  }

  Future<double> getNav() async {
    final response = await http.get(
      Uri.parse('$baseUrl/api/v1/fee-vault/nav'),
    );

    if (response.statusCode == 200) {
      final data = json.decode(response.body);
      return (data['nav'] ?? 0).toDouble();
    }
    throw Exception('Failed to get NAV');
  }
}
