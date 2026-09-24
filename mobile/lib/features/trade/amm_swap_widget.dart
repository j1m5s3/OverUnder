import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:web3dart/crypto.dart';
import 'package:web3dart/web3dart.dart';
import '../../models/models.dart';
import '../../models/deployments.dart';
import '../../services/api_client.dart';
import '../../theme/app_theme.dart';
import '../../utils/time.dart';
import '../../utils/units.dart';

class AmmSwapWidget extends StatefulWidget {
  final ApiClient apiClient;
  final String marketId;
  final bool walletConnected;

  /// Drives the trading-closed state (resolved, or past `tradingHaltsAt`).
  /// Without it the widget relies on the API's 409 alone.
  final Market? market;

  const AmmSwapWidget({
    super.key,
    required this.apiClient,
    required this.marketId,
    this.walletConnected = false,
    this.market,
  });

  @override
  State<AmmSwapWidget> createState() => _AmmSwapWidgetState();
}

class _AmmSwapWidgetState extends State<AmmSwapWidget> {
  // Web timers longer than ~24.8 days fire at once, so long waits hop.
  static const _maxTimerWait = Duration(days: 24);

  final _amountController = TextEditingController();
  final _slippageController = TextEditingController(text: '0.5');
  bool _isBuy = true;
  bool _isYes = true;
  AmmQuote? _quote;
  // Base units (micro-USDC for buys, token units for sells) that _quote is for.
  int? _quotedAmount;
  int _quoteSeq = 0;
  bool _quoting = false;
  bool _executing = false;
  String? _error;
  String? _status;
  Deployments? _deployments;
  late Future<Deployments> _deploymentsFuture;
  Timer? _haltTimer;
  bool _serverClosed = false;
  String? _serverClosedReason;

  @override
  void initState() {
    super.initState();
    _loadDeployments();
    _scheduleHaltTimer();
  }

