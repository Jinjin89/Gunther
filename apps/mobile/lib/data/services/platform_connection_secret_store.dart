import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:gunther_mobile/data/services/connection_profile_store.dart';

abstract interface class SecureValueStore {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
  Future<void> delete(String key);
}

class FlutterSecureValueStore implements SecureValueStore {
  FlutterSecureValueStore({FlutterSecureStorage? storage})
    : _storage =
          storage ??
          const FlutterSecureStorage(
            aOptions: AndroidOptions(
              resetOnError: false,
              migrateWithBackup: true,
              storageNamespace: 'gunther_connections',
            ),
            iOptions: IOSOptions(
              accessibility: KeychainAccessibility.first_unlock_this_device,
              accountName: 'Gunther workspace connections',
            ),
          );

  final FlutterSecureStorage _storage;

  @override
  Future<String?> read(String key) => _storage.read(key: key);

  @override
  Future<void> write(String key, String value) =>
      _storage.write(key: key, value: value);

  @override
  Future<void> delete(String key) => _storage.delete(key: key);
}

class PlatformConnectionSecretStore implements ConnectionSecretStore {
  PlatformConnectionSecretStore({SecureValueStore? values})
    : _values = values ?? FlutterSecureValueStore();

  static final RegExp _safeProfileId = RegExp(
    r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$',
  );
  static const String _prefix = 'gunther.connection.v1.';
  final SecureValueStore _values;

  @override
  Future<ConnectionSecrets?> read(String profileId) async {
    final encoded = await _values.read(_key(profileId));
    if (encoded == null) return null;
    try {
      final decoded = jsonDecode(encoded);
      if (decoded is! Map<String, Object?> || decoded['version'] != 1) {
        return null;
      }
      final token = decoded['accessToken'];
      final caCertificatePem = decoded['caCertificatePem'];
      if (token is! String ||
          (caCertificatePem != null && caCertificatePem is! String)) {
        return null;
      }
      return ConnectionSecrets(
        accessToken: token,
        caCertificatePem: caCertificatePem as String?,
      );
    } on Object {
      // Corrupt keychain data fails closed and requires explicit re-pairing. It
      // is not automatically deleted, preserving support/recovery evidence.
      return null;
    }
  }

  @override
  Future<void> write(String profileId, ConnectionSecrets secrets) {
    final encoded = jsonEncode(<String, Object?>{
      'version': 1,
      'accessToken': secrets.accessToken,
      'caCertificatePem': secrets.caCertificatePem,
    });
    return _values.write(_key(profileId), encoded);
  }

  @override
  Future<void> delete(String profileId) => _values.delete(_key(profileId));

  String _key(String profileId) {
    if (!_safeProfileId.hasMatch(profileId)) {
      throw const FormatException('Connection profile id is invalid.');
    }
    return '$_prefix$profileId';
  }
}
