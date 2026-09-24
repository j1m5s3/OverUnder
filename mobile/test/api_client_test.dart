import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:overunder/services/api_client.dart';

const _cid = '0x7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a';

Map<String, dynamic> _market(String cid, int marketType, {String? parent}) => {
      'conditionId': cid,
      'parentConditionId': parent,
      'question': 'Q $cid',
      'marketType': marketType,
      'closeTime': 1700000000,
      'paused': false,
      'resolved': false,
      'tradingHaltsAt': null,
      'tradingOpen': true,
    };

void main() {
  group('getAmmQuote', () {
    test('sends buy_yes and usdc_in for a buy', () async {
      late Uri seen;
      final api = ApiClient(
        baseUrl: 'http://api.test',
        httpClient: MockClient((request) async {
          seen = request.url;
          return http.Response(
            json.encode({'conditionId': _cid, 'buyYes': false, 'usdcIn': 2500000, 'tokensOut': 4100000}),
            200,
          );
        }),
      );

      final quote = await api.getAmmQuote(marketId: _cid, isBuy: true, isYes: false, amount: 2500000);

      expect(seen.path, '/api/v1/amm/$_cid/quote');
      expect(seen.queryParameters, {'buy_yes': 'false', 'usdc_in': '2500000'});
      expect(quote.tokensOut, 4100000);
    });

    test('sends sell_yes and token_amount for a sell and reads usdcOut', () async {
      late Uri seen;
      final api = ApiClient(
        baseUrl: 'http://api.test',
        httpClient: MockClient((request) async {
          seen = request.url;
          return http.Response(
            json.encode({'conditionId': _cid, 'sellYes': true, 'tokenAmount': 1000000, 'usdcOut': 580000}),
            200,
          );
        }),
      );

      final quote = await api.getAmmQuote(marketId: _cid, isBuy: false, isYes: true, amount: 1000000);

      expect(seen.queryParameters, {'sell_yes': 'true', 'token_amount': '1000000'});
      expect(quote.usdcOut, 580000);
    });

    test('maps 409 to TradingClosedException with the server reason', () async {
      final api = ApiClient(
        baseUrl: 'http://api.test',
        httpClient: MockClient((_) async => http.Response('{"detail":"market closed"}', 409)),
      );

      await expectLater(
        api.getAmmQuote(marketId: _cid, isBuy: true, isYes: true, amount: 1000000),
        throwsA(isA<TradingClosedException>().having((e) => e.reason, 'reason', 'market closed')),
      );
    });

    test('other failures stay generic exceptions with the server detail', () async {
      final api = ApiClient(
        baseUrl: 'http://api.test',
        httpClient: MockClient((_) async => http.Response('{"detail":"price bound"}', 422)),
      );

      await expectLater(
        api.getAmmQuote(marketId: _cid, isBuy: true, isYes: true, amount: 1000000),
        throwsA(allOf(
          isA<Exception>(),
          isNot(isA<TradingClosedException>()),
          predicate((e) => e.toString().contains('422: price bound')),
        )),
      );
    });
  });

  group('cdpSend', () {
    test('maps 409 to TradingClosedException', () async {
      final api = ApiClient(
        baseUrl: 'http://api.test',
        httpClient: MockClient((_) async => http.Response('{"detail":"market resolved"}', 409)),
      );

      await expectLater(
        api.cdpSend([
          {'to': '0x0', 'data': '0x', 'value': 0},
        ]),
        throwsA(isA<TradingClosedException>().having((e) => e.reason, 'reason', 'market resolved')),
      );
    });

    test('includes the server detail on other failures', () async {
      final api = ApiClient(
        baseUrl: 'http://api.test',
        httpClient: MockClient((_) async => http.Response('{"detail":"Operation not allowed"}', 403)),
      );

      await expectLater(
        api.cdpSend([
          {'to': '0x0', 'data': '0x', 'value': 0},
        ]),
        throwsA(predicate((e) => e.toString().contains('403: Operation not allowed'))),
      );
    });

    test('TradingClosedException tolerates a non-JSON body', () {
      expect(TradingClosedException.fromBody('').reason, 'trading closed');
      expect(TradingClosedException.fromBody('closed').reason, 'closed');
    });
  });

  group('markets', () {
    final cards = [
      {
        'primary': _market('0xp1', 0),
        'children': [_market('0xw1', 1, parent: '0xp1')],
      },
      {'primary': _market('0xu1', 2), 'children': <dynamic>[]},
    ];

    test('getEventCards parses EventCard[]', () async {
      final api = ApiClient(
        baseUrl: 'http://api.test',
        httpClient: MockClient((_) async => http.Response(json.encode(cards), 200)),
      );

      final result = await api.getEventCards();

      expect(result.map((c) => c.primary.conditionId), ['0xp1', '0xu1']);
      expect(result.first.children.single.conditionId, '0xw1');
    });

    test('getMarkets flattens cards and passes parentId through', () async {
      final seen = <Uri>[];
      final api = ApiClient(
        baseUrl: 'http://api.test',
        httpClient: MockClient((request) async {
          seen.add(request.url);
          if (request.url.queryParameters.containsKey('parentId')) {
            return http.Response(json.encode([_market('0xw1', 1, parent: '0xp1')]), 200);
          }
          return http.Response(json.encode(cards), 200);
        }),
      );

      final all = await api.getMarkets();
      final kids = await api.getMarkets(parentId: '0xp1');

      expect(all.map((m) => m.conditionId), ['0xp1', '0xw1', '0xu1']);
      expect(kids.single.parentConditionId, '0xp1');
      expect(seen.last.queryParameters, {'parentId': '0xp1'});
    });

    test('getMarket reads the camelCase detail', () async {
      final api = ApiClient(
        baseUrl: 'http://api.test',
        httpClient: MockClient(
          (_) async => http.Response(
            json.encode({
              ..._market(_cid, 0),
              'tradingHaltsAt': 1700000000,
              'tradingOpen': false,
              'children': [_market('0xw1', 1, parent: _cid)],
            }),
            200,
          ),
        ),
      );

      final m = await api.getMarket(_cid);

      expect(m.closeTime, 1700000000);
      expect(m.tradingHaltsAt, 1700000000);
      expect(m.tradingOpen, isFalse);
      expect(m.children.single.conditionId, '0xw1');
    });
  });

  group('getChainAddresses', () {
    ApiClient client(int status, String body, [void Function(Uri)? onRequest]) => ApiClient(
          baseUrl: 'http://api.test',
          httpClient: MockClient((request) async {
            onRequest?.call(request.url);
            return http.Response(body, status);
          }),
        );

    test('returns the payload from GET /api/v1/chain/addresses', () async {
      late Uri seen;
      final body = {'chainId': 84532, 'MarketAMM': '0x7A446c5853c3BFEF992e430e604Ea610d9E686ef'};
      final out = await client(200, json.encode(body), (u) => seen = u).getChainAddresses();
      expect(seen.path, '/api/v1/chain/addresses');
      expect(out, body);
    });

    test('returns null when the API has no such route', () async {
      expect(await client(404, '{"detail":"Not Found"}').getChainAddresses(), isNull);
      expect(await client(405, '').getChainAddresses(), isNull);
      expect(await client(200, '[]').getChainAddresses(), isNull);
    });

    test('throws on a server error', () async {
      await expectLater(client(500, '{"detail":"boom"}').getChainAddresses(), throwsException);
    });
  });
}
