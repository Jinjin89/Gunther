import 'dart:io';

enum ConnectionTrustMode { system, pinnedCertificate }

class ConnectionProfile {
  ConnectionProfile({
    required this.id,
    required this.workspaceId,
    required this.label,
    required Uri baseUri,
    required this.trustMode,
    required this.deviceId,
    required this.protocolVersion,
    String? caFingerprint,
    this.lastConnectedAt,
  }) : baseUri = normalizeBaseUri(baseUri),
       caFingerprint = caFingerprint?.toLowerCase() {
    _validateIdentifier(id, 'connection profile id');
    _validateIdentifier(workspaceId, 'workspace id');
    _validateIdentifier(deviceId, 'device id');
    if (label.trim().isEmpty || label.length > 120) {
      throw const FormatException('Connection profile label is invalid.');
    }
    if (protocolVersion < 1 || protocolVersion > 1_000_000) {
      throw const FormatException('Connection protocol version is invalid.');
    }
    final fingerprint = this.caFingerprint;
    if (trustMode == ConnectionTrustMode.pinnedCertificate) {
      if (fingerprint == null ||
          !RegExp(r'^[0-9a-f]{64}$').hasMatch(fingerprint)) {
        throw const FormatException(
          'A pinned connection requires a SHA-256 certificate fingerprint.',
        );
      }
    } else if (fingerprint != null) {
      throw const FormatException(
        'A system-trusted connection cannot also contain a certificate pin.',
      );
    }
    if (lastConnectedAt != null && !lastConnectedAt!.isUtc) {
      throw const FormatException('Connection timestamps must be UTC.');
    }
  }

  factory ConnectionProfile.fromJson(Map<String, Object?> json) {
    final rawTrustMode = _requiredString(json, 'trustMode');
    final trustMode = ConnectionTrustMode.values.where(
      (value) => value.name == rawTrustMode,
    );
    if (trustMode.length != 1) {
      throw const FormatException('Connection trust mode is invalid.');
    }
    final lastConnectedAt = _optionalString(json['lastConnectedAt']);
    return ConnectionProfile(
      id: _requiredString(json, 'id'),
      workspaceId: _requiredString(json, 'workspaceId'),
      label: _requiredString(json, 'label'),
      baseUri: Uri.parse(_requiredString(json, 'baseUri')),
      trustMode: trustMode.single,
      caFingerprint: _optionalString(json['caFingerprint']),
      deviceId: _requiredString(json, 'deviceId'),
      protocolVersion: _requiredPositiveInt(json, 'protocolVersion'),
      lastConnectedAt: lastConnectedAt == null
          ? null
          : DateTime.parse(lastConnectedAt).toUtc(),
    );
  }

  final String id;
  final String workspaceId;
  final String label;
  final Uri baseUri;
  final ConnectionTrustMode trustMode;
  final String? caFingerprint;
  final String deviceId;
  final int protocolVersion;
  final DateTime? lastConnectedAt;

  ConnectionProfile copyWith({String? label, DateTime? lastConnectedAt}) {
    return ConnectionProfile(
      id: id,
      workspaceId: workspaceId,
      label: label ?? this.label,
      baseUri: baseUri,
      trustMode: trustMode,
      caFingerprint: caFingerprint,
      deviceId: deviceId,
      protocolVersion: protocolVersion,
      lastConnectedAt: lastConnectedAt ?? this.lastConnectedAt,
    );
  }

  Map<String, Object?> toJson() => {
    'id': id,
    'workspaceId': workspaceId,
    'label': label,
    'baseUri': baseUri.toString(),
    'trustMode': trustMode.name,
    'caFingerprint': caFingerprint,
    'deviceId': deviceId,
    'protocolVersion': protocolVersion,
    'lastConnectedAt': lastConnectedAt?.toUtc().toIso8601String(),
  };

  static Uri normalizeBaseUri(Uri input) {
    if (!input.hasScheme || !input.hasAuthority || input.host.isEmpty) {
      throw const FormatException('Connection URL must be absolute.');
    }
    final scheme = input.scheme.toLowerCase();
    if (scheme != 'https' && scheme != 'http') {
      throw const FormatException('Connection URL must use HTTPS.');
    }
    if (input.userInfo.isNotEmpty ||
        input.hasQuery ||
        input.hasFragment ||
        input.pathSegments.any((part) => part == '..' || part == '.')) {
      throw const FormatException('Connection URL contains unsafe components.');
    }
    final host = input.host.toLowerCase();
    if (scheme == 'http' && !_isLoopbackHost(host)) {
      throw const FormatException(
        'Cleartext HTTP is allowed only for a loopback development server.',
      );
    }
    var path = input.path.isEmpty ? '/' : input.path;
    path = path.replaceAll(RegExp(r'/+'), '/');
    if (!path.endsWith('/')) path = '$path/';
    return input.replace(
      scheme: scheme,
      host: host,
      path: path,
      query: null,
      fragment: null,
    );
  }
}

bool _isLoopbackHost(String host) {
  if (host == 'localhost' || host == '::1') return true;
  final address = InternetAddress.tryParse(host);
  return address?.isLoopback ?? false;
}

void _validateIdentifier(String value, String field) {
  if (!RegExp(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$').hasMatch(value)) {
    throw FormatException('The $field is invalid.');
  }
}

String _requiredString(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value is! String || value.isEmpty) {
    throw FormatException('Connection profile field "$key" is invalid.');
  }
  return value;
}

String? _optionalString(Object? value) {
  if (value == null) return null;
  if (value is! String || value.isEmpty) {
    throw const FormatException('An optional connection field is invalid.');
  }
  return value;
}

int _requiredPositiveInt(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value is! int || value < 1) {
    throw FormatException('Connection profile field "$key" is invalid.');
  }
  return value;
}
