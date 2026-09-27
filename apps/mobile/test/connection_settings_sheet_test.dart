import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gunther_mobile/data/models/connection_profile.dart';
import 'package:gunther_mobile/data/services/connection_profile_store.dart';
import 'package:gunther_mobile/data/services/workspace_connection_service.dart';
import 'package:gunther_mobile/features/connection/presentation/connection_controller.dart';
import 'package:gunther_mobile/features/connection/presentation/connection_settings_sheet.dart';

void main() {
  testWidgets('pairs, reports online, and disconnects without exposing token', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final profileStore = _MemoryProfileStore();
    final secretStore = MemoryConnectionSecretStore();
    final controller = ConnectionController(
      profileStore: profileStore,
      secretStore: secretStore,
      probe: const _OnlineProbe(),
    );
    addTearDown(controller.dispose);
    await controller.initialize();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ConnectionSettingsSheet(
            controller: controller,
            pairingService: const _FakePairingService(),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Not connected'), findsWidgets);
    await tester.enterText(
      find.byKey(const Key('pairing-bundle')),
      '''Gunther device pairing
Connection address: https://knowledge.example/api/
Pairing ID: pair_0123456789abcdef01234567
Pairing code: abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ''',
    );
    await tester.scrollUntilVisible(
      find.byKey(const Key('apply-pairing-bundle')),
      220,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.byKey(const Key('apply-pairing-bundle')));
    await tester.pump();
    expect(
      find.textContaining('Review the workspace address and device name'),
      findsOneWidget,
    );
    expect(
      tester
          .widget<TextField>(find.byKey(const Key('workspace-address')))
          .controller
          ?.text,
      'https://knowledge.example/api/',
    );
    await tester.scrollUntilVisible(
      find.byKey(const Key('pair-workspace')),
      360,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.byKey(const Key('pair-workspace')));
    await tester.pumpAndSettle();

    expect(controller.status, WorkspaceConnectionStatus.online);
    expect(controller.activeProfile?.workspaceId, 'workspace-test');
    expect(controller.activeSecrets?.accessToken, 'paired-secret-token');
    expect(find.text('Test workspace'), findsOneWidget);
    expect(
      find.textContaining('Connected. Captures can now sync'),
      findsOneWidget,
    );
    expect(find.text('paired-secret-token'), findsNothing);

    await tester.scrollUntilVisible(
      find.byKey(const Key('disconnect-workspace')),
      -360,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.byKey(const Key('disconnect-workspace')));
    await tester.pumpAndSettle();

    expect(controller.status, WorkspaceConnectionStatus.unconfigured);
    expect(controller.activeProfile, isNull);
    expect(controller.activeSecrets, isNull);
    expect(await secretStore.read('workspace:workspace-test'), isNull);
  });
}

class _FakePairingService extends WorkspacePairingService {
  const _FakePairingService();

  @override
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
    return PairedWorkspace(
      profile: ConnectionProfile(
        id: 'workspace:workspace-test',
        workspaceId: 'workspace-test',
        label: 'Test workspace',
        baseUri: baseUri,
        trustMode: ConnectionTrustMode.system,
        deviceId: 'device-test',
        protocolVersion: 1,
      ),
      secrets: ConnectionSecrets(accessToken: 'paired-secret-token'),
    );
  }
}

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
