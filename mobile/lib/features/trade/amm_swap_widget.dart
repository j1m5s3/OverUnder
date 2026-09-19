import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:web3dart/web3dart.dart';
import '../../models/models.dart';
import '../../models/deployments.dart';
import '../../services/api_client.dart';
import '../../theme/app_theme.dart';

class AmmSwapWidget extends StatefulWidget {
  final ApiClient apiClient;
  final String marketId;
  final Web3Client? web3Client;
  final Credentials? credentials;

  const AmmSwapWidget({
    super.key,
    required this.apiClient,
    required this.marketId,
    this.web3Client,
    this.credentials,
  });

  @override
  State<AmmSwapWidget> createState() => _AmmSwapWidgetState();
}

class _AmmSwapWidgetState extends State<AmmSwapWidget> {
  final _amountController = TextEditingController();
  final _slippageController = TextEditingController(text: '0.5');
  bool _isBuy = true;
  bool _isYes = true;
  AmmQuote? _quote;
  bool _quoting = false;
  bool _executing = false;
  String? _error;
  String? _status;
  Deployments? _deployments;

  @override
  void initState() {
    super.initState();
    _loadDeployments();
  }

  @override
  void dispose() {
    _amountController.dispose();
    _slippageController.dispose();
    super.dispose();
  }

