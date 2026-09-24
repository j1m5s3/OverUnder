import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:overunder/features/markets/market_list_screen.dart';
import 'package:overunder/services/api_client.dart';

Map<String, dynamic> _market(String cid, String question, int marketType, {bool resolved = false}) => {
      'conditionId': cid,
      'question': question,
      'marketType': marketType,
      'closeTime': 4102444800,
      'paused': false,
      'resolved': resolved,
      'suggestedProbability': 0.5,
      'tradingHaltsAt': null,
      'tradingOpen': !resolved,
      'yesPriceMicros': 640000,
    };

void main() {
  testWidgets('renders each primary with its wildcards', (tester) async {
    final api = ApiClient(
      baseUrl: 'http://api.test',
      httpClient: MockClient(
        (_) async => http.Response(
          json.encode([
            {
              'primary': _market('0xp1', 'Chiefs beat the Bills?', 0),
              'children': [_market('0xw1', 'Over 47.5 total points?', 1)],
            },
            {
              'primary': _market('0xu1', 'Will it snow in Denver?', 2, resolved: true),
              'children': <dynamic>[],
            },
          ]),
          200,
        ),
      ),
    );

    await tester.pumpWidget(MaterialApp(home: MarketListScreen(apiClient: api)));
    await tester.pumpAndSettle();

    expect(find.text('Chiefs beat the Bills?'), findsOneWidget);
    expect(find.text('Over 47.5 total points?'), findsOneWidget);
    expect(find.text('Will it snow in Denver?'), findsOneWidget);
    expect(find.text('User market'), findsOneWidget);
    expect(find.text('Resolved'), findsOneWidget);
    expect(find.text('Probability: 64.0%'), findsNWidgets(2));
    expect(find.byType(MarketCard), findsNWidgets(2));
  });
}
