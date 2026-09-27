import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gunther_mobile/app.dart';
import 'package:gunther_mobile/data/models/assertion.dart';
import 'package:gunther_mobile/data/models/capture.dart';
import 'package:gunther_mobile/data/models/capture_outbox.dart';
import 'package:gunther_mobile/data/models/inbox_item.dart';
import 'package:gunther_mobile/data/models/knowledge_base.dart';
import 'package:gunther_mobile/data/models/knowledge_graph.dart';
import 'package:gunther_mobile/data/models/overview.dart';
import 'package:gunther_mobile/data/models/source.dart';
import 'package:gunther_mobile/data/models/web_search.dart';
import 'package:gunther_mobile/data/repositories/knowledge_repository.dart';
import 'package:gunther_mobile/data/services/capture_device_service.dart';
import 'package:gunther_mobile/data/services/capture_outbox_service.dart';
import 'package:gunther_mobile/data/services/live_transcription_service.dart';
import 'package:gunther_mobile/data/services/recording_draft_store.dart';
import 'package:gunther_mobile/data/services/system_share_ingress_service.dart';
import 'package:gunther_mobile/features/capture/presentation/capture_view_model.dart';

void main() {
  testWidgets(
    'uses Home, Libraries, and Inbox as the only primary destinations',
    (tester) async {
      await _pumpApp(tester, FakeKnowledgeRepository());

      expect(find.byKey(const Key('nav-home')), findsOneWidget);
      expect(find.byKey(const Key('nav-libraries')), findsOneWidget);
      expect(find.byKey(const Key('nav-inbox')), findsOneWidget);
      expect(find.byType(NavigationDestination), findsNWidgets(3));
      expect(find.byKey(const Key('global-capture-button')), findsOneWidget);
      expect(
        find.text('Everything you capture, ready when you are.'),
        findsOneWidget,
      );
    },
  );

  testWidgets(
    'durable PCM recording feeds live transcription and persists its format',
    (tester) async {
      final repository = FakeKnowledgeRepository();
      final device = FakeLivePcmCaptureDevice();
      final draftStore = MemoryRecordingDraftStore();
      final transcription = FakeLiveTranscriptionSession();
      await _pumpApp(
        tester,
        repository,
        captureDevice: device,
        recordingDraftStore: draftStore,
        liveTranscriptionFactory: (_) => transcription,
      );

      await _openCaptureLauncher(tester);
      await tester.tap(find.byKey(const Key('capture-recording')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('start-recording')));
      await tester.pumpAndSettle();

      expect(transcription.connectCalls, 1);
      expect(
        find.byKey(const Key('recording-transcription-status')),
        findsOneWidget,
      );
      device.emitPcm([1, 2, 3, 4]);
      await tester.pump();
      expect(transcription.pcmBytes, [1, 2, 3, 4]);

      transcription.emit(
        const LiveTranscriptionDeltaEvent(delta: '实时', itemId: 'segment-1'),
      );
      await tester.pumpAndSettle();
      expect(find.text('Listening: 实时'), findsOneWidget);
      transcription.emit(
        const LiveTranscriptionCompletedEvent(
          transcript: '实时转写已经进入知识采集。',
          itemId: 'segment-1',
          provider: 'sensevoice',
        ),
      );
      await tester.pumpAndSettle();
      final transcriptField = tester.widget<TextField>(
        find.byKey(const Key('recording-transcript')),
      );
      expect(transcriptField.controller?.text, contains('实时转写'));

      await tester.ensureVisible(find.byKey(const Key('stop-recording')));
      await tester.tap(find.byKey(const Key('stop-recording')));
      await tester.pumpAndSettle();

      expect(transcription.commitCalls, 1);
      expect(
        draftStore.draft?.encoding,
        RecordingDraft.guntherPcm16WavEncoding,
      );
      expect(draftStore.draft?.sampleRate, 24000);
      expect(draftStore.draft?.channels, 1);
      expect(draftStore.draft?.mediaType, 'audio/wav');
    },
  );

  testWidgets(
    'global Capture launcher gives six source types equal placement',
    (tester) async {
      await _pumpApp(tester, FakeKnowledgeRepository());

      await _openCaptureLauncher(tester);

      expect(find.byKey(const Key('capture-quickNote')), findsOneWidget);
      expect(find.byKey(const Key('capture-document')), findsOneWidget);
      expect(find.byKey(const Key('capture-photo')), findsOneWidget);
      expect(find.byKey(const Key('capture-link')), findsOneWidget);
      expect(find.byKey(const Key('capture-recording')), findsOneWidget);
      expect(find.byKey(const Key('capture-web')), findsOneWidget);
      expect(find.text('Destination: Inbox · Organize later'), findsOneWidget);
    },
  );

  testWidgets(
    'capture recovery sheet explains failures and exports redacted diagnostics',
    (tester) async {
      final now = DateTime.utc(2026, 8, 30);
      final outbox = MaintenanceMemoryCaptureOutboxStore(
        entries: [
          CaptureOutboxEntry(
            id: 'diagnostic_entry_01',
            kind: CaptureOutboxKind.quickNote,
            payload: const {
              'title': 'PRIVATE TITLE',
              'content': 'TOP SECRET BODY',
            },
            state: CaptureOutboxState.retryable,
            attemptCount: 2,
            createdAt: now,
            updatedAt: now,
            lastError: 'Backend offline at https://private.example',
            targetWorkspaceId: 'private-workspace-id',
            connectionProfileId: 'private-profile-id',
          ),
        ],
        maintenance: const CaptureOutboxMaintenanceSnapshot(
          manifestHealthy: true,
          confirmedFilesReadyForCleanup: 1,
          quarantinedManifestCount: 0,
        ),
      );
      await _pumpApp(
        tester,
        FakeKnowledgeRepository(failQuickNotes: true),
        captureOutboxStore: outbox,
      );

      await tester.tap(find.byKey(const Key('pending-captures-button')));
      await tester.pumpAndSettle();
      expect(find.textContaining('Failed ·'), findsOneWidget);
      expect(find.byKey(const Key('clean-confirmed-files')), findsOneWidget);

      String? copied;
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        SystemChannels.platform,
        (call) async {
          if (call.method == 'Clipboard.setData') {
            copied =
                (call.arguments as Map<Object?, Object?>)['text'] as String?;
          }
          return null;
        },
      );
      addTearDown(
        () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          SystemChannels.platform,
          null,
        ),
      );
      await tester.tap(find.byKey(const Key('copy-capture-diagnostics')));
      await tester.pumpAndSettle();

      expect(copied, contains('gunther-capture-diagnostics-v1'));
      expect(copied, isNot(contains('PRIVATE TITLE')));
      expect(copied, isNot(contains('TOP SECRET BODY')));
      expect(copied, isNot(contains('private-workspace-id')));
      expect(copied, isNot(contains('private-profile-id')));
      expect(copied, isNot(contains('private.example')));
    },
  );

  testWidgets(
    'partial system share failure is visible without losing siblings',
    (tester) async {
      await _pumpApp(
        tester,
        FakeKnowledgeRepository(),
        systemShareIngress: _StatusShareIngress('partial_failure'),
      );
      await tester.pump(const Duration(milliseconds: 100));

      await tester.tap(find.byKey(const Key('pending-captures-button')));
      await tester.pumpAndSettle();

      expect(
        find.byKey(const Key('system-share-partial-failure')),
        findsOneWidget,
      );
      expect(
        find.textContaining('Some shared items were saved'),
        findsOneWidget,
      );
    },
  );

  testWidgets('document and camera capture upload original assets', (
    tester,
  ) async {
    final repository = FakeKnowledgeRepository();
    final device = FakeCaptureDevice();
    await _pumpApp(tester, repository, captureDevice: device);

    await _openCaptureLauncher(tester);
    await tester.tap(find.byKey(const Key('capture-document')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('choose-document')));
    await tester.pumpAndSettle();

    expect(device.documentPicks, 1);
    expect(repository.uploadedAssets.single.kind, 'file');
    expect(repository.uploadedAssets.single.asset.fileName, 'paper.pdf');

    await _openCaptureLauncher(tester);
    await tester.tap(find.byKey(const Key('capture-photo')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('take-photo')));
    await tester.pumpAndSettle();

    expect(device.photoSources, [PhotoCaptureSource.camera]);
    expect(repository.uploadedAssets.last.kind, 'image');
    expect(repository.uploadedAssets.last.asset.fileName, 'whiteboard.jpg');
  });

  testWidgets(
    'recording can pause, minimize, finish upload, and become an Inbox source',
    (tester) async {
      final repository = FakeKnowledgeRepository();
      final device = FakeCaptureDevice();
      final draftStore = MemoryRecordingDraftStore();
      await _pumpApp(
        tester,
        repository,
        captureDevice: device,
        recordingDraftStore: draftStore,
      );

      await _openCaptureLauncher(tester);
      await tester.tap(find.byKey(const Key('capture-recording')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('recording-workspace')), findsOneWidget);
      expect(
        find.textContaining('full audio stays independent'),
        findsOneWidget,
      );
      expect(find.byKey(const Key('start-recording')), findsOneWidget);

      await tester.enterText(
        find.byKey(const Key('recording-title')),
        'Algorithms lecture',
      );
      await tester.tap(find.byKey(const Key('start-recording')));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      expect(device.starts, 1);
      expect(find.byKey(const Key('pause-recording')), findsOneWidget);
      await tester.tap(find.byKey(const Key('mark-recording-moment')));
      await tester.tap(find.byKey(const Key('pause-recording')));
      await tester.pump();
      expect(device.pauses, 1);
      await tester.tap(find.byKey(const Key('resume-recording')));
      await tester.pump();
      expect(device.resumes, 1);

      await tester.ensureVisible(find.byKey(const Key('minimize-recording')));
      await tester.tap(find.byKey(const Key('minimize-recording')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('mini-recording-bar')), findsOneWidget);
      expect(find.byKey(const Key('global-capture-button')), findsNothing);

      final miniBar = find.byKey(const Key('mini-recording-bar'));
      await tester.tapAt(tester.getTopLeft(miniBar) + const Offset(24, 20));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('recording-workspace')), findsOneWidget);

      await tester.ensureVisible(find.byKey(const Key('recording-transcript')));
      await tester.enterText(
        find.byKey(const Key('recording-transcript')),
        'Needleman–Wunsch aligns complete sequences.',
      );
      await tester.ensureVisible(find.byKey(const Key('stop-recording')));
      await tester.tap(find.byKey(const Key('stop-recording')));
      await tester.pumpAndSettle();

      expect(device.stops, 1);
      expect(repository.recordingUploads, 1);
      expect(repository.recordingCompletes, 1);
      expect(find.byKey(const Key('save-recording-inbox')), findsOneWidget);
      expect(draftStore.draft?.localPath, '/tmp/lecture.m4a');
      expect(draftStore.draft?.serverRecordingId, 'recording-1');
      final newRecording = tester.widget<OutlinedButton>(
        find.byKey(const Key('new-recording')),
      );
      expect(newRecording.onPressed, isNull);
      expect(
        find.text('Keep in Inbox before starting another'),
        findsOneWidget,
      );
      expect(draftStore.draft, isNotNull);

      final saveRecording = find.byKey(const Key('save-recording-inbox'));
      await tester.scrollUntilVisible(
        saveRecording,
        300,
        scrollable: find.byType(Scrollable).last,
      );
      await tester.tap(saveRecording);
      await tester.pumpAndSettle();

      expect(repository.importedSources, hasLength(1));
      expect(repository.importedSources.single.kind, 'recording');
      expect(repository.importedSources.single.content, contains('Needleman'));
      expect(repository.recordingCheckpoints, isNotEmpty);
      expect(draftStore.draft, isNull);
      expect(draftStore.clearCount, 1);
    },
  );

  testWidgets(
    'restores a local-only recording and retries its verified upload',
    (tester) async {
      final draftStore = MemoryRecordingDraftStore(
        RecordingDraft(
          title: 'Recovered lecture',
          context: 'lecture',
          transcript: 'Local transcript survives.',
          durationSeconds: 42,
          moments: const [RecordingMoment(seconds: 12, label: 'Key point')],
          localPath: '/recovery/recovered.m4a',
          fileName: 'recovered.m4a',
          mediaType: 'audio/mp4',
          sizeBytes: 128,
          phase: 'recording',
          checkpointRevision: 3,
          updatedAt: DateTime.utc(2026, 8, 30),
        ),
      );
      final repository = FakeKnowledgeRepository();

      await _pumpApp(
        tester,
        repository,
        captureDevice: FakeCaptureDevice(),
        recordingDraftStore: draftStore,
        settle: false,
      );
      await tester.pump();
      await tester.pump();

      expect(find.byKey(const Key('mini-recording-bar')), findsOneWidget);
      expect(find.text('On device · Tap to retry'), findsOneWidget);
      expect(draftStore.draft?.sizeBytes, 128);
      expect(draftStore.draft?.checkpointRevision, 3);

      final recoveredMiniBar = find.byKey(const Key('mini-recording-bar'));
      await tester.tapAt(
        tester.getTopLeft(recoveredMiniBar) + const Offset(24, 20),
      );
      await tester.pumpAndSettle();
      expect(find.text('ON DEVICE'), findsOneWidget);
      await tester.tap(find.byKey(const Key('retry-recording-upload')));
      await tester.pumpAndSettle();

      expect(repository.recordingUploads, 1);
      expect(repository.recordingCompletes, 1);
      expect(find.byKey(const Key('save-recording-inbox')), findsOneWidget);
      expect(draftStore.draft?.phase, 'completed');
      expect(draftStore.draft?.checkpointRevision, greaterThan(0));

      final saveRecoveredRecording = find.byKey(
        const Key('save-recording-inbox'),
      );
      await tester.ensureVisible(saveRecoveredRecording);
      await tester.pumpAndSettle();
      await tester.tap(saveRecoveredRecording);
      await tester.pumpAndSettle();

      expect(repository.importedSources.single.title, 'Recovered lecture');
      expect(draftStore.draft, isNull);
      expect(draftStore.clearCount, 1);
      expect(draftStore.deletedOriginalPaths, isEmpty);
    },
  );

  testWidgets('quick notes are preserved through the repository', (
    tester,
  ) async {
    final repository = FakeKnowledgeRepository();
    await _pumpApp(tester, repository);

    await _openCaptureLauncher(tester);
    await tester.tap(find.byKey(const Key('capture-quickNote')));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const Key('quick-note-content')),
      'Follow up on the bioinformatics lecture.',
    );
    await tester.ensureVisible(find.byKey(const Key('save-quick-note')));
    await tester.tap(find.byKey(const Key('save-quick-note')));
    await tester.pumpAndSettle();

    expect(repository.savedNotes, hasLength(1));
    expect(repository.savedNotes.single.content, contains('bioinformatics'));
  });

  testWidgets(
    'offline quick note remains pending and retries with a stable capture id',
    (tester) async {
      final repository = FakeKnowledgeRepository(failQuickNotes: true);
      final outbox = MemoryCaptureOutboxStore();
      await _pumpApp(tester, repository, captureOutboxStore: outbox);

      await _openCaptureLauncher(tester);
      await tester.tap(find.byKey(const Key('capture-quickNote')));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byKey(const Key('quick-note-content')),
        'Keep this even while offline.',
      );
      await tester.tap(find.byKey(const Key('save-quick-note')));
      await tester.pumpAndSettle();

      expect(repository.savedNotes, isEmpty);
      expect(repository.attemptedNotes, hasLength(1));
      expect(outbox.entries, hasLength(1));
      final pending = outbox.entries.single;
      expect(pending.state, CaptureOutboxState.retryable);
      expect(repository.attemptedNotes.single.clientCaptureId, pending.id);
      expect(find.byKey(const Key('pending-captures-button')), findsOneWidget);

      repository.failQuickNotes = false;
      await tester.tap(find.byKey(const Key('pending-captures-button')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('pending-captures-sheet')), findsOneWidget);
      await tester.tap(find.byKey(Key('retry-capture-${pending.id}')));
      await tester.pumpAndSettle();

      expect(repository.savedNotes, hasLength(1));
      expect(repository.savedNotes.single.clientCaptureId, pending.id);
      expect(outbox.entries, isEmpty);
      expect(outbox.confirmedIds, [pending.id]);
    },
  );

  testWidgets('link capture rejects non-http URLs before durable enqueue', (
    tester,
  ) async {
    final repository = FakeKnowledgeRepository();
    final outbox = MemoryCaptureOutboxStore();
    await _pumpApp(tester, repository, captureOutboxStore: outbox);

    await _openCaptureLauncher(tester);
    await tester.tap(find.byKey(const Key('capture-link')));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const Key('web-snapshot-url')),
      'ftp://example.com/private',
    );
    await tester.tap(find.byKey(const Key('save-web-snapshot')));
    await tester.pumpAndSettle();

    expect(find.text('Enter a complete http or https URL.'), findsOneWidget);
    expect(repository.attemptedWebSnapshots, isEmpty);
    expect(outbox.entries, isEmpty);
    expect(find.byKey(const Key('web-snapshot-url')), findsOneWidget);
  });

  testWidgets(
    'offline webpage snapshot retries with one stable id and clears only on success',
    (tester) async {
      final repository = FakeKnowledgeRepository(
        failWebSnapshots: true,
        webReplayOnSuccess: true,
      );
      final outbox = MemoryCaptureOutboxStore();
      await _pumpApp(tester, repository, captureOutboxStore: outbox);

      await _openCaptureLauncher(tester);
      await tester.tap(find.byKey(const Key('capture-link')));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byKey(const Key('web-snapshot-url')),
        '  https://example.com/research?q=alignment  ',
      );
      await tester.enterText(
        find.byKey(const Key('web-snapshot-title')),
        'Alignment reference',
      );
      await tester.enterText(
        find.byKey(const Key('web-snapshot-notes')),
        'Use this in the methods chapter.',
      );
      await tester.tap(find.byKey(const Key('save-web-snapshot')));
      await tester.pumpAndSettle();

      expect(repository.savedWebSnapshots, isEmpty);
      expect(repository.attemptedWebSnapshots, hasLength(1));
      expect(outbox.entries, hasLength(1));
      final pending = outbox.entries.single;
      expect(pending.kind, CaptureOutboxKind.link);
      expect(pending.state, CaptureOutboxState.retryable);
      expect(pending.payload, {
        'url': 'https://example.com/research?q=alignment',
        'title': 'Alignment reference',
        'notes': 'Use this in the methods chapter.',
      });
      expect(pending.payload, isNot(contains('content')));
      expect(pending.payload, isNot(contains('clientCaptureId')));
      expect(
        repository.attemptedWebSnapshots.single.clientCaptureId,
        pending.id,
      );

      repository.failWebSnapshots = false;
      await tester.tap(find.byKey(const Key('pending-captures-button')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(Key('retry-capture-${pending.id}')));
      await tester.pumpAndSettle();

      expect(repository.attemptedWebSnapshots, hasLength(2));
      expect(
        repository.attemptedWebSnapshots
            .map((draft) => draft.clientCaptureId)
            .toSet(),
        {pending.id},
      );
      expect(repository.savedWebSnapshots.single.clientCaptureId, pending.id);
      expect(repository.lastWebSnapshotReceipt?.idempotentReplay, isTrue);
      expect(outbox.entries, isEmpty);
      expect(outbox.confirmedIds, [pending.id]);
    },
  );

  testWidgets(
    'audio import uses an app-owned copy until its Source is confirmed',
    (tester) async {
      final repository = FakeKnowledgeRepository();
      final outbox = MemoryCaptureOutboxStore(copyAssets: true);
      final draftStore = MemoryRecordingDraftStore();
      await _pumpApp(
        tester,
        repository,
        captureDevice: FakeCaptureDevice(),
        recordingDraftStore: draftStore,
        captureOutboxStore: outbox,
      );

      await _openCaptureLauncher(tester);
      await tester.tap(find.byKey(const Key('capture-recording')));
      await tester.pumpAndSettle();
      final importAudio = find.byKey(const Key('import-audio'));
      await tester.drag(
        find.descendant(
          of: find.byKey(const Key('recording-workspace')),
          matching: find.byType(ListView),
        ),
        const Offset(0, -650),
      );
      await tester.pumpAndSettle();
      await tester.tap(importAudio);
      await tester.pumpAndSettle();

      expect(outbox.stagedInputs.single.path, '/tmp/import.m4a');
      expect(draftStore.draft?.localPath, startsWith('/app-support/'));
      expect(outbox.entries.single.kind, CaptureOutboxKind.audio);
      expect(
        outbox.entries.single.state,
        CaptureOutboxState.awaitingConfirmation,
      );
      expect(repository.recordingCompletes, 1);

      final save = find.byKey(const Key('save-recording-inbox'));
      await tester.ensureVisible(save);
      await tester.tap(save);
      await tester.pumpAndSettle();

      expect(repository.importedSources.single.kind, 'recording');
      expect(draftStore.draft, isNull);
      expect(outbox.entries, isEmpty);
      expect(outbox.confirmedIds, hasLength(1));
    },
  );

  testWidgets('startup surfaces staged imported audio in the recording bar', (
    tester,
  ) async {
    const id = 'imported_audio_0001';
    const staged = CapturedAsset(
      path: '/app-support/capture-outbox/files/$id.m4a',
      fileName: 'seminar.m4a',
      mediaType: 'audio/mp4',
      sizeBytes: 4096,
    );
    final createdAt = DateTime.utc(2026, 8, 30);
    final outbox = MemoryCaptureOutboxStore(
      entries: [
        CaptureOutboxEntry(
          id: id,
          kind: CaptureOutboxKind.audio,
          payload: const {
            'workflow': 'recordingImport',
            'title': 'Seminar',
            'serverKind': 'recording',
          },
          stagedFile: const CaptureOutboxFile(
            relativePath: 'files/imported_audio_0001.m4a',
            fileName: 'seminar.m4a',
            mediaType: 'audio/mp4',
            sizeBytes: 4096,
            sha256:
                '0000000000000000000000000000000000000000000000000000000000000000',
          ),
          state: CaptureOutboxState.retryable,
          attemptCount: 1,
          createdAt: createdAt,
          updatedAt: createdAt,
          lastError: 'Previous upload stopped.',
        ),
      ],
      assets: {id: staged},
    );

    await _pumpApp(
      tester,
      FakeKnowledgeRepository(),
      captureDevice: FakeCaptureDevice(),
      captureOutboxStore: outbox,
    );

    expect(find.byKey(const Key('mini-recording-bar')), findsOneWidget);
    expect(find.text('Seminar'), findsOneWidget);
    expect(find.text('On device · Tap to retry'), findsOneWidget);
    expect(find.byKey(const Key('pending-captures-button')), findsOneWidget);
  });

  testWidgets('an unfiled source can be moved from Inbox into a library', (
    tester,
  ) async {
    final repository = FakeKnowledgeRepository(
      inboxItems: [
        InboxItem(
          id: 'source-1',
          itemType: 'source',
          state: 'unfiled',
          title: 'Lecture transcript',
          preview: 'Sequence alignment and scoring.',
          sourceKind: 'recording',
          knowledgeBases: const [],
          sourceId: 'source-1',
          assertionCount: 2,
          createdAt: FakeKnowledgeRepository.now,
          updatedAt: FakeKnowledgeRepository.now,
        ),
      ],
    );
    await _pumpApp(tester, repository);

    await tester.tap(find.byKey(const Key('nav-inbox')));
    await tester.pumpAndSettle();
    expect(find.text('Lecture transcript'), findsOneWidget);
    await tester.tap(find.text('Add to library'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('file-to-kb-1')));
    await tester.pumpAndSettle();

    expect(repository.filedSources, ['source-1:kb-1']);
    expect(find.text('Lecture transcript'), findsNothing);
  });

  test('outbox captures stay bound to their original workspace', () async {
    final repository = FakeKnowledgeRepository(failQuickNotes: true);
    final outbox = MemoryCaptureOutboxStore();
    CaptureTarget? activeTarget = (
      workspaceId: 'workspace-alpha',
      profileId: 'profile-alpha',
    );
    final viewModel = CaptureViewModel(
      repository,
      FakeCaptureDevice(),
      recordingDraftStore: MemoryRecordingDraftStore(),
      captureOutboxStore: outbox,
      captureTargetProvider: () => activeTarget,
    );
    addTearDown(viewModel.dispose);

    expect(
      await viewModel.saveQuickNote(
        const QuickNoteDraft(title: 'Bound note', content: 'Evidence'),
      ),
      isTrue,
    );
    await viewModel.flushCaptureOutbox();
    expect(outbox.entries.single.targetWorkspaceId, 'workspace-alpha');
    expect(outbox.entries.single.connectionProfileId, 'profile-alpha');
    expect(outbox.entries.single.state, CaptureOutboxState.retryable);
    final attempts = repository.attemptedNotes.length;

    activeTarget = (workspaceId: 'workspace-beta', profileId: 'profile-beta');
    repository.failQuickNotes = false;
    expect(await viewModel.retryCapture(outbox.entries.single.id), isFalse);
    expect(repository.attemptedNotes, hasLength(attempts));
    expect(outbox.entries.single.targetWorkspaceId, 'workspace-alpha');
    expect(viewModel.outboxError, contains('another workspace'));
  });

  test(
    'web snapshot retry never moves to the newly active workspace',
    () async {
      final repository = FakeKnowledgeRepository(failWebSnapshots: true);
      final outbox = MemoryCaptureOutboxStore();
      CaptureTarget? activeTarget = (
        workspaceId: 'workspace-web-alpha',
        profileId: 'profile-web-alpha',
      );
      final viewModel = CaptureViewModel(
        repository,
        FakeCaptureDevice(),
        recordingDraftStore: MemoryRecordingDraftStore(),
        captureOutboxStore: outbox,
        captureTargetProvider: () => activeTarget,
      );
      addTearDown(viewModel.dispose);

      expect(
        await viewModel.saveWebSnapshot(
          WebSnapshotDraft(
            url: 'https://example.com/bound',
            title: 'Bound snapshot',
            knowledgeBaseId: 'kb-bound',
          ),
        ),
        isTrue,
      );
      await viewModel.flushCaptureOutbox();
      final pending = outbox.entries.single;
      expect(pending.targetWorkspaceId, 'workspace-web-alpha');
      expect(pending.connectionProfileId, 'profile-web-alpha');
      expect(
        repository.attemptedWebSnapshots.map((draft) => draft.knowledgeBaseId),
        everyElement('kb-bound'),
      );
      expect(
        repository.attemptedWebSnapshots
            .map((draft) => draft.clientCaptureId)
            .toSet(),
        {pending.id},
      );
      final attempts = repository.attemptedWebSnapshots.length;

      activeTarget = (
        workspaceId: 'workspace-web-beta',
        profileId: 'profile-web-beta',
      );
      repository.failWebSnapshots = false;
      expect(await viewModel.retryCapture(pending.id), isFalse);
      expect(repository.attemptedWebSnapshots, hasLength(attempts));
      expect(outbox.entries.single.targetWorkspaceId, 'workspace-web-alpha');
      expect(viewModel.outboxError, contains('another workspace'));
    },
  );

  test(
    'note, file, and link uploads keep their pinned target during a profile switch',
    () async {
      final scenarios =
          <
            ({
              String label,
              CaptureOutboxKind kind,
              Map<String, Object?> payload,
              CapturedAsset? asset,
            })
          >[
            (
              label: 'note',
              kind: CaptureOutboxKind.quickNote,
              payload: const {'title': 'Bound note', 'content': 'Evidence'},
              asset: null,
            ),
            (
              label: 'file',
              kind: CaptureOutboxKind.document,
              payload: const {'title': 'Bound paper', 'serverKind': 'file'},
              asset: const CapturedAsset(
                path: '/app-support/capture-outbox/files/bound-paper.pdf',
                fileName: 'bound-paper.pdf',
                mediaType: 'application/pdf',
                sizeBytes: 32,
              ),
            ),
            (
              label: 'link',
              kind: CaptureOutboxKind.link,
              payload: const {
                'url': 'https://example.com/bound',
                'title': 'Bound snapshot',
              },
              asset: null,
            ),
          ];

      for (final scenario in scenarios) {
        CaptureTarget? activeTarget = (
          workspaceId: 'workspace-alpha',
          profileId: 'profile-alpha',
        );
        final attemptStarted = Completer<void>();
        final continueAttempt = Completer<void>();
        final entryId = 'race_${scenario.label}_0001';
        final asset = scenario.asset;
        final originalTarget = activeTarget;
        final entry = CaptureOutboxEntry(
          id: entryId,
          kind: scenario.kind,
          payload: scenario.payload,
          stagedFile: asset == null
              ? null
              : CaptureOutboxFile(
                  relativePath: 'files/$entryId.asset',
                  fileName: asset.fileName,
                  mediaType: asset.mediaType,
                  sizeBytes: asset.sizeBytes,
                  sha256:
                      '0000000000000000000000000000000000000000000000000000000000000000',
                ),
          state: CaptureOutboxState.pending,
          attemptCount: 0,
          createdAt: DateTime.utc(2026, 8, 30),
          updatedAt: DateTime.utc(2026, 8, 30),
          targetWorkspaceId: originalTarget.workspaceId,
          connectionProfileId: originalTarget.profileId,
        );
        final outbox = MemoryCaptureOutboxStore(
          entries: [entry],
          assets: asset == null ? const {} : {entryId: asset},
          beforeMarkAttemptStarted: (_) async {
            if (!attemptStarted.isCompleted) attemptStarted.complete();
            await continueAttempt.future;
          },
        );
        final repository = _SwitchingCaptureRepository(
          activeTarget: () => activeTarget,
        );
        final viewModel = CaptureViewModel(
          repository,
          FakeCaptureDevice(),
          recordingDraftStore: MemoryRecordingDraftStore(),
          captureOutboxStore: outbox,
          captureTargetProvider: () => activeTarget,
        );

        final flushing = viewModel.flushCaptureOutbox();
        await attemptStarted.future;
        activeTarget = (
          workspaceId: 'workspace-beta',
          profileId: 'profile-beta',
        );
        continueAttempt.complete();

        expect(await flushing, 1, reason: scenario.label);
        expect(repository.deliveries, [
          '${scenario.label}:workspace-alpha:profile-alpha',
        ], reason: scenario.label);
        expect(outbox.entries, isEmpty, reason: scenario.label);
        viewModel.dispose();
      }
    },
  );

  test(
    'recording upload, checkpoints, and Source keep one pinned target during a profile switch',
    () async {
      CaptureTarget? activeTarget = (
        workspaceId: 'workspace-recording-alpha',
        profileId: 'profile-recording-alpha',
      );
      final attemptStarted = Completer<void>();
      final continueAttempt = Completer<void>();
      final outbox = MemoryCaptureOutboxStore(
        copyAssets: true,
        beforeMarkAttemptStarted: (_) async {
          if (!attemptStarted.isCompleted) attemptStarted.complete();
          await continueAttempt.future;
        },
      );
      final repository = _SwitchingCaptureRepository(
        activeTarget: () => activeTarget,
      );
      final viewModel = CaptureViewModel(
        repository,
        FakeCaptureDevice(),
        recordingDraftStore: MemoryRecordingDraftStore(),
        captureOutboxStore: outbox,
        captureTargetProvider: () => activeTarget,
      );
      addTearDown(viewModel.dispose);

      final importing = viewModel.importAudio();
      await attemptStarted.future;
      activeTarget = (
        workspaceId: 'workspace-recording-beta',
        profileId: 'profile-recording-beta',
      );
      continueAttempt.complete();
      await importing;

      expect(viewModel.recordingPhase, RecordingCapturePhase.completed);
      expect(await viewModel.saveRecordingToInbox(), isTrue);
      expect(repository.deliveries, isNotEmpty);
      expect(
        repository.deliveries,
        everyElement(
          endsWith(':workspace-recording-alpha:profile-recording-alpha'),
        ),
      );
      expect(repository.deliveries, contains(startsWith('recording-start:')));
      expect(repository.deliveries, contains(startsWith('recording-upload:')));
      expect(
        repository.deliveries,
        contains(startsWith('recording-complete:')),
      );
      expect(
        repository.deliveries,
        contains(startsWith('recording-checkpoint:')),
      );
      expect(repository.deliveries, contains(startsWith('recording-source:')));
      expect(outbox.entries, isEmpty);
    },
  );

  test(
    'live recording pins its server before microphone startup yields',
    () async {
      CaptureTarget? activeTarget = (
        workspaceId: 'workspace-live-alpha',
        profileId: 'profile-live-alpha',
      );
      final repository = _SwitchingCaptureRepository(
        activeTarget: () => activeTarget,
      );
      final device = _BlockingRecordingCaptureDevice();
      final transcription = FakeLiveTranscriptionSession();
      final transcriptionTargets = <CaptureTarget>[];
      final viewModel = CaptureViewModel(
        repository,
        device,
        recordingDraftStore: MemoryRecordingDraftStore(),
        captureOutboxStore: MemoryCaptureOutboxStore(),
        captureTargetProvider: () => activeTarget,
        liveTranscriptionFactory: (_) {
          transcriptionTargets.add(activeTarget!);
          return transcription;
        },
      );
      addTearDown(viewModel.dispose);

      final starting = viewModel.startRecording('Pinned lecture');
      await device.startRequested.future;
      activeTarget = (
        workspaceId: 'workspace-live-beta',
        profileId: 'profile-live-beta',
      );
      device.continueStart.complete('/tmp/pinned-lecture.m4a');
      await starting;

      expect(repository.deliveries, contains(startsWith('recording-start:')));
      expect(
        repository.deliveries,
        contains(startsWith('recording-checkpoint:')),
      );
      expect(
        repository.deliveries,
        everyElement(endsWith(':workspace-live-alpha:profile-live-alpha')),
      );
      expect(transcriptionTargets, [
        (workspaceId: 'workspace-live-alpha', profileId: 'profile-live-alpha'),
      ]);
      expect(transcription.connectCalls, 1);
    },
  );

  test('legacy saved-link payload upgrades to the snapshot endpoint', () async {
    const id = 'legacy_link_0001';
    final createdAt = DateTime.utc(2026, 8, 29);
    final outbox = MemoryCaptureOutboxStore(
      entries: [
        CaptureOutboxEntry(
          id: id,
          kind: CaptureOutboxKind.link,
          payload: const {
            'title': 'Legacy saved link',
            'kind': 'link',
            'content': 'https://example.com/legacy',
          },
          state: CaptureOutboxState.retryable,
          attemptCount: 1,
          createdAt: createdAt,
          updatedAt: createdAt,
          lastError: 'Upgrade interrupted',
        ),
      ],
    );
    final repository = FakeKnowledgeRepository();
    final viewModel = CaptureViewModel(
      repository,
      FakeCaptureDevice(),
      recordingDraftStore: MemoryRecordingDraftStore(),
      captureOutboxStore: outbox,
    );
    addTearDown(viewModel.dispose);

    await viewModel.restoreCaptureOutbox();

    expect(repository.importedSources, isEmpty);
    expect(repository.attemptedWebSnapshots, hasLength(1));
    expect(
      repository.attemptedWebSnapshots.single.url,
      'https://example.com/legacy',
    );
    expect(repository.attemptedWebSnapshots.single.clientCaptureId, id);
    expect(outbox.entries, isEmpty);
    expect(outbox.confirmedIds, [id]);
  });
}

