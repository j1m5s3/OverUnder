import 'package:flutter/foundation.dart';
import 'package:web3dart/web3dart.dart';
import 'package:http/http.dart' as http;

class WalletProvider extends ChangeNotifier {
  Web3Client? _web3Client;
  Credentials? _credentials;
  String? _address;
  bool _isConnecting = false;

  Web3Client? get web3Client => _web3Client;
  Credentials? get credentials => _credentials;
  String? get address => _address;
  bool get isConnected => _credentials != null && _web3Client != null;
  bool get isConnecting => _isConnecting;

  Future<void> connect(String privateKey, String rpcUrl) async {
    _isConnecting = true;
    notifyListeners();

    try {
      _web3Client = Web3Client(rpcUrl, http.Client());
      _credentials = EthPrivateKey.fromHex(privateKey);
      _address = await _credentials!.extractAddress().then((addr) => addr.hex);
      
      _isConnecting = false;
      notifyListeners();
    } catch (e) {
      _isConnecting = false;
      notifyListeners();
      rethrow;
    }
  }

  void disconnect() {
    _web3Client?.dispose();
    _web3Client = null;
    _credentials = null;
    _address = null;
    notifyListeners();
  }
}
