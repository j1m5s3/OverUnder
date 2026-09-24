import 'package:flutter/services.dart';

/// USDC and the CTF outcome tokens it splits into both use 6 decimals.
const int usdcDecimals = 6;

/// Largest integer that survives a round trip through a JS number (Flutter web).
const int _maxSafeUnits = 9007199254740991;

/// Parses a typed decimal amount ("5", "0.25", ".5", "1,5") into base units,
/// so "5" is 5000000 micro-USDC. Returns null when the text is empty,
/// malformed, has more than [decimals] fraction digits or is too large.
int? parseUnits(String text, {int decimals = usdcDecimals}) {
  final match = RegExp(r'^(\d*)(?:[.,](\d*))?$').firstMatch(text.trim());
  if (match == null) return null;
  final whole = match.group(1) ?? '';
  final fraction = match.group(2) ?? '';
  if (whole.isEmpty && fraction.isEmpty) return null;
  if (fraction.length > decimals) return null;
  final units = BigInt.parse('${whole.isEmpty ? '0' : whole}${fraction.padRight(decimals, '0')}');
  if (units > BigInt.from(_maxSafeUnits)) return null;
  return units.toInt();
}

/// Formats base units as a decimal string without trailing zeros: 1500000 -> "1.5".
String formatUnits(int units, {int decimals = usdcDecimals}) {
  final digits = units.abs().toString().padLeft(decimals + 1, '0');
  final whole = digits.substring(0, digits.length - decimals);
  final fraction = digits.substring(digits.length - decimals).replaceFirst(RegExp(r'0+$'), '');
  return '${units < 0 ? '-' : ''}$whole${fraction.isEmpty ? '' : '.$fraction'}';
}

/// Parses a slippage tolerance in percent. Null unless 0 <= value < 100.
double? parseSlippagePercent(String text) {
  final value = double.tryParse(text.trim().replaceAll(',', '.'));
  if (value == null || value.isNaN || value < 0 || value >= 100) return null;
  return value;
}

/// [amount] minus [slippagePercent] percent, rounded down, in exact integer math.
int applySlippage(int amount, double slippagePercent) {
  final bps = (slippagePercent * 100).round().clamp(0, 10000);
  return (BigInt.from(amount) * BigInt.from(10000 - bps) ~/ BigInt.from(10000)).toInt();
}

/// Rejects any edit that would not leave a decimal with at most [decimals]
/// fraction digits, so the amount field never holds text [parseUnits] refuses.
class DecimalAmountFormatter extends TextInputFormatter {
  DecimalAmountFormatter({int decimals = usdcDecimals})
      : _pattern = RegExp('^\\d*(?:[.,]\\d{0,$decimals})?\$');

  final RegExp _pattern;

  @override
  TextEditingValue formatEditUpdate(TextEditingValue oldValue, TextEditingValue newValue) {
    return _pattern.hasMatch(newValue.text) ? newValue : oldValue;
  }
}
