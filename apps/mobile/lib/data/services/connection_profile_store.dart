import 'dart:convert';
import 'dart:io';

import 'package:gunther_mobile/data/models/connection_profile.dart';
import 'package:path_provider/path_provider.dart';

class ConnectionProfileCatalog {
  const ConnectionProfileCatalog({
    this.profiles = const [],
    this.activeProfileId,
  });

  factory ConnectionProfileCatalog.fromJson(Map<String, Object?> json) {
    if (json['version'] != 1) {
      throw const FormatException('Unsupported connection catalog version.');
    }
    final rawProfiles = json['profiles'];
    if (rawProfiles is! List<Object?> || rawProfiles.length > 100) {
      throw const FormatException('Connection profile list is invalid.');
    }
    final profiles = rawProfiles
        .map((item) {
          if (item is! Map<String, Object?>) {
            throw const FormatException('A connection profile is invalid.');
          }
          return ConnectionProfile.fromJson(item);
        })
        .toList(growable: false);
    final ids = profiles.map((profile) => profile.id).toSet();
    if (ids.length != profiles.length) {
      throw const FormatException('Connection profile ids must be unique.');
    }
    final activeProfileId = _optionalString(json['activeProfileId']);
    if (activeProfileId != null && !ids.contains(activeProfileId)) {
      throw const FormatException('The active connection profile is missing.');
    }
    return ConnectionProfileCatalog(
      profiles: List<ConnectionProfile>.unmodifiable(profiles),
      activeProfileId: activeProfileId,
    );
  }

  final List<ConnectionProfile> profiles;
  final String? activeProfileId;

  ConnectionProfile? get activeProfile {
    final id = activeProfileId;
    if (id == null) return null;
    return profiles.where((profile) => profile.id == id).firstOrNull;
  }

  Map<String, Object?> toJson() => {
    'version': 1,
    'activeProfileId': activeProfileId,
    'profiles': profiles.map((profile) => profile.toJson()).toList(),
  };
}

abstract interface class ConnectionProfileStore {
  Future<ConnectionProfileCatalog> load();
  Future<void> save(ConnectionProfileCatalog catalog);
}

class FileConnectionProfileStore implements ConnectionProfileStore {
  FileConnectionProfileStore({Future<Directory> Function()? directoryProvider})
    : _directoryProvider =
          directoryProvider ??
          (() async {
            final support = await getApplicationSupportDirectory();
            return Directory('${support.path}/connections');
          });

  static const _maximumCatalogBytes = 2 * 1024 * 1024;
  static const _fileName = 'profiles.json';
  final Future<Directory> Function() _directoryProvider;

  @override
  Future<ConnectionProfileCatalog> load() async {
    final file = await _catalogFile(createDirectory: false);
    final type = await FileSystemEntity.type(file.path, followLinks: false);
    if (type == FileSystemEntityType.notFound) {
      return const ConnectionProfileCatalog();
    }
    if (type != FileSystemEntityType.file ||
        await file.length() > _maximumCatalogBytes) {
      throw const FormatException('The connection catalog is not a safe file.');
    }
    final decoded = jsonDecode(await file.readAsString());
    if (decoded is! Map<String, Object?>) {
      throw const FormatException('The connection catalog is invalid.');
    }
    return ConnectionProfileCatalog.fromJson(decoded);
  }

  @override
  Future<void> save(ConnectionProfileCatalog catalog) async {
    // Round-trip validation prevents a caller from persisting a catalog whose
    // active id or profile collection is inconsistent.
    final payload = jsonEncode(catalog.toJson());
    final checked = jsonDecode(payload);
    if (checked is! Map<String, Object?>) {
      throw const FormatException('The connection catalog is invalid.');
    }
    ConnectionProfileCatalog.fromJson(checked);
    if (utf8.encode(payload).length > _maximumCatalogBytes) {
      throw const FormatException('The connection catalog is too large.');
    }
    final file = await _catalogFile(createDirectory: true);
    final temporary = File('${file.path}.tmp');
    await temporary.writeAsString(payload, flush: true);
    await temporary.rename(file.path);
  }

  Future<File> _catalogFile({required bool createDirectory}) async {
    final directory = await _directoryProvider();
    if (createDirectory) await directory.create(recursive: true);
    return File('${directory.path}/$_fileName');
  }
}

class ConnectionSecrets {
  ConnectionSecrets({required this.accessToken, this.caCertificatePem}) {
    if (accessToken.isEmpty || accessToken.length > 8192) {
      throw const FormatException('Connection access token is invalid.');
    }
    if (caCertificatePem != null &&
        (caCertificatePem!.isEmpty || caCertificatePem!.length > 256 * 1024)) {
      throw const FormatException('Connection CA certificate is invalid.');
    }
  }

  final String accessToken;
  final String? caCertificatePem;
}

/// Production implementations belong in the platform keychain/keystore. This
/// interface keeps credentials out of profile JSON and out of application
/// logs; the in-memory implementation is deliberately test-only/ephemeral.
abstract interface class ConnectionSecretStore {
  Future<ConnectionSecrets?> read(String profileId);
  Future<void> write(String profileId, ConnectionSecrets secrets);
  Future<void> delete(String profileId);
}

class MemoryConnectionSecretStore implements ConnectionSecretStore {
  final Map<String, ConnectionSecrets> _values = {};

  @override
  Future<ConnectionSecrets?> read(String profileId) async => _values[profileId];

  @override
  Future<void> write(String profileId, ConnectionSecrets secrets) async {
    _values[profileId] = secrets;
  }

  @override
  Future<void> delete(String profileId) async {
    _values.remove(profileId);
  }
}

String? _optionalString(Object? value) {
  if (value == null) return null;
  if (value is! String || value.isEmpty) {
    throw const FormatException('An optional connection field is invalid.');
  }
  return value;
}
