import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:gunther_mobile/data/models/connection_profile.dart';
import 'package:gunther_mobile/data/services/connection_profile_store.dart';
import 'package:gunther_mobile/features/connection/presentation/connection_controller.dart';

void main() {
  test('normalizes secure and loopback URLs and rejects unsafe endpoints', () {
    final secure = _profile(
      id: 'secure',
      workspaceId: 'workspace-secure',
      baseUri: Uri.parse('HTTPS://Knowledge.Example//api'),
    );
    final loopback = _profile(
      id: 'local',
      workspaceId: 'workspace-local',
      baseUri: Uri.parse('http://127.0.0.1:8787/api'),
    );

    expect(secure.baseUri.toString(), 'https://knowledge.example/api/');
    expect(loopback.baseUri.toString(), 'http://127.0.0.1:8787/api/');
    for (final value in <String>[
      'http://192.168.1.50:8787/api/',
      'https://user:secret@knowledge.example/api/',
      'https://knowledge.example/api/?token=secret',
      'https://knowledge.example/api/#secret',
      'ftp://knowledge.example/api/',
    ]) {
      expect(
        () => _profile(
          id: 'unsafe',
          workspaceId: 'workspace-unsafe',
          baseUri: Uri.parse(value),
        ),
        throwsFormatException,
        reason: value,
      );
    }
  });

  test('requires a valid SHA-256 fingerprint only for pinned trust', () {
    final fingerprint = List<String>.filled(32, 'ab').join();
    expect(
      () => _profile(
        id: 'pinned',
        workspaceId: 'workspace-pinned',
        baseUri: Uri.parse('https://knowledge.example/api/'),
        trustMode: ConnectionTrustMode.pinnedCertificate,
      ),
      throwsFormatException,
    );
    final pinned = _profile(
      id: 'pinned',
      workspaceId: 'workspace-pinned',
      baseUri: Uri.parse('https://knowledge.example/api/'),
      trustMode: ConnectionTrustMode.pinnedCertificate,
      caFingerprint: fingerprint.toUpperCase(),
    );
    expect(pinned.caFingerprint, fingerprint);
    expect(
      () => _profile(
        id: 'system',
        workspaceId: 'workspace-system',
        baseUri: Uri.parse('https://knowledge.example/api/'),
        caFingerprint: fingerprint,
      ),
      throwsFormatException,
    );
  });

  test('file store atomically round-trips profiles without secrets', () async {
    final root = await Directory.systemTemp.createTemp('gunther-connections-');
    addTearDown(() => root.delete(recursive: true));
    final store = FileConnectionProfileStore(
      directoryProvider: () async => root,
    );
    final profile = _profile(
      id: 'primary',
      workspaceId: 'workspace-1',
      baseUri: Uri.parse('https://knowledge.example/api/'),
    );
    final catalog = ConnectionProfileCatalog(
      profiles: [profile],
      activeProfileId: profile.id,
    );

    await store.save(catalog);

    final file = File('${root.path}/profiles.json');
    final text = await file.readAsString();
    expect(text, isNot(contains('device-access-token')));
    expect(await File('${file.path}.tmp').exists(), isFalse);
    final json = jsonDecode(text) as Map<String, Object?>;
    expect(json['version'], 1);
    final restored = await store.load();
    expect(restored.activeProfile?.workspaceId, 'workspace-1');
    expect(restored.activeProfile?.baseUri, profile.baseUri);
  });

  test(
    'controller ignores a stale probe after selecting a newer profile',
    () async {
      final store = _MemoryProfileStore();
      final secrets = MemoryConnectionSecretStore();
      final probe = _ControlledProbe();
      final controller = ConnectionController(
        profileStore: store,
        secretStore: secrets,
        probe: probe,
      );
      addTearDown(controller.dispose);

      await controller.initialize();
      expect(controller.status, WorkspaceConnectionStatus.unconfigured);
      final first = _profile(
        id: 'first',
        workspaceId: 'workspace-first',
        baseUri: Uri.parse('https://first.example/api/'),
      );
      final second = _profile(
        id: 'second',
        workspaceId: 'workspace-second',
        baseUri: Uri.parse('https://second.example/api/'),
      );
      final firstUpdate = controller.upsertAndSelect(
        first,
        ConnectionSecrets(accessToken: 'first-token'),
      );
      await _waitUntil(() => probe.requests.containsKey('first'));
      final secondUpdate = controller.upsertAndSelect(
        second,
        ConnectionSecrets(accessToken: 'second-token'),
      );
      await _waitUntil(() => probe.requests.containsKey('second'));
      probe.requests['second']!.complete(
        const ConnectionProbeResult(
          outcome: ConnectionProbeOutcome.online,
          workspaceId: 'workspace-second',
          protocolVersion: 1,
        ),
      );
      await secondUpdate;
      probe.requests['first']!.complete(
        const ConnectionProbeResult(
          outcome: ConnectionProbeOutcome.authExpired,
        ),
      );
      await firstUpdate;

      expect(controller.activeProfile?.id, 'second');
      expect(controller.status, WorkspaceConnectionStatus.online);
    },
  );

  test(
    'controller fails closed when the server workspace identity changes',
    () async {
      final profile = _profile(
        id: 'primary',
        workspaceId: 'expected-workspace',
        baseUri: Uri.parse('https://knowledge.example/api/'),
      );
      final store = _MemoryProfileStore(
        ConnectionProfileCatalog(
          profiles: [profile],
          activeProfileId: profile.id,
        ),
      );
      final secrets = MemoryConnectionSecretStore();
      await secrets.write(
        profile.id,
        ConnectionSecrets(accessToken: 'device-token'),
      );
      final controller = ConnectionController(
        profileStore: store,
        secretStore: secrets,
        probe: const _ImmediateProbe(
          ConnectionProbeResult(
            outcome: ConnectionProbeOutcome.online,
            workspaceId: 'different-workspace',
            protocolVersion: 1,
          ),
        ),
      );
      addTearDown(controller.dispose);

      await controller.initialize();

      expect(controller.status, WorkspaceConnectionStatus.incompatible);
      expect(controller.statusMessage, contains('does not match'));
    },
  );
}

