import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:gunther_mobile/data/models/capture.dart';
import 'package:gunther_mobile/data/models/capture_outbox.dart';
import 'package:gunther_mobile/data/services/capture_outbox_service.dart';

void main() {
  group('CaptureOutboxService', () {
    test(
      'persists versioned payload entries with stable ids across restart',
      () async {
        final root = await _temporaryOutbox();
        addTearDown(() => _deleteDirectory(root));
        final service = _service(root, ids: ['note_entry_0001']);

        final queued = await service.enqueuePayload(
          kind: CaptureOutboxKind.quickNote,
          payload: const {
            'title': 'Offline note',
            'content': 'Captured before the request.',
          },
          targetWorkspaceId: 'workspace-alpha',
          connectionProfileId: 'profile-alpha',
        );

        expect(queued.id, 'note_entry_0001');
        expect(queued.state, CaptureOutboxState.pending);
        expect(queued.targetWorkspaceId, 'workspace-alpha');
        expect(queued.connectionProfileId, 'profile-alpha');
        final manifest =
            jsonDecode(await File('${root.path}/manifest.json').readAsString())
                as Map<String, Object?>;
        expect(manifest['version'], CaptureOutboxService.manifestVersion);
        expect(
          root.listSync().whereType<File>().where(
            (file) => file.path.endsWith('.tmp'),
          ),
          isEmpty,
        );

        final restarted = _service(root, ids: ['unused_entry_01']);
        final loaded = await restarted.loadEntry(queued.id);
        expect(loaded?.payload['content'], 'Captured before the request.');
        expect(loaded?.targetWorkspaceId, 'workspace-alpha');
        expect(loaded?.connectionProfileId, 'profile-alpha');
        expect((await restarted.listEntries()).single.id, queued.id);
      },
    );

    test(
      'stages an independent original and deletes it only after success',
      () async {
        final root = await _temporaryOutbox();
        final sourceDirectory = await Directory.systemTemp.createTemp(
          'gunther-outbox-source-',
        );
        addTearDown(() => _deleteDirectory(root));
        addTearDown(() => _deleteDirectory(sourceDirectory));
        final source = File('${sourceDirectory.path}/paper.pdf');
        await source.writeAsBytes(utf8.encode('original bytes'), flush: true);
        final service = _service(root, ids: ['asset_entry_0001']);

        final queued = await service.enqueueAsset(
          kind: CaptureOutboxKind.document,
          asset: CapturedAsset(
            path: source.path,
            fileName: 'paper.pdf',
            mediaType: 'application/pdf',
            sizeBytes: await source.length(),
          ),
          payload: const {'title': 'Paper', 'kind': 'file'},
          targetWorkspaceId: 'workspace-assets',
          connectionProfileId: 'profile-assets',
        );
        final staged = await service.resolveStagedAsset(queued.id);
        expect(staged.path, isNot(source.path));
        expect(await File(staged.path).readAsString(), 'original bytes');

        await source.writeAsString('changed picker file', flush: true);
        final uploading = await service.markAttemptStarted(queued.id);
        expect(uploading.attemptCount, 1);
        expect(uploading.targetWorkspaceId, 'workspace-assets');
        expect(uploading.connectionProfileId, 'profile-assets');
        final failed = await service.markAttemptFailed(
          queued.id,
          error: 'network unavailable',
        );
        expect(failed.state, CaptureOutboxState.retryable);
        expect(failed.lastError, 'network unavailable');

        final restarted = _service(root, ids: ['unused_entry_02']);
        final recovered = (await restarted.listEntries()).single;
        expect(recovered.targetWorkspaceId, 'workspace-assets');
        expect(recovered.connectionProfileId, 'profile-assets');
        final recoveredAsset = await restarted.resolveStagedAsset(recovered.id);
        expect(
          await File(recoveredAsset.path).readAsString(),
          'original bytes',
        );

        await restarted.confirmServerSuccess(recovered.id);
        expect(await restarted.loadEntry(recovered.id), isNull);
        expect(await File(recoveredAsset.path).exists(), isFalse);
        expect(await source.exists(), isTrue);
      },
    );

    test(
      'atomically migrates v1 checksum data to an unassigned v3 manifest',
      () async {
        final root = await _temporaryOutbox();
        final sourceDirectory = await Directory.systemTemp.createTemp(
          'gunther-outbox-v1-source-',
        );
        addTearDown(() => _deleteDirectory(root));
        addTearDown(() => _deleteDirectory(sourceDirectory));
        final source = File('${sourceDirectory.path}/legacy.pdf');
        await source.writeAsBytes(utf8.encode('legacy original'), flush: true);
        final service = _service(root, ids: ['legacy_asset_001']);
        final queued = await service.enqueueAsset(
          kind: CaptureOutboxKind.document,
          asset: CapturedAsset(
            path: source.path,
            fileName: 'legacy.pdf',
            mediaType: 'application/pdf',
            sizeBytes: await source.length(),
          ),
          payload: const {'title': 'Legacy paper', 'serverKind': 'file'},
        );
        final expectedChecksum = queued.stagedFile!.sha256;
        final manifestFile = File('${root.path}/manifest.json');
        final legacy =
            jsonDecode(await manifestFile.readAsString())
                as Map<String, Object?>;
        legacy['version'] = 1;
        final entries = legacy['entries']! as List<Object?>;
        final entry = entries.single! as Map<String, Object?>;
        final staged = entry['stagedFile']! as Map<String, Object?>;
        staged.remove('sha256');
        entry
          ..['targetWorkspaceId'] = 'must-not-bind'
          ..['connectionProfileId'] = 'must-not-bind';
        await manifestFile.writeAsString(jsonEncode(legacy), flush: true);

        final restarted = _service(root, ids: ['unused_legacy_01']);
        final migrated = (await restarted.listEntries()).single;

        expect(migrated.stagedFile?.sha256, expectedChecksum);
        expect(migrated.targetWorkspaceId, isNull);
        expect(migrated.connectionProfileId, isNull);
        final rewritten =
            jsonDecode(await manifestFile.readAsString())
                as Map<String, Object?>;
        expect(rewritten['version'], CaptureOutboxService.manifestVersion);
        final rewrittenEntry =
            (rewritten['entries']! as List<Object?>).single!
                as Map<String, Object?>;
        final rewrittenFile =
            rewrittenEntry['stagedFile']! as Map<String, Object?>;
        expect(rewrittenFile['sha256'], expectedChecksum);
        expect(rewrittenEntry['targetWorkspaceId'], isNull);
        expect(rewrittenEntry['connectionProfileId'], isNull);
        expect(
          root.listSync().whereType<File>().where(
            (file) => file.path.endsWith('.tmp'),
          ),
          isEmpty,
        );
        final recovered = await restarted.resolveStagedAsset(migrated.id);
        expect(await File(recovered.path).readAsString(), 'legacy original');
      },
    );

    test(
      'atomically migrates v2 entries as explicitly unassigned v3 entries',
      () async {
        final root = await _temporaryOutbox();
        addTearDown(() => _deleteDirectory(root));
        final service = _service(root, ids: ['version2_entry_01']);
        final queued = await service.enqueuePayload(
          kind: CaptureOutboxKind.source,
          payload: const {'title': 'Old capture', 'content': 'Preserve me'},
          targetWorkspaceId: 'injected-workspace',
          connectionProfileId: 'injected-profile',
        );
        final manifestFile = File('${root.path}/manifest.json');
        final version2 =
            jsonDecode(await manifestFile.readAsString())
                as Map<String, Object?>;
        version2['version'] = 2;
        await manifestFile.writeAsString(jsonEncode(version2), flush: true);

        final restarted = _service(root, ids: ['unused_version2_1']);
        final migrated = (await restarted.listEntries()).single;

        expect(migrated.id, queued.id);
        expect(migrated.payload['content'], 'Preserve me');
        expect(migrated.targetWorkspaceId, isNull);
        expect(migrated.connectionProfileId, isNull);
        final rewritten =
            jsonDecode(await manifestFile.readAsString())
                as Map<String, Object?>;
        expect(rewritten['version'], 3);
        final rewrittenEntry =
            (rewritten['entries']! as List<Object?>).single!
                as Map<String, Object?>;
        expect(rewrittenEntry['targetWorkspaceId'], isNull);
        expect(rewrittenEntry['connectionProfileId'], isNull);
        expect(
          root.listSync().whereType<File>().where(
            (file) => file.path.endsWith('.tmp'),
          ),
          isEmpty,
        );
      },
    );

    test(
      'a failed v1 migration preserves the legacy manifest and staged file',
      () async {
        final root = await _temporaryOutbox();
        final sourceDirectory = await Directory.systemTemp.createTemp(
          'gunther-outbox-v1-invalid-',
        );
        addTearDown(() => _deleteDirectory(root));
        addTearDown(() => _deleteDirectory(sourceDirectory));
        final source = File('${sourceDirectory.path}/legacy.txt');
        await source.writeAsString('preserve these bytes', flush: true);
        final service = _service(root, ids: ['legacy_asset_002']);
        final queued = await service.enqueueAsset(
          kind: CaptureOutboxKind.document,
          asset: CapturedAsset(
            path: source.path,
            fileName: 'legacy.txt',
            mediaType: 'text/plain',
            sizeBytes: await source.length(),
          ),
        );
        final recovered = await service.resolveStagedAsset(queued.id);
        final stagedFile = File(recovered.path);
        final manifestFile = File('${root.path}/manifest.json');
        final legacy =
            jsonDecode(await manifestFile.readAsString())
                as Map<String, Object?>;
        legacy['version'] = 1;
        final entry =
            (legacy['entries']! as List<Object?>).single!
                as Map<String, Object?>;
        final staged = entry['stagedFile']! as Map<String, Object?>;
        staged
          ..remove('sha256')
          ..['sizeBytes'] = (staged['sizeBytes']! as int) + 1;
        final legacyText = jsonEncode(legacy);
        await manifestFile.writeAsString(legacyText, flush: true);

        final restarted = _service(root, ids: ['unused_legacy_02']);
        await expectLater(
          restarted.listEntries(),
          throwsA(isA<CaptureOutboxCorruptManifestException>()),
        );

        expect(await manifestFile.readAsString(), legacyText);
        expect(await stagedFile.readAsString(), 'preserve these bytes');
        expect(await stagedFile.exists(), isTrue);
      },
    );

    test(
      'a missing v1 staged path fails closed without rewriting or deleting bytes',
      () async {
        final root = await _temporaryOutbox();
        final sourceDirectory = await Directory.systemTemp.createTemp(
          'gunther-outbox-v1-missing-',
        );
        addTearDown(() => _deleteDirectory(root));
        addTearDown(() => _deleteDirectory(sourceDirectory));
        final source = File('${sourceDirectory.path}/legacy.m4a');
        await source.writeAsString('recoverable audio bytes', flush: true);
        final service = _service(root, ids: ['legacy_audio_001']);
        final queued = await service.enqueueAsset(
          kind: CaptureOutboxKind.audio,
          asset: CapturedAsset(
            path: source.path,
            fileName: 'legacy.m4a',
            mediaType: 'audio/mp4',
            sizeBytes: await source.length(),
          ),
        );
        final resolved = await service.resolveStagedAsset(queued.id);
        final stagedFile = File(resolved.path);
        final displacedFile = await stagedFile.rename(
          '${stagedFile.path}.missing',
        );
        final manifestFile = File('${root.path}/manifest.json');
        final legacy =
            jsonDecode(await manifestFile.readAsString())
                as Map<String, Object?>;
        legacy['version'] = 1;
        final entry =
            (legacy['entries']! as List<Object?>).single!
                as Map<String, Object?>;
        final staged = entry['stagedFile']! as Map<String, Object?>;
        staged.remove('sha256');
        final legacyText = jsonEncode(legacy);
        await manifestFile.writeAsString(legacyText, flush: true);

        final restarted = _service(root, ids: ['unused_legacy_03']);
        await expectLater(
          restarted.listEntries(),
          throwsA(isA<CaptureOutboxCorruptManifestException>()),
        );

        expect(await manifestFile.readAsString(), legacyText);
        expect(await displacedFile.readAsString(), 'recoverable audio bytes');
        expect(await source.readAsString(), 'recoverable audio bytes');
      },
    );

    test('explicitly binds an unattempted unassigned entry', () async {
      final root = await _temporaryOutbox();
      addTearDown(() => _deleteDirectory(root));
      final service = _service(root, ids: ['bind_entry_00001']);
      final queued = await service.enqueuePayload(
        kind: CaptureOutboxKind.link,
        payload: const {'url': 'https://example.com/evidence'},
      );

      final bound = await service.bindUnassignedEntry(
        queued.id,
        workspaceId: 'workspace-explicit',
        profileId: 'profile-explicit',
      );

      expect(bound.state, CaptureOutboxState.pending);
      expect(bound.attemptCount, 0);
      expect(bound.targetWorkspaceId, 'workspace-explicit');
      expect(bound.connectionProfileId, 'profile-explicit');
      final restarted = _service(root, ids: ['unused_bind_001']);
      final restored = await restarted.loadEntry(queued.id);
      expect(restored?.targetWorkspaceId, 'workspace-explicit');
      expect(restored?.connectionProfileId, 'profile-explicit');
    });

    test(
      'persists webpage snapshot semantics and stable id across an offline retry',
      () async {
        final root = await _temporaryOutbox();
        addTearDown(() => _deleteDirectory(root));
        final service = _service(root, ids: ['web_snapshot_0001']);
        final queued = await service.enqueuePayload(
          kind: CaptureOutboxKind.link,
          payload: const {
            'url': 'https://example.com/evidence',
            'title': 'Evidence page',
            'notes': 'Capture the original page, not only this URL.',
            'knowledgeBaseId': 'biology',
          },
          targetWorkspaceId: 'workspace-web',
          connectionProfileId: 'profile-web',
        );
        await service.markAttemptStarted(queued.id);
        await service.markAttemptFailed(queued.id, error: 'Workspace offline');

        final restarted = _service(root, ids: ['unused_web_id_1']);
        final retry = await restarted.loadEntry(queued.id);

        expect(retry, isNotNull);
        expect(retry?.id, 'web_snapshot_0001');
        expect(retry?.kind, CaptureOutboxKind.link);
        expect(retry?.state, CaptureOutboxState.retryable);
        expect(retry?.attemptCount, 1);
        expect(retry?.targetWorkspaceId, 'workspace-web');
        expect(retry?.connectionProfileId, 'profile-web');
        expect(retry?.payload, queued.payload);
        expect(retry?.payload, isNot(contains('content')));
        expect(retry?.payload, isNot(contains('clientCaptureId')));
      },
    );

    test('binds a retryable entry only when no attempt has started', () async {
      final root = await _temporaryOutbox();
      addTearDown(() => _deleteDirectory(root));
      final service = _service(root, ids: ['retry_bind_00001']);
      final queued = await service.enqueuePayload(
        kind: CaptureOutboxKind.web,
        payload: const {'query': 'offline research'},
      );
      final retryable = await service.markAttemptFailed(
        queued.id,
        error: 'Connection was unavailable before upload.',
      );
      expect(retryable.attemptCount, 0);
      expect(retryable.lastAttemptAt, isNull);

      final bound = await service.bindUnassignedEntry(
        queued.id,
        workspaceId: 'workspace-retry',
        profileId: 'profile-retry',
      );

      expect(bound.state, CaptureOutboxState.retryable);
      expect(bound.targetWorkspaceId, 'workspace-retry');
      expect(bound.connectionProfileId, 'profile-retry');
    });

    test('rejects partial and malformed target bindings before enqueue', () {
      final root = Directory('${Directory.systemTemp.path}/unused-outbox-root');
      final service = _service(root, ids: ['never_allocated_1']);

      expect(
        () => service.enqueuePayload(
          kind: CaptureOutboxKind.quickNote,
          payload: const {'content': 'Do not persist'},
          targetWorkspaceId: 'workspace-only',
        ),
        throwsA(isA<CaptureOutboxException>()),
      );
      expect(
        () => service.enqueuePayload(
          kind: CaptureOutboxKind.quickNote,
          payload: const {'content': 'Do not persist'},
          targetWorkspaceId: 'unsafe/workspace',
          connectionProfileId: 'profile-safe',
        ),
        throwsA(isA<CaptureOutboxException>()),
      );
    });

    test('rejects rebinding an entry that already has a target', () async {
      final root = await _temporaryOutbox();
      addTearDown(() => _deleteDirectory(root));
      final service = _service(root, ids: ['bound_entry_0001']);
      final queued = await service.enqueuePayload(
        kind: CaptureOutboxKind.source,
        payload: const {'content': 'Bound once'},
        targetWorkspaceId: 'workspace-original',
        connectionProfileId: 'profile-original',
      );

      await expectLater(
        service.bindUnassignedEntry(
          queued.id,
          workspaceId: 'workspace-other',
          profileId: 'profile-other',
        ),
        throwsA(
          isA<CaptureOutboxException>().having(
            (error) => error.message,
            'message',
            contains('already has an assigned target'),
          ),
        ),
      );

      final preserved = await service.loadEntry(queued.id);
      expect(preserved?.targetWorkspaceId, 'workspace-original');
      expect(preserved?.connectionProfileId, 'profile-original');
    });

    test('rejects binding while uploading or after an attempt', () async {
      final root = await _temporaryOutbox();
      addTearDown(() => _deleteDirectory(root));
      final service = _service(root, ids: ['attempted_entry1']);
      final queued = await service.enqueuePayload(
        kind: CaptureOutboxKind.quickNote,
        payload: const {'content': 'Attempted capture'},
      );
      await service.markAttemptStarted(queued.id);

      await expectLater(
        service.bindUnassignedEntry(
          queued.id,
          workspaceId: 'workspace-late',
          profileId: 'profile-late',
        ),
        throwsA(isA<CaptureOutboxException>()),
      );
      await service.markAttemptFailed(queued.id, error: 'Offline');
      await expectLater(
        service.bindUnassignedEntry(
          queued.id,
          workspaceId: 'workspace-late',
          profileId: 'profile-late',
        ),
        throwsA(
          isA<CaptureOutboxException>().having(
            (error) => error.message,
            'message',
            contains('after an upload attempt'),
          ),
        ),
      );
      final preserved = await service.loadEntry(queued.id);
      expect(preserved?.targetWorkspaceId, isNull);
      expect(preserved?.connectionProfileId, isNull);
    });

    test(
      'detects same-length staged-file tampering with streaming SHA-256',
      () async {
        final root = await _temporaryOutbox();
        final sourceDirectory = await Directory.systemTemp.createTemp(
          'gunther-outbox-checksum-',
        );
        addTearDown(() => _deleteDirectory(root));
        addTearDown(() => _deleteDirectory(sourceDirectory));
        final source = File('${sourceDirectory.path}/evidence.txt');
        await source.writeAsString('abcdef', flush: true);
        final service = _service(root, ids: ['hash_entry_00001']);
        final queued = await service.enqueueAsset(
          kind: CaptureOutboxKind.document,
          asset: CapturedAsset(
            path: source.path,
            fileName: 'evidence.txt',
            mediaType: 'text/plain',
            sizeBytes: 6,
          ),
        );
        final relativePath = queued.stagedFile!.relativePath.replaceAll(
          '/',
          Platform.pathSeparator,
        );
        final staged = File(
          '${root.path}${Platform.pathSeparator}$relativePath',
        );
        await staged.writeAsString('ghijkl', flush: true);

        await expectLater(
          service.resolveStagedAsset(queued.id),
          throwsA(
            isA<CaptureOutboxException>().having(
              (error) => error.message,
              'message',
              contains('checksum'),
            ),
          ),
        );
        expect(await staged.length(), 6);
        expect(await service.loadEntry(queued.id), isNotNull);
      },
    );

    test(
      'recovers an interrupted upload into an explicit retry state',
      () async {
        final root = await _temporaryOutbox();
        addTearDown(() => _deleteDirectory(root));
        final service = _service(root, ids: ['source_entry_001']);
        final queued = await service.enqueuePayload(
          kind: CaptureOutboxKind.source,
          payload: const {'title': 'Saved source', 'content': 'Exact payload'},
        );
        await service.markAttemptStarted(queued.id);

        final restarted = _service(root, ids: ['unused_entry_03']);
        final recovered = (await restarted.recoverInterruptedAttempts()).single;
        expect(recovered.state, CaptureOutboxState.retryable);
        expect(recovered.attemptCount, 1);
        expect(recovered.lastError, contains('before the server confirmed'));
      },
    );

    test('applies separate bounded generic and audio policies', () async {
      final root = await _temporaryOutbox();
      final sourceDirectory = await Directory.systemTemp.createTemp(
        'gunther-outbox-limits-',
      );
      addTearDown(() => _deleteDirectory(root));
      addTearDown(() => _deleteDirectory(sourceDirectory));
      final source = File('${sourceDirectory.path}/sample.bin');
      await source.writeAsBytes([1, 2, 3, 4, 5], flush: true);
      final ids = ['asset_limit_0001', 'audio_limit_0001'].iterator;
      final service = CaptureOutboxService(
        directoryProvider: () async => root,
        idProvider: () {
          ids.moveNext();
          return ids.current;
        },
        maxAssetBytes: 4,
        maxAudioBytes: 8,
      );
      final asset = CapturedAsset(
        path: source.path,
        fileName: 'sample.bin',
        mediaType: 'application/octet-stream',
        sizeBytes: 5,
      );

      await expectLater(
        service.enqueueAsset(kind: CaptureOutboxKind.document, asset: asset),
        throwsA(
          isA<CaptureOutboxException>().having(
            (error) => error.message,
            'message',
            contains('4 B'),
          ),
        ),
      );
      final audio = await service.enqueueAsset(
        kind: CaptureOutboxKind.audio,
        asset: CapturedAsset(
          path: source.path,
          fileName: 'sample.m4a',
          mediaType: 'audio/mp4',
          sizeBytes: 5,
        ),
      );
      expect(audio.stagedFile?.sizeBytes, 5);
      expect(CaptureOutboxService.defaultMaxAssetBytes, 512 * 1024 * 1024);
      expect(CaptureOutboxService.defaultMaxAudioBytes, 2 * 1024 * 1024 * 1024);
    });

    test('rejects a sparse over-limit audio file before staging', () async {
      final root = await _temporaryOutbox();
      final sourceDirectory = await Directory.systemTemp.createTemp(
        'gunther-outbox-sparse-limit-',
      );
      addTearDown(() => _deleteDirectory(root));
      addTearDown(() => _deleteDirectory(sourceDirectory));
      final source = File('${sourceDirectory.path}/too-large.wav');
      final handle = await source.open(mode: FileMode.write);
      await handle.truncate(CaptureOutboxService.defaultMaxAudioBytes + 1);
      await handle.close();
      final service = _service(root, ids: ['must_not_allocate']);

      await expectLater(
        service.enqueueAsset(
          kind: CaptureOutboxKind.audio,
          asset: CapturedAsset(
            path: source.absolute.path,
            fileName: 'too-large.wav',
            mediaType: 'audio/wav',
            sizeBytes: 1,
          ),
        ),
        throwsA(
          isA<CaptureOutboxException>().having(
            (error) => error.message,
            'message',
            contains('2 GB'),
          ),
        ),
      );
      expect(await Directory('${root.path}/files').exists(), isFalse);
      expect(
        await source.length(),
        CaptureOutboxService.defaultMaxAudioBytes + 1,
      );
    });

    test(
      'a corrupt manifest blocks cleanup and preserves staged originals',
      () async {
        final root = await _temporaryOutbox();
        final sourceDirectory = await Directory.systemTemp.createTemp(
          'gunther-outbox-corrupt-',
        );
        addTearDown(() => _deleteDirectory(root));
        addTearDown(() => _deleteDirectory(sourceDirectory));
        final source = File('${sourceDirectory.path}/photo.jpg');
        await source.writeAsBytes([8, 6, 7, 5, 3, 0, 9], flush: true);
        final service = _service(root, ids: ['photo_entry_0001']);
        final queued = await service.enqueueAsset(
          kind: CaptureOutboxKind.photo,
          asset: CapturedAsset(
            path: source.path,
            fileName: 'photo.jpg',
            mediaType: 'image/jpeg',
            sizeBytes: await source.length(),
          ),
        );
        final staged = await service.resolveStagedAsset(queued.id);
        await File(
          '${root.path}/manifest.json',
        ).writeAsString('{"version":1,"entries":[broken', flush: true);

        await expectLater(
          service.listEntries(),
          throwsA(isA<CaptureOutboxCorruptManifestException>()),
        );
        await expectLater(
          service.confirmServerSuccess(queued.id),
          throwsA(isA<CaptureOutboxCorruptManifestException>()),
        );
        expect(await File(staged.path).exists(), isTrue);
        expect(await File(staged.path).readAsBytes(), [8, 6, 7, 5, 3, 0, 9]);
      },
    );

    test(
      'quarantines only a damaged index after confirmation and preserves originals',
      () async {
        final root = await _temporaryOutbox();
        final sourceRoot = await Directory.systemTemp.createTemp(
          'gunther-quarantine-source-',
        );
        addTearDown(() => _deleteDirectory(root));
        addTearDown(() => _deleteDirectory(sourceRoot));
        final source = File('${sourceRoot.path}/evidence.jpg');
        await source.writeAsBytes([1, 3, 3, 7], flush: true);
        final service = _service(root, ids: ['quarantine_entry']);
        final queued = await service.enqueueAsset(
          kind: CaptureOutboxKind.photo,
          asset: CapturedAsset(
            path: source.path,
            fileName: 'evidence.jpg',
            mediaType: 'image/jpeg',
            sizeBytes: 4,
          ),
        );
        final staged = await service.resolveStagedAsset(queued.id);
        await File(
          '${root.path}/manifest.json',
        ).writeAsString('{damaged-index', flush: true);

        final damaged = await service.inspectMaintenance();
        expect(damaged.manifestHealthy, isFalse);
        expect(
          () => service.quarantineDamagedManifest(userConfirmed: false),
          throwsA(isA<CaptureOutboxException>()),
        );
        expect(await File(staged.path).exists(), isTrue);

        final quarantined = await service.quarantineDamagedManifest(
          userConfirmed: true,
        );

        expect(await File(quarantined).readAsString(), '{damaged-index');
        expect(await File(staged.path).readAsBytes(), [1, 3, 3, 7]);
        expect(await service.listEntries(), isEmpty);
        final repaired = await service.inspectMaintenance();
        expect(repaired.manifestHealthy, isTrue);
        expect(repaired.quarantinedManifestCount, 1);
      },
    );

    test(
      'cleanup deletes only paths proven server-confirmed by its journal',
      () async {
        final root = await _temporaryOutbox();
        final sourceRoot = await Directory.systemTemp.createTemp(
          'gunther-confirmed-cleanup-',
        );
        addTearDown(() => _deleteDirectory(root));
        addTearDown(() => _deleteDirectory(sourceRoot));
        final source = File('${sourceRoot.path}/paper.pdf');
        await source.writeAsBytes([4, 2], flush: true);
        final service = _service(root, ids: ['cleanup_entry_01']);
        final queued = await service.enqueueAsset(
          kind: CaptureOutboxKind.document,
          asset: CapturedAsset(
            path: source.path,
            fileName: 'paper.pdf',
            mediaType: 'application/pdf',
            sizeBytes: 2,
          ),
        );
        final staged = await service.resolveStagedAsset(queued.id);
        final relative = queued.stagedFile!.relativePath;
        await File('${root.path}/confirmed-cleanup.json').writeAsString(
          jsonEncode({
            'version': 1,
            'paths': [relative],
          }),
          flush: true,
        );

        // A journal path still referenced by the active manifest is never safe.
        expect(await service.cleanConfirmedFiles(), 0);
        expect(await File(staged.path).exists(), isTrue);

        await File('${root.path}/manifest.json').writeAsString(
          jsonEncode({'version': 3, 'entries': <Object?>[]}),
          flush: true,
        );
        final health = await service.inspectMaintenance();
        expect(health.confirmedFilesReadyForCleanup, 1);
        expect(await service.cleanConfirmedFiles(), 1);
        expect(await File(staged.path).exists(), isFalse);
        expect(
          await File('${root.path}/confirmed-cleanup.json').exists(),
          isFalse,
        );
      },
    );

    test(
      'damaged cleanup journal is quarantined without deleting captures',
      () async {
        final root = await _temporaryOutbox();
        addTearDown(() => _deleteDirectory(root));
        final service = _service(root, ids: ['cleanup_damage_01']);
        await service.enqueuePayload(
          kind: CaptureOutboxKind.quickNote,
          payload: const {'title': 'Keep', 'content': 'Keep this capture'},
        );
        final journal = File('${root.path}/confirmed-cleanup.json');
        await journal.writeAsString('{broken-cleanup', flush: true);

        final health = await service.inspectMaintenance();
        expect(health.manifestHealthy, isTrue);
        expect(health.issueCode, 'cleanup_journal_corrupt');
        await expectLater(
          service.cleanConfirmedFiles(),
          throwsA(isA<CaptureOutboxCorruptCleanupException>()),
        );

        final quarantined = await service.quarantineDamagedCleanupJournal(
          userConfirmed: true,
        );
        expect(await File(quarantined).readAsString(), '{broken-cleanup');
        expect(await service.listEntries(), hasLength(1));
        expect((await service.inspectMaintenance()).issueCode, isNull);
      },
    );

    test('rejects unsafe file names and manifest traversal paths', () async {
      final root = await _temporaryOutbox();
      final sourceDirectory = await Directory.systemTemp.createTemp(
        'gunther-outbox-paths-',
      );
      addTearDown(() => _deleteDirectory(root));
      addTearDown(() => _deleteDirectory(sourceDirectory));
      final source = File('${sourceDirectory.path}/safe.pdf');
      await source.writeAsString('safe', flush: true);
      final service = _service(root, ids: ['unsafe_entry_001']);

      await expectLater(
        service.enqueueAsset(
          kind: CaptureOutboxKind.document,
          asset: CapturedAsset(
            path: source.path,
            fileName: '../safe.pdf',
            mediaType: 'application/pdf',
            sizeBytes: 4,
          ),
        ),
        throwsA(isA<CaptureOutboxException>()),
      );

      await root.create(recursive: true);
      await File('${root.path}/manifest.json').writeAsString(
        jsonEncode({
          'version': 1,
          'entries': [
            {
              'id': 'unsafe_entry_001',
              'kind': 'document',
              'payload': <String, Object?>{},
              'stagedFile': {
                'relativePath': 'files/../outside.pdf',
                'fileName': 'safe.pdf',
                'mediaType': 'application/pdf',
                'sizeBytes': 4,
              },
              'state': 'pending',
              'attemptCount': 0,
              'createdAt': '2026-08-30T00:00:00.000Z',
              'updatedAt': '2026-08-30T00:00:00.000Z',
              'lastAttemptAt': null,
              'lastError': null,
            },
          ],
        }),
        flush: true,
      );
      await expectLater(
        service.listEntries(),
        throwsA(isA<CaptureOutboxCorruptManifestException>()),
      );
    });

    test(
      'serializes concurrent writers without lost manifest entries',
      () async {
        final root = await _temporaryOutbox();
        addTearDown(() => _deleteDirectory(root));
        var alpha = 0;
        var beta = 0;
        final serviceA = CaptureOutboxService(
          directoryProvider: () async => root,
          idProvider: () => 'alpha_${(++alpha).toString().padLeft(8, '0')}',
        );
        final serviceB = CaptureOutboxService(
          directoryProvider: () async => root,
          idProvider: () => 'beta_${(++beta).toString().padLeft(8, '0')}',
        );

        await Future.wait([
          for (var index = 0; index < 10; index += 1)
            serviceA.enqueuePayload(
              kind: CaptureOutboxKind.link,
              payload: {'url': 'https://example.com/a/$index'},
            ),
          for (var index = 0; index < 10; index += 1)
            serviceB.enqueuePayload(
              kind: CaptureOutboxKind.web,
              payload: {'query': 'query $index'},
            ),
        ]);

        final entries = await serviceA.listEntries();
        expect(entries, hasLength(20));
        expect(entries.map((entry) => entry.id).toSet(), hasLength(20));
      },
    );
  });
}

Future<Directory> _temporaryOutbox() {
  return Directory.systemTemp.createTemp('gunther-capture-outbox-');
}

CaptureOutboxService _service(Directory root, {required List<String> ids}) {
  final iterator = ids.iterator;
  return CaptureOutboxService(
    directoryProvider: () async => root,
    idProvider: () {
      if (!iterator.moveNext()) {
        throw StateError('The test ran out of capture ids.');
      }
      return iterator.current;
    },
    clock: () => DateTime.utc(2026, 8, 30, 8),
  );
}

Future<void> _deleteDirectory(Directory directory) async {
  if (await directory.exists()) await directory.delete(recursive: true);
}
