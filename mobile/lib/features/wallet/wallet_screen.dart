import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../theme/app_theme.dart';
import 'package:url_launcher/url_launcher.dart';

class WalletScreen extends StatefulWidget {
  final ApiClient apiClient;

  const WalletScreen({super.key, required this.apiClient});

  @override
  State<WalletScreen> createState() => _WalletScreenState();
}

class _WalletScreenState extends State<WalletScreen> {
  String? _address;
  double _nav = 0.0;
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    _loadNav();
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

  Future<void> _openRamp() async {
    if (_address == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Connect wallet first')),
      );
      return;
    }

    setState(() => _loading = true);

    try {
      final result = await widget.apiClient.getRampUrl(_address!);
      final urlString = result['url'];

      if (urlString != null) {
        final url = Uri.parse(urlString);
        if (await canLaunchUrl(url)) {
          await launchUrl(url, mode: LaunchMode.externalApplication);
        }
      }
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Failed to get ramp URL: $e')),
      );
    } finally {
      setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
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
                    if (_address != null)
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
                                '${_address!.substring(0, 6)}...${_address!.substring(_address!.length - 4)}',
                                style: const TextStyle(fontFamily: 'monospace'),
                              ),
                            ),
                            IconButton(
                              icon: const Icon(Icons.close),
                              onPressed: () {
                                setState(() => _address = null);
                              },
                            ),
                          ],
                        ),
                      )
                    else
                      ElevatedButton(
                        onPressed: () {
                          // TODO: Privy/wallet integration
                          ScaffoldMessenger.of(context).showSnackBar(
                            const SnackBar(
                              content: Text('Wallet connection requires Privy integration'),
                            ),
                          );
                        },
                        child: const Text('Connect Wallet'),
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
                        onPressed: _loading ? null : _openRamp,
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
