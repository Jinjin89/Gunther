import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:gunther_mobile/data/models/connection_profile.dart';
import 'package:gunther_mobile/data/services/connection_http_client.dart';
import 'package:gunther_mobile/data/services/connection_profile_store.dart';
import 'package:gunther_mobile/features/connection/presentation/connection_controller.dart';

const int supportedWorkspaceProtocolVersion = 1;

enum PairingFailure {
  invalidDetails,
  rejected,
  expired,
  alreadyUsed,
  certificateChanged,
  incompatible,
  unavailable,
}

class PairingException implements Exception {
  const PairingException(this.failure, this.message);

  final PairingFailure failure;
  final String message;

  @override
  String toString() => message;
}

class PairedWorkspace {
  const PairedWorkspace({required this.profile, required this.secrets});

  final ConnectionProfile profile;
  final ConnectionSecrets secrets;
}

class WorkspacePairingService {
  const WorkspacePairingService();

  Future<PairedWorkspace> exchange({
    required Uri baseUri,
    required String pairingId,
    required String pairingCode,
    required String deviceName,
    required String platform,
    ConnectionTrustMode trustMode = ConnectionTrustMode.system,
    String? caCertificatePem,
    String? caFingerprint,
  }) async {
    final Uri normalizedBaseUri;
    try {
      normalizedBaseUri = ConnectionProfile.normalizeBaseUri(baseUri);
    } on FormatException catch (error) {
      throw PairingException(PairingFailure.invalidDetails, error.message);
    }
    final normalizedPairingId = pairingId.trim();
    final normalizedPairingCode = pairingCode.trim();
    final normalizedDeviceName = deviceName.trim();
    final normalizedPlatform = platform.trim();
    if (!RegExp(r'^pair_[a-f0-9]{24,35}$').hasMatch(normalizedPairingId) ||
        !RegExp(r'^[A-Za-z0-9_-]{43,128}$').hasMatch(normalizedPairingCode) ||
        normalizedDeviceName.isEmpty ||
        normalizedDeviceName.length > 120 ||
        !RegExp(r'^[A-Za-z0-9._ -]{1,40}$').hasMatch(normalizedPlatform)) {
      throw const PairingException(
        PairingFailure.invalidDetails,
        'The pairing details are incomplete or invalid.',
      );
    }

    HttpClient? client;
    try {
      client = createTrustedHttpClient(
        trustMode: trustMode,
        caCertificatePem: caCertificatePem,
        expectedSha256: caFingerprint,
      );
      final request = await client
          .postUrl(normalizedBaseUri.resolve('pairing/exchange'))
          .timeout(const Duration(seconds: 12));
      request.headers.contentType = ContentType.json;
      request.write(
        jsonEncode(<String, Object>{
          'pairing_id': normalizedPairingId,
          'pairing_code': normalizedPairingCode,
          'device_name': normalizedDeviceName,
          'platform': normalizedPlatform,
        }),
      );
      final response = await request.close().timeout(
        const Duration(seconds: 15),
      );
      final payload = await _readJsonObject(response);
      switch (response.statusCode) {
        case HttpStatus.created:
          return _decodeCredential(
            payload,
            normalizedBaseUri,
            trustMode,
            caCertificatePem,
            caFingerprint,
          );
        case HttpStatus.unauthorized:
        case HttpStatus.notFound:
          throw const PairingException(
            PairingFailure.rejected,
            'The pairing code was not accepted.',
          );
        case HttpStatus.gone:
          throw const PairingException(
            PairingFailure.expired,
            'The pairing code has expired. Create a new code on the desktop.',
          );
        case HttpStatus.conflict:
          throw const PairingException(
            PairingFailure.alreadyUsed,
            'This pairing code was already used. Create a new code.',
          );
        case HttpStatus.upgradeRequired:
          throw const PairingException(
            PairingFailure.incompatible,
            'This workspace requires a newer version of Gunther.',
          );
        default:
          throw PairingException(
            PairingFailure.unavailable,
            _serverMessage(payload) ??
                'The workspace could not complete pairing right now.',
          );
      }
    } on PairingException {
      rethrow;
    } on HandshakeException {
      throw const PairingException(
        PairingFailure.certificateChanged,
        'The workspace certificate could not be verified.',
      );
    } on FormatException catch (error) {
      throw PairingException(PairingFailure.invalidDetails, error.message);
    } on TimeoutException {
      throw const PairingException(
        PairingFailure.unavailable,
        'The workspace did not respond in time.',
      );
    } on SocketException {
      throw const PairingException(
        PairingFailure.unavailable,
        'The workspace is not reachable from this device.',
      );
    } on HttpException {
      throw const PairingException(
        PairingFailure.unavailable,
        'The workspace returned an invalid response.',
      );
    } finally {
      client?.close(force: true);
    }
  }

