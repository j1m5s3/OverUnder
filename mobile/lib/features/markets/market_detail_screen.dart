import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../models/models.dart';
import '../../services/api_client.dart';
import '../../providers/wallet_provider.dart';
import '../../theme/app_theme.dart';
import '../../utils/time.dart';
import '../trade/amm_swap_widget.dart';
import '../oracle/oracle_status_widget.dart';
import 'market_tag.dart';

String _shortAddress(String address) => address.length > 10
    ? '${address.substring(0, 6)}...${address.substring(address.length - 4)}'
    : address;

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
      if (!mounted) return;
      setState(() {
        _market = market;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
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
                      Text('Error: $_error', style: const TextStyle(color: AppTheme.no)),
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
                                  Expanded(
                                    child: Wrap(
                                      spacing: AppTheme.spacingSm,
                                      runSpacing: AppTheme.spacingXs,
                                      children: marketTags(_market!, DateTime.now()),
                                    ),
                                  ),
                                  Text(
                                    'Prob: ${(_market!.yesProbability * 100).toStringAsFixed(1)}%',
                                    style: const TextStyle(
                                      color: AppTheme.textMuted,
                                      fontSize: AppTheme.sizeSm,
                                    ),
                                  ),
                                ],
                              ),
                              if (_market!.closeTime > 0) ...[
                                const SizedBox(height: AppTheme.spacingSm),
                                Text(
                                  'Closes ${formatUnixSeconds(_market!.closeTime)}',
                                  style: const TextStyle(
                                    color: AppTheme.textMuted,
                                    fontSize: AppTheme.sizeSm,
                                  ),
                                ),
                              ],
                              if (_market!.creator != null) ...[
                                const SizedBox(height: AppTheme.spacingXs),
                                Text(
                                  'Listed by ${_shortAddress(_market!.creator!)}',
                                  style: const TextStyle(
                                    color: AppTheme.textMuted,
                                    fontSize: AppTheme.sizeSm,
                                  ),
                                ),
                              ],
                              if (_market!.resolutionCriteria.isNotEmpty) ...[
                                const SizedBox(height: AppTheme.spacingMd),
                                Text(
                                  'Resolution criteria',
                                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                                        fontWeight: AppTheme.weightBold,
                                      ),
                                ),
                                const SizedBox(height: AppTheme.spacingXs),
                                Text(
                                  _market!.resolutionCriteria,
                                  style: const TextStyle(
                                    color: AppTheme.textMuted,
                                    fontSize: AppTheme.sizeSm,
                                  ),
                                ),
                              ],
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
                          market: _market,
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
                                const Text(
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
