import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:overunder/features/trade/amm_swap_widget.dart';
import 'package:overunder/models/models.dart';
import 'package:overunder/services/api_client.dart';

const _cid = '0x1111111111111111111111111111111111111111111111111111111111111111';

int _nowSec() => DateTime.now().millisecondsSinceEpoch ~/ 1000;

Market _market({int? tradingHaltsAt, bool resolved = false}) => Market.fromJson({
      'conditionId': _cid,
      'question': 'Will it rain?',
      'marketType': 0,
      'closeTime': tradingHaltsAt ?? 0,
      'paused': false,
      'resolved': resolved,
      'tradingHaltsAt': tradingHaltsAt,
      'tradingOpen': true,
    });

Widget _host(ApiClient api, Market? market) => MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: AmmSwapWidget(
            apiClient: api,
            marketId: _cid,
            walletConnected: true,
            market: market,
          ),
        ),
      ),
    );

ElevatedButton _actionButton(WidgetTester tester) => tester.widget<ElevatedButton>(find.byType(ElevatedButton));

TextField _amountField(WidgetTester tester) => tester.widget<TextField>(find.byType(TextField).first);

void main() {
  testWidgets('open market shows the Buy action', (tester) async {
    final api = ApiClient(baseUrl: 'http://api.test', httpClient: MockClient((_) async => http.Response('{}', 200)));
    await tester.pumpWidget(_host(api, _market(tradingHaltsAt: _nowSec() + 3600)));

    expect(find.textContaining('Trading closes'), findsOneWidget);
    expect(find.text('Buy'), findsWidgets);
    expect(_amountField(tester).enabled, isTrue);
  });

  testWidgets('market past tradingHaltsAt shows the closed banner and disables trading', (tester) async {
    final api = ApiClient(baseUrl: 'http://api.test', httpClient: MockClient((_) async => http.Response('{}', 200)));
    await tester.pumpWidget(_host(api, _market(tradingHaltsAt: _nowSec() - 60)));

    expect(find.textContaining('Awaiting resolution'), findsOneWidget);
    expect(find.widgetWithText(ElevatedButton, 'Trading closed'), findsOneWidget);
    expect(_actionButton(tester).onPressed, isNull);
    expect(_amountField(tester).enabled, isFalse);
  });

  testWidgets('resolved market reads Market resolved', (tester) async {
    final api = ApiClient(baseUrl: 'http://api.test', httpClient: MockClient((_) async => http.Response('{}', 200)));
    await tester.pumpWidget(_host(api, _market(resolved: true)));

    expect(find.widgetWithText(ElevatedButton, 'Market resolved'), findsOneWidget);
    expect(_actionButton(tester).onPressed, isNull);
  });

  testWidgets('quotes in micro-USDC and enables the action', (tester) async {
    late Uri seen;
    final api = ApiClient(
      baseUrl: 'http://api.test',
      httpClient: MockClient((request) async {
        seen = request.url;
        return http.Response(json.encode({'tokensOut': 8123457}), 200);
      }),
    );
    await tester.pumpWidget(_host(api, _market()));

    await tester.enterText(find.byType(TextField).first, '2.5');
    await tester.tap(find.byIcon(Icons.search));
    await tester.pumpAndSettle();

    expect(seen.queryParameters, {'buy_yes': 'true', 'usdc_in': '2500000'});
    expect(find.text('8.123457 tokens'), findsOneWidget);
    // 0.5% default slippage, rounded down.
    expect(find.text('8.082839 tokens'), findsOneWidget);
    expect(_actionButton(tester).onPressed, isNotNull);
  });

  testWidgets('a 409 quote flips the widget to trading closed', (tester) async {
    final api = ApiClient(
      baseUrl: 'http://api.test',
      httpClient: MockClient((_) async => http.Response('{"detail":"market closed"}', 409)),
    );
    await tester.pumpWidget(_host(api, _market()));

    await tester.enterText(find.byType(TextField).first, '5');
    await tester.tap(find.byIcon(Icons.search));
    await tester.pumpAndSettle();

    expect(find.textContaining('Awaiting resolution'), findsOneWidget);
    expect(find.widgetWithText(ElevatedButton, 'Trading closed'), findsOneWidget);
    expect(_actionButton(tester).onPressed, isNull);
  });

  testWidgets('an open screen closes itself when tradingHaltsAt passes', (tester) async {
    final api = ApiClient(baseUrl: 'http://api.test', httpClient: MockClient((_) async => http.Response('{}', 200)));
    await tester.pumpWidget(_host(api, _market(tradingHaltsAt: _nowSec() + 1)));
    expect(find.widgetWithText(ElevatedButton, 'Buy'), findsOneWidget);

    // isTradingClosed reads the wall clock, so let real time reach the halt,
    // then fire the widget's (fake-async) halt timer.
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 1500)));
    await tester.pump(const Duration(seconds: 2));

    expect(find.widgetWithText(ElevatedButton, 'Trading closed'), findsOneWidget);
    expect(find.textContaining('Awaiting resolution'), findsOneWidget);
  });

  testWidgets('buys against the API trade targets, not a stale bundled asset', (tester) async {
    const apiAmm = '0x7a446c5853c3bfef992e430e604ea610d9e686ef';
    const apiUsdc = '0xaf4d478f9494221dd3cca7c192100878f86327e5';
    List<dynamic>? sent;
    final api = ApiClient(
      baseUrl: 'http://api.test',
      httpClient: MockClient((request) async {
        switch (request.url.path) {
          case '/api/v1/chain/addresses':
            // The widget test binding runs with CHAIN_ID unset (31337).
            return http.Response(json.encode({'chainId': 31337, 'MockUSDC': apiUsdc, 'MarketAMM': apiAmm}), 200);
          case '/api/v1/aa/cdp-send':
            sent = (json.decode(request.body) as Map)['calls'] as List;
            return http.Response('{"userOpHash":"0x01"}', 200);
          default:
            return http.Response(json.encode({'tokensOut': 8123457}), 200);
        }
      }),
    );
    // Earlier tests cached a bundled-asset load started in their own fake
    // clock zone; start this one fresh.
    rootBundle.clear();
    await tester.pumpWidget(_host(api, _market(tradingHaltsAt: _nowSec() + 3600)));

    await tester.enterText(find.byType(TextField).first, '2.5');
    await tester.tap(find.byIcon(Icons.search));
    await tester.pumpAndSettle();
    final buy = find.widgetWithText(ElevatedButton, 'Buy');
    await tester.ensureVisible(buy);
    await tester.tap(buy);
    // Address resolution reads the bundled asset with real I/O, which fake
    // async pumping alone does not complete.
    for (var i = 0; i < 100 && sent == null; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 20)));
      await tester.pump();
    }
    await tester.pumpAndSettle();

    expect(sent, isNotNull, reason: tester.widgetList<Text>(find.byType(Text)).map((t) => t.data).join(' | '));
    expect(sent!.map((c) => (c['to'] as String).toLowerCase()).toList(), [apiUsdc, apiAmm]);
    expect(find.textContaining('Success'), findsOneWidget);
  });

  testWidgets('sells against the API CTF and AMM', (tester) async {
    const apiAmm = '0x7a446c5853c3bfef992e430e604ea610d9e686ef';
    const apiCtf = '0x5b891e69bea73546862a51232908a99f3f7cd58c';
    List<dynamic>? sent;
    final api = ApiClient(
      baseUrl: 'http://api.test',
      httpClient: MockClient((request) async {
        switch (request.url.path) {
          case '/api/v1/chain/addresses':
            return http.Response(json.encode({'chainId': 31337, 'ConditionalTokens': apiCtf, 'MarketAMM': apiAmm}), 200);
          case '/api/v1/aa/cdp-send':
            sent = (json.decode(request.body) as Map)['calls'] as List;
            return http.Response('{"userOpHash":"0x01"}', 200);
          default:
            return http.Response(json.encode({'usdcOut': 612345}), 200);
        }
      }),
    );
    rootBundle.clear();
    await tester.pumpWidget(_host(api, _market(tradingHaltsAt: _nowSec() + 3600)));

    await tester.tap(find.descendant(of: find.byType(SegmentedButton<bool>).first, matching: find.text('Sell')));
    await tester.pump();
    await tester.enterText(find.byType(TextField).first, '1');
    await tester.tap(find.byIcon(Icons.search));
    await tester.pumpAndSettle();
    final sell = find.widgetWithText(ElevatedButton, 'Sell');
    await tester.ensureVisible(sell);
    await tester.tap(sell);
    for (var i = 0; i < 100 && sent == null; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 20)));
      await tester.pump();
    }
    await tester.pumpAndSettle();

    expect(sent, isNotNull, reason: tester.widgetList<Text>(find.byType(Text)).map((t) => t.data).join(' | '));
    expect(sent!.map((c) => (c['to'] as String).toLowerCase()).toList(), [apiCtf, apiAmm]);
    expect(find.textContaining('Success'), findsOneWidget);
  });
}
