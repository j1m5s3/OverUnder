import '../config/app_config.dart';

// The API sends camelCase (backend MarketPublic); older payloads used
// snake_case. Every reader takes the camelCase key first, then the snake one.
dynamic _field(Map<String, dynamic> json, String camel, [String? snake]) {
  final value = json[camel];
  if (value != null || snake == null) return value;
  return json[snake];
}

bool _hasField(Map<String, dynamic> json, String camel, String snake) =>
    json.containsKey(camel) || json.containsKey(snake);

int? _intOrNull(dynamic value) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  if (value is String) return int.tryParse(value);
  return null;
}

int _int(dynamic value) => _intOrNull(value) ?? 0;

double _double(dynamic value, double fallback) {
  if (value is num) return value.toDouble();
  if (value is String) return double.tryParse(value) ?? fallback;
  return fallback;
}

bool _bool(dynamic value, bool fallback) {
  if (value is bool) return value;
  if (value is num) return value != 0;
  if (value is String) {
    if (value.toLowerCase() == 'true') return true;
    if (value.toLowerCase() == 'false') return false;
  }
  return fallback;
}

String _string(dynamic value) => value == null ? '' : value.toString();

List<Market> _markets(dynamic value, bool haltAtCloseFallback) {
  if (value is! List) return const [];
  return value
      .whereType<Map<String, dynamic>>()
      .map((m) => Market.fromJson(m, haltAtCloseFallback: haltAtCloseFallback))
      .toList();
}

class Market {
  final String conditionId;
  final String question;
  final String parentConditionId;
  final String resolutionCriteria;
  final int marketType;
  final int closeTime;
  final bool paused;
  final bool resolved;
  final int payoutYes;
  final int payoutNo;
  final double suggestedProbability;

  /// Unix seconds at which the API stops quoting and relaying trades; null
  /// when the server-side halt flag is off.
  final int? tradingHaltsAt;

  /// Server snapshot of whether trading was open when this row was fetched.
  final bool tradingOpen;

  /// Latest indexed YES price in micro-USDC (1000000 = 100%), when known.
  final int? yesPriceMicros;

  /// Lister of a user market (marketType 2), when the API reports one.
  final String? creator;

  /// Wildcards under a primary (event card or market detail).
  final List<Market> children;

  Market({
    required this.conditionId,
    required this.question,
    required this.parentConditionId,
    required this.marketType,
    required this.closeTime,
    required this.paused,
    required this.resolved,
    required this.suggestedProbability,
    this.resolutionCriteria = '',
    this.payoutYes = 0,
    this.payoutNo = 0,
    this.tradingHaltsAt,
    this.tradingOpen = true,
    this.yesPriceMicros,
    this.creator,
    this.children = const [],
  });

  /// [haltAtCloseFallback] applies only when the payload has no
  /// `tradingHaltsAt` key at all (an older API): trading then halts at
  /// `closeTime`. An explicit null from the server means no halt.
  factory Market.fromJson(
    Map<String, dynamic> json, {
    bool haltAtCloseFallback = AppConfig.tradingHaltAtClose,
  }) {
    final closeTime = _int(_field(json, 'closeTime', 'close_time'));
    final int? tradingHaltsAt = _hasField(json, 'tradingHaltsAt', 'trading_halts_at')
        ? _intOrNull(_field(json, 'tradingHaltsAt', 'trading_halts_at'))
        : (haltAtCloseFallback && closeTime > 0 ? closeTime : null);
    final creator = _field(json, 'creator');

    return Market(
      conditionId: _string(_field(json, 'conditionId', 'condition_id')),
      question: _string(_field(json, 'question')),
      parentConditionId: _string(_field(json, 'parentConditionId', 'parent_condition_id')),
      resolutionCriteria: _string(_field(json, 'resolutionCriteria', 'resolution_criteria')),
      marketType: _int(_field(json, 'marketType', 'market_type')),
      closeTime: closeTime,
      paused: _bool(_field(json, 'paused'), false),
      resolved: _bool(_field(json, 'resolved'), false),
      payoutYes: _int(_field(json, 'payoutYes', 'payout_yes')),
      payoutNo: _int(_field(json, 'payoutNo', 'payout_no')),
      suggestedProbability:
          _double(_field(json, 'suggestedProbability', 'suggested_probability'), 0.5),
      tradingHaltsAt: tradingHaltsAt,
      tradingOpen: _bool(_field(json, 'tradingOpen', 'trading_open'), true),
      yesPriceMicros: _intOrNull(_field(json, 'yesPriceMicros', 'yes_price_micros')),
      creator: creator == null || _string(creator).isEmpty ? null : _string(creator),
      children: _markets(json['children'], haltAtCloseFallback),
    );
  }

  Market withChildren(List<Market> children) => Market(
        conditionId: conditionId,
        question: question,
        parentConditionId: parentConditionId,
        resolutionCriteria: resolutionCriteria,
        marketType: marketType,
        closeTime: closeTime,
        paused: paused,
        resolved: resolved,
        payoutYes: payoutYes,
        payoutNo: payoutNo,
        suggestedProbability: suggestedProbability,
        tradingHaltsAt: tradingHaltsAt,
        tradingOpen: tradingOpen,
        yesPriceMicros: yesPriceMicros,
        creator: creator,
        children: children,
      );

  /// Operator primaries (0) and user-listed markets (2) head an event card.
  bool get isPrimary => marketType == 0 || marketType == 2;
  bool get isWildcard => marketType == 1;
  bool get isUserListed => marketType == 2;

  /// Live AMM price when the indexer has one, else the listing suggestion.
  double get yesProbability =>
      yesPriceMicros != null ? yesPriceMicros! / 1000000 : suggestedProbability;

