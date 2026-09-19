import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../../models/models.dart';
import '../../services/api_client.dart';
import '../../theme/app_theme.dart';

class AmmSwapWidget extends StatefulWidget {
  final ApiClient apiClient;
  final String marketId;

  const AmmSwapWidget({
    super.key,
    required this.apiClient,
    required this.marketId,
  });

  @override
  State<AmmSwapWidget> createState() => _AmmSwapWidgetState();
}

class _AmmSwapWidgetState extends State<AmmSwapWidget> {
  final _amountController = TextEditingController();
  bool _isBuy = true;
  bool _isYes = true;
  AmmQuote? _quote;
  bool _quoting = false;
  String? _error;

  @override
  void dispose() {
    _amountController.dispose();
    super.dispose();
  }

  Future<void> _getQuote() async {
    final amountText = _amountController.text;
    if (amountText.isEmpty) {
      setState(() {
        _quote = null;
        _error = null;
      });
      return;
    }

    try {
      final amount = int.parse(amountText);
      setState(() {
        _quoting = true;
        _error = null;
      });

      final quote = await widget.apiClient.getAmmQuote(
        marketId: widget.marketId,
        isBuy: _isBuy,
        amount: amount,
      );

      setState(() {
        _quote = quote;
        _quoting = false;
      });
    } catch (e) {
      setState(() {
        _error = e.toString();
        _quoting = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.spacingMd),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'AMM Swap',
              style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                    fontWeight: AppTheme.weightBold,
                  ),
            ),
            const SizedBox(height: AppTheme.spacingMd),
            // Buy/Sell Toggle
            Row(
              children: [
                Expanded(
                  child: SegmentedButton<bool>(
                    segments: const [
                      ButtonSegment(value: true, label: Text('Buy')),
                      ButtonSegment(value: false, label: Text('Sell')),
                    ],
                    selected: {_isBuy},
                    onSelectionChanged: (Set<bool> selection) {
                      setState(() {
                        _isBuy = selection.first;
                        _quote = null;
                      });
                    },
                  ),
                ),
              ],
            ),
            const SizedBox(height: AppTheme.spacingMd),
            // Yes/No Toggle
            Row(
              children: [
                Expanded(
                  child: SegmentedButton<bool>(
                    segments: [
                      ButtonSegment(
                        value: true,
                        label: Text('Yes', style: TextStyle(color: AppTheme.yes)),
                      ),
                      ButtonSegment(
                        value: false,
                        label: Text('No', style: TextStyle(color: AppTheme.no)),
                      ),
                    ],
                    selected: {_isYes},
                    onSelectionChanged: (Set<bool> selection) {
                      setState(() {
                        _isYes = selection.first;
                      });
                    },
                  ),
                ),
              ],
            ),
            const SizedBox(height: AppTheme.spacingMd),
            // Amount Input
            TextField(
              controller: _amountController,
              decoration: InputDecoration(
                labelText: _isBuy ? 'USDC Amount' : 'Token Amount',
                border: const OutlineInputBorder(),
                suffixIcon: IconButton(
                  icon: const Icon(Icons.search),
                  onPressed: _getQuote,
                ),
              ),
              keyboardType: TextInputType.number,
              inputFormatters: [FilteringTextInputFormatter.digitsOnly],
              onChanged: (_) => setState(() => _quote = null),
            ),
            const SizedBox(height: AppTheme.spacingMd),
            // Quote Display
            if (_quoting)
              const Center(child: CircularProgressIndicator())
            else if (_error != null)
              Text('Error: $_error', style: TextStyle(color: AppTheme.no))
            else if (_quote != null)
              Container(
                padding: const EdgeInsets.all(AppTheme.spacingMd),
                decoration: BoxDecoration(
                  color: AppTheme.bgElevated,
                  borderRadius: BorderRadius.circular(AppTheme.radiusMd),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(
                          _isBuy ? 'You receive:' : 'You pay:',
                          style: TextStyle(color: AppTheme.textMuted),
                        ),
                        Text(
                          _isBuy
                              ? '${_quote!.tokensOut} tokens'
                              : '${_quote!.usdc} USDC',
                          style: const TextStyle(fontWeight: AppTheme.weightBold),
                        ),
                      ],
                    ),
                    if (_quote!.simulated)
                      Padding(
                        padding: const EdgeInsets.only(top: AppTheme.spacingSm),
                        child: Text(
                          'Simulated quote',
                          style: TextStyle(
                            color: AppTheme.warning,
                            fontSize: AppTheme.sizeSm,
                          ),
                        ),
                      ),
                  ],
                ),
              ),
            const SizedBox(height: AppTheme.spacingMd),
            // Execute Button
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: _quote != null
                    ? () {
                        // TODO: Execute swap via wallet
                        ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(
                            content: Text('Swap execution requires wallet integration'),
                          ),
                        );
                      }
                    : null,
                child: Text(_isBuy ? 'Buy' : 'Sell'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