  PairedWorkspace _decodeCredential(
    Map<String, Object?> payload,
    Uri baseUri,
    ConnectionTrustMode trustMode,
    String? caCertificatePem,
    String? caFingerprint,
  ) {
    final token = _requiredString(payload, 'access_token');
    if (payload['token_type'] != 'Bearer' ||
        !RegExp(r'^gdt_[A-Za-z0-9_-]{43}$').hasMatch(token)) {
      throw const FormatException(
        'The workspace returned an invalid credential.',
      );
    }
    final protocolVersion = _requiredInt(payload, 'protocol_version');
    if (protocolVersion != supportedWorkspaceProtocolVersion) {
      throw const PairingException(
        PairingFailure.incompatible,
        'This workspace uses an unsupported connection protocol.',
      );
    }
    final workspaceId = _requiredString(payload, 'workspace_id');
    final workspaceName = _requiredString(payload, 'workspace_name').trim();
    final rawDevice = payload['device'];
    if (rawDevice is! Map<String, Object?>) {
      throw const FormatException('The workspace returned an invalid device.');
    }
    final deviceId = _requiredString(rawDevice, 'id');
    if (_requiredString(rawDevice, 'workspace_id') != workspaceId) {
      throw const FormatException(
        'The paired device belongs to another workspace.',
      );
    }
    final rawScopes = rawDevice['scopes'];
    if (rawScopes is! List<Object?> || !rawScopes.contains('api:access')) {
      throw const FormatException(
        'The paired device cannot access this workspace.',
      );
    }
    if (workspaceName.isEmpty || workspaceName.length > 120) {
      throw const FormatException('The workspace returned an invalid name.');
    }

    final profile = ConnectionProfile(
      // Stable per workspace so revoking/re-pairing a phone rotates the device
      // credential without orphaning captures already bound to this home.
      id: 'workspace:$workspaceId',
      workspaceId: workspaceId,
      label: workspaceName,
      baseUri: baseUri,
      trustMode: trustMode,
      caFingerprint: trustMode == ConnectionTrustMode.pinnedCertificate
          ? caFingerprint?.toLowerCase()
          : null,
      deviceId: deviceId,
      protocolVersion: protocolVersion,
    );
    return PairedWorkspace(
      profile: profile,
      secrets: ConnectionSecrets(
        accessToken: token,
        caCertificatePem: trustMode == ConnectionTrustMode.pinnedCertificate
            ? caCertificatePem
            : null,
      ),
    );
  }
}

class WorkspaceConnectionProbe implements ConnectionProbe {
  const WorkspaceConnectionProbe();

