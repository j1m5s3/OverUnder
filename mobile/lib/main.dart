import 'package:flutter/material.dart';
import 'theme/app_theme.dart';
import 'services/api_client.dart';
import 'features/markets/market_list_screen.dart';
import 'features/wallet/wallet_screen.dart';

void main() {
  runApp(const OverUnderApp());
}

class OverUnderApp extends StatelessWidget {
  const OverUnderApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'OverUnder',
      theme: AppTheme.theme,
      home: const MainScreen(),
      debugShowCheckedModeBanner: false,
    );
  }
}

class MainScreen extends StatefulWidget {
  const MainScreen({super.key});

  @override
  State<MainScreen> createState() => _MainScreenState();
}

class _MainScreenState extends State<MainScreen> {
  int _selectedIndex = 0;
  late final ApiClient _apiClient;

  @override
  void initState() {
    super.initState();
    // Default to localhost for local development
    // Production would read from environment or config
    _apiClient = ApiClient(baseUrl: 'http://127.0.0.1:8000');
  }

  void _onItemTapped(int index) {
    setState(() {
      _selectedIndex = index;
    });
  }

  @override
  Widget build(BuildContext context) {
    final screens = [
      MarketListScreen(apiClient: _apiClient),
      WalletScreen(apiClient: _apiClient),
    ];

    return Scaffold(
      body: screens[_selectedIndex],
      bottomNavigationBar: BottomNavigationBar(
        items: const [
          BottomNavigationBarItem(
            icon: Icon(Icons.list),
            label: 'Markets',
          ),
          BottomNavigationBarItem(
            icon: Icon(Icons.account_balance_wallet),
            label: 'Wallet',
          ),
        ],
        currentIndex: _selectedIndex,
        onTap: _onItemTapped,
        backgroundColor: AppTheme.bgElevated,
        selectedItemColor: AppTheme.accent,
        unselectedItemColor: AppTheme.textMuted,
      ),
    );
  }
}