Future<void> _openCaptureLauncher(WidgetTester tester) async {
  await tester.tap(find.byKey(const Key('global-capture-button')));
  await tester.pumpAndSettle();
}

Future<void> _pumpApp(
  WidgetTester tester,
  KnowledgeRepository repository, {
  CaptureDevice? captureDevice,
  RecordingDraftStore? recordingDraftStore,
  CaptureOutboxStore? captureOutboxStore,
  LiveTranscriptionSessionFactory? liveTranscriptionFactory,
  SystemShareIngress? systemShareIngress,
  bool settle = true,
}) async {
  tester.view.physicalSize = const Size(390, 844);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(
    GuntherApp(
      repository: repository,
      captureDevice: captureDevice,
      recordingDraftStore: recordingDraftStore ?? MemoryRecordingDraftStore(),
      captureOutboxStore: captureOutboxStore ?? MemoryCaptureOutboxStore(),
      liveTranscriptionFactory: liveTranscriptionFactory,
      systemShareIngress: systemShareIngress,
    ),
  );
  if (settle) {
    await tester.pumpAndSettle();
  } else {
    await tester.pump();
  }
}

class _StatusShareIngress
    implements SystemShareIngress, SystemShareIngressStatusSource {
  _StatusShareIngress(this.state);

  final String state;

  @override
  Future<void> acknowledge(List<String> ids) async {}

  @override
  Future<String> currentStagingState() async => state;

  @override
  Future<List<SystemShareItem>> pendingItems() async => const [];

  @override
  void setItemsAvailableHandler(Future<void> Function()? handler) {}

  @override
  void setStagingStateHandler(void Function(String state)? handler) {
    if (handler != null) scheduleMicrotask(() => handler(state));
  }
}

