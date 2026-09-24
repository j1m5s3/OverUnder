import 'package:flutter_test/flutter_test.dart';
import 'package:overunder/models/models.dart';

Map<String, dynamic> _camelMarket({
  String cid = '0xaa',
  int marketType = 0,
  int closeTime = 1700000000,
  bool resolved = false,
  Object? tradingHaltsAt = 1700000000,
  bool tradingOpen = true,
}) =>
    {
      'conditionId': cid,
      'parentConditionId': null,
      'question': 'Will the Chiefs cover -3.5?',
      'resolutionCriteria': 'Final score from the NFL feed.',
      'marketType': marketType,
      'closeTime': closeTime,
      'paused': false,
      'resolved': resolved,
      'payoutYes': 0,
      'payoutNo': 0,
      'suggestedProbability': 0.62,
      'tradingHaltsAt': tradingHaltsAt,
      'tradingOpen': tradingOpen,
      'yesPriceMicros': 575000,
    };

DateTime _at(int unixSeconds) => DateTime.fromMillisecondsSinceEpoch(unixSeconds * 1000);

void main() {
  group('Market.fromJson', () {
    test('reads the camelCase API payload', () {
      final m = Market.fromJson(_camelMarket());

      expect(m.conditionId, '0xaa');
      expect(m.parentConditionId, '');
      expect(m.question, 'Will the Chiefs cover -3.5?');
      expect(m.resolutionCriteria, 'Final score from the NFL feed.');
      expect(m.marketType, 0);
      expect(m.closeTime, 1700000000);
      expect(m.paused, isFalse);
      expect(m.resolved, isFalse);
      expect(m.suggestedProbability, 0.62);
      expect(m.tradingHaltsAt, 1700000000);
      expect(m.tradingOpen, isTrue);
      expect(m.yesPriceMicros, 575000);
      expect(m.creator, isNull);
      expect(m.children, isEmpty);
    });

    test('falls back to snake_case keys', () {
      final m = Market.fromJson({
        'condition_id': '0xbb',
        'parent_condition_id': '0xaa',
        'question': 'Longest TD over 40.5 yards?',
        'resolution_criteria': 'Official play-by-play.',
        'market_type': 1,
        'close_time': 1800000000,
        'paused': true,
        'resolved': true,
        'payout_yes': 1,
        'payout_no': 0,
        'suggested_probability': 0.4,
        'trading_halts_at': 1800000000,
        'trading_open': false,
        'yes_price_micros': 410000,
      });

      expect(m.conditionId, '0xbb');
      expect(m.parentConditionId, '0xaa');
      expect(m.resolutionCriteria, 'Official play-by-play.');
      expect(m.marketType, 1);
      expect(m.closeTime, 1800000000);
      expect(m.paused, isTrue);
      expect(m.resolved, isTrue);
      expect(m.payoutYes, 1);
      expect(m.suggestedProbability, 0.4);
      expect(m.tradingHaltsAt, 1800000000);
      expect(m.tradingOpen, isFalse);
      expect(m.yesPriceMicros, 410000);
    });

    test('prefers camelCase over snake_case when both are present', () {
      final m = Market.fromJson({
        'conditionId': '0xcamel',
        'condition_id': '0xsnake',
        'closeTime': 5,
        'close_time': 9,
      });

      expect(m.conditionId, '0xcamel');
      expect(m.closeTime, 5);
    });

    test('defaults missing fields', () {
      final m = Market.fromJson(<String, dynamic>{}, haltAtCloseFallback: false);

      expect(m.conditionId, '');
      expect(m.closeTime, 0);
      expect(m.suggestedProbability, 0.5);
      expect(m.tradingHaltsAt, isNull);
      expect(m.tradingOpen, isTrue);
      expect(m.yesPriceMicros, isNull);
    });

    test('coerces numeric strings and doubles', () {
      final m = Market.fromJson({
        'closeTime': '1700000000',
        'marketType': 2.0,
        'suggestedProbability': '0.25',
        'tradingHaltsAt': 1700000000.0,
      });

      expect(m.closeTime, 1700000000);
      expect(m.marketType, 2);
      expect(m.suggestedProbability, 0.25);
      expect(m.tradingHaltsAt, 1700000000);
    });

    test('halt fallback applies only when the API omits tradingHaltsAt', () {
      final legacy = _camelMarket()..remove('tradingHaltsAt');
      expect(Market.fromJson(legacy, haltAtCloseFallback: true).tradingHaltsAt, 1700000000);
      expect(Market.fromJson(legacy, haltAtCloseFallback: false).tradingHaltsAt, isNull);

      // An explicit null from the server means the halt flag is off.
      final flagOff = _camelMarket(tradingHaltsAt: null);
      expect(Market.fromJson(flagOff, haltAtCloseFallback: true).tradingHaltsAt, isNull);

      // closeTime 0 never produces a halt.
      final noClose = _camelMarket(closeTime: 0)..remove('tradingHaltsAt');
      expect(Market.fromJson(noClose, haltAtCloseFallback: true).tradingHaltsAt, isNull);
    });

    test('parses creator and detail children', () {
      final m = Market.fromJson({
        ..._camelMarket(marketType: 2),
        'creator': '0x1234567890abcdef1234567890abcdef12345678',
        'children': [_camelMarket(cid: '0xc1', marketType: 1)],
      });

      expect(m.creator, '0x1234567890abcdef1234567890abcdef12345678');
      expect(m.children.single.conditionId, '0xc1');
      expect(Market.fromJson({..._camelMarket(), 'creator': ''}).creator, isNull);
    });
  });

  group('Market helpers', () {
    test('types 0 and 2 are primaries, 1 is a wildcard', () {
      expect(Market.fromJson(_camelMarket(marketType: 0)).isPrimary, isTrue);
      expect(Market.fromJson(_camelMarket(marketType: 2)).isPrimary, isTrue);
      expect(Market.fromJson(_camelMarket(marketType: 2)).isUserListed, isTrue);
      final wildcard = Market.fromJson(_camelMarket(marketType: 1));
      expect(wildcard.isPrimary, isFalse);
      expect(wildcard.isWildcard, isTrue);
    });

    test('yesProbability prefers the indexed price', () {
      expect(Market.fromJson(_camelMarket()).yesProbability, closeTo(0.575, 1e-9));
      final noPrice = _camelMarket()..remove('yesPriceMicros');
      expect(Market.fromJson(noPrice).yesProbability, 0.62);
    });
  });

  group('Market.isTradingClosed', () {
    const haltsAt = 1700000000;

    test('open before tradingHaltsAt, closed at and after it', () {
      final m = Market.fromJson(_camelMarket(tradingHaltsAt: haltsAt));
      expect(m.isTradingClosed(_at(haltsAt - 1)), isFalse);
      expect(m.isTradingClosed(_at(haltsAt)), isTrue);
      expect(m.isTradingClosed(_at(haltsAt + 3600)), isTrue);
    });

    test('never closes on time when tradingHaltsAt is null or 0', () {
      expect(Market.fromJson(_camelMarket(tradingHaltsAt: null)).isTradingClosed(_at(haltsAt * 2)), isFalse);
      expect(Market.fromJson(_camelMarket(tradingHaltsAt: 0)).isTradingClosed(_at(haltsAt * 2)), isFalse);
    });

    test('resolved markets are always closed', () {
      final m = Market.fromJson(_camelMarket(resolved: true, tradingHaltsAt: null));
      expect(m.isTradingClosed(_at(0)), isTrue);
    });

    test('a server tradingOpen false closes it regardless of the local clock', () {
      final m = Market.fromJson(_camelMarket(tradingHaltsAt: haltsAt, tradingOpen: false));
      expect(m.isTradingClosed(_at(haltsAt - 600)), isTrue);
    });
  });

  group('EventCard', () {
    final payload = [
      {
        'primary': _camelMarket(cid: '0xp1'),
        'children': [
          {..._camelMarket(cid: '0xw1', marketType: 1), 'parentConditionId': '0xp1'},
          {..._camelMarket(cid: '0xw2', marketType: 1), 'parentConditionId': '0xp1'},
        ],
      },
      {'primary': _camelMarket(cid: '0xu1', marketType: 2), 'children': <dynamic>[]},
    ];

    test('parses {primary, children} cards', () {
      final cards = EventCard.listFromJson(payload);

      expect(cards, hasLength(2));
      expect(cards[0].primary.conditionId, '0xp1');
      expect(cards[0].children.map((c) => c.conditionId), ['0xw1', '0xw2']);
      expect(cards[0].children.first.parentConditionId, '0xp1');
      expect(cards[0].primary.children, hasLength(2));
      expect(cards[1].primary.isUserListed, isTrue);
      expect(cards[1].children, isEmpty);
    });

    test('flattens each primary followed by its wildcards', () {
      final flat = flattenEventCards(EventCard.listFromJson(payload));
      expect(flat.map((m) => m.conditionId), ['0xp1', '0xw1', '0xw2', '0xu1']);
    });

    test('accepts bare market rows from older APIs or parentId listings', () {
      final cards = EventCard.listFromJson([
        _camelMarket(cid: '0xw9', marketType: 1),
      ]);
      expect(cards.single.primary.conditionId, '0xw9');
      expect(cards.single.children, isEmpty);
    });

    test('rejects a non-list payload', () {
      expect(() => EventCard.listFromJson({'detail': 'nope'}), throwsFormatException);
    });
  });

  group('AmmQuote.fromJson', () {
    test('reads a buy quote', () {
      final q = AmmQuote.fromJson({'conditionId': '0xaa', 'buyYes': true, 'usdcIn': 5000000, 'tokensOut': 8123456});
      expect(q.tokensOut, 8123456);
      expect(q.usdcOut, 0);
      expect(q.simulated, isFalse);
    });

    test('reads a sell quote from usdcOut', () {
      final q = AmmQuote.fromJson({'sellYes': false, 'tokenAmount': 1000000, 'usdcOut': 612345});
      expect(q.usdcOut, 612345);
      expect(q.tokensOut, 0);
    });

    test('falls back to snake_case and the legacy usdc key', () {
      expect(AmmQuote.fromJson({'usdc_out': 7, 'tokens_out': 9}).usdcOut, 7);
      expect(AmmQuote.fromJson({'usdc_out': 7, 'tokens_out': 9}).tokensOut, 9);
      expect(AmmQuote.fromJson({'usdc': 11, 'simulated': true}).usdcOut, 11);
      expect(AmmQuote.fromJson({'usdc': 11, 'simulated': true}).simulated, isTrue);
    });
  });

  group('OracleStatus.fromJson', () {
    Map<String, dynamic> att(String agent, int outcome) => {
          'agent': agent,
          'outcome': outcome,
          'summary': '$agent says YES',
          'evidenceHash': '0x${agent.codeUnitAt(0).toRadixString(16).padLeft(64, '0')}',
        };

    test('reads the API attestations shape and derives the unanimous outcome', () {
      final s = OracleStatus.fromJson({
        'conditionId': '0xabc',
        'attestations': [att('gpt', 0), att('claude', 0), att('gemini', 0)],
        'votes': [
          {'voter': '0x01', 'outcome': 1, 'weight': 5},
        ],
        'unanimous': true,
      });
      expect(s.conditionId, '0xabc');
      expect(s.attestations.length, 3);
      expect(s.unanimous, isTrue);
      expect(s.outcome, 0);
      expect(s.attestations.first.agent, 'gpt');
      expect(s.attestations.first.summary, 'gpt says YES');
      expect(s.attestations.every((a) => a.evidenceHash.startsWith('0x')), isTrue);
      expect(s.votes.single.voter, '0x01');
      expect(s.votes.single.outcome, 1);
      expect(s.votes.single.weight, 5);
    });

    test('no attestations gives an empty list and no outcome', () {
      final s = OracleStatus.fromJson({
        'conditionId': '0xabc',
        'attestations': [],
        'votes': [],
        'unanimous': false,
      });
      expect(s.attestations, isEmpty);
      expect(s.votes, isEmpty);
      expect(s.outcome, isNull);
      expect(s.unanimous, isFalse);
    });

    test('split attestations are not unanimous and carry no outcome', () {
      final s = OracleStatus.fromJson({
        'attestations': [att('gpt', 0), att('claude', 1)],
        'unanimous': false,
      });
      expect(s.attestations.length, 2);
      expect(s.outcome, isNull);
    });

    test('accepts the legacy reports key and snake_case evidence_hash', () {
      final s = OracleStatus.fromJson({
        'reports': [
          {'agent': 'gpt', 'outcome': '1', 'summary': null, 'evidence_hash': '0xee'},
        ],
      });
      expect(s.attestations.single.outcome, 1);
      expect(s.attestations.single.summary, '');
      expect(s.attestations.single.evidenceHash, '0xee');
      expect(s.attestations.single.kind, 'resolution');
      expect(s.unanimous, isFalse);
    });

    test('research rows are not resolution evidence; a missing kind counts as resolution', () {
      Map<String, dynamic> research(String agent) => {...att(agent, 2), 'kind': 'research'};
      final s = OracleStatus.fromJson({
        'conditionId': '0xabc',
        // Older research attempts come first (the API orders by id).
        'attestations': [
          research('alpha'),
          research('beta'),
          {...att('alpha', 1), 'kind': 'resolution'},
          {...att('beta', 1), 'kind': 'RESOLUTION'},
          att('gamma', 1),
        ],
        'unanimous': true,
      });
      expect(s.attestations.map((a) => a.agent), ['alpha', 'beta', 'gamma']);
      expect(s.attestations.every((a) => !a.isResearch && a.outcome == 1), isTrue);
      expect(s.attestations.map((a) => a.kind).toSet(), {'resolution'});
      // The derived outcome comes from resolution rows, never a research row's outcome 2.
      expect(s.outcome, 1);
    });

    test('only research rows leave no displayed evidence and no outcome', () {
      final s = OracleStatus.fromJson({
        'attestations': [
          {...att('alpha', 2), 'kind': 'research'},
        ],
        'unanimous': false,
      });
      expect(s.attestations, isEmpty);
      expect(s.outcome, isNull);
      expect(Attestation.fromJson({...att('alpha', 2), 'kind': 'research'}).isResearch, isTrue);
    });
  });
}
