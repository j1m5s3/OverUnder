import 'package:flutter_test/flutter_test.dart';
import 'package:overunder/utils/units.dart';

void main() {
  group('parseUnits', () {
    test('converts whole and decimal USDC to 6-decimal base units', () {
      expect(parseUnits('5'), 5000000);
      expect(parseUnits('0.25'), 250000);
      expect(parseUnits('.5'), 500000);
      expect(parseUnits('5.'), 5000000);
      expect(parseUnits('1,5'), 1500000);
      expect(parseUnits(' 12.345678 '), 12345678);
      expect(parseUnits('0.000001'), 1);
      expect(parseUnits('0'), 0);
    });

    test('rejects empty, malformed, over-precise and oversized input', () {
      expect(parseUnits(''), isNull);
      expect(parseUnits('.'), isNull);
      expect(parseUnits('abc'), isNull);
      expect(parseUnits('1.2.3'), isNull);
      expect(parseUnits('-1'), isNull);
      expect(parseUnits('1e6'), isNull);
      expect(parseUnits('0.0000001'), isNull);
      expect(parseUnits('99999999999'), isNull);
    });

    test('honours other decimals', () {
      expect(parseUnits('0.0015', decimals: 18), 1500000000000000);
      expect(parseUnits('2', decimals: 0), 2);
      expect(parseUnits('2.1', decimals: 0), isNull);
    });
  });

  group('formatUnits', () {
    test('formats base units without trailing zeros', () {
      expect(formatUnits(0), '0');
      expect(formatUnits(1), '0.000001');
      expect(formatUnits(250000), '0.25');
      expect(formatUnits(1500000), '1.5');
      expect(formatUnits(5000000), '5');
      expect(formatUnits(123456789), '123.456789');
      expect(formatUnits(-1500000), '-1.5');
    });

    test('round-trips with parseUnits', () {
      for (final text in ['0.000001', '0.25', '1.5', '42', '123.456789']) {
        expect(formatUnits(parseUnits(text)!), text);
      }
    });
  });

  group('slippage', () {
    test('parseSlippagePercent accepts 0 <= value < 100', () {
      expect(parseSlippagePercent('0.5'), 0.5);
      expect(parseSlippagePercent('0'), 0);
      expect(parseSlippagePercent('1,5'), 1.5);
      expect(parseSlippagePercent(''), isNull);
      expect(parseSlippagePercent('-1'), isNull);
      expect(parseSlippagePercent('100'), isNull);
      expect(parseSlippagePercent('abc'), isNull);
    });

    test('applySlippage rounds down in integer math', () {
      expect(applySlippage(1000000, 0.5), 995000);
      expect(applySlippage(8123457, 1), 8042222);
      expect(applySlippage(1000000, 0), 1000000);
      expect(applySlippage(1, 0.5), 0);
      expect(applySlippage(9007199254740991, 0.5), 8962163258467286);
    });
  });

  group('DecimalAmountFormatter', () {
    final formatter = DecimalAmountFormatter();
    TextEditingValue edit(String from, String to) => formatter.formatEditUpdate(
          TextEditingValue(text: from),
          TextEditingValue(text: to),
        );

    test('allows decimals up to 6 places', () {
      expect(edit('', '5').text, '5');
      expect(edit('5', '5.').text, '5.');
      expect(edit('5.', '5.25').text, '5.25');
      expect(edit('0.12345', '0.123456').text, '0.123456');
      expect(edit('1', '1,5').text, '1,5');
    });

    test('keeps the previous text for invalid edits', () {
      expect(edit('0.123456', '0.1234567').text, '0.123456');
      expect(edit('5.2', '5.2.').text, '5.2');
      expect(edit('5', '5a').text, '5');
      expect(edit('5', '-5').text, '5');
    });
  });
}