class MemoryCaptureOutboxStore implements CaptureOutboxStore {
  MemoryCaptureOutboxStore({
    List<CaptureOutboxEntry> entries = const [],
    Map<String, CapturedAsset> assets = const {},
    this.copyAssets = false,
    this.beforeMarkAttemptStarted,
  }) : entries = [...entries],
       assets = {...assets};

  final List<CaptureOutboxEntry> entries;
  final Map<String, CapturedAsset> assets;
  final bool copyAssets;
  final Future<void> Function(String id)? beforeMarkAttemptStarted;
  final List<CapturedAsset> stagedInputs = [];
  final List<String> confirmedIds = [];
  int _nextId = 0;

  @override
  Future<void> confirmServerSuccess(String id) async {
    final index = entries.indexWhere((entry) => entry.id == id);
    if (index < 0) throw CaptureOutboxEntryNotFoundException(id);
    entries.removeAt(index);
    assets.remove(id);
    confirmedIds.add(id);
  }

  @override
  Future<CaptureOutboxEntry> enqueueAsset({
    required CaptureOutboxKind kind,
    required CapturedAsset asset,
    Map<String, Object?> payload = const <String, Object?>{},
    String? targetWorkspaceId,
    String? connectionProfileId,
  }) async {
    final id = _id();
    final now = DateTime.utc(2026, 8, 30);
    stagedInputs.add(asset);
    final stagedAsset = copyAssets
        ? CapturedAsset(
            path: '/app-support/capture-outbox/files/$id.asset',
            fileName: asset.fileName,
            mediaType: asset.mediaType,
            sizeBytes: asset.sizeBytes,
          )
        : asset;
    final entry = CaptureOutboxEntry(
      id: id,
      kind: kind,
      payload: Map<String, Object?>.unmodifiable(payload),
      stagedFile: CaptureOutboxFile(
        relativePath: 'files/$id.asset',
        fileName: asset.fileName,
        mediaType: asset.mediaType,
        sizeBytes: asset.sizeBytes,
        sha256:
            '0000000000000000000000000000000000000000000000000000000000000000',
      ),
      state: CaptureOutboxState.pending,
      attemptCount: 0,
      createdAt: now,
      updatedAt: now,
      targetWorkspaceId: targetWorkspaceId,
      connectionProfileId: connectionProfileId,
    );
    entries.add(entry);
    assets[id] = stagedAsset;
    return entry;
  }