  /// True once the market is resolved, the server reported trading closed,
  /// or [now] has reached [tradingHaltsAt].
  bool isTradingClosed(DateTime now) {
    if (resolved || !tradingOpen) return true;
    final haltsAt = tradingHaltsAt;
    return haltsAt != null && haltsAt > 0 && now.millisecondsSinceEpoch ~/ 1000 >= haltsAt;
  }
}

/// One row of `GET /api/v1/markets`: a primary and its wildcards.
class EventCard {
  final Market primary;
  final List<Market> children;

  EventCard({required this.primary, required this.children});

  /// Accepts `{primary, children}` and, for older APIs or `?parentId=`
  /// listings, a bare market row (which becomes a card with no children).
  factory EventCard.fromJson(
    Map<String, dynamic> json, {
    bool haltAtCloseFallback = AppConfig.tradingHaltAtClose,
  }) {
    final primaryJson = json['primary'];
    if (primaryJson is Map<String, dynamic>) {
      final children = _markets(json['children'], haltAtCloseFallback);
      final primary = Market.fromJson(primaryJson, haltAtCloseFallback: haltAtCloseFallback);
      return EventCard(primary: primary.withChildren(children), children: children);
    }
    final market = Market.fromJson(json, haltAtCloseFallback: haltAtCloseFallback);
    return EventCard(primary: market, children: market.children);
  }

  static List<EventCard> listFromJson(
    dynamic data, {
    bool haltAtCloseFallback = AppConfig.tradingHaltAtClose,
  }) {
    if (data is! List) {
      throw const FormatException('Expected a JSON list of markets');
    }
    return data
        .whereType<Map<String, dynamic>>()
        .map((c) => EventCard.fromJson(c, haltAtCloseFallback: haltAtCloseFallback))
        .toList();
  }
}

/// Every market of [cards] in list order, each primary followed by its wildcards.
List<Market> flattenEventCards(List<EventCard> cards) => [
      for (final card in cards) ...[card.primary, ...card.children],
    ];

class AmmQuote {
  final int tokensOut;
  final int usdcOut;
  final bool simulated;

  AmmQuote({
    required this.tokensOut,
    required this.usdcOut,
    required this.simulated,
  });

  /// Buy quotes carry `tokensOut`, sell quotes `usdcOut` (both 6-decimal base
  /// units); `usdc` is the pre-fix key.
  factory AmmQuote.fromJson(Map<String, dynamic> json) {
    return AmmQuote(
      tokensOut: _int(_field(json, 'tokensOut', 'tokens_out')),
      usdcOut: _int(_field(json, 'usdcOut', 'usdc_out') ?? json['usdc']),
      simulated: _bool(json['simulated'], false),
    );
  }
}

/// GET /api/v1/oracle/{conditionId}/status:
/// `{conditionId, attestations: [{agent, outcome, summary, evidenceHash,
/// createdAt, kind}], votes: [{voter, outcome, weight}], unanimous}`. The API
/// sends no top-level outcome; it is derived from unanimous attestations.
///
/// `kind: 'research'` rows are research attempts that did not resolve (they
/// drive the oracle's retry cooldown, often with outcome 2); they are not
/// resolution evidence, so [attestations] keeps only resolution rows. A missing
/// kind (older APIs) counts as 'resolution'.
class OracleStatus {
  final String conditionId;
  final bool unanimous;
  final int? outcome;
  final List<Attestation> attestations;
  final List<OracleVote> votes;

  OracleStatus({
    this.conditionId = '',
    required this.unanimous,
    this.outcome,
    required this.attestations,
    this.votes = const [],
  });

  factory OracleStatus.fromJson(Map<String, dynamic> json) {
    // `reports` is a legacy key; the API sends `attestations`.
    final attestations = _maps(json['attestations'] ?? json['reports'])
        .map(Attestation.fromJson)
        .where((a) => !a.isResearch)
        .toList();
    final unanimous = _bool(json['unanimous'], false);
    final outcome = unanimous && attestations.isNotEmpty
        ? attestations.first.outcome
        : _intOrNull(json['outcome']);
    return OracleStatus(
      conditionId: _string(_field(json, 'conditionId', 'condition_id')),
      unanimous: unanimous,
      outcome: outcome,
      attestations: attestations,
      votes: _maps(json['votes']).map(OracleVote.fromJson).toList(),
    );
  }
}

List<Map<String, dynamic>> _maps(dynamic value) {
  if (value is! List) return const [];
  return value.whereType<Map>().map((m) => Map<String, dynamic>.from(m)).toList();
}

class Attestation {
  static const kindResolution = 'resolution';
  static const kindResearch = 'research';

  final String agent;
  final int outcome;
  final String summary;
  final String evidenceHash;

  /// 'resolution' or 'research'; see [OracleStatus].
  final String kind;

  Attestation({
    required this.agent,
    required this.outcome,
    required this.summary,
    required this.evidenceHash,
    this.kind = kindResolution,
  });

  bool get isResearch => kind == kindResearch;

  factory Attestation.fromJson(Map<String, dynamic> json) {
    final kind = _string(json['kind']).trim().toLowerCase();
    return Attestation(
      agent: _string(json['agent']),
      outcome: _int(json['outcome']),
      summary: _string(json['summary']),
      evidenceHash: _string(_field(json, 'evidenceHash', 'evidence_hash')),
      kind: kind.isEmpty ? kindResolution : kind,
    );
  }
}

class OracleVote {
  final String voter;
  final int outcome;
  final int weight;

  OracleVote({required this.voter, required this.outcome, required this.weight});

  factory OracleVote.fromJson(Map<String, dynamic> json) {
    return OracleVote(
      voter: _string(json['voter']),
      outcome: _int(json['outcome']),
      weight: _int(json['weight']),
    );
  }
}
