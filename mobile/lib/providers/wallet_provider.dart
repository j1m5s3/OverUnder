import 'package:flutter/foundation.dart';

class WalletProvider extends ChangeNotifier {
  String? _address;
  String? _jwt;
  bool _isConnecting = false;

  String? get address => _address;
  String? get jwt => _jwt;
  bool get isConnected => _jwt != null && _address != null;
  bool get isConnecting => _isConnecting;

  void setConnecting(bool value) {
    _isConnecting = value;
    notifyListeners();
  }

  void setSession({required String address, required String jwt}) {
    _address = address;
    _jwt = jwt;
    _isConnecting = false;
    notifyListeners();
  }

  void disconnect() {
    _address = null;
    _jwt = null;
    _isConnecting = false;
    notifyListeners();
  }
}