  @override
  Future<CaptureOutboxEntry> enqueuePayload({
    required CaptureOutboxKind kind,
    required Map<String, Object?> payload,
    String? targetWorkspaceId,
    String? connectionProfileId,
  }) async {
    final now = DateTime.utc(2026, 8, 30);
    final entry = CaptureOutboxEntry(
      id: _id(),
      kind: kind,
      payload: Map<String, Object?>.unmodifiable(payload),
      state: CaptureOutboxState.pending,
      attemptCount: 0,
      createdAt: now,
      updatedAt: now,
      targetWorkspaceId: targetWorkspaceId,
      connectionProfileId: connectionProfileId,
    );
    entries.add(entry);
    return entry;
  }

  @override
  Future<CaptureOutboxEntry> bindUnassignedEntry(
    String id, {
    required String workspaceId,
    required String profileId,
  }) async {
    return _update(
      id,
      (entry) => entry.copyWith(
        targetWorkspaceId: workspaceId,
        connectionProfileId: profileId,
      ),
    );
  }

  @override
  Future<List<CaptureOutboxEntry>> listEntries() async =>
      List<CaptureOutboxEntry>.unmodifiable(entries);

  @override
  Future<CaptureOutboxEntry?> loadEntry(String id) async {
    final index = entries.indexWhere((entry) => entry.id == id);
    return index < 0 ? null : entries[index];
  }

