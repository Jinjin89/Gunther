import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:gunther_mobile/data/models/connection_profile.dart';
import 'package:gunther_mobile/data/services/connection_profile_store.dart';
import 'package:gunther_mobile/data/services/workspace_connection_service.dart';
import 'package:gunther_mobile/features/connection/presentation/connection_controller.dart';

void main() {
  test(
    'pairing and probe keep the device token in Authorization only',
    () async {
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final requests =
          <({String path, String? authorization, String query, String body})>[];
      final subscription = server.listen((request) async {
        final body = await utf8.decoder.bind(request).join();
        requests.add((
          path: request.uri.path,
          authorization: request.headers.value(HttpHeaders.authorizationHeader),
          query: request.uri.query,
          body: body,
        ));
        request.response.headers.contentType = ContentType.json;
        if (request.uri.path.endsWith('/pairing/exchange')) {
          request.response.statusCode = HttpStatus.created;
          request.response.write(jsonEncode(_credentialPayload));
        } else if (request.uri.path.endsWith('/workspace/bootstrap')) {
          request.response.write(
            jsonEncode(<String, Object?>{
              'workspace_id': _workspaceId,
              'workspace_name': 'Bioinformatics',
              'protocol_version': 1,
              'minimum_protocol_version': 1,
              'auth_kind': 'device',
              'device_id': _deviceId,
              'scopes': ['api:access', 'transcription:stream'],
              'capabilities': ['capture', 'live-transcription'],
            }),
          );
        } else {
          request.response.statusCode = HttpStatus.notFound;
          request.response.write('{}');
        }
        await request.response.close();
      });
      addTearDown(() async {
        await subscription.cancel();
        await server.close(force: true);
      });

      final paired = await const WorkspacePairingService().exchange(
        baseUri: Uri.parse('http://127.0.0.1:${server.port}/api/'),
        pairingId: _pairingId,
        pairingCode: _pairingCode,
        deviceName: 'Keke phone',
        platform: 'android',
      );
      final probe = await const WorkspaceConnectionProbe().check(
        paired.profile,
        paired.secrets,
      );

      expect(paired.profile.workspaceId, _workspaceId);
      expect(paired.profile.deviceId, _deviceId);
      expect(paired.profile.label, 'Bioinformatics');
      expect(paired.profile.baseUri.path, '/api/');
      expect(paired.secrets.accessToken, _deviceToken);
      expect(probe.outcome, ConnectionProbeOutcome.online);
      expect(probe.workspaceId, _workspaceId);
      expect(requests, hasLength(2));
      expect(requests[0].path, '/api/pairing/exchange');
      expect(requests[0].authorization, isNull);
      expect(requests[0].query, isEmpty);
      expect(jsonDecode(requests[0].body), <String, Object>{
        'pairing_id': _pairingId,
        'pairing_code': _pairingCode,
        'device_name': 'Keke phone',
        'platform': 'android',
      });
      expect(requests[1].path, '/api/workspace/bootstrap');
      expect(requests[1].authorization, 'Bearer $_deviceToken');
      expect(requests[1].query, isEmpty);
    },
  );

  test(
    'one-time code conflicts are reported without saving a credential',
    () async {
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final subscription = server.listen((request) async {
        await request.drain<void>();
        request.response.statusCode = HttpStatus.conflict;
        request.response.headers.contentType = ContentType.json;
        request.response.write(jsonEncode({'detail': 'already used'}));
        await request.response.close();
      });
      addTearDown(() async {
        await subscription.cancel();
        await server.close(force: true);
      });

      await expectLater(
        const WorkspacePairingService().exchange(
          baseUri: Uri.parse('http://127.0.0.1:${server.port}/api/'),
          pairingId: _pairingId,
          pairingCode: _pairingCode,
          deviceName: 'Phone',
          platform: 'ios',
        ),
        throwsA(
          isA<PairingException>().having(
            (error) => error.failure,
            'failure',
            PairingFailure.alreadyUsed,
          ),
        ),
      );
    },
  );

  test(
    'remote cleartext pairing is rejected before credentials can leave',
    () async {
      await expectLater(
        const WorkspacePairingService().exchange(
          baseUri: Uri.parse('http://192.168.1.8:8787/api/'),
          pairingId: _pairingId,
          pairingCode: _pairingCode,
          deviceName: 'Phone',
          platform: 'android',
        ),
        throwsA(
          isA<PairingException>().having(
            (error) => error.failure,
            'failure',
            PairingFailure.invalidDetails,
          ),
        ),
      );
    },
  );

  test('probe maps revoked credentials and identity changes safely', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    var revoked = true;
    final subscription = server.listen((request) async {
      request.response.headers.contentType = ContentType.json;
      if (revoked) {
        request.response.statusCode = HttpStatus.unauthorized;
        request.response.write('{}');
      } else {
        request.response.write(
          jsonEncode(<String, Object?>{
            'workspace_id': 'wsp_${List<String>.filled(32, 'b').join()}',
            'workspace_name': 'Another workspace',
            'protocol_version': 1,
            'minimum_protocol_version': 1,
            'auth_kind': 'device',
            'device_id': _deviceId,
            'scopes': ['api:access'],
            'capabilities': ['capture'],
          }),
        );
      }
      await request.response.close();
    });
    addTearDown(() async {
      await subscription.cancel();
      await server.close(force: true);
    });
    final profile = ConnectionProfile(
      id: 'workspace:$_workspaceId',
      workspaceId: _workspaceId,
      label: 'Bioinformatics',
      baseUri: Uri.parse('http://127.0.0.1:${server.port}/api/'),
      trustMode: ConnectionTrustMode.system,
      deviceId: _deviceId,
      protocolVersion: 1,
    );
    final secrets = ConnectionSecrets(accessToken: _deviceToken);
    const probe = WorkspaceConnectionProbe();

    expect(
      (await probe.check(profile, secrets)).outcome,
      ConnectionProbeOutcome.authExpired,
    );
    revoked = false;
    final changed = await probe.check(profile, secrets);
    expect(changed.outcome, ConnectionProbeOutcome.online);
    expect(changed.workspaceId, isNot(profile.workspaceId));
  });
}

const _pairingId = 'pair_0123456789abcdef01234567';
const _pairingCode = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ';
const _workspaceId = 'wsp_0123456789abcdef0123456789abcdef';
const _deviceId = 'dev_0123456789abcdef01234567';
const _deviceToken = 'gdt_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ';

const _credentialPayload = <String, Object?>{
  'access_token': _deviceToken,
  'token_type': 'Bearer',
  'protocol_version': 1,
  'workspace_id': _workspaceId,
  'workspace_name': 'Bioinformatics',
  'device': <String, Object?>{
    'id': _deviceId,
    'workspace_id': _workspaceId,
    'name': 'Keke phone',
    'platform': 'android',
    'scopes': ['api:access', 'transcription:stream'],
    'created_at': '2026-08-30T00:00:00Z',
    'last_used_at': null,
    'revoked_at': null,
  },
};
