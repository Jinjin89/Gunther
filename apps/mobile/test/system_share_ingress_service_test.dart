import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gunther_mobile/data/models/capture_outbox.dart';
import 'package:gunther_mobile/data/services/capture_outbox_service.dart';
import 'package:gunther_mobile/data/services/system_share_ingress_service.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test(
    'method channel reports native processing and completion states',
    () async {
      const channel = MethodChannel(systemShareMethodChannelName);
      final messenger =
          TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
      messenger.setMockMethodCallHandler(channel, (call) async {
        if (call.method == 'getShareStagingState') return 'processing';
        return null;
      });
      addTearDown(() => messenger.setMockMethodCallHandler(channel, null));
      final ingress = MethodChannelSystemShareIngress(channel: channel);
      addTearDown(ingress.dispose);
      String? reported;
      ingress.setStagingStateHandler((state) => reported = state);

      expect(await ingress.currentStagingState(), 'processing');
      await messenger.handlePlatformMessage(
        channel.name,
        const StandardMethodCodec().encodeMethodCall(
          const MethodCall('shareStagingState', {'state': 'partial_failure'}),
        ),
        null,
      );

      expect(reported, 'partial_failure');
    },
  );

  test(
    'native queue diagnostics require confirmation before quarantine',
    () async {
      const channel = MethodChannel(systemShareMethodChannelName);
      final messenger =
          TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
      final calls = <MethodCall>[];
      messenger.setMockMethodCallHandler(channel, (call) async {
        calls.add(call);
        if (call.method == 'getShareDiagnostics') {
          return <String, Object?>{
            'healthy': false,
            'pendingCount': 0,
            'quarantinedQueueCount': 2,
            'issueCode': 'native_manifest_corrupt',
          };
        }
        return null;
      });
      addTearDown(() => messenger.setMockMethodCallHandler(channel, null));
      final ingress = MethodChannelSystemShareIngress(channel: channel);
      addTearDown(ingress.dispose);

      final diagnostics = await ingress.inspectShareMaintenance();
      expect(diagnostics.healthy, isFalse);
      expect(diagnostics.quarantinedQueueCount, 2);
      expect(
        () => ingress.quarantineDamagedShareQueue(userConfirmed: false),
        throwsFormatException,
      );
      await ingress.quarantineDamagedShareQueue(userConfirmed: true);

      expect(calls.last.method, 'quarantineDamagedShareQueue');
      expect(calls.last.arguments, {'userConfirmed': true});
    },
  );

  test('parses only bounded platform share shapes', () {
    final item = SystemShareItem.fromPlatform(const <Object?, Object?>{
      'id': 'android-share-0001',
      'kind': 'file',
      'path': '/owned/photo.jpg',
      'fileName': 'photo.jpg',
      'mediaType': 'image/jpeg',
      'sizeBytes': 42,
    });
    expect(item.kind, SystemShareKind.file);
    expect(item.fileName, 'photo.jpg');

    expect(
      () => SystemShareItem.fromPlatform(const <Object?, Object?>{
        'id': '../unsafe',
        'kind': 'text',
        'text': 'No',
      }),
      throwsFormatException,
    );
    expect(
      () => SystemShareItem.fromPlatform(const <Object?, Object?>{
        'id': 'share-empty-0001',
        'kind': 'file',
        'path': '/tmp/a',
        'fileName': 'a',
        'mediaType': 'invalid',
        'sizeBytes': 1,
      }),
      throwsFormatException,
    );
  });

  test(
    'stages text, URL, and multiple originals before native acknowledgement',
    () async {
      final root = await Directory.systemTemp.createTemp('gunther-share-test-');
      addTearDown(() async {
        if (await root.exists()) await root.delete(recursive: true);
      });
      final sourceOne = File('${root.path}/incoming-paper.pdf');
      final sourceTwo = File('${root.path}/incoming-photo.jpg');
      await sourceOne.writeAsBytes([1, 2, 3], flush: true);
      await sourceTwo.writeAsBytes([4, 5, 6, 7], flush: true);
      final ingress = _MemoryShareIngress([
        const SystemShareItem(
          id: 'share-text-0001',
          kind: SystemShareKind.text,
          text: 'A useful thought\nwith detail',
        ),
        const SystemShareItem(
          id: 'share-url-00001',
          kind: SystemShareKind.url,
          text: 'https://example.com/research',
        ),
        SystemShareItem(
          id: 'share-file-0001',
          kind: SystemShareKind.file,
          path: sourceOne.path,
          fileName: 'paper.pdf',
          mediaType: 'application/pdf',
          sizeBytes: 3,
        ),
        SystemShareItem(
          id: 'share-image-001',
          kind: SystemShareKind.file,
          path: sourceTwo.path,
          fileName: 'photo.jpg',
          mediaType: 'image/jpeg',
          sizeBytes: 4,
        ),
      ]);
      var nextId = 0;
      final outbox = CaptureOutboxService(
        directoryProvider: () async => Directory('${root.path}/outbox'),
        idProvider: () => 'outbox_share_${nextId++}'.padRight(16, '0'),
      );
      final coordinator = SystemShareIngressCoordinator(
        ingress: ingress,
        outbox: outbox,
        targetProvider: () =>
            (workspaceId: 'workspace-alpha', profileId: 'profile-alpha'),
      );

      final accepted = await coordinator.drain();

      expect(accepted.map((entry) => entry.kind), [
        CaptureOutboxKind.quickNote,
        CaptureOutboxKind.link,
        CaptureOutboxKind.document,
        CaptureOutboxKind.photo,
      ]);
      expect(ingress.acknowledged, [
        'share-text-0001',
        'share-url-00001',
        'share-file-0001',
        'share-image-001',
      ]);
      expect(
        accepted.every(
          (entry) =>
              entry.targetWorkspaceId == 'workspace-alpha' &&
              entry.connectionProfileId == 'profile-alpha',
        ),
        isTrue,
      );
      final stagedPaper = await outbox.resolveStagedAsset(accepted[2].id);
      final stagedPhoto = await outbox.resolveStagedAsset(accepted[3].id);
      expect(stagedPaper.path, isNot(sourceOne.path));
      expect(stagedPhoto.path, isNot(sourceTwo.path));
      expect(await File(stagedPaper.path).readAsBytes(), [1, 2, 3]);
      expect(await File(stagedPhoto.path).readAsBytes(), [4, 5, 6, 7]);
    },
  );

  test(
    'keeps unverified shares unassigned and replay after failed ack is deduplicated',
    () async {
      final root = await Directory.systemTemp.createTemp('gunther-share-ack-');
      addTearDown(() async {
        if (await root.exists()) await root.delete(recursive: true);
      });
      final ingress = _MemoryShareIngress([
        const SystemShareItem(
          id: 'share-offline-01',
          kind: SystemShareKind.text,
          text: 'Captured with no verified workspace',
        ),
      ], failFirstAcknowledgement: true);
      var generatedIds = 0;
      final outbox = CaptureOutboxService(
        directoryProvider: () async => Directory('${root.path}/outbox'),
        idProvider: () => 'share_entry_${generatedIds++}'.padRight(16, '0'),
      );
      final coordinator = SystemShareIngressCoordinator(
        ingress: ingress,
        outbox: outbox,
      );

      expect(await coordinator.drain(), isEmpty);
      expect(coordinator.lastError, isA<StateError>());
      final afterFailedAck = await outbox.listEntries();
      expect(afterFailedAck, hasLength(1));
      expect(afterFailedAck.single.isTargetAssigned, isFalse);

      final replayed = await coordinator.drain();
      expect(replayed, hasLength(1));
      expect(await outbox.listEntries(), hasLength(1));
      expect(generatedIds, 1);
      expect(ingress.acknowledged, ['share-offline-01']);
    },
  );

  test(
    'an acknowledgement retry cannot move a share to a new profile',
    () async {
      final root = await Directory.systemTemp.createTemp('gunther-share-pin-');
      addTearDown(() async {
        if (await root.exists()) await root.delete(recursive: true);
      });
      final ingress = _MemoryShareIngress([
        const SystemShareItem(
          id: 'share-pinned-001',
          kind: SystemShareKind.text,
          text: 'Keep this in the original workspace',
        ),
      ], failFirstAcknowledgement: true);
      final outbox = CaptureOutboxService(
        directoryProvider: () async => Directory('${root.path}/outbox'),
        idProvider: () => 'share_pinned_entry',
      );
      var target = (workspaceId: 'workspace-a', profileId: 'profile-a');
      final coordinator = SystemShareIngressCoordinator(
        ingress: ingress,
        outbox: outbox,
        targetProvider: () => target,
      );

      expect(await coordinator.drain(), isEmpty);
      target = (workspaceId: 'workspace-b', profileId: 'profile-b');
      final replayed = await coordinator.drain();

      expect(replayed, hasLength(1));
      expect(replayed.single.targetWorkspaceId, 'workspace-a');
      expect(replayed.single.connectionProfileId, 'profile-a');
      expect(await outbox.listEntries(), hasLength(1));
    },
  );
}

class _MemoryShareIngress implements SystemShareIngress {
  _MemoryShareIngress(
    List<SystemShareItem> items, {
    this.failFirstAcknowledgement = false,
  }) : _pending = [...items];

  final List<SystemShareItem> _pending;
  final bool failFirstAcknowledgement;
  final List<String> acknowledged = [];
  var _ackAttempts = 0;

  @override
  Future<void> acknowledge(List<String> ids) async {
    _ackAttempts += 1;
    if (failFirstAcknowledgement && _ackAttempts == 1) {
      throw StateError('simulated native acknowledgement failure');
    }
    acknowledged.addAll(ids);
    _pending.removeWhere((item) => ids.contains(item.id));
  }

  @override
  Future<List<SystemShareItem>> pendingItems() async => [..._pending];

  @override
  void setItemsAvailableHandler(Future<void> Function()? handler) {}
}