  @override
  Future<CaptureOutboxEntry> markAttemptFailed(
    String id, {
    required String error,
  }) async {
    return _update(
      id,
      (entry) => entry.copyWith(
        state: CaptureOutboxState.retryable,
        updatedAt: DateTime.utc(2026, 8, 30, 0, 1),
        lastError: error,
      ),
    );
  }

  @override
  Future<CaptureOutboxEntry> markAttemptStarted(String id) async {
    await beforeMarkAttemptStarted?.call(id);
    return _update(
      id,
      (entry) => entry.copyWith(
        state: CaptureOutboxState.uploading,
        attemptCount: entry.attemptCount + 1,
        updatedAt: DateTime.utc(2026, 8, 30, 0, 1),
        lastAttemptAt: DateTime.utc(2026, 8, 30, 0, 1),
        clearLastError: true,
      ),
    );
  }

  @override
  Future<CaptureOutboxEntry> markAwaitingConfirmation(String id) async {
    return _update(
      id,
      (entry) => entry.copyWith(
        state: CaptureOutboxState.awaitingConfirmation,
        updatedAt: DateTime.utc(2026, 8, 30, 0, 2),
        clearLastError: true,
      ),
    );
  }

  @override
  Future<List<CaptureOutboxEntry>> recoverInterruptedAttempts() async {
    for (var index = 0; index < entries.length; index += 1) {
      final entry = entries[index];
      if (entry.state == CaptureOutboxState.uploading) {
        entries[index] = entry.copyWith(
          state: CaptureOutboxState.retryable,
          updatedAt: DateTime.utc(2026, 8, 30, 0, 2),
          lastError: 'Interrupted upload',
        );
      }
    }
    return listEntries();
  }