  @override
  Future<ConnectionProbeResult> check(
    ConnectionProfile profile,
    ConnectionSecrets secrets,
  ) async {
    HttpClient? client;
    try {
      client = createConnectionHttpClient(profile, secrets);
      final request = await client
          .getUrl(profile.baseUri.resolve('workspace/bootstrap'))
          .timeout(const Duration(seconds: 12));
      request.headers.set(
        HttpHeaders.authorizationHeader,
        'Bearer ${secrets.accessToken}',
      );
      final response = await request.close().timeout(
        const Duration(seconds: 15),
      );
      final payload = await _readJsonObject(response);
      if (response.statusCode == HttpStatus.unauthorized ||
          response.statusCode == HttpStatus.forbidden) {
        return const ConnectionProbeResult(
          outcome: ConnectionProbeOutcome.authExpired,
        );
      }
      if (response.statusCode == HttpStatus.upgradeRequired) {
        return const ConnectionProbeResult(
          outcome: ConnectionProbeOutcome.incompatible,
        );
      }
      if (response.statusCode != HttpStatus.ok) {
        return ConnectionProbeResult(
          outcome: ConnectionProbeOutcome.offline,
          message: _serverMessage(payload),
        );
      }

      final workspaceId = _requiredString(payload, 'workspace_id');
      final protocolVersion = _requiredInt(payload, 'protocol_version');
      final minimumProtocolVersion = _requiredInt(
        payload,
        'minimum_protocol_version',
      );
      final authKind = _requiredString(payload, 'auth_kind');
      final deviceId = payload['device_id'];
      if (authKind != 'device' || deviceId != profile.deviceId) {
        return const ConnectionProbeResult(
          outcome: ConnectionProbeOutcome.authExpired,
        );
      }
      if (minimumProtocolVersion > supportedWorkspaceProtocolVersion ||
          protocolVersion < minimumProtocolVersion) {
        return ConnectionProbeResult(
          outcome: ConnectionProbeOutcome.incompatible,
          workspaceId: workspaceId,
          protocolVersion: protocolVersion,
        );
      }
      return ConnectionProbeResult(
        outcome: ConnectionProbeOutcome.online,
        workspaceId: workspaceId,
        protocolVersion: protocolVersion,
      );
    } on HandshakeException {
      return const ConnectionProbeResult(
        outcome: ConnectionProbeOutcome.certificateChanged,
      );
    } on FormatException {
      return const ConnectionProbeResult(
        outcome: ConnectionProbeOutcome.incompatible,
      );
    } on TimeoutException {
      return const ConnectionProbeResult(
        outcome: ConnectionProbeOutcome.offline,
      );
    } on SocketException {
      return const ConnectionProbeResult(
        outcome: ConnectionProbeOutcome.offline,
      );
    } on HttpException {
      return const ConnectionProbeResult(
        outcome: ConnectionProbeOutcome.offline,
      );
    } finally {
      client?.close(force: true);
    }
  }
}

Future<Map<String, Object?>> _readJsonObject(
  HttpClientResponse response,
) async {
  const maximumBytes = 512 * 1024;
  final bytes = <int>[];
  await for (final chunk in response.timeout(const Duration(seconds: 15))) {
    if (bytes.length + chunk.length > maximumBytes) {
      throw const HttpException('Workspace response is too large.');
    }
    bytes.addAll(chunk);
  }
  if (bytes.isEmpty) return <String, Object?>{};
  final decoded = jsonDecode(utf8.decode(bytes));
  if (decoded is! Map<String, Object?>) {
    throw const FormatException('Workspace response must be an object.');
  }
  return decoded;
}

String _requiredString(Map<String, Object?> payload, String key) {
  final value = payload[key];
  if (value is! String || value.isEmpty) {
    throw FormatException('Workspace field "$key" is invalid.');
  }
  return value;
}

int _requiredInt(Map<String, Object?> payload, String key) {
  final value = payload[key];
  if (value is! int || value < 1) {
    throw FormatException('Workspace field "$key" is invalid.');
  }
  return value;
}

String? _serverMessage(Map<String, Object?> payload) {
  final detail = payload['detail'];
  if (detail is String && detail.trim().isNotEmpty && detail.length <= 240) {
    return detail.trim();
  }
  return null;
}