  @override
  void didUpdateWidget(covariant AmmSwapWidget oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.marketId != widget.marketId ||
        oldWidget.market?.tradingHaltsAt != widget.market?.tradingHaltsAt) {
      _scheduleHaltTimer();
    }
  }

  @override
  void dispose() {
    _haltTimer?.cancel();
    _amountController.dispose();
    _slippageController.dispose();
    super.dispose();
  }

  bool get _closed => _serverClosed || (widget.market?.isTradingClosed(DateTime.now()) ?? false);

  bool get _resolved => (widget.market?.resolved ?? false) || _serverClosedReason == 'market resolved';

  String get _closedLabel => _resolved ? 'Market resolved' : 'Trading closed';

  String get _closedMessage {
    if (_resolved) return 'Market resolved. Trading is closed.';
    final haltsAt = widget.market?.tradingHaltsAt;
    final at = haltsAt != null && haltsAt > 0 ? ' at ${formatUnixSeconds(haltsAt)}' : '';
    return 'Trading closed$at. Awaiting resolution.';
  }

  /// Rebuilds when the market reaches `tradingHaltsAt`, so an open screen
  /// flips to the closed state without a refresh.
  void _scheduleHaltTimer() {
    _haltTimer?.cancel();
    _haltTimer = null;
    final haltsAt = widget.market?.tradingHaltsAt;
    if (haltsAt == null || haltsAt <= 0 || _closed) return;
    final wait = DateTime.fromMillisecondsSinceEpoch(haltsAt * 1000).difference(DateTime.now());
    _haltTimer = Timer(wait < _maxTimerWait ? wait : _maxTimerWait, () {
      if (!mounted) return;
      setState(() {
        if (_closed) _invalidateQuote();
      });
      _scheduleHaltTimer();
    });
  }

  /// Drops the current quote and any quote request still in flight.
  void _invalidateQuote() {
    _quoteSeq++;
    _quote = null;
    _quotedAmount = null;
    _quoting = false;
  }

  void _markServerClosed(TradingClosedException e) {
    _serverClosed = true;
    _serverClosedReason = e.reason;
    _invalidateQuote();
    _error = null;
    _status = null;
    _haltTimer?.cancel();
  }

  /// Resolves trade targets, preferring the API's addresses (the ones
  /// cdp-send allowlists) over the bundled asset. Errors surface on execute.
  void _loadDeployments() {
    _deploymentsFuture = Deployments.resolve(fetchApiAddresses: widget.apiClient.getChainAddresses).then((resolved) {
      if (resolved.drift.isNotEmpty) {
        debugPrint(
          'Bundled assets/deployments/${resolved.deployments.chainId}.json is stale '
          '(${resolved.drift.join(', ')}); using the API addresses. '
          'Regenerate it with scripts/sync_mobile_deployments.py.',
        );
      }
      if (mounted) setState(() => _deployments = resolved.deployments);
      return resolved.deployments;
    });
    // Keep an unawaited failure from being reported as unhandled.
    _deploymentsFuture.ignore();
  }

  Future<void> _getQuote() async {
    if (_closed) return;

    final amountText = _amountController.text;
    if (amountText.trim().isEmpty) {
      setState(() {
        _invalidateQuote();
        _error = null;
      });
      return;
    }

    final amount = parseUnits(amountText);
    if (amount == null || amount <= 0) {
      setState(() {
        _invalidateQuote();
        _error = 'Enter an amount above 0 with at most $usdcDecimals decimals';
      });
      return;
    }

    final seq = ++_quoteSeq;
    setState(() {
      _quoting = true;
      _error = null;
    });

    try {
      final quote = await widget.apiClient.getAmmQuote(
        marketId: widget.marketId,
        isBuy: _isBuy,
        isYes: _isYes,
        amount: amount,
      );
      // Ignore a response for inputs the user has since changed.
      if (!mounted || seq != _quoteSeq) return;
      setState(() {
        _quote = quote;
        _quotedAmount = amount;
        _quoting = false;
      });
    } on TradingClosedException catch (e) {
      if (!mounted) return;
      setState(() => _markServerClosed(e));
    } catch (e) {
      if (!mounted || seq != _quoteSeq) return;
      setState(() {
        _error = e.toString();
        _quoting = false;
      });
    }
  }

  Future<void> _executeSwap() async {
    if (_closed) {
      setState(() {
        _error = null;
        _status = '$_closedLabel.';
      });
      return;
    }

    if (!widget.walletConnected) {
      setState(() {
        _error = 'Sign in to execute swaps.';
      });
      return;
    }

    if (_deployments == null) {
      try {
        _deployments = await _deploymentsFuture;
      } catch (e) {
        if (!mounted) return;
        setState(() {
          _error = 'Contract deployments not loaded: $e';
        });
        return;
      }
      if (!mounted) return;
    }

    if (_quote == null || _quotedAmount == null) {
      setState(() {
        _error = 'Get a quote first';
      });
      return;
    }

    final slippagePercent = parseSlippagePercent(_slippageController.text);
    if (slippagePercent == null) {
      setState(() {
        _error = 'Enter a slippage tolerance from 0 to 100%';
      });
      return;
    }

    setState(() {
      _executing = true;
      _error = null;
      _status = null;
    });

    try {
      if (_isBuy) {
        await _executeBuy(slippagePercent);
      } else {
        await _executeSell(slippagePercent);
      }
    } on TradingClosedException catch (e) {
      if (!mounted) return;
      setState(() {
        _markServerClosed(e);
        _executing = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = 'Transaction failed: $e';
        _status = null;
        _executing = false;
      });
    }
  }

  String _hexData(Uint8List bytes) => '0x${bytesToHex(bytes, include0x: false)}';

  Future<void> _executeBuy(double slippagePercent) async {
    final ammAddress = _deployments!.marketAmm;
    final usdcAddress = _deployments!.mockUsdc;

    if (ammAddress == null || usdcAddress == null) {
      throw Exception('AMM or USDC address not found in deployments');
    }

    if (_quote!.tokensOut <= 0) {
      throw Exception('Quote too small. Get a fresh quote.');
    }

    final usdcAmount = BigInt.from(_quotedAmount!);
    final minOut = applySlippage(_quote!.tokensOut, slippagePercent);
    if (minOut <= 0) {
      throw Exception('Quote too small or slippage too high. Get a fresh quote.');
    }

    setState(() => _status = 'Buying tokens...');

    final usdcContract = DeployedContract(
      ContractAbi.fromJson(
        '[{"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"}]',
        'USDC',
      ),
      EthereumAddress.fromHex(usdcAddress),
    );
    final ammContract = DeployedContract(
      ContractAbi.fromJson(
        '[{"inputs":[{"name":"conditionId","type":"bytes32"},{"name":"buyYes","type":"bool"},{"name":"usdcIn","type":"uint256"},{"name":"minOut","type":"uint256"}],"name":"buyWithUSDC","outputs":[{"name":"","type":"uint256"}],"stateMutability":"nonpayable","type":"function"}]',
        'MarketAMM',
      ),
      EthereumAddress.fromHex(ammAddress),
    );

    final approveData = usdcContract.function('approve').encodeCall([
      EthereumAddress.fromHex(ammAddress),
      usdcAmount,
    ]);
    final buyData = ammContract.function('buyWithUSDC').encodeCall([
      hexToBytes(widget.marketId),
      _isYes,
      usdcAmount,
      BigInt.from(minOut),
    ]);

    await widget.apiClient.cdpSend([
      {'to': usdcAddress, 'data': _hexData(approveData), 'value': 0},
      {'to': ammAddress, 'data': _hexData(buyData), 'value': 0},
    ]);

    if (!mounted) return;
    setState(() {
      _status = 'Success! Tokens received.';
      _executing = false;
      _invalidateQuote();
    });
  }

  Future<void> _executeSell(double slippagePercent) async {
    final ammAddress = _deployments!.marketAmm;
    final ctfAddress = _deployments!.conditionalTokens;

    if (ammAddress == null || ctfAddress == null) {
      throw Exception('AMM or CTF address not found in deployments');
    }

    if (_quote!.usdcOut <= 0) {
      throw Exception('Quote too small. Get a fresh quote.');
    }

    final minUsdc = applySlippage(_quote!.usdcOut, slippagePercent);
    if (minUsdc <= 0) {
      throw Exception('Quote too small or slippage too high. Get a fresh quote.');
    }

    setState(() => _status = 'Selling tokens...');

    final ctfContract = DeployedContract(
      ContractAbi.fromJson(
        '[{"inputs":[{"name":"operator","type":"address"},{"name":"approved","type":"bool"}],"name":"setApprovalForAll","outputs":[],"stateMutability":"nonpayable","type":"function"}]',
        'CTF',
      ),
      EthereumAddress.fromHex(ctfAddress),
    );
    final ammContract = DeployedContract(
      ContractAbi.fromJson(
        '[{"inputs":[{"name":"conditionId","type":"bytes32"},{"name":"sellYes","type":"bool"},{"name":"tokenAmount","type":"uint256"},{"name":"minUsdc","type":"uint256"}],"name":"sellToUSDC","outputs":[{"name":"","type":"uint256"}],"stateMutability":"nonpayable","type":"function"}]',
        'MarketAMM',
      ),
      EthereumAddress.fromHex(ammAddress),
    );

    final approveData = ctfContract.function('setApprovalForAll').encodeCall([
      EthereumAddress.fromHex(ammAddress),
      true,
    ]);
    final sellData = ammContract.function('sellToUSDC').encodeCall([
      hexToBytes(widget.marketId),
      _isYes,
      BigInt.from(_quotedAmount!),
      BigInt.from(minUsdc),
    ]);

    await widget.apiClient.cdpSend([
      {'to': ctfAddress, 'data': _hexData(approveData), 'value': 0},
      {'to': ammAddress, 'data': _hexData(sellData), 'value': 0},
    ]);

    if (!mounted) return;
    setState(() {
      _status = 'Success! USDC received.';
      _executing = false;
      _invalidateQuote();
    });
  }

  Uint8List hexToBytes(String hex) {
    if (hex.startsWith('0x')) hex = hex.substring(2);
    return Uint8List.fromList(
      List.generate(hex.length ~/ 2, (i) => int.parse(hex.substring(i * 2, i * 2 + 2), radix: 16)),
    );
  }

  String _minAfterSlippage(int amount, String unit) {
    final slippagePercent = parseSlippagePercent(_slippageController.text);
    if (slippagePercent == null) return '-';
    return '${formatUnits(applySlippage(amount, slippagePercent))} $unit';
  }

  @override
  Widget build(BuildContext context) {
    final closed = _closed;
    final haltsAt = widget.market?.tradingHaltsAt;

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
            if (closed)
              Container(
                width: double.infinity,
                margin: const EdgeInsets.only(bottom: AppTheme.spacingMd),
                padding: const EdgeInsets.all(AppTheme.spacingMd),
                decoration: BoxDecoration(
                  color: AppTheme.warning.withAlpha(51),
                  borderRadius: BorderRadius.circular(AppTheme.radiusMd),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.lock_clock, color: AppTheme.warning),
                    const SizedBox(width: AppTheme.spacingSm),
                    Expanded(
                      child: Text(
                        _closedMessage,
                        style: const TextStyle(color: AppTheme.warning),
                      ),
                    ),
                  ],
                ),
              )
            else if (haltsAt != null && haltsAt > 0)
              Padding(
                padding: const EdgeInsets.only(bottom: AppTheme.spacingMd),
                child: Text(
                  'Trading closes ${formatUnixSeconds(haltsAt)}',
                  style: const TextStyle(color: AppTheme.textMuted, fontSize: AppTheme.sizeSm),
                ),
              ),
            Row(
              children: [
                Expanded(
                  child: SegmentedButton<bool>(
                    segments: const [
                      ButtonSegment(value: true, label: Text('Buy')),
                      ButtonSegment(value: false, label: Text('Sell')),
                    ],
                    selected: {_isBuy},
                    onSelectionChanged: closed
                        ? null
                        : (Set<bool> selection) {
                            setState(() {
                              _isBuy = selection.first;
                              _invalidateQuote();
                            });
                          },
                  ),
                ),
              ],
            ),
            const SizedBox(height: AppTheme.spacingMd),
            Row(
              children: [
                Expanded(
                  child: SegmentedButton<bool>(
                    segments: const [
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
                    onSelectionChanged: closed
                        ? null
                        : (Set<bool> selection) {
                            setState(() {
                              _isYes = selection.first;
                              _invalidateQuote();
                            });
                          },
                  ),
                ),
              ],
            ),
            const SizedBox(height: AppTheme.spacingMd),
            TextField(
              controller: _amountController,
              enabled: !closed,
              decoration: InputDecoration(
                labelText: _isBuy ? 'USDC Amount' : 'Token Amount',
                border: const OutlineInputBorder(),
                suffixIcon: IconButton(
                  icon: const Icon(Icons.search),
                  onPressed: closed ? null : _getQuote,
                ),
              ),
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              inputFormatters: [DecimalAmountFormatter()],
              onChanged: (_) => setState(_invalidateQuote),
              onSubmitted: (_) => _getQuote(),
            ),
            const SizedBox(height: AppTheme.spacingMd),
            TextField(
              controller: _slippageController,
              enabled: !closed,
              decoration: const InputDecoration(
                labelText: 'Slippage tolerance (%)',
                border: OutlineInputBorder(),
              ),
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              onChanged: (_) => setState(() {}),
            ),
            const SizedBox(height: AppTheme.spacingMd),
            if (closed)
              const SizedBox.shrink()
            else if (_quoting)
              const Center(child: CircularProgressIndicator())
            else if (_error != null)
              Text('Error: $_error', style: const TextStyle(color: AppTheme.no))
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
                        const Text(
                          'You receive:',
                          style: TextStyle(color: AppTheme.textMuted),
                        ),
                        Text(
                          _isBuy
                              ? '${formatUnits(_quote!.tokensOut)} tokens'
                              : '${formatUnits(_quote!.usdcOut)} USDC',
                          style: const TextStyle(fontWeight: AppTheme.weightBold),
                        ),
                      ],
                    ),
                    const SizedBox(height: AppTheme.spacingXs),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        const Text(
                          'Min after slippage:',
                          style: TextStyle(color: AppTheme.textMuted, fontSize: AppTheme.sizeSm),
                        ),
                        Text(
                          _isBuy
                              ? _minAfterSlippage(_quote!.tokensOut, 'tokens')
                              : _minAfterSlippage(_quote!.usdcOut, 'USDC'),
                          style: const TextStyle(fontSize: AppTheme.sizeSm),
                        ),
                      ],
                    ),
                    if (_quote!.simulated)
                      const Padding(
                        padding: EdgeInsets.only(top: AppTheme.spacingSm),
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
            if (_status != null)
              Padding(
                padding: const EdgeInsets.only(bottom: AppTheme.spacingMd),
                child: Text(
                  _status!,
                  style: const TextStyle(color: AppTheme.accent, fontSize: AppTheme.sizeSm),
                ),
              ),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: (!closed && _quote != null && !_executing) ? _executeSwap : null,
                child: _executing
                    ? const SizedBox(
                        height: 20,
                        width: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : Text(closed ? _closedLabel : (_isBuy ? 'Buy' : 'Sell')),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