  Future<void> _loadDeployments() async {
    try {
      final deployments = await Deployments.load(31337);
      setState(() {
        _deployments = deployments;
      });
    } catch (e) {
      // Silently fail - will show error when trying to execute
    }
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

  Future<void> _executeSwap() async {
    if (widget.credentials == null || widget.web3Client == null) {
      setState(() {
        _error = 'Wallet not connected. Connect wallet to execute swaps.';
      });
      return;
    }

    if (_deployments == null) {
      setState(() {
        _error = 'Contract deployments not loaded';
      });
      return;
    }

    if (_quote == null) {
      setState(() {
        _error = 'Get a quote first';
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
        await _executeBuy();
      } else {
        await _executeSell();
      }
    } catch (e) {
      setState(() {
        _error = 'Transaction failed: $e';
        _executing = false;
      });
    }
  }

  Future<void> _executeBuy() async {
    final ammAddress = _deployments!.marketAmm;
    final usdcAddress = _deployments!.mockUsdc;

    if (ammAddress == null || usdcAddress == null) {
      throw Exception('AMM or USDC address not found in deployments');
    }

    // Check quote validity
    if (_quote!.tokensOut <= 0) {
      throw Exception('Quote too small. Get a fresh quote.');
    }

    setState(() => _status = 'Checking USDC approval...');

    // Check and approve USDC if needed
    final usdcContract = DeployedContract(
      ContractAbi.fromJson(
        '[{"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"}]',
        'USDC',
      ),
      EthereumAddress.fromHex(usdcAddress),
    );

    final allowanceFunction = usdcContract.function('allowance');
    final allowance = await widget.web3Client!.call(
      contract: usdcContract,
      function: allowanceFunction,
      params: [
        widget.credentials!.address,
        EthereumAddress.fromHex(ammAddress),
      ],
    );

    final usdcAmount = BigInt.from(int.parse(_amountController.text));

    if ((allowance[0] as BigInt) < usdcAmount) {
      setState(() => _status = 'Approving USDC...');
      final approveFunction = usdcContract.function('approve');
      await widget.web3Client!.sendTransaction(
        widget.credentials!,
        Transaction.callContract(
          contract: usdcContract,
          function: approveFunction,
          parameters: [EthereumAddress.fromHex(ammAddress), usdcAmount],
        ),
        chainId: _deployments!.chainId,
      );
    }

    setState(() => _status = 'Buying tokens...');

    // Calculate minOut with slippage
    final slippagePercent = double.parse(_slippageController.text);
    final minOut = (_quote!.tokensOut * (100 - slippagePercent) / 100).floor();

    if (minOut <= 0) {
      throw Exception('Quote too small or slippage too high. Get a fresh quote.');
    }

    // Execute buy
    final ammContract = DeployedContract(
      ContractAbi.fromJson(
        '[{"inputs":[{"name":"conditionId","type":"bytes32"},{"name":"buyYes","type":"bool"},{"name":"usdcIn","type":"uint256"},{"name":"minOut","type":"uint256"}],"name":"buyWithUSDC","outputs":[{"type":"uint256"}],"stateMutability":"nonpayable","type":"function"}]',
        'MarketAMM',
      ),
      EthereumAddress.fromHex(ammAddress),
    );

    final buyFunction = ammContract.function('buyWithUSDC');
    await widget.web3Client!.sendTransaction(
      widget.credentials!,
      Transaction.callContract(
        contract: ammContract,
        function: buyFunction,
        parameters: [
          hexToBytes(widget.marketId),
          _isYes,
          usdcAmount,
          BigInt.from(minOut),
        ],
      ),
      chainId: _deployments!.chainId,
    );

    setState(() {
      _status = 'Success! Tokens received.';
      _executing = false;
      _quote = null;
    });
  }

  Future<void> _executeSell() async {
    final ammAddress = _deployments!.marketAmm;
    final ctfAddress = _deployments!.conditionalTokens;

    if (ammAddress == null || ctfAddress == null) {
      throw Exception('AMM or CTF address not found in deployments');
    }

    // Check quote validity
    if (_quote!.usdc <= 0) {
      throw Exception('Quote too small. Get a fresh quote.');
    }

    setState(() => _status = 'Checking token approval...');

    // Check and approve CTF if needed
    final ctfContract = DeployedContract(
      ContractAbi.fromJson(
        '[{"inputs":[{"name":"operator","type":"address"},{"name":"approved","type":"bool"}],"name":"setApprovalForAll","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"name":"account","type":"address"},{"name":"operator","type":"address"}],"name":"isApprovedForAll","outputs":[{"type":"bool"}],"stateMutability":"view","type":"function"}]',
        'CTF',
      ),
      EthereumAddress.fromHex(ctfAddress),
    );

    final isApprovedFunction = ctfContract.function('isApprovedForAll');
    final isApproved = await widget.web3Client!.call(
      contract: ctfContract,
      function: isApprovedFunction,
      params: [
        widget.credentials!.address,
        EthereumAddress.fromHex(ammAddress),
      ],
    );

    if (!(isApproved[0] as bool)) {
      setState(() => _status = 'Approving tokens...');
      final approveFunction = ctfContract.function('setApprovalForAll');
      await widget.web3Client!.sendTransaction(
        widget.credentials!,
        Transaction.callContract(
          contract: ctfContract,
          function: approveFunction,
          parameters: [EthereumAddress.fromHex(ammAddress), true],
        ),
        chainId: _deployments!.chainId,
      );
    }

    setState(() => _status = 'Selling tokens...');

    // Calculate minUsdc with slippage
    final slippagePercent = double.parse(_slippageController.text);
    final minUsdc = (_quote!.usdc * (100 - slippagePercent) / 100).floor();

    if (minUsdc <= 0) {
      throw Exception('Quote too small or slippage too high. Get a fresh quote.');
    }

    // Execute sell
    final ammContract = DeployedContract(
      ContractAbi.fromJson(
        '[{"inputs":[{"name":"conditionId","type":"bytes32"},{"name":"sellYes","type":"bool"},{"name":"tokenAmount","type":"uint256"},{"name":"minUsdc","type":"uint256"}],"name":"sellToUSDC","outputs":[{"type":"uint256"}],"stateMutability":"nonpayable","type":"function"}]',
        'MarketAMM',
      ),
      EthereumAddress.fromHex(ammAddress),
    );

    final sellFunction = ammContract.function('sellToUSDC');
    final tokenAmount = BigInt.from(int.parse(_amountController.text));

    await widget.web3Client!.sendTransaction(
      widget.credentials!,
      Transaction.callContract(
        contract: ammContract,
        function: sellFunction,
        parameters: [
          hexToBytes(widget.marketId),
          _isYes,
          tokenAmount,
          BigInt.from(minUsdc),
        ],
      ),
      chainId: _deployments!.chainId,
    );

    setState(() {
      _status = 'Success! USDC received.';
      _executing = false;
      _quote = null;
    });
  }

  Uint8List hexToBytes(String hex) {
    if (hex.startsWith('0x')) hex = hex.substring(2);
    return Uint8List.fromList(
      List.generate(hex.length ~/ 2, (i) => int.parse(hex.substring(i * 2, i * 2 + 2), radix: 16)),
    );
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
            // Slippage Input
            TextField(
              controller: _slippageController,
              decoration: const InputDecoration(
                labelText: 'Slippage tolerance (%)',
                border: OutlineInputBorder(),
              ),
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
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
                    const SizedBox(height: AppTheme.spacingXs),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(
                          'Min after slippage:',
                          style: TextStyle(color: AppTheme.textMuted, fontSize: AppTheme.sizeSm),
                        ),
                        Text(
                          _isBuy
                              ? '${(_quote!.tokensOut * (100 - double.parse(_slippageController.text)) / 100).floor()} tokens'
                              : '${(_quote!.usdc * (100 - double.parse(_slippageController.text)) / 100).floor()} USDC',
                          style: TextStyle(fontSize: AppTheme.sizeSm),
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
            // Status
            if (_status != null)
              Padding(
                padding: const EdgeInsets.only(bottom: AppTheme.spacingMd),
                child: Text(
                  _status!,
                  style: TextStyle(color: AppTheme.accent, fontSize: AppTheme.sizeSm),
                ),
              ),
            // Execute Button
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: (_quote != null && !_executing) ? _executeSwap : null,
                child: _executing
                    ? const SizedBox(
                        height: 20,
                        width: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : Text(_isBuy ? 'Buy' : 'Sell'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
