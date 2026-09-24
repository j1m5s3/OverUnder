import 'package:flutter/material.dart';
import '../../models/models.dart';
import '../../services/api_client.dart';
import '../../theme/app_theme.dart';
import 'market_detail_screen.dart';
import 'market_tag.dart';

class MarketListScreen extends StatefulWidget {
  final ApiClient apiClient;

  const MarketListScreen({super.key, required this.apiClient});

  @override
  State<MarketListScreen> createState() => _MarketListScreenState();
}

class _MarketListScreenState extends State<MarketListScreen> {
  List<EventCard> _cards = [];
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
      final cards = await widget.apiClient.getEventCards();
      if (!mounted) return;
      setState(() {
        _cards = cards;
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

  void _openMarket(Market market) {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) => MarketDetailScreen(
          apiClient: widget.apiClient,
          marketId: market.conditionId,
        ),
      ),
    );
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
                      Text('Error: $_error', style: const TextStyle(color: AppTheme.no)),
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
                  child: _cards.isEmpty
                      ? ListView(
                          padding: const EdgeInsets.all(AppTheme.spacingMd),
                          children: const [
                            Center(
                              child: Text(
                                'No markets yet',
                                style: TextStyle(color: AppTheme.textMuted),
                              ),
                            ),
                          ],
                        )
                      : ListView.builder(
                          padding: const EdgeInsets.all(AppTheme.spacingMd),
                          itemCount: _cards.length,
                          itemBuilder: (context, index) {
                            final card = _cards[index];
                            return MarketCard(
                              market: card.primary,
                              wildcards: card.children,
                              onTap: () => _openMarket(card.primary),
                              onWildcardTap: _openMarket,
                            );
                          },
                        ),
                ),
    );
  }
}

/// An event card: the primary market with its wildcards listed underneath.
class MarketCard extends StatelessWidget {
  final Market market;
  final VoidCallback onTap;
  final List<Market> wildcards;
  final ValueChanged<Market>? onWildcardTap;

  const MarketCard({
    super.key,
    required this.market,
    required this.onTap,
    this.wildcards = const [],
    this.onWildcardTap,
  });

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    final tags = marketTags(market, now);

    return Card(
      margin: const EdgeInsets.only(bottom: AppTheme.spacingMd),
      clipBehavior: Clip.antiAlias,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          InkWell(
            onTap: onTap,
            child: Padding(
              padding: const EdgeInsets.all(AppTheme.spacingMd),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    market.question,
                    style: Theme.of(context).textTheme.bodyLarge,
                  ),
                  const SizedBox(height: AppTheme.spacingSm),
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          'Probability: ${(market.yesProbability * 100).toStringAsFixed(1)}%',
                          style: const TextStyle(color: AppTheme.textMuted, fontSize: AppTheme.sizeSm),
                        ),
                      ),
                      if (tags.isNotEmpty)
                        Wrap(
                          spacing: AppTheme.spacingXs,
                          children: tags,
                        ),
                    ],
                  ),
                ],
              ),
            ),
          ),
          if (wildcards.isNotEmpty) ...[
            const Divider(height: 1, color: AppTheme.border),
            for (final wildcard in wildcards)
              InkWell(
                onTap: onWildcardTap == null ? null : () => onWildcardTap!(wildcard),
                child: Padding(
                  padding: const EdgeInsets.symmetric(
                    horizontal: AppTheme.spacingMd,
                    vertical: AppTheme.spacingSm,
                  ),
                  child: Row(
                    children: [
                      const Icon(Icons.subdirectory_arrow_right, size: 16, color: AppTheme.textMuted),
                      const SizedBox(width: AppTheme.spacingSm),
                      Expanded(
                        child: Text(
                          wildcard.question,
                          style: Theme.of(context).textTheme.bodyMedium,
                        ),
                      ),
                      const SizedBox(width: AppTheme.spacingSm),
                      Text(
                        '${(wildcard.yesProbability * 100).toStringAsFixed(0)}%',
                        style: const TextStyle(color: AppTheme.textMuted, fontSize: AppTheme.sizeSm),
                      ),
                      if (wildcard.resolved || wildcard.isTradingClosed(now)) ...[
                        const SizedBox(width: AppTheme.spacingXs),
                        Icon(
                          wildcard.resolved ? Icons.check_circle : Icons.lock_clock,
                          size: 16,
                          color: wildcard.resolved ? AppTheme.yes : AppTheme.warning,
                        ),
                      ],
                    ],
                  ),
                ),
              ),
          ],
        ],
      ),
    );
  }
}
