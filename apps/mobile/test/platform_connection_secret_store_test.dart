import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:gunther_mobile/data/services/connection_profile_store.dart';
import 'package:gunther_mobile/data/services/platform_connection_secret_store.dart';

void main() {
  test(
    'round-trips token and CA only through the secure value boundary',
    () async {
      final values = _MemorySecureValues();
      final store = PlatformConnectionSecretStore(values: values);
      final secrets = ConnectionSecrets(
        accessToken: 'device-token-value',
        caCertificatePem:
            '-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----',
      );

      await store.write('profile-1', secrets);
      final persisted = values.data.values.single;
      expect(persisted, contains('device-token-value'));
      expect(jsonDecode(persisted), isA<Map<String, Object?>>());
      final restored = await store.read('profile-1');
      expect(restored?.accessToken, secrets.accessToken);
      expect(restored?.caCertificatePem, secrets.caCertificatePem);

      await store.delete('profile-1');
      expect(await store.read('profile-1'), isNull);
    },
  );

  test('corrupt or unsupported secure values fail closed', () async {
    final values = _MemorySecureValues();
    final store = PlatformConnectionSecretStore(values: values);
    values.data['gunther.connection.v1.profile-1'] = '{broken';
    expect(await store.read('profile-1'), isNull);
    values.data['gunther.connection.v1.profile-1'] = jsonEncode({
      'version': 99,
      'accessToken': 'old-token',
    });
    expect(await store.read('profile-1'), isNull);
  });

  test('rejects profile ids that could escape the secure namespace', () async {
    final store = PlatformConnectionSecretStore(values: _MemorySecureValues());
    expect(
      () => store.write('../profile', ConnectionSecrets(accessToken: 'token')),
      throwsFormatException,
    );
    expect(() => store.read('profile/other'), throwsFormatException);
  });
}

class _MemorySecureValues implements SecureValueStore {
  final Map<String, String> data = {};

  @override
  Future<String?> read(String key) async => data[key];

  @override
  Future<void> write(String key, String value) async {
    data[key] = value;
  }

  @override
  Future<void> delete(String key) async {
    data.remove(key);
  }
}
