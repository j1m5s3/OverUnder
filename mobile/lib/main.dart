import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'config/app_config.dart';
import 'theme/app_theme.dart';
import 'services/api_client.dart';
import 'providers/wallet_provider.dart';
import 'features/markets/market_list_screen.dart';
import 'features/wallet/wallet_screen.dart';

void main() {
  runApp(const OverUnderApp());
}

class OverUnderApp extends StatelessWidget {
  const OverUnderApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider(
      create: (_) => WalletProvider(),
      child: MaterialApp(
        title: 'OverUnder',
        theme: AppTheme.theme,
        home: const MainScreen(),
        debugShowCheckedModeBanner: false,
      ),
    );
  }
}

class MainScreen extends StatefulWidget {
  const MainScreen({super.key});

  @override
  State<MainScreen> createState() => _MainScreenState();
}

class _MainScreenState extends State<MainScreen> with SingleTickerProviderStateMixin {
  late final ApiClient _apiClient;
  late final TabController _tabController;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 2, vsync: this);
    // API_BASE_URL via --dart-define; defaults to localhost for local development
    _apiClient = ApiClient(baseUrl: AppConfig.apiBaseUrl);
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        body: TabBarView(
          controller: _tabController,
          children: [
            MarketListScreen(apiClient: _apiClient),
            WalletScreen(apiClient: _apiClient),
          ],
        ),
        bottomNavigationBar: TabBar(
          controller: _tabController,
          tabs: const [
            Tab(icon: Icon(Icons.list), text: 'Markets'),
            Tab(icon: Icon(Icons.account_balance_wallet), text: 'Wallet'),
          ],
          indicatorColor: AppTheme.accent,
          labelColor: AppTheme.accent,
          unselectedLabelColor: AppTheme.textMuted,
        ),
      ),
    );
  }
}