ConnectionProfile _profile({
  required String id,
  required String workspaceId,
  required Uri baseUri,
  ConnectionTrustMode trustMode = ConnectionTrustMode.system,
  String? caFingerprint,
}) {
  return ConnectionProfile(
    id: id,
    workspaceId: workspaceId,
    label: id,
    baseUri: baseUri,
    trustMode: trustMode,
    caFingerprint: caFingerprint,
    deviceId: 'device-1',
    protocolVersion: 1,
  );
}

class _MemoryProfileStore implements ConnectionProfileStore {
  _MemoryProfileStore([this.value = const ConnectionProfileCatalog()]);

  ConnectionProfileCatalog value;

  @override
  Future<ConnectionProfileCatalog> load() async => value;

  @override
  Future<void> save(ConnectionProfileCatalog catalog) async {
    value = catalog;
  }
}

class _ControlledProbe implements ConnectionProbe {
  final Map<String, Completer<ConnectionProbeResult>> requests = {};

  @override
  Future<ConnectionProbeResult> check(
    ConnectionProfile profile,
    ConnectionSecrets secrets,
  ) {
    final completer = Completer<ConnectionProbeResult>();
    requests[profile.id] = completer;
    return completer.future;
  }
}

class _ImmediateProbe implements ConnectionProbe {
  const _ImmediateProbe(this.result);

  final ConnectionProbeResult result;

  @override
  Future<ConnectionProbeResult> check(
    ConnectionProfile profile,
    ConnectionSecrets secrets,
  ) async => result;
}

Future<void> _waitUntil(bool Function() condition) async {
  final deadline = DateTime.now().add(const Duration(seconds: 2));
  while (!condition()) {
    if (DateTime.now().isAfter(deadline)) {
      throw TimeoutException('Condition was not reached before timeout.');
    }
    await Future<void>.delayed(Duration.zero);
  }
}
