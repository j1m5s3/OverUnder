import 'dart:convert';
import 'package:flutter/services.dart';

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

  static Future<Deployments> load(int chainId) async {
    try {
      final jsonString = await rootBundle.loadString('assets/deployments/$chainId.json');
      final Map<String, dynamic> data = json.decode(jsonString);
      return Deployments.fromJson(data);
    } catch (e) {
      // Return empty deployments if file not found
      return Deployments(chainId: chainId);
    }
  }
}
