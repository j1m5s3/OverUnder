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
  final _emailController = TextEditingController();
  final _otpController = TextEditingController();
  String? _flowId;
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
    _emailController.dispose();
    _otpController.dispose();
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

  Future<void> _sendOtp() async {
    final email = _emailController.text.trim();
    if (email.isEmpty) {
      setState(() {
        _error = 'Email is required';
      });
      return;
    }

    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final flowId = await widget.apiClient.authCdpEmail(email);
      setState(() {
        _flowId = flowId;
        _loading = false;
      });
    } catch (e) {
      setState(() {
        _loading = false;
        _error = 'Failed to send code: $e';
      });
    }
  }

  Future<void> _verifyOtp() async {
    final otp = _otpController.text.trim();
    final flowId = _flowId;
    if (flowId == null || otp.isEmpty) {
      setState(() {
        _error = 'Enter the email code';
      });
      return;
    }

    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final result = await widget.apiClient.authCdpVerify(flowId, otp);
      widget.apiClient.setJwt(result['token'] as String);
      if (!mounted) return;
      context.read<WalletProvider>().setSession(
            address: result['address'] as String,
            jwt: result['token'] as String,
          );
      setState(() {
        _loading = false;
      });
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Signed in')),
        );
      }
    } catch (e) {
      setState(() {
        _loading = false;
        _error = 'Failed to verify: $e';
      });
    }
  }

  void _disconnect() {
    widget.apiClient.setJwt('');
    context.read<WalletProvider>().disconnect();
    setState(() {
      _flowId = null;
      _otpController.clear();
      _error = null;
    });
  }

  Future<void> _openRamp() async {
    final walletProvider = context.read<WalletProvider>();

    if (!walletProvider.isConnected) {
      setState(() {
        _error = 'Sign in first';
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
                            controller: _emailController,
                            decoration: const InputDecoration(
                              labelText: 'Email',
                              border: OutlineInputBorder(),
                            ),
                            keyboardType: TextInputType.emailAddress,
                            enabled: _flowId == null,
                          ),
                          const SizedBox(height: AppTheme.spacingMd),
                          if (_flowId != null)
                            TextField(
                              controller: _otpController,
                              decoration: const InputDecoration(
                                labelText: 'Email code',
                                border: OutlineInputBorder(),
                              ),
                              keyboardType: TextInputType.number,
                            ),
                          if (_flowId != null) const SizedBox(height: AppTheme.spacingMd),
                          if (_error != null)
                            Padding(
                              padding: const EdgeInsets.only(bottom: AppTheme.spacingMd),
                              child: Text(
                                _error!,
                                style: TextStyle(color: AppTheme.no),
                              ),
                            ),
                          ElevatedButton(
                            onPressed: _loading
                                ? null
                                : _flowId == null
                                    ? _sendOtp
                                    : _verifyOtp,
                            child: _loading
                                ? const SizedBox(
                                    height: 20,
                                    width: 20,
                                    child: CircularProgressIndicator(strokeWidth: 2),
                                  )
                                : Text(_flowId == null ? 'Send code' : 'Verify'),
                          ),
                        ],
                      ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: AppTheme.spacingMd),
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
