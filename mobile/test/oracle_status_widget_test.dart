import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:overunder/features/oracle/oracle_status_widget.dart';
import 'package:overunder/services/api_client.dart';

const _cid = '0x2222222222222222222222222222222222222222222222222222222222222222';

Map<String, dynamic> _att(String agent, int outcome, String summary, [String? kind]) => {
      'agent': agent,
      'outcome': outcome,
      'summary': summary,
      'evidenceHash': '0x${'ab' * 32}',
      'createdAt': '2026-09-22T16:20:08Z',
      if (kind != null) 'kind': kind,
    };

void main() {
  testWidgets('research attempts are not shown as resolution evidence', (tester) async {
    final body = jsonEncode({
      'conditionId': _cid,
      'attestations': [
        _att('alpha', 2, 'still in progress', 'research'),
        _att('alpha', 1, 'Broncos won 24-17', 'resolution'),
        _att('beta', 1, 'Final: Broncos 24-17'),
        _att('gamma', 1, 'Broncos beat the Chiefs', 'resolution'),
      ],
      'votes': [],
      'unanimous': true,
    });
    final api = ApiClient(
      baseUrl: 'http://api.test',
      httpClient: MockClient((request) async {
        expect(request.url.path, '/api/v1/oracle/$_cid/status');
        return http.Response(body, 200);
      }),
    );
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: SingleChildScrollView(child: OracleStatusWidget(apiClient: api, marketId: _cid))),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Unanimous'), findsOneWidget);
    expect(find.text('Outcome: NO'), findsOneWidget);
    expect(find.text('still in progress'), findsNothing);
    expect(find.text('ALPHA'), findsOneWidget);
    expect(find.text('BETA'), findsOneWidget);
    expect(find.text('GAMMA'), findsOneWidget);
  });
}
