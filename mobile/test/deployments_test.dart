import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:overunder/config/app_config.dart';
import 'package:overunder/models/deployments.dart';

class _FakeBundle extends CachingAssetBundle {
  _FakeBundle(this.files);

  final Map<String, String> files;

  @override
  Future<ByteData> load(String key) async {
    final text = files[key];
    if (text == null) throw FlutterError('Unable to load asset: "$key".');
    return ByteData.sublistView(Uint8List.fromList(utf8.encode(text)));
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('defaults to the configured chain, 31337 without CHAIN_ID', () async {
    expect(AppConfig.chainId, AppConfig.defaultChainId);

    final d = await Deployments.load();

    expect(d.chainId, 31337);
    expect(d.marketAmm, isNotNull);
    expect(d.mockUsdc, isNotNull);
    expect(d.conditionalTokens, isNotNull);
  });

  test('loads the asset for an explicit chain id', () async {
    final bundle = _FakeBundle({
      'assets/deployments/84532.json': json.encode({
        'chainId': 84532,
        'MarketAMM': '0x00000000000000000000000000000000000000a1',
        'MockUSDC': '0x00000000000000000000000000000000000000b2',
      }),
    });

    final d = await Deployments.load(84532, bundle);

    expect(d.chainId, 84532);
    expect(d.marketAmm, '0x00000000000000000000000000000000000000a1');
    expect(d.mockUsdc, '0x00000000000000000000000000000000000000b2');
  });

  test('uses the requested chain id when the file omits chainId', () async {
    final bundle = _FakeBundle({
      'assets/deployments/8453.json': json.encode({'MarketAMM': '0x00000000000000000000000000000000000000a1'}),
    });

    expect((await Deployments.load(8453, bundle)).chainId, 8453);
  });

  test('fails closed on a chain id mismatch', () async {
    final bundle = _FakeBundle({
      'assets/deployments/84532.json': json.encode({'chainId': 31337}),
    });

    await expectLater(Deployments.load(84532, bundle), throwsStateError);
  });

  test('fails closed when the asset is missing', () async {
    await expectLater(Deployments.load(1, _FakeBundle({})), throwsA(anything));
  });

  group('Deployments.resolve', () {
    const bundledAmm = '0x00000000000000000000000000000000000000a1';
    const bundledUsdc = '0x00000000000000000000000000000000000000b2';
    const apiAmm = '0x7A446c5853c3BFEF992e430e604Ea610d9E686ef';
    const apiUsdc = '0xaf4D478f9494221Dd3CCa7C192100878F86327E5';
    const apiCtf = '0x5b891E69bEA73546862A51232908a99f3F7Cd58c';

    _FakeBundle bundle() => _FakeBundle({
          'assets/deployments/84532.json': json.encode({
            'chainId': 84532,
            'MarketAMM': bundledAmm,
            'MockUSDC': bundledUsdc,
            'ConditionalTokens': '0x00000000000000000000000000000000000000c3',
            'FeeVault': '0x00000000000000000000000000000000000000d4',
          }),
        });

    test('API trade targets win over a stale bundled asset and report drift', () async {
      final r = await Deployments.resolve(
        fetchApiAddresses: () async => {
          'chainId': 84532,
          'MockUSDC': apiUsdc,
          'ConditionalTokens': apiCtf,
          'MarketAMM': apiAmm,
        },
        chainId: 84532,
        bundle: bundle(),
      );

      expect(r.source, DeploymentsSource.api);
      expect(r.deployments.marketAmm, apiAmm);
      expect(r.deployments.mockUsdc, apiUsdc);
      expect(r.deployments.conditionalTokens, apiCtf);
      // Non-trade keys still come from the bundle.
      expect(r.deployments.feeVault, '0x00000000000000000000000000000000000000d4');
      expect(r.drift, unorderedEquals(['MockUSDC', 'ConditionalTokens', 'MarketAMM']));
    });

    test('matching addresses (any case) report no drift', () async {
      final r = await Deployments.resolve(
        fetchApiAddresses: () async => {'chainId': '84532', 'MarketAMM': bundledAmm.toUpperCase().replaceFirst('0X', '0x')},
        chainId: 84532,
        bundle: bundle(),
      );
      expect(r.source, DeploymentsSource.api);
      expect(r.drift, isEmpty);
    });

    test('reads short aliases under an addresses object', () async {
      final r = await Deployments.resolve(
        fetchApiAddresses: () async => {
          'chainId': 84532,
          'addresses': {'usdc': apiUsdc, 'ctf': apiCtf, 'amm': apiAmm},
        },
        chainId: 84532,
        bundle: bundle(),
      );
      expect(r.deployments.marketAmm, apiAmm);
      expect(r.deployments.mockUsdc, apiUsdc);
      expect(r.deployments.conditionalTokens, apiCtf);
    });

    test('API addresses work without a bundled asset', () async {
      final r = await Deployments.resolve(
        fetchApiAddresses: () async => {'chainId': 84532, 'MarketAMM': apiAmm, 'MockUSDC': apiUsdc},
        chainId: 84532,
        bundle: _FakeBundle({}),
      );
      expect(r.deployments.marketAmm, apiAmm);
      expect(r.drift, isEmpty);
    });

    test('falls back to the bundle when the API has no route, errors, or sends no addresses', () async {
      for (final fetch in <Future<Map<String, dynamic>?> Function()>[
        () async => null,
        () async => throw Exception('offline'),
        () async => <String, dynamic>{},
        () async => {'MarketAMM': 'not-an-address'},
      ]) {
        final r = await Deployments.resolve(fetchApiAddresses: fetch, chainId: 84532, bundle: bundle());
        expect(r.source, DeploymentsSource.bundle);
        expect(r.deployments.marketAmm, bundledAmm);
      }
    });

    test('fails closed when the API serves another chain', () async {
      await expectLater(
        Deployments.resolve(
          fetchApiAddresses: () async => {'chainId': 31337, 'MarketAMM': apiAmm},
          chainId: 84532,
          bundle: bundle(),
        ),
        throwsStateError,
      );
    });

    test('fails closed with neither API addresses nor an asset', () async {
      await expectLater(
        Deployments.resolve(fetchApiAddresses: () async => null, chainId: 84532, bundle: _FakeBundle({})),
        throwsA(anything),
      );
    });
  });
}