  @override
  Future<CapturedAsset> resolveStagedAsset(String id) async {
    final asset = assets[id];
    if (asset == null) {
      throw CaptureOutboxException('Test staged asset "$id" is missing.');
    }
    return asset;
  }

  CaptureOutboxEntry _update(
    String id,
    CaptureOutboxEntry Function(CaptureOutboxEntry entry) update,
  ) {
    final index = entries.indexWhere((entry) => entry.id == id);
    if (index < 0) throw CaptureOutboxEntryNotFoundException(id);
    final updated = update(entries[index]);
    entries[index] = updated;
    return updated;
  }

  String _id() {
    _nextId += 1;
    return 'test_capture_${_nextId.toString().padLeft(8, '0')}';
  }
}

class MaintenanceMemoryCaptureOutboxStore extends MemoryCaptureOutboxStore
    implements CaptureOutboxMaintenanceStore {
  MaintenanceMemoryCaptureOutboxStore({
    super.entries,
    required this.maintenance,
  });

  CaptureOutboxMaintenanceSnapshot maintenance;

  @override
  Future<int> cleanConfirmedFiles() async {
    final count = maintenance.confirmedFilesReadyForCleanup;
    maintenance = CaptureOutboxMaintenanceSnapshot(
      manifestHealthy: maintenance.manifestHealthy,
      confirmedFilesReadyForCleanup: 0,
      quarantinedManifestCount: maintenance.quarantinedManifestCount,
      issueCode: maintenance.issueCode,
    );
    return count;
  }

  @override
  Future<CaptureOutboxMaintenanceSnapshot> inspectMaintenance() async =>
      maintenance;

  @override
  Future<String> quarantineDamagedManifest({
    required bool userConfirmed,
  }) async {
    if (!userConfirmed) throw StateError('confirmation required');
    maintenance = CaptureOutboxMaintenanceSnapshot(
      manifestHealthy: true,
      confirmedFilesReadyForCleanup: 0,
      quarantinedManifestCount: maintenance.quarantinedManifestCount + 1,
    );
    entries.clear();
    return '/quarantine/manifest.json';
  }

  @override
  Future<String> quarantineDamagedCleanupJournal({
    required bool userConfirmed,
  }) async {
    if (!userConfirmed) throw StateError('confirmation required');
    maintenance = CaptureOutboxMaintenanceSnapshot(
      manifestHealthy: maintenance.manifestHealthy,
      confirmedFilesReadyForCleanup: 0,
      quarantinedManifestCount: maintenance.quarantinedManifestCount + 1,
    );
    return '/quarantine/confirmed-cleanup.json';
  }
}

class MemoryRecordingDraftStore implements RecordingDraftStore {
  MemoryRecordingDraftStore([this.draft]);

  RecordingDraft? draft;
  int saveCount = 0;
  int clearCount = 0;
  final List<String> deletedOriginalPaths = [];

  @override
  Future<RecordingDraft?> load() async => draft;

  @override
  Future<CapturedAsset?> resolveLocalAsset(RecordingDraft value) async {
    return CapturedAsset(
      path: value.localPath,
      fileName: value.fileName,
      mediaType: value.mediaType,
      sizeBytes: value.sizeBytes,
    );
  }

  @override
  Future<void> save(RecordingDraft value) async {
    saveCount += 1;
    draft = value;
  }

  @override
  Future<void> clear() async {
    clearCount += 1;
    draft = null;
  }
}

class FakeCaptureDevice implements CaptureDevice {
  int documentPicks = 0;
  int audioPicks = 0;
  int starts = 0;
  int pauses = 0;
  int resumes = 0;
  int stops = 0;
  final List<PhotoCaptureSource> photoSources = [];

  @override
  Future<CapturedAsset?> pickDocument() async {
    documentPicks += 1;
    return const CapturedAsset(
      path: '/tmp/paper.pdf',
      fileName: 'paper.pdf',
      mediaType: 'application/pdf',
      sizeBytes: 128,
    );
  }

  @override
  Future<CapturedAsset?> pickAudio() async {
    audioPicks += 1;
    return const CapturedAsset(
      path: '/tmp/import.m4a',
      fileName: 'import.m4a',
      mediaType: 'audio/mp4',
      sizeBytes: 512,
    );
  }

