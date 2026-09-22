import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../models/models.dart';
import '../../services/api_client.dart';
import '../../providers/wallet_provider.dart';
import '../../theme/app_theme.dart';
import '../trade/amm_swap_widget.dart';
import '../oracle/oracle_status_widget.dart';

class MarketDetailScreen extends StatefulWidget {
  final ApiClient apiClient;
  final String marketId;

  const MarketDetailScreen({
    super.key,
    required this.apiClient,
    required this.marketId,
  });

  @override
  State<MarketDetailScreen> createState() => _MarketDetailScreenState();
}

class _MarketDetailScreenState extends State<MarketDetailScreen> {
  Market? _market;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadMarket();
  }

  Future<void> _loadMarket() async {
    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final market = await widget.apiClient.getMarket(widget.marketId);
      setState(() {
        _market = market;
        _loading = false;
      });
    } catch (e) {
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final walletProvider = context.watch<WalletProvider>();

    return Scaffold(
      appBar: AppBar(
        title: const Text('Market Details'),
        backgroundColor: AppTheme.bgElevated,
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Text('Error: $_error', style: TextStyle(color: AppTheme.no)),
                      const SizedBox(height: AppTheme.spacingMd),
                      ElevatedButton(
                        onPressed: _loadMarket,
                        child: const Text('Retry'),
                      ),
                    ],
                  ),
                )
              : SingleChildScrollView(
                  padding: const EdgeInsets.all(AppTheme.spacingMd),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Card(
                        child: Padding(
                          padding: const EdgeInsets.all(AppTheme.spacingMd),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                _market!.question,
                                style: Theme.of(context).textTheme.titleLarge,
                              ),
                              const SizedBox(height: AppTheme.spacingMd),
                              Row(
                                children: [
                                  if (_market!.isWildcard)
                                    Container(
                                      padding: const EdgeInsets.symmetric(
                                        horizontal: AppTheme.spacingSm,
                                        vertical: AppTheme.spacingXs,
                                      ),
                                      decoration: BoxDecoration(
                                        color: AppTheme.accent.withOpacity(0.2),
                                        borderRadius: BorderRadius.circular(AppTheme.radiusSm),
                                      ),
                                      child: Text(
                                        'Wildcard',
                                        style: TextStyle(
                                          color: AppTheme.accent,
                                          fontSize: AppTheme.sizeSm,
                                        ),
                                      ),
                                    ),
                                  const Spacer(),
                                  Text(
                                    'Prob: ${(_market!.suggestedProbability * 100).toStringAsFixed(1)}%',
                                    style: TextStyle(
                                      color: AppTheme.textMuted,
                                      fontSize: AppTheme.sizeSm,
                                    ),
                                  ),
                                ],
                              ),
                            ],
                          ),
                        ),
                      ),
                      const SizedBox(height: AppTheme.spacingLg),
                      // AMM Swap - only mount when wallet connected
                      if (walletProvider.isConnected)
                        AmmSwapWidget(
                          apiClient: widget.apiClient,
                          marketId: widget.marketId,
                          walletConnected: true,
                        )
                      else
                        Card(
                          child: Padding(
                            padding: const EdgeInsets.all(AppTheme.spacingMd),
                            child: Column(
                              children: [
                                Text(
                                  'AMM Swap',
                                  style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                                        fontWeight: AppTheme.weightBold,
                                      ),
                                ),
                                const SizedBox(height: AppTheme.spacingMd),
                                Text(
                                  'Connect wallet to trade',
                                  style: TextStyle(color: AppTheme.textMuted),
                                ),
                                const SizedBox(height: AppTheme.spacingMd),
                                ElevatedButton(
                                  onPressed: () {
                                    // Navigate to wallet screen
                                    DefaultTabController.of(context).animateTo(1);
                                  },
                                  child: const Text('Go to Wallet'),
                                ),
                              ],
                            ),
                          ),
                        ),
                      const SizedBox(height: AppTheme.spacingLg),
                      // Oracle Status
                      OracleStatusWidget(
                        apiClient: widget.apiClient,
                        marketId: widget.marketId,
                      ),
                    ],
                  ),
                ),
    );
  }
}
