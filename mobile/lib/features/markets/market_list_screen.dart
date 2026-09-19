import 'package:flutter/material.dart';
import '../models/models.dart';
import '../services/api_client.dart';
import '../theme/app_theme.dart';
import 'market_detail_screen.dart';

class MarketListScreen extends StatefulWidget {
  final ApiClient apiClient;

  const MarketListScreen({super.key, required this.apiClient});

  @override
  State<MarketListScreen> createState() => _MarketListScreenState();
}

class _MarketListScreenState extends State<MarketListScreen> {
  List<Market> _markets = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadMarkets();
  }

  Future<void> _loadMarkets() async {
    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final markets = await widget.apiClient.getMarkets();
      setState(() {
        _markets = markets;
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
    return Scaffold(
      appBar: AppBar(
        title: const Text('OverUnder'),
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
                        onPressed: _loadMarkets,
                        child: const Text('Retry'),
                      ),
                    ],
                  ),
                )
              : RefreshIndicator(
                  onRefresh: _loadMarkets,
                  child: ListView.builder(
                    padding: const EdgeInsets.all(AppTheme.spacingMd),
                    itemCount: _markets.length,
                    itemBuilder: (context, index) {
                      final market = _markets[index];
                      return MarketCard(
                        market: market,
                        onTap: () {
                          Navigator.push(
                            context,
                            MaterialPageRoute(
                              builder: (context) => MarketDetailScreen(
                                apiClient: widget.apiClient,
                                marketId: market.conditionId,
                              ),
                            ),
                          );
                        },
                      );
                    },
                  ),
                ),
    );
  }
}

class MarketCard extends StatelessWidget {
  final Market market;
  final VoidCallback onTap;

  const MarketCard({
    super.key,
    required this.market,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: AppTheme.spacingMd),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(AppTheme.radiusMd),
        child: Padding(
          padding: const EdgeInsets.all(AppTheme.spacingMd),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      market.question,
                      style: Theme.of(context).textTheme.bodyLarge,
                    ),
                  ),
                  if (market.isWildcard)
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
                ],
              ),
              const SizedBox(height: AppTheme.spacingSm),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    'Probability: ${(market.suggestedProbability * 100).toStringAsFixed(1)}%',
                    style: TextStyle(color: AppTheme.textMuted, fontSize: AppTheme.sizeSm),
                  ),
                  if (market.resolved)
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: AppTheme.spacingSm,
                        vertical: AppTheme.spacingXs,
                      ),
                      decoration: BoxDecoration(
                        color: AppTheme.yes.withOpacity(0.2),
                        borderRadius: BorderRadius.circular(AppTheme.radiusSm),
                      ),
                      child: Text(
                        'Resolved',
                        style: TextStyle(
                          color: AppTheme.yes,
                          fontSize: AppTheme.sizeSm,
                        ),
                      ),
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