  @override
  Future<CapturedAsset?> pickPhoto(PhotoCaptureSource source) async {
    photoSources.add(source);
    return const CapturedAsset(
      path: '/tmp/whiteboard.jpg',
      fileName: 'whiteboard.jpg',
      mediaType: 'image/jpeg',
      sizeBytes: 256,
    );
  }

  @override
  Future<String> startRecording(String title) async {
    starts += 1;
    return '/tmp/lecture.m4a';
  }

  @override
  Future<void> pauseRecording() async {
    pauses += 1;
  }

  @override
  Future<void> resumeRecording() async {
    resumes += 1;
  }

  @override
  Future<CapturedAsset> stopRecording() async {
    stops += 1;
    return const CapturedAsset(
      path: '/tmp/lecture.m4a',
      fileName: 'lecture.m4a',
      mediaType: 'audio/mp4',
      sizeBytes: 1024,
    );
  }

  @override
  Future<void> dispose() async {}
}

class FakeLivePcmCaptureDevice extends FakeCaptureDevice
    implements LivePcmCaptureDevice {
  final StreamController<Uint8List> _pcm =
      StreamController<Uint8List>.broadcast();

  void emitPcm(List<int> bytes) => _pcm.add(Uint8List.fromList(bytes));

  @override
  Stream<Uint8List>? get livePcmStream => _pcm.stream;

  @override
  int get livePcmSampleRate => 24000;

  @override
  int get livePcmChannels => 1;

  @override
  String get liveRecordingMediaType => 'audio/wav';

  @override
  Future<String> startRecording(String title) async {
    starts += 1;
    return '/tmp/live.wav';
  }

  @override
  Future<CapturedAsset> stopRecording() async {
    stops += 1;
    if (!_pcm.isClosed) await _pcm.close();
    return const CapturedAsset(
      path: '/tmp/live.wav',
      fileName: 'live.wav',
      mediaType: 'audio/wav',
      sizeBytes: 48,
    );
  }

  @override
  Future<void> dispose() async {
    if (!_pcm.isClosed) await _pcm.close();
  }
}

class _BlockingRecordingCaptureDevice extends FakeCaptureDevice
    implements LivePcmCaptureDevice {
  final Completer<void> startRequested = Completer<void>();
  final Completer<String> continueStart = Completer<String>();
  final StreamController<Uint8List> _pcm =
      StreamController<Uint8List>.broadcast();

  @override
  Stream<Uint8List>? get livePcmStream => _pcm.stream;

  @override
  int get livePcmSampleRate => liveTranscriptionSampleRate;

  @override
  int get livePcmChannels => 1;

  @override
  String get liveRecordingMediaType => 'audio/wav';

  @override
  Future<String> startRecording(String title) {
    starts += 1;
    if (!startRequested.isCompleted) startRequested.complete();
    return continueStart.future;
  }

  @override
  Future<void> dispose() async {
    if (!_pcm.isClosed) await _pcm.close();
  }
}

class FakeLiveTranscriptionSession implements LiveTranscriptionSession {
  final StreamController<LiveTranscriptionEvent> _events =
      StreamController<LiveTranscriptionEvent>.broadcast();
  final List<int> pcmBytes = [];
  int connectCalls = 0;
  int commitCalls = 0;
  LiveTranscriptionConnectionState _state =
      LiveTranscriptionConnectionState.idle;

  void emit(LiveTranscriptionEvent event) => _events.add(event);

  @override
  Stream<LiveTranscriptionEvent> get events => _events.stream;

  @override
  LiveTranscriptionConnectionState get state => _state;

  @override
  int get bufferedBytes => 0;

  @override
  int get droppedBytes => 0;

  @override
  Future<void> connect() async {
    connectCalls += 1;
    _state = LiveTranscriptionConnectionState.ready;
    emit(
      const LiveTranscriptionReadyEvent(
        provider: 'sensevoice',
        model: 'sensevoice-small',
        local: true,
        diarize: false,
      ),
    );
  }

  @override
  void appendPcm(Uint8List pcm) => pcmBytes.addAll(pcm);

  @override
  void commit() {
    commitCalls += 1;
  }

  @override
  Future<void> stop() async {
    _state = LiveTranscriptionConnectionState.stopped;
  }

  @override
  Future<void> dispose() async {
    await stop();
    if (!_events.isClosed) await _events.close();
  }
}

class FakeKnowledgeRepository
    implements KnowledgeRepository, CaptureTargetRepository {
  static final now = DateTime(2026, 8, 30);

  FakeKnowledgeRepository({
    List<InboxItem> inboxItems = const [],
    this.failQuickNotes = false,
    this.failWebSnapshots = false,
    this.webReplayOnSuccess = false,
  }) : inboxItems = [...inboxItems],
       _recording = _recordingSession(status: 'recording');

  final List<QuickNoteDraft> savedNotes = [];
  final List<QuickNoteDraft> attemptedNotes = [];
  final List<WebSnapshotDraft> savedWebSnapshots = [];
  final List<WebSnapshotDraft> attemptedWebSnapshots = [];
  final List<SourceDraft> importedSources = [];
  final List<String> filedSources = [];
  final List<InboxItem> inboxItems;
  final List<_UploadedAsset> uploadedAssets = [];
  final List<RecordingCheckpointDraft> recordingCheckpoints = [];
  int recordingUploads = 0;
  int recordingCompletes = 0;
  bool failQuickNotes;
  bool failWebSnapshots;
  final bool webReplayOnSuccess;
  WebSnapshotReceipt? lastWebSnapshotReceipt;
  RecordingSession _recording;

  @override
  CaptureTargetLease pinCaptureTarget({
    required String workspaceId,
    required String profileId,
  }) => _BorrowedCaptureTargetLease(this);

  @override
  Future<Overview> getOverview() async => const Overview(
    counts: OverviewCounts(
      sources: 1,
      entities: 2,
      assertions: 1,
      provisional: 0,
    ),
    recentSources: [],
    recentAssertions: [],
  );

  @override
  Future<KnowledgeGraph> getGraph() async =>
      const KnowledgeGraph(nodes: [], edges: []);

  @override
  Future<List<KnowledgeBaseSummary>> getKnowledgeBases() async => [
    KnowledgeBaseSummary(
      id: 'kb-1',
      title: 'Bioinformatics',
      eyebrow: 'Course',
      subtitle: 'Methods, papers, and lectures',
      question: 'How do computational methods explain biological data?',
      description: 'A durable home for the subject.',
      color: 'green',
      status: 'Living',
      sourceCount: 1,
      sessionCount: 0,
      pendingProposalCount: 0,
      createdAt: now,
      updatedAt: now,
    ),
  ];

  @override
  Future<KnowledgeBaseSummary> createKnowledgeBase(
    KnowledgeBaseDraft draft,
  ) async {
    return KnowledgeBaseSummary(
      id: 'kb-created',
      title: draft.title,
      eyebrow: draft.eyebrow,
      subtitle: draft.subtitle,
      question: draft.question,
      description: draft.description,
      color: draft.color,
      status: 'Outline',
      sourceCount: 0,
      sessionCount: 0,
      pendingProposalCount: 0,
      createdAt: now,
      updatedAt: now,
    );
  }

  @override
  Future<List<InboxItem>> getInbox({String? state}) async => state == null
      ? List.unmodifiable(inboxItems)
      : inboxItems.where((item) => item.state == state).toList(growable: false);

  @override
  Future<List<Assertion>> getProvisionalAssertions() async => [];

  @override
  Future<List<SourceSummary>> getSources() async => [];

  @override
  Future<void> importSource(SourceDraft draft) async {
    importedSources.add(draft);
  }

  @override
  Future<void> createQuickNote(QuickNoteDraft draft) async {
    attemptedNotes.add(draft);
    if (failQuickNotes) throw Exception('Backend offline');
    savedNotes.add(draft);
  }

  @override
  Future<WebSnapshotReceipt> captureWebSnapshot(WebSnapshotDraft draft) async {
    attemptedWebSnapshots.add(draft);
    if (failWebSnapshots) throw Exception('Backend offline');
    savedWebSnapshots.add(draft);
    final receipt = WebSnapshotReceipt(
      sourceId: 'src-web-snapshot',
      idempotentReplay: webReplayOnSuccess,
    );
    lastWebSnapshotReceipt = receipt;
    return receipt;
  }

  @override
  Future<void> uploadAsset(
    CapturedAsset asset, {
    required String title,
    required String kind,
    String? knowledgeBaseId,
    String notes = '',
  }) async {
    uploadedAssets.add(_UploadedAsset(asset: asset, title: title, kind: kind));
  }

  @override
  Future<RecordingSession> startRecordingSession(
    String title,
    String contentType,
  ) async {
    _recording = _recordingSession(
      title: title,
      contentType: contentType,
      status: 'recording',
    );
    return _recording;
  }

  @override
  Future<RecordingSession> getRecordingMetadata(String recordingId) async =>
      _recording;

  @override
  Future<RecordingSession> uploadRecordingFile(
    RecordingSession session,
    CapturedAsset asset, {
    void Function(int sentBytes, int totalBytes)? onProgress,
  }) async {
    recordingUploads += 1;
    onProgress?.call(0, asset.sizeBytes);
    onProgress?.call(asset.sizeBytes, asset.sizeBytes);
    _recording = _recordingSession(
      title: session.title,
      contentType: asset.mediaType,
      status: 'uploading',
      sizeBytes: asset.sizeBytes,
      checkpointRevision: session.checkpointRevision,
      transcript: session.transcript,
    );
    return _recording;
  }

  @override
  Future<RecordingSession> completeRecordingSession(String recordingId) async {
    recordingCompletes += 1;
    _recording = _recordingSession(
      title: _recording.title,
      contentType: _recording.contentType,
      status: 'completed',
      sizeBytes: _recording.sizeBytes,
      checkpointRevision: _recording.checkpointRevision,
      transcript: _recording.transcript,
    );
    return _recording;
  }

  @override
  Future<RecordingSession> checkpointRecording(
    RecordingSession session,
    RecordingCheckpointDraft checkpoint,
  ) async {
    recordingCheckpoints.add(checkpoint);
    _recording = _recordingSession(
      title: session.title,
      contentType: session.contentType,
      status: session.status,
      sizeBytes: session.sizeBytes,
      checkpointRevision: session.checkpointRevision + 1,
      transcript: checkpoint.transcript,
      durationSeconds: checkpoint.durationSeconds,
      moments: checkpoint.moments,
      recordingContext: checkpoint.recordingContext,
    );
    return _recording;
  }

  @override
  Future<void> fileSource(String sourceId, String knowledgeBaseId) async {
    filedSources.add('$sourceId:$knowledgeBaseId');
    inboxItems.removeWhere((item) => item.sourceId == sourceId);
  }

  @override
  Future<void> fileQuickNote(String noteId, String knowledgeBaseId) async {}

  @override
  Future<WebSearchResult> searchWeb(String query) async => WebSearchResult(
    query: query,
    answer: 'Answer',
    sources: const [],
    mode: 'openai',
  );

  @override
  Future<void> updateAssertionStatus(String id, String status) async {}
}

