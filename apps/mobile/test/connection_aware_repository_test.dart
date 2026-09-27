import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:gunther_mobile/data/models/capture.dart';
import 'package:gunther_mobile/data/models/connection_profile.dart';
import 'package:gunther_mobile/data/repositories/connection_aware_knowledge_repository.dart';
import 'package:gunther_mobile/data/repositories/knowledge_repository.dart';
import 'package:gunther_mobile/data/services/connection_profile_store.dart';
import 'package:gunther_mobile/data/services/knowledge_api_service.dart';
import 'package:gunther_mobile/features/connection/presentation/connection_controller.dart';

void main() {
  test('routes repository calls to the paired bearer workspace', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final authorizations = <String?>[];
    final subscription = server.listen((request) async {
      authorizations.add(
        request.headers.value(HttpHeaders.authorizationHeader),
      );
      request.response.headers.contentType = ContentType.json;
      request.response.write(
        jsonEncode(<String, Object?>{
          'counts': {
            'sources': 2,
            'entities': 3,
            'assertions': 4,
            'provisional': 1,
          },
          'recentSources': <Object?>[],
          'recentAssertions': <Object?>[],
        }),
      );
      await request.response.close();
    });
    addTearDown(() async {
      await subscription.cancel();
      await server.close(force: true);
    });
    final controller = ConnectionController(
      profileStore: _MemoryProfileStore(),
      secretStore: MemoryConnectionSecretStore(),
      probe: const _OnlineProbe(),
    );
    addTearDown(controller.dispose);
    await controller.initialize();
    await controller.upsertAndSelect(
      _profile(server.port),
      ConnectionSecrets(accessToken: 'paired-token'),
    );
    final fallbackApi = KnowledgeApiService(baseUrl: 'http://127.0.0.1:1/api/');
    addTearDown(fallbackApi.close);
    final repository = ConnectionAwareKnowledgeRepository(
      connections: controller,
      fallback: RemoteKnowledgeRepository(apiService: fallbackApi),
    );
    addTearDown(repository.dispose);

    final overview = await repository.getOverview();

    expect(overview.counts.sources, 2);
    expect(authorizations, ['Bearer paired-token']);
  });

  test('does not fall back to another server after trust fails', () async {
    final controller = ConnectionController(
      profileStore: _MemoryProfileStore(),
      secretStore: MemoryConnectionSecretStore(),
      probe: const _IncompatibleProbe(),
    );
    addTearDown(controller.dispose);
    await controller.initialize();
    await controller.upsertAndSelect(
      _profile(8788),
      ConnectionSecrets(accessToken: 'paired-token'),
    );
    final fallbackApi = KnowledgeApiService(baseUrl: 'http://127.0.0.1:1/api/');
    addTearDown(fallbackApi.close);
    final repository = ConnectionAwareKnowledgeRepository(
      connections: controller,
      fallback: RemoteKnowledgeRepository(apiService: fallbackApi),
    );
    addTearDown(repository.dispose);

    expect(repository.getOverview, throwsStateError);
  });

  test(
    'capture lease stays on its bound profile after active profile changes',
    () async {
      final serverA = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final serverB = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final requestsA = <String>[];
      final requestsB = <String>[];
      final subscriptionA = serverA.listen((request) async {
        requestsA.add(
          '${request.method} ${request.uri.path} '
          '${request.headers.value(HttpHeaders.authorizationHeader)}',
        );
        await utf8.decoder.bind(request).join();
        request.response.headers.contentType = ContentType.json;
        request.response.write('{}');
        await request.response.close();
      });
      final subscriptionB = serverB.listen((request) async {
        requestsB.add(
          '${request.method} ${request.uri.path} '
          '${request.headers.value(HttpHeaders.authorizationHeader)}',
        );
        await utf8.decoder.bind(request).join();
        request.response.headers.contentType = ContentType.json;
        request.response.write('{}');
        await request.response.close();
      });
      addTearDown(() async {
        await subscriptionA.cancel();
        await subscriptionB.cancel();
        await serverA.close(force: true);
        await serverB.close(force: true);
      });

      final controller = ConnectionController(
        profileStore: _MemoryProfileStore(),
        secretStore: MemoryConnectionSecretStore(),
        probe: const _OnlineProbe(),
      );
      addTearDown(controller.dispose);
      await controller.initialize();
      final profileA = _profile(
        serverA.port,
        id: 'profile-alpha',
        workspaceId: 'workspace-alpha',
      );
      final profileB = _profile(
        serverB.port,
        id: 'profile-beta',
        workspaceId: 'workspace-beta',
      );
      await controller.upsertAndSelect(
        profileA,
        ConnectionSecrets(accessToken: 'token-alpha'),
      );
      final fallbackApi = KnowledgeApiService(
        baseUrl: 'http://127.0.0.1:1/api/',
      );
      addTearDown(fallbackApi.close);
      final repository = ConnectionAwareKnowledgeRepository(
        connections: controller,
        fallback: RemoteKnowledgeRepository(apiService: fallbackApi),
      );
      addTearDown(repository.dispose);

      final lease = repository.pinCaptureTarget(
        workspaceId: profileA.workspaceId,
        profileId: profileA.id,
      );
      addTearDown(lease.close);
      await controller.upsertAndSelect(
        profileB,
        ConnectionSecrets(accessToken: 'token-beta'),
      );
      await lease.repository.createQuickNote(
        const QuickNoteDraft(
          title: 'Pinned capture',
          content: 'Must stay in workspace alpha.',
          clientCaptureId: 'capture_pinned_0001',
        ),
      );

      expect(requestsA, ['POST /api/notes Bearer token-alpha']);
      expect(requestsB, isEmpty);
    },
  );
}

ConnectionProfile _profile(
  int port, {
  String id = 'workspace:workspace-test',
  String workspaceId = 'workspace-test',
}) => ConnectionProfile(
  id: id,
  workspaceId: workspaceId,
  label: 'Test workspace',
  baseUri: Uri.parse('http://127.0.0.1:$port/api/'),
  trustMode: ConnectionTrustMode.system,
  deviceId: 'device-$workspaceId',
  protocolVersion: 1,
);

class _MemoryProfileStore implements ConnectionProfileStore {
  ConnectionProfileCatalog value = const ConnectionProfileCatalog();

  @override
  Future<ConnectionProfileCatalog> load() async => value;

  @override
  Future<void> save(ConnectionProfileCatalog catalog) async {
    value = catalog;
  }
}

class _OnlineProbe implements ConnectionProbe {
  const _OnlineProbe();

  @override
  Future<ConnectionProbeResult> check(
    ConnectionProfile profile,
    ConnectionSecrets secrets,
  ) async => ConnectionProbeResult(
    outcome: ConnectionProbeOutcome.online,
    workspaceId: profile.workspaceId,
    protocolVersion: profile.protocolVersion,
  );
}

class _IncompatibleProbe implements ConnectionProbe {
  const _IncompatibleProbe();

  @override
  Future<ConnectionProbeResult> check(
    ConnectionProfile profile,
    ConnectionSecrets secrets,
  ) async =>
      const ConnectionProbeResult(outcome: ConnectionProbeOutcome.incompatible);
}
