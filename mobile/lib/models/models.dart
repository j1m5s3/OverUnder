class Market {
  final String conditionId;
  final String question;
  final String parentConditionId;
  final int marketType;
  final int closeTime;
  final bool paused;
  final bool resolved;
  final double suggestedProbability;

  Market({
    required this.conditionId,
    required this.question,
    required this.parentConditionId,
    required this.marketType,
    required this.closeTime,
    required this.paused,
    required this.resolved,
    required this.suggestedProbability,
  });

  factory Market.fromJson(Map<String, dynamic> json) {
    return Market(
      conditionId: json['condition_id'] ?? '',
      question: json['question'] ?? '',
      parentConditionId: json['parent_condition_id'] ?? '',
      marketType: json['market_type'] ?? 0,
      closeTime: json['close_time'] ?? 0,
      paused: json['paused'] ?? false,
      resolved: json['resolved'] ?? false,
      suggestedProbability: (json['suggested_probability'] ?? 0.5).toDouble(),
    );
  }

  bool get isPrimary => marketType == 0;
  bool get isWildcard => marketType == 1;
}

class AmmQuote {
  final int tokensOut;
  final int usdc;
  final bool simulated;

  AmmQuote({
    required this.tokensOut,
    required this.usdc,
    required this.simulated,
  });

  factory AmmQuote.fromJson(Map<String, dynamic> json) {
    return AmmQuote(
      tokensOut: json['tokensOut'] ?? 0,
      usdc: json['usdc'] ?? 0,
      simulated: json['simulated'] ?? false,
    );
  }
}

class OracleStatus {
  final bool unanimous;
  final int? outcome;
  final List<Attestation> reports;

  OracleStatus({
    required this.unanimous,
    this.outcome,
    required this.reports,
  });

  factory OracleStatus.fromJson(Map<String, dynamic> json) {
    return OracleStatus(
      unanimous: json['unanimous'] ?? false,
      outcome: json['outcome'],
      reports: (json['reports'] as List?)
              ?.map((r) => Attestation.fromJson(r))
              .toList() ??
          [],
    );
  }
}

class Attestation {
  final String agent;
  final int outcome;
  final String summary;
  final String evidenceHash;

  Attestation({
    required this.agent,
    required this.outcome,
    required this.summary,
    required this.evidenceHash,
  });

  factory Attestation.fromJson(Map<String, dynamic> json) {
    return Attestation(
      agent: json['agent'] ?? '',
      outcome: json['outcome'] ?? 0,
      summary: json['summary'] ?? '',
      evidenceHash: json['evidenceHash'] ?? '',
    );
  }
}