class _SwitchingCaptureRepository extends FakeKnowledgeRepository {
  _SwitchingCaptureRepository({required this.activeTarget});

  final CaptureTargetProvider activeTarget;
  final List<String> deliveries = [];

  @override
  CaptureTargetLease pinCaptureTarget({
    required String workspaceId,
    required String profileId,
  }) {
    final active = activeTarget();
    if (active == null ||
        active.workspaceId != workspaceId ||
        active.profileId != profileId) {
      throw StateError('Capture target changed before it could be pinned.');
    }
    return _BorrowedCaptureTargetLease(
      _PinnedCaptureRepository(this, workspaceId, profileId),
    );
  }
}

class _PinnedCaptureRepository extends FakeKnowledgeRepository {
  _PinnedCaptureRepository(this.owner, this.workspaceId, this.profileId);

  final _SwitchingCaptureRepository owner;
  final String workspaceId;
  final String profileId;

  void _record(String kind) {
    owner.deliveries.add('$kind:$workspaceId:$profileId');
  }

  @override
  Future<void> createQuickNote(QuickNoteDraft draft) async {
    _record('note');
  }

  @override
  Future<void> uploadAsset(
    CapturedAsset asset, {
    required String title,
    required String kind,
    String? knowledgeBaseId,
    String notes = '',
  }) async {
    _record('file');
  }

  @override
  Future<WebSnapshotReceipt> captureWebSnapshot(WebSnapshotDraft draft) async {
    _record('link');
    return const WebSnapshotReceipt(
      sourceId: 'src-pinned-snapshot',
      idempotentReplay: false,
    );
  }

  @override
  Future<void> importSource(SourceDraft draft) async {
    if (draft.kind == 'recording') _record('recording-source');
    owner.importedSources.add(draft);
  }

  @override
  Future<RecordingSession> startRecordingSession(
    String title,
    String contentType,
  ) async {
    _record('recording-start');
    return super.startRecordingSession(title, contentType);
  }

  @override
  Future<RecordingSession> getRecordingMetadata(String recordingId) async {
    _record('recording-metadata');
    return super.getRecordingMetadata(recordingId);
  }

  @override
  Future<RecordingSession> uploadRecordingFile(
    RecordingSession session,
    CapturedAsset asset, {
    void Function(int sentBytes, int totalBytes)? onProgress,
  }) async {
    _record('recording-upload');
    return super.uploadRecordingFile(session, asset, onProgress: onProgress);
  }

  @override
  Future<RecordingSession> completeRecordingSession(String recordingId) async {
    _record('recording-complete');
    return super.completeRecordingSession(recordingId);
  }

  @override
  Future<RecordingSession> checkpointRecording(
    RecordingSession session,
    RecordingCheckpointDraft checkpoint,
  ) async {
    _record('recording-checkpoint');
    return super.checkpointRecording(session, checkpoint);
  }
}

class _BorrowedCaptureTargetLease implements CaptureTargetLease {
  const _BorrowedCaptureTargetLease(this.repository);

  @override
  final KnowledgeRepository repository;

  @override
  void close() {}
}

class _UploadedAsset {
  const _UploadedAsset({
    required this.asset,
    required this.title,
    required this.kind,
  });

  final CapturedAsset asset;
  final String title;
  final String kind;
}

RecordingSession _recordingSession({
  String title = 'Voice memo',
  String contentType = 'audio/mp4',
  String status = 'recording',
  int sizeBytes = 0,
  int checkpointRevision = 0,
  String transcript = '',
  int durationSeconds = 0,
  List<RecordingMoment> moments = const [],
  String recordingContext = 'memo',
}) {
  return RecordingSession(
    id: 'recording-1',
    title: title,
    status: status,
    fileName: 'recording-1.m4a',
    contentType: contentType,
    sizeBytes: sizeBytes,
    nextExpectedSequence: sizeBytes == 0 ? 0 : 1,
    transcript: transcript,
    durationSeconds: durationSeconds,
    moments: moments,
    recordingContext: recordingContext,
    checkpointRevision: checkpointRevision,
    recovery: RecordingRecovery(
      canResume: status != 'completed',
      audioAvailable: sizeBytes > 0,
      nextExpectedSequence: sizeBytes == 0 ? 0 : 1,
      checkpointRevision: checkpointRevision,
    ),
    createdAt: FakeKnowledgeRepository.now,
    updatedAt: FakeKnowledgeRepository.now,
    storedAt: FakeKnowledgeRepository.now,
    completedAt: status == 'completed' ? FakeKnowledgeRepository.now : null,
  );
}
