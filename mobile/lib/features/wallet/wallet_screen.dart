import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../services/api_client.dart';
import '../../providers/wallet_provider.dart';
import '../../theme/app_theme.dart';
import 'package:url_launcher/url_launcher.dart';

class WalletScreen extends StatefulWidget {
  final ApiClient apiClient;

  const WalletScreen({super.key, required this.apiClient});

  @override
  State<WalletScreen> createState() => _WalletScreenState();
}

class _WalletScreenState extends State<WalletScreen> {
  final _privateKeyController = TextEditingController();
  final _rpcController = TextEditingController(text: 'http://127.0.0.1:8545');
  double _nav = 0.0;
  bool _loading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadNav();
  }

  @override
  void dispose() {
    _privateKeyController.dispose();
    _rpcController.dispose();
    super.dispose();
  }

  Future<void> _loadNav() async {
    try {
      final nav = await widget.apiClient.getNav();
      setState(() {
        _nav = nav;
      });
    } catch (e) {
      // Silently fail nav loading
    }
  }

  Future<void> _connect() async {
    final privateKey = _privateKeyController.text.trim();
    final rpcUrl = _rpcController.text.trim();

    if (privateKey.isEmpty || rpcUrl.isEmpty) {
      setState(() {
        _error = 'Private key and RPC URL are required';
      });
      return;
    }

    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final walletProvider = context.read<WalletProvider>();
      await walletProvider.connect(privateKey, rpcUrl);
      
      setState(() {
        _loading = false;
      });

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Wallet connected successfully')),
        );
      }
    } catch (e) {
      setState(() {
        _loading = false;
        _error = 'Failed to connect: $e';
      });
    }
  }

  void _disconnect() {
    context.read<WalletProvider>().disconnect();
    setState(() {
      _error = null;
    });
  }

  Future<void> _openRamp() async {
    final walletProvider = context.read<WalletProvider>();
    
    if (!walletProvider.isConnected) {
      setState(() {
        _error = 'Connect wallet first';
      });
      return;
    }

    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final result = await widget.apiClient.getRampUrl(walletProvider.address!);
      final urlString = result['url'];

      if (urlString != null) {
        final url = Uri.parse(urlString);
        if (await canLaunchUrl(url)) {
          await launchUrl(url, mode: LaunchMode.externalApplication);
        }
      }
    } catch (e) {
      setState(() {
        _error = 'Failed to get ramp URL: $e';
      });
    } finally {
      setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final walletProvider = context.watch<WalletProvider>();

    return Scaffold(
      appBar: AppBar(
        title: const Text('Wallet'),
        backgroundColor: AppTheme.bgElevated,
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(AppTheme.spacingMd),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // Connection Card
            Card(
              child: Padding(
                padding: const EdgeInsets.all(AppTheme.spacingMd),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Connection',
                      style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                            fontWeight: AppTheme.weightBold,
                          ),
                    ),
                    const SizedBox(height: AppTheme.spacingMd),
                    if (walletProvider.isConnected)
                      Container(
                        padding: const EdgeInsets.all(AppTheme.spacingMd),
                        decoration: BoxDecoration(
                          color: AppTheme.bgElevated,
                          borderRadius: BorderRadius.circular(AppTheme.radiusMd),
                        ),
                        child: Row(
                          children: [
                            const Icon(Icons.account_balance_wallet, color: AppTheme.yes),
                            const SizedBox(width: AppTheme.spacingSm),
                            Expanded(
                              child: Text(
                                '${walletProvider.address!.substring(0, 6)}...${walletProvider.address!.substring(walletProvider.address!.length - 4)}',
                                style: const TextStyle(fontFamily: 'monospace'),
                              ),
                            ),
                            IconButton(
                              icon: const Icon(Icons.close),
                              onPressed: _disconnect,
                            ),
                          ],
                        ),
                      )
                    else
                      Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          TextField(
                            controller: _privateKeyController,
                            decoration: const InputDecoration(
                              labelText: 'Private Key',
                              border: OutlineInputBorder(),
                              hintText: '0x...',
                            ),
                            obscureText: true,
                          ),
                          const SizedBox(height: AppTheme.spacingMd),
                          TextField(
                            controller: _rpcController,
                            decoration: const InputDecoration(
                              labelText: 'RPC URL',
                              border: OutlineInputBorder(),
                            ),
                          ),
                          const SizedBox(height: AppTheme.spacingMd),
                          if (_error != null)
                            Padding(
                              padding: const EdgeInsets.only(bottom: AppTheme.spacingMd),
                              child: Text(
                                _error!,
                                style: TextStyle(color: AppTheme.no),
                              ),
                            ),
                          ElevatedButton(
                            onPressed: _loading ? null : _connect,
                            child: _loading
                                ? const SizedBox(
                                    height: 20,
                                    width: 20,
                                    child: CircularProgressIndicator(strokeWidth: 2),
                                  )
                                : const Text('Connect Wallet'),
                          ),
                          const SizedBox(height: AppTheme.spacingSm),
                          Text(
                            'Development only: Use Anvil test account private keys',
                            style: TextStyle(
                              color: AppTheme.warning,
                              fontSize: AppTheme.sizeSm,
                            ),
                            textAlign: TextAlign.center,
                          ),
                        ],
                      ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: AppTheme.spacingMd),
            // OU NAV Card
            Card(
              child: Padding(
                padding: const EdgeInsets.all(AppTheme.spacingMd),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'OU Token NAV',
                      style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                            fontWeight: AppTheme.weightBold,
                          ),
                    ),
                    const SizedBox(height: AppTheme.spacingSm),
                    Text(
                      '\$${(_nav / 1e6).toStringAsFixed(6)} USDC per OU',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: AppTheme.spacingSm),
                    Text(
                      'Net Asset Value',
                      style: TextStyle(color: AppTheme.textMuted, fontSize: AppTheme.sizeSm),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: AppTheme.spacingMd),
            // Buy USDC Card
            Card(
              child: Padding(
                padding: const EdgeInsets.all(AppTheme.spacingMd),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Buy USDC',
                      style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                            fontWeight: AppTheme.weightBold,
                          ),
                    ),
                    const SizedBox(height: AppTheme.spacingSm),
                    Text(
                      'Purchase USDC on Base with MoonPay or Coinbase Onramp',
                      style: TextStyle(color: AppTheme.textMuted),
                    ),
                    const SizedBox(height: AppTheme.spacingMd),
                    SizedBox(
                      width: double.infinity,
                      child: ElevatedButton(
                        onPressed: walletProvider.isConnected && !_loading ? _openRamp : null,
                        child: _loading
                            ? const SizedBox(
                                height: 20,
                                width: 20,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Text('Buy USDC'),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
