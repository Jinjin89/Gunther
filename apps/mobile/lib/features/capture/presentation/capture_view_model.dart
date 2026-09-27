import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:gunther_mobile/data/models/capture.dart';
import 'package:gunther_mobile/data/models/capture_outbox.dart';
import 'package:gunther_mobile/data/models/source.dart';
import 'package:gunther_mobile/data/models/web_search.dart';
import 'package:gunther_mobile/data/repositories/knowledge_repository.dart';
import 'package:gunther_mobile/data/services/capture_device_service.dart';
import 'package:gunther_mobile/data/services/capture_outbox_service.dart';
import 'package:gunther_mobile/data/services/knowledge_api_service.dart';
import 'package:gunther_mobile/data/services/live_transcription_service.dart';
import 'package:gunther_mobile/data/services/pcm_wav_file.dart';
import 'package:gunther_mobile/data/services/recording_draft_store.dart';
import 'package:gunther_mobile/data/services/system_share_ingress_service.dart';

typedef CaptureTarget = ({String workspaceId, String profileId});
typedef CaptureTargetProvider = CaptureTarget? Function();

class CaptureViewModel extends ChangeNotifier {
  CaptureViewModel(
    this._repository,
    this._captureDevice, {
    RecordingDraftStore? recordingDraftStore,
    CaptureOutboxStore? captureOutboxStore,
    LiveTranscriptionSessionFactory? liveTranscriptionFactory,
    Future<void> Function()? onOutboxUploaded,
    CaptureTargetProvider? captureTargetProvider,
    SystemShareIngressMaintenanceSource? shareIngressMaintenanceSource,
  }) : _recordingDraftStore = recordingDraftStore ?? FileRecordingDraftStore(),
       _captureOutboxStore = captureOutboxStore ?? CaptureOutboxService(),
       _liveTranscriptionFactory = liveTranscriptionFactory,
       _onOutboxUploaded = onOutboxUploaded,
       _captureTargetProvider = captureTargetProvider,
       _shareIngressMaintenanceSource = shareIngressMaintenanceSource;

  static const maxAssetBytes = 512 * 1024 * 1024;
  static const maxRecordingBytes = 2 * 1024 * 1024 * 1024;

  final KnowledgeRepository _repository;
  final CaptureDevice _captureDevice;
  final RecordingDraftStore _recordingDraftStore;
  final CaptureOutboxStore _captureOutboxStore;
  final LiveTranscriptionSessionFactory? _liveTranscriptionFactory;
  final Future<void> Function()? _onOutboxUploaded;
  final CaptureTargetProvider? _captureTargetProvider;
  final SystemShareIngressMaintenanceSource? _shareIngressMaintenanceSource;

  bool saving = false;
  bool searching = false;
  bool outboxSyncing = false;
  String? error;
  String? captureStatus;
  String? outboxError;
  String? systemIngressIssueCode;
  String systemIngressStagingState = 'idle';
  String? maintenanceNotice;
  CaptureOutboxMaintenanceSnapshot? outboxMaintenance;
  SystemShareMaintenanceSnapshot? shareIngressMaintenance;
  bool maintenanceBusy = false;
  WebSearchResult? webResult;
  List<CaptureOutboxEntry> outboxEntries = const [];

  RecordingCapturePhase recordingPhase = RecordingCapturePhase.idle;
  RecordingSession? recordingSession;
  CapturedAsset? localRecording;
  String recordingTitle = 'Voice memo';
  String recordingContext = 'memo';
  String? recordingKnowledgeBaseId;
  String transcript = '';
  int recordingSeconds = 0;
  List<RecordingMoment> moments = const [];
  double recordingUploadProgress = 0;
  String recordingNotice =
      'Audio is saved to an app file first. Live transcription needs a verified compatible PCM stream.';
  String recordingTranscriptionStatus =
      'Live transcription is not connected. The audio file remains authoritative.';
  String liveTranscriptPreview = '';
  bool recordingMinimized = false;
  bool recordingFiledToInbox = false;

  Timer? _recordingClock;
  Timer? _checkpointDebounce;
  Timer? _liveCleanupTimer;
  Future<void> _checkpointChain = Future<void>.value();
  Future<void> _draftWriteChain = Future<void>.value();
  Future<void> _outboxFlushBarrier = Future<void>.value();
  Future<void>? _restoreFuture;
  Future<void>? _outboxRestoreFuture;
  String? _recoveredServerRecordingId;
  int _recoveredCheckpointRevision = 0;
  String? _importedAudioOutboxEntryId;
  LiveTranscriptionSession? _liveTranscription;
  StreamSubscription<LiveTranscriptionEvent>? _liveEventSubscription;
  StreamSubscription<Uint8List>? _livePcmSubscription;
  ({String encoding, int sampleRate, int channels})? _recordingPcmFormat;
  CaptureTarget? _recordingTarget;
  CaptureTargetLease? _recordingTargetLease;
  final Set<String> _completedLiveItemIds = <String>{};
  int _recordingGeneration = 0;
  bool _disposed = false;

  bool get recordingIsCapturing =>
      recordingPhase == RecordingCapturePhase.recording ||
      recordingPhase == RecordingCapturePhase.paused;

  bool get recordingIsBusy =>
      recordingPhase == RecordingCapturePhase.starting ||
      recordingIsCapturing ||
      recordingPhase == RecordingCapturePhase.stopping ||
      recordingPhase == RecordingCapturePhase.uploading;

  bool get hasMinimizedRecording =>
      recordingMinimized && recordingPhase != RecordingCapturePhase.idle;

  int get pendingCaptureCount => outboxEntries.length;

  int get retryableCaptureCount => outboxEntries
      .where((entry) => entry.state == CaptureOutboxState.retryable)
      .length;

  bool get hasRetryableCaptureNow => outboxEntries.any(canRetryOutboxEntry);

  bool get hasCaptureAttention =>
      outboxEntries.isNotEmpty ||
      outboxError != null ||
      systemIngressIssueCode != null ||
      systemIngressStagingState == 'processing' ||
      systemIngressStagingState == 'partial_failure' ||
      systemIngressStagingState == 'failed' ||
      shareIngressMaintenance?.healthy == false ||
      outboxMaintenance?.manifestHealthy == false ||
      outboxMaintenance?.issueCode != null ||
      (outboxMaintenance?.confirmedFilesReadyForCleanup ?? 0) > 0;

  /// Restores a recording after the first app frame. A recording that was
  /// active when the process ended cannot honestly be resumed as an active
  /// microphone session, so it is surfaced as a local-only file ready for a
  /// verified upload retry.
  Future<void> restoreRecordingDraft() {
    return _restoreFuture ??= _restoreRecordingDraft();
  }

  Future<void> _restoreRecordingDraft() async {
    final generation = _recordingGeneration;
    RecordingDraft? draft;
    try {
      draft = await _recordingDraftStore.load();
    } on Object {
      return;
    }
    if (draft == null || _disposed || generation != _recordingGeneration) {
      return;
    }

    final recoveredAsset = await _recordingDraftStore.resolveLocalAsset(draft);
    final localExists = recoveredAsset != null;
    if (_disposed || generation != _recordingGeneration) return;

    recordingTitle = draft.title;
    recordingContext =
        const {'lecture', 'meeting', 'memo'}.contains(draft.context)
        ? draft.context
        : 'memo';
    recordingKnowledgeBaseId = draft.knowledgeBaseId;
    transcript = draft.transcript;
    recordingSeconds = draft.durationSeconds;
    moments = draft.moments;
    _recoveredServerRecordingId = draft.serverRecordingId;
    _recoveredCheckpointRevision = draft.checkpointRevision;
    _replaceRecordingTarget(
      draft.targetWorkspaceId != null && draft.connectionProfileId != null
          ? (
              workspaceId: draft.targetWorkspaceId!,
              profileId: draft.connectionProfileId!,
            )
          : null,
    );
    _recordingPcmFormat = draft.isGuntherPcm16Wav
        ? (
            encoding: draft.encoding!,
            sampleRate: draft.sampleRate!,
            channels: draft.channels!,
          )
        : null;
    if (recoveredAsset != null) {
      localRecording = recoveredAsset;
    }

    RecordingSession? remote;
    final serverId = draft.serverRecordingId;
    if (serverId != null) {
      try {
        final repository = _recordingRepository();
        remote = await repository.getRecordingMetadata(serverId);
      } on Object {
        // Offline recovery remains useful; retry uses this same server id.
      }
    }
    if (_disposed || generation != _recordingGeneration) return;
    if (remote != null) {
      recordingSession = remote;
      _recoveredServerRecordingId = remote.id;
      _recoveredCheckpointRevision = remote.checkpointRevision;
      if (remote.checkpointRevision > draft.checkpointRevision) {
        transcript = remote.transcript;
        recordingSeconds = remote.durationSeconds;
        moments = remote.moments;
        recordingContext = remote.recordingContext;
        recordingKnowledgeBaseId = remote.knowledgeBaseId;
      }
    }

    if (remote?.status == 'completed') {
      recordingPhase = RecordingCapturePhase.completed;
      recordingNotice =
          'Recovered a recording already saved in Gunther. Review it or keep it in Inbox.';
    } else if (localExists) {
      recordingPhase = RecordingCapturePhase.localOnly;
      recordingNotice =
          'Recovered audio from this device. Tap to retry the verified upload; active recording did not continue in the background.';
    } else {
      // A server-side completed session is still recoverable without a local
      // retry file. Otherwise avoid presenting an action that cannot work.
      recordingPhase = RecordingCapturePhase.idle;
      recordingNotice =
          'A recording recovery record was found, but its local audio file is no longer available.';
      error = recordingNotice;
    }
    recordingMinimized = recordingPhase != RecordingCapturePhase.idle;
    _safeNotify();
    if (recordingPhase != RecordingCapturePhase.idle) {
      unawaited(_queueDraftPersist());
    }
  }

  Future<void> restoreCaptureOutbox() {
    return _outboxRestoreFuture ??= _restoreCaptureOutbox();
  }

  Future<void> _restoreCaptureOutbox() async {
    try {
      final recovered = await _captureOutboxStore.recoverInterruptedAttempts();
      if (_disposed) return;
      outboxEntries = List<CaptureOutboxEntry>.unmodifiable(recovered);
      await _restoreOrBindStagedAudio();
      if (_disposed) return;
      outboxError = null;
      _safeNotify();
      await refreshOutboxMaintenance();
      await flushCaptureOutbox();
    } on Object catch (exception) {
      if (_disposed) return;
      outboxError = _friendlyIssue(exception.toString());
      _safeNotify();
      await refreshOutboxMaintenance();
    }
  }

  Future<int> flushCaptureOutbox({String? onlyEntryId}) {
    final result = _outboxFlushBarrier
        .catchError((Object _, StackTrace __) {})
        .then((_) => _flushCaptureOutbox(onlyEntryId: onlyEntryId));
    _outboxFlushBarrier = result.then<void>(
      (_) {},
      onError: (Object _, StackTrace __) {},
    );
    return result;
  }

  Future<int> _flushCaptureOutbox({String? onlyEntryId}) async {
    if (_disposed) return 0;
    outboxSyncing = true;
    outboxError = null;
    _safeNotify();
    var uploaded = 0;
    try {
      final current = await _captureOutboxStore.listEntries();
      if (_disposed) return 0;
      outboxEntries = List<CaptureOutboxEntry>.unmodifiable(current);
      final candidates = onlyEntryId == null
          ? current
          : current.where((entry) => entry.id == onlyEntryId).toList();
      for (final candidate in candidates) {
        if (_isRecordingImport(candidate)) continue;
        try {
          var uploadCandidate = candidate;
          CaptureTarget? target = _targetFromEntry(uploadCandidate);
          final targetProvider = _captureTargetProvider;
          if (targetProvider != null) {
            final activeTarget = targetProvider();
            if (activeTarget == null) {
              outboxError =
                  'Connect a knowledge workspace before syncing saved captures.';
              continue;
            }
            if (uploadCandidate.isTargetAssigned &&
                (uploadCandidate.targetWorkspaceId !=
                        activeTarget.workspaceId ||
                    uploadCandidate.connectionProfileId !=
                        activeTarget.profileId)) {
              outboxError =
                  'A saved capture belongs to another workspace and was not moved.';
              continue;
            }
            target = activeTarget;
          }

          Future<void> uploadWith(KnowledgeRepository repository) async {
            if (!uploadCandidate.isTargetAssigned) {
              final assignedTarget = target;
              if (assignedTarget != null) {
                uploadCandidate = await _captureOutboxStore.bindUnassignedEntry(
                  uploadCandidate.id,
                  workspaceId: assignedTarget.workspaceId,
                  profileId: assignedTarget.profileId,
                );
                _replaceOutboxEntry(uploadCandidate);
              }
            }
            final uploading = await _captureOutboxStore.markAttemptStarted(
              uploadCandidate.id,
            );
            _replaceOutboxEntry(uploading);
            await _uploadOutboxEntry(uploading, repository);
            await _captureOutboxStore.confirmServerSuccess(uploading.id);
            _removeOutboxEntry(uploading.id);
          }

          if (target case final assignedTarget?) {
            if (_repository is! CaptureTargetRepository) {
              throw StateError(
                'The knowledge repository cannot pin a capture to its saved workspace.',
              );
            }
            // Pin before the first asynchronous outbox mutation. The active
            // profile may change while binding, hashing, or uploading, but this
            // lease cannot be redirected to that newer profile.
            final lease = (_repository as CaptureTargetRepository)
                .pinCaptureTarget(
                  workspaceId: assignedTarget.workspaceId,
                  profileId: assignedTarget.profileId,
                );
            try {
              await uploadWith(lease.repository);
            } finally {
              lease.close();
            }
          } else {
            await uploadWith(_repository);
          }
          uploaded += 1;
        } on Object catch (exception) {
          try {
            final failed = await _captureOutboxStore.markAttemptFailed(
              candidate.id,
              error: exception.toString(),
            );
            _replaceOutboxEntry(failed);
          } on CaptureOutboxEntryNotFoundException {
            // Manifest acknowledgement already committed. A staged-file
            // cleanup error must not cause the network request to be repeated.
            _removeOutboxEntry(candidate.id);
          }
          outboxError = _friendlyIssue(exception.toString());
        }
        _safeNotify();
      }
      if (uploaded > 0) await _onOutboxUploaded?.call();
      return uploaded;
    } on Object catch (exception) {
      outboxError = _friendlyIssue(exception.toString());
      return uploaded;
    } finally {
      outboxSyncing = false;
      _safeNotify();
    }
  }

  Future<bool> retryCapture(String id) async {
    final entry = await _captureOutboxStore.loadEntry(id);
    if (entry == null) return false;
    if (_isRecordingImport(entry)) {
      return _retryImportedAudio(entry);
    }
    await flushCaptureOutbox(onlyEntryId: id);
    return !outboxEntries.any((candidate) => candidate.id == id);
  }

  /// Publishes platform Share Sheet items only after their app-owned originals
  /// have been committed to this outbox and acknowledged by native code.
  Future<void> acceptSystemShareEntries(
    List<CaptureOutboxEntry> entries,
  ) async {
    if (_disposed || entries.isEmpty) return;
    for (final entry in entries) {
      _replaceOutboxEntry(entry);
    }
    captureStatus = entries.length == 1
        ? 'Shared item saved on this device · syncing to Inbox.'
        : '${entries.length} shared items saved on this device · syncing to Inbox.';
    error = null;
    _safeNotify();
    await flushCaptureOutbox();
  }

  void reportSystemShareError(Object exception) {
    if (_disposed) return;
    systemIngressIssueCode = _diagnosticIssueCode(exception.toString());
    error =
        'A shared item is still safe on this device. Open Saved on this device for recovery details.';
    _safeNotify();
  }

  void reportSystemShareStagingState(String state) {
    if (_disposed ||
        !const {
          'idle',
          'processing',
          'completed',
          'partial_failure',
          'failed',
        }.contains(state)) {
      return;
    }
    systemIngressStagingState = state;
    if (state == 'failed') systemIngressIssueCode = 'native_staging_failed';
    if (state == 'partial_failure') {
      systemIngressIssueCode = 'native_staging_partial_failure';
    }
    if (state == 'completed' &&
        (systemIngressIssueCode == 'native_staging_failed' ||
            systemIngressIssueCode == 'native_staging_partial_failure')) {
      systemIngressIssueCode = null;
    }
    _safeNotify();
  }

  String outboxUserStatus(CaptureOutboxEntry entry) {
    final targetProvider = _captureTargetProvider;
    if (targetProvider != null) {
      final active = targetProvider();
      if (entry.isTargetAssigned && active == null) {
        return 'Blocked · Reconnect its original workspace';
      }
      if (entry.isTargetAssigned &&
          (entry.targetWorkspaceId != active?.workspaceId ||
              entry.connectionProfileId != active?.profileId)) {
        return 'Blocked · Belongs to another workspace';
      }
      if (!entry.isTargetAssigned && active == null) {
        return 'Blocked · Connect a workspace to choose its destination';
      }
    }
    if (entry.lastError != null) {
      return 'Failed · ${_friendlyIssue(entry.lastError!)}';
    }
    return switch (entry.state) {
      CaptureOutboxState.pending => 'Pending · Saved on device',
      CaptureOutboxState.uploading => 'Syncing · Original remains safe',
      CaptureOutboxState.retryable => 'Failed · Ready to retry',
      CaptureOutboxState.awaitingConfirmation =>
        'Blocked · Finish the imported recording',
    };
  }

  bool canRetryOutboxEntry(CaptureOutboxEntry entry) {
    if (!entry.canRetry) return false;
    final provider = _captureTargetProvider;
    if (provider == null) return true;
    final active = provider();
    if (active == null) return false;
    return !entry.isTargetAssigned ||
        (entry.targetWorkspaceId == active.workspaceId &&
            entry.connectionProfileId == active.profileId);
  }

  Future<void> refreshOutboxMaintenance() async {
    await _refreshLocalOutboxMaintenance();
    final shareSource = _shareIngressMaintenanceSource;
    if (shareSource != null) {
      try {
        shareIngressMaintenance = await shareSource.inspectShareMaintenance();
      } on MissingPluginException {
        // Desktop/widget hosts do not install the mobile share channel.
      } on Object catch (exception) {
        systemIngressIssueCode = _diagnosticIssueCode(exception.toString());
      }
    }
    _safeNotify();
  }

  Future<void> _refreshLocalOutboxMaintenance() async {
    final store = _captureOutboxStore;
    if (store is CaptureOutboxMaintenanceStore) {
      final maintenanceStore = store as CaptureOutboxMaintenanceStore;
      try {
        outboxMaintenance = await maintenanceStore.inspectMaintenance();
      } on Object catch (exception) {
        outboxError = _friendlyIssue(exception.toString());
      }
    }
  }

  Future<bool> quarantineDamagedShareQueue({
    required bool userConfirmed,
  }) async {
    final source = _shareIngressMaintenanceSource;
    if (source == null || maintenanceBusy) return false;
    maintenanceBusy = true;
    maintenanceNotice = null;
    _safeNotify();
    try {
      await source.quarantineDamagedShareQueue(userConfirmed: userConfirmed);
      maintenanceNotice =
          'The damaged incoming-share index was quarantined. Shared originals were not deleted.';
      systemIngressIssueCode = null;
      await refreshOutboxMaintenance();
      return true;
    } on Object catch (exception) {
      systemIngressIssueCode = _diagnosticIssueCode(exception.toString());
      return false;
    } finally {
      maintenanceBusy = false;
      _safeNotify();
    }
  }

  Future<int> cleanConfirmedOutboxFiles() async {
    final store = _captureOutboxStore;
    if (store is! CaptureOutboxMaintenanceStore || maintenanceBusy) return 0;
    final maintenanceStore = store as CaptureOutboxMaintenanceStore;
    maintenanceBusy = true;
    maintenanceNotice = null;
    _safeNotify();
    try {
      final count = await maintenanceStore.cleanConfirmedFiles();
      maintenanceNotice = count == 0
          ? 'No confirmed leftover files needed cleanup.'
          : 'Cleaned $count server-confirmed leftover file${count == 1 ? '' : 's'}.';
      await refreshOutboxMaintenance();
      return count;
    } on Object catch (exception) {
      outboxError = _friendlyIssue(exception.toString());
      return 0;
    } finally {
      maintenanceBusy = false;
      _safeNotify();
    }
  }

  Future<bool> quarantineDamagedOutbox({required bool userConfirmed}) async {
    final store = _captureOutboxStore;
    if (store is! CaptureOutboxMaintenanceStore || maintenanceBusy) {
      return false;
    }
    final maintenanceStore = store as CaptureOutboxMaintenanceStore;
    maintenanceBusy = true;
    maintenanceNotice = null;
    _safeNotify();
    try {
      await maintenanceStore.quarantineDamagedManifest(
        userConfirmed: userConfirmed,
      );
      outboxEntries = await _captureOutboxStore.recoverInterruptedAttempts();
      outboxError = null;
      maintenanceNotice =
          'The damaged index was quarantined. Captured originals were not deleted.';
      await refreshOutboxMaintenance();
      return true;
    } on Object catch (exception) {
      outboxError = _friendlyIssue(exception.toString());
      return false;
    } finally {
      maintenanceBusy = false;
      _safeNotify();
    }
  }

  Future<bool> quarantineDamagedCleanupJournal({
    required bool userConfirmed,
  }) async {
    final store = _captureOutboxStore;
    if (store is! CaptureOutboxMaintenanceStore || maintenanceBusy) {
      return false;
    }
    final maintenanceStore = store as CaptureOutboxMaintenanceStore;
    maintenanceBusy = true;
    maintenanceNotice = null;
    _safeNotify();
    try {
      await maintenanceStore.quarantineDamagedCleanupJournal(
        userConfirmed: userConfirmed,
      );
      maintenanceNotice =
          'The damaged cleanup journal was quarantined. No capture file was deleted.';
      await refreshOutboxMaintenance();
      return true;
    } on Object catch (exception) {
      outboxError = _friendlyIssue(exception.toString());
      return false;
    } finally {
      maintenanceBusy = false;
      _safeNotify();
    }
  }

  Future<String> sanitizedOutboxDiagnostics() async {
    // Export must not depend on a live platform-channel response. The native
    // snapshot is refreshed when the recovery sheet opens and is optional.
    await _refreshLocalOutboxMaintenance();
    final activeTarget = _captureTargetProvider?.call();
    final entries = outboxEntries.indexed.map((indexed) {
      final (index, entry) = indexed;
      final binding = !entry.isTargetAssigned
          ? 'unassigned'
          : activeTarget != null &&
                entry.targetWorkspaceId == activeTarget.workspaceId &&
                entry.connectionProfileId == activeTarget.profileId
          ? 'active-workspace'
          : 'different-workspace';
      return <String, Object?>{
        'reference': index + 1,
        'kind': entry.kind.name,
        'state': entry.state.name,
        'attemptCount': entry.attemptCount,
        'createdAt': entry.createdAt.toUtc().toIso8601String(),
        'updatedAt': entry.updatedAt.toUtc().toIso8601String(),
        'targetBinding': binding,
        'issueCode': entry.lastError == null
            ? null
            : _diagnosticIssueCode(entry.lastError!),
        'hasOriginal': entry.stagedFile != null,
        'originalSizeBytes': entry.stagedFile?.sizeBytes,
        'mediaClass': entry.stagedFile?.mediaType.split('/').first,
      };
    }).toList();
    final maintenance = outboxMaintenance;
    return const JsonEncoder.withIndent('  ').convert({
      'format': 'gunther-capture-diagnostics-v1',
      'generatedAt': DateTime.now().toUtc().toIso8601String(),
      'captureCount': entries.length,
      'systemIngressIssueCode': systemIngressIssueCode,
      'systemIngressStagingState': systemIngressStagingState,
      'shareManifestHealthy': shareIngressMaintenance?.healthy,
      'nativeSharePendingCount': shareIngressMaintenance?.pendingCount ?? 0,
      'quarantinedShareQueueCount':
          shareIngressMaintenance?.quarantinedQueueCount ?? 0,
      'manifestHealthy': maintenance?.manifestHealthy,
      'maintenanceIssueCode': maintenance?.issueCode,
      'confirmedFilesReadyForCleanup':
          maintenance?.confirmedFilesReadyForCleanup ?? 0,
      'quarantinedManifestCount': maintenance?.quarantinedManifestCount ?? 0,
      'captures': entries,
    });
  }

  Future<void> _uploadOutboxEntry(
    CaptureOutboxEntry entry,
    KnowledgeRepository repository,
  ) async {
    switch (entry.kind) {
      case CaptureOutboxKind.quickNote:
        await repository.createQuickNote(
          QuickNoteDraft(
            title: _payloadString(entry, 'title'),
            content: _payloadString(entry, 'content'),
            clientCaptureId: entry.id,
          ),
        );
        return;
      case CaptureOutboxKind.source:
      case CaptureOutboxKind.web:
        await repository.importSource(
          SourceDraft(
            title: _payloadString(entry, 'title'),
            kind: _payloadString(entry, 'kind'),
            content: _payloadString(entry, 'content'),
            knowledgeBaseId: _optionalPayloadString(
              entry.payload['knowledgeBaseId'],
            ),
          ),
        );
        return;
      case CaptureOutboxKind.link:
        await repository.captureWebSnapshot(
          WebSnapshotDraft(
            // v3 outboxes may still use `content` for a saved link. It is
            // upgraded in memory into the snapshot request and is never sent
            // to the generic Source endpoint again.
            url:
                _optionalPayloadString(entry.payload['url']) ??
                _payloadString(entry, 'content'),
            title: _optionalPayloadString(entry.payload['title']),
            notes: _optionalPayloadString(entry.payload['notes']) ?? '',
            knowledgeBaseId: _optionalPayloadString(
              entry.payload['knowledgeBaseId'],
            ),
            clientCaptureId: entry.id,
          ),
        );
        return;
      case CaptureOutboxKind.document:
      case CaptureOutboxKind.photo:
      case CaptureOutboxKind.audio:
        final asset = await _captureOutboxStore.resolveStagedAsset(entry.id);
        await repository.uploadAsset(
          asset,
          title: _payloadString(entry, 'title'),
          kind: _payloadString(entry, 'serverKind'),
          knowledgeBaseId: _optionalPayloadString(
            entry.payload['knowledgeBaseId'],
          ),
          notes: _optionalPayloadString(entry.payload['notes']) ?? '',
        );
        return;
    }
  }

  Future<bool> _retryImportedAudio(CaptureOutboxEntry entry) async {
    if (recordingIsBusy) return false;
    try {
      final staged = await _captureOutboxStore.resolveStagedAsset(entry.id);
      final current = localRecording;
      if (current != null &&
          current.path != staged.path &&
          recordingPhase != RecordingCapturePhase.idle) {
        outboxError =
            'Finish the current recording before recovering another imported audio file.';
        _safeNotify();
        return false;
      }
      if (current == null || current.path != staged.path) {
        _prepareNewRecording(_payloadString(entry, 'title'));
        _replaceRecordingTarget(_targetFromEntry(entry));
        localRecording = staged;
        recordingPhase = RecordingCapturePhase.localOnly;
        recordingMinimized = true;
      }
      _importedAudioOutboxEntryId = entry.id;
      await _queueDraftPersist();
      await _syncRecordingFile();
      return recordingPhase == RecordingCapturePhase.completed;
    } on Object catch (exception) {
      outboxError = _friendlyIssue(exception.toString());
      _safeNotify();
      return false;
    }
  }

  Future<void> _restoreOrBindStagedAudio() async {
    final imports = outboxEntries.where(_isRecordingImport);
    if (imports.isEmpty) return;
    final entry = imports.first;
    final staged = await _captureOutboxStore.resolveStagedAsset(entry.id);
    final current = localRecording;
    if (current != null) {
      if (current.path == staged.path) {
        _importedAudioOutboxEntryId = entry.id;
      }
      return;
    }
    if (recordingPhase != RecordingCapturePhase.idle) return;
    _prepareNewRecording(_payloadString(entry, 'title'));
    _replaceRecordingTarget(_targetFromEntry(entry));
    _importedAudioOutboxEntryId = entry.id;
    localRecording = staged;
    recordingPhase = RecordingCapturePhase.localOnly;
    recordingMinimized = true;
    recordingNotice =
        'Recovered imported audio from Gunther device storage. Tap to retry its verified upload.';
    await _queueDraftPersist();
  }

  void _replaceOutboxEntry(CaptureOutboxEntry entry) {
    final entries = [...outboxEntries];
    final index = entries.indexWhere((candidate) => candidate.id == entry.id);
    if (index < 0) {
      entries.add(entry);
    } else {
      entries[index] = entry;
    }
    entries.sort((left, right) => left.createdAt.compareTo(right.createdAt));
    outboxEntries = List<CaptureOutboxEntry>.unmodifiable(entries);
  }

  void _removeOutboxEntry(String id) {
    outboxEntries = List<CaptureOutboxEntry>.unmodifiable(
      outboxEntries.where((entry) => entry.id != id),
    );
  }

  void resetFeedback() {
    error = null;
    captureStatus = null;
    webResult = null;
    _safeNotify();
  }

  Future<bool> saveQuickNote(QuickNoteDraft draft) async {
    return _enqueuePayload(
      kind: CaptureOutboxKind.quickNote,
      payload: draft.toJson(),
    );
  }

  Future<bool> saveSource(SourceDraft draft) async {
    if (draft.kind == 'link') {
      return saveWebSnapshot(
        WebSnapshotDraft(
          url: draft.content,
          title: draft.title,
          knowledgeBaseId: draft.knowledgeBaseId,
        ),
      );
    }
    return _enqueuePayload(
      kind: CaptureOutboxKind.source,
      payload: draft.toJson(),
    );
  }

  Future<bool> saveWebSnapshot(WebSnapshotDraft draft) {
    return _enqueuePayload(
      kind: CaptureOutboxKind.link,
      // The outbox id is the only authoritative idempotency key. A caller
      // cannot inject a different clientCaptureId into durable state.
      payload: draft.toJson(includeClientCaptureId: false),
    );
  }

  Future<bool> captureDocument() {
    return _pickAndUpload(
      _captureDevice.pickDocument,
      outboxKind: CaptureOutboxKind.document,
      serverKind: 'file',
    );
  }

  Future<bool> capturePhoto(PhotoCaptureSource source) {
    return _pickAndUpload(
      () => _captureDevice.pickPhoto(source),
      outboxKind: CaptureOutboxKind.photo,
      serverKind: 'image',
    );
  }

  Future<bool> _pickAndUpload(
    Future<CapturedAsset?> Function() pick, {
    required CaptureOutboxKind outboxKind,
    required String serverKind,
  }) async {
    saving = true;
    error = null;
    captureStatus = null;
    _safeNotify();
    try {
      final asset = await pick();
      if (asset == null) return false;
      if (asset.sizeBytes > maxAssetBytes) {
        throw const CaptureDeviceException(
          'This file is larger than the 512 MB capture limit.',
        );
      }
      final target = _captureTargetProvider?.call();
      final queued = await _captureOutboxStore.enqueueAsset(
        kind: outboxKind,
        asset: asset,
        payload: {
          'title': _titleFromFileName(asset.fileName),
          'serverKind': serverKind,
        },
        targetWorkspaceId: target?.workspaceId,
        connectionProfileId: target?.profileId,
      );
      _replaceOutboxEntry(queued);
      captureStatus = 'Saved on this device · syncing to Inbox.';
      unawaited(flushCaptureOutbox(onlyEntryId: queued.id));
      return true;
    } on Object catch (exception) {
      error = exception.toString();
      return false;
    } finally {
      saving = false;
      _safeNotify();
    }
  }

  Future<bool> _enqueuePayload({
    required CaptureOutboxKind kind,
    required Map<String, Object?> payload,
  }) async {
    saving = true;
    error = null;
    captureStatus = null;
    _safeNotify();
    try {
      final target = _captureTargetProvider?.call();
      final queued = await _captureOutboxStore.enqueuePayload(
        kind: kind,
        payload: payload,
        targetWorkspaceId: target?.workspaceId,
        connectionProfileId: target?.profileId,
      );
      _replaceOutboxEntry(queued);
      captureStatus = 'Saved on this device · syncing to Inbox.';
      unawaited(flushCaptureOutbox(onlyEntryId: queued.id));
      return true;
    } on Object catch (exception) {
      error = exception.toString();
      return false;
    } finally {
      saving = false;
      _safeNotify();
    }
  }

  Future<bool> _save(Future<void> Function() action) async {
    saving = true;
    error = null;
    _safeNotify();
    try {
      await action();
      return true;
    } on Object catch (exception) {
      error = exception.toString();
      return false;
    } finally {
      saving = false;
      _safeNotify();
    }
  }

  Future<void> searchWeb(String query) async {
    searching = true;
    error = null;
    webResult = null;
    _safeNotify();
    try {
      webResult = await _repository.searchWeb(query);
    } on Object catch (exception) {
      error = exception.toString();
    } finally {
      searching = false;
      _safeNotify();
    }
  }

  Future<bool> saveWebResult() async {
    final result = webResult;
    if (result == null || result.answer.trim().isEmpty) return false;
    final citations = result.sources
        .map((source) => '${source.title}: ${source.url}')
        .join('\n');
    final source = SourceDraft(
      title: 'Web research: ${result.query}',
      kind: 'link',
      content: '${result.answer}\n\nSources\n$citations',
    );
    return _enqueuePayload(
      kind: CaptureOutboxKind.web,
      payload: {
        ...source.toJson(),
        'searchQuery': result.query,
        'citationSnapshot': citations,
      },
    );
  }

  Future<void> startRecording(String title) async {
    if (recordingIsBusy) return;
    final selectedContext = recordingContext;
    _prepareNewRecording(title);
    recordingContext = selectedContext;
    final generation = _recordingGeneration;
    KnowledgeRepository? recordingRepository;
    try {
      // Pin before microphone permission or file preparation yields. A profile
      // switch while those operations are pending must not redirect either the
      // server recording or its eventual Source.
      recordingRepository = _recordingRepository(allowInitialBinding: true);
    } on Object {
      // Local recording remains available even when its saved workspace cannot
      // currently provide a trusted transport.
    }
    final preparedTranscription =
        recordingRepository != null && _captureDevice is LivePcmCaptureDevice
        ? _createLiveTranscriptionSession()
        : null;
    recordingPhase = RecordingCapturePhase.starting;
    recordingNotice =
        'Requesting microphone access. No audio is claimed until recording starts.';
    _safeNotify();
    try {
      final path = await _captureDevice.startRecording(recordingTitle);
      final liveDevice = _captureDevice is LivePcmCaptureDevice
          ? _captureDevice as LivePcmCaptureDevice
          : null;
      final mediaType = liveDevice?.liveRecordingMediaType ?? 'audio/mp4';
      if (liveDevice != null) {
        _recordingPcmFormat = (
          encoding: RecordingDraft.guntherPcm16WavEncoding,
          sampleRate: liveDevice.livePcmSampleRate,
          channels: liveDevice.livePcmChannels,
        );
      }
      localRecording = CapturedAsset(
        path: path,
        fileName: Uri.file(path).pathSegments.last,
        mediaType: mediaType,
        // The file is still open and growing. Its authoritative final size is
        // captured by stopRecording; recovery recomputes size from disk.
        sizeBytes: 0,
      );
      recordingPhase = RecordingCapturePhase.recording;
      recordingNotice =
          'Recording to a recoverable app file in the foreground.';
      _startClock();
      // Persist the path before attempting any network request. The local
      // recording stays recoverable even if the backend is offline.
      await _queueDraftPersist();
      _startLiveTranscription(
        generation,
        liveDevice,
        preparedSession: preparedTranscription,
      );
      _safeNotify();
    } on Object catch (exception) {
      unawaited(preparedTranscription?.dispose());
      _closeRecordingTargetLease();
      recordingPhase = RecordingCapturePhase.failed;
      error = exception.toString();
      recordingNotice = 'Recording did not start. No audio was captured.';
      _safeNotify();
      return;
    }

    try {
      final repository = recordingRepository;
      if (repository == null) {
        throw StateError(
          'The recording workspace is not available for sync right now.',
        );
      }
      recordingSession = await repository.startRecordingSession(
        recordingTitle,
        localRecording?.mediaType ?? 'audio/wav',
      );
      _recoveredServerRecordingId = recordingSession!.id;
      _recoveredCheckpointRevision = recordingSession!.checkpointRevision;
      recordingNotice =
          'Audio is being written on this device; progress metadata is also checkpointed to Gunther.';
      await _queueDraftPersist();
      await _queueCheckpoint(repository: repository);
    } on Object catch (_) {
      recordingNotice =
          'Audio is still being written on this device. Server sync is unavailable and will be retried when you finish.';
    }
    _safeNotify();
  }

  void _startLiveTranscription(
    int generation,
    LivePcmCaptureDevice? liveDevice, {
    LiveTranscriptionSession? preparedSession,
  }) {
    final stream = liveDevice?.livePcmStream;
    if (liveDevice == null || stream == null) {
      unawaited(preparedSession?.dispose());
      recordingTranscriptionStatus =
          'This recorder does not expose a compatible live PCM stream. The audio file is still safe.';
      return;
    }
    if (liveDevice.livePcmSampleRate != liveTranscriptionSampleRate ||
        liveDevice.livePcmChannels != 1) {
      unawaited(preparedSession?.dispose());
      recordingTranscriptionStatus =
          'Live transcription needs 24 kHz mono PCM. This recording remains local and complete.';
      return;
    }
    if (_liveTranscriptionFactory == null) {
      unawaited(preparedSession?.dispose());
      recordingTranscriptionStatus =
          'No verified live transcription connection is configured. The WAV recording continues locally.';
      return;
    }

    final session = preparedSession;
    if (session == null) {
      recordingTranscriptionStatus =
          'The current server URL is not safe for live transcription. The WAV recording continues locally.';
      return;
    }
    _liveTranscription = session;
    recordingTranscriptionStatus = 'Connecting live transcription…';
    _liveEventSubscription = session.events.listen(
      (event) => _handleLiveTranscriptionEvent(generation, session, event),
      onError: (Object _) {
        if (_disposed || generation != _recordingGeneration) return;
        recordingTranscriptionStatus =
            'Live transcription stopped responding. The WAV recording continues locally.';
        _safeNotify();
      },
    );
    _livePcmSubscription = stream.listen(
      session.appendPcm,
      onError: (Object _) {
        if (_disposed || generation != _recordingGeneration) return;
        recordingTranscriptionStatus =
            'The microphone stream ended unexpectedly; the recoverable WAV is being finalized.';
        _safeNotify();
      },
    );
    unawaited(
      session.connect().catchError((Object _) {
        if (!_disposed && generation == _recordingGeneration) {
          recordingTranscriptionStatus =
              'Live transcription is offline. The WAV recording continues locally.';
          _safeNotify();
        }
      }),
    );
  }

  LiveTranscriptionSession? _createLiveTranscriptionSession() {
    final factory = _liveTranscriptionFactory;
    if (factory == null) return null;
    try {
      // The factory snapshots profile, credentials, and TLS policy. Calling it
      // before the first await keeps live PCM on the same target as the pinned
      // recording repository even if the active profile changes afterward.
      return factory('$recordingTitle · $recordingContext');
    } on Object {
      return null;
    }
  }

  void _handleLiveTranscriptionEvent(
    int generation,
    LiveTranscriptionSession session,
    LiveTranscriptionEvent event,
  ) {
    if (_disposed ||
        generation != _recordingGeneration ||
        !identical(session, _liveTranscription)) {
      return;
    }
    switch (event) {
      case LiveTranscriptionStatusEvent():
        recordingTranscriptionStatus = switch (event.state) {
          LiveTranscriptionConnectionState.idle =>
            'Live transcription is waiting to connect.',
          LiveTranscriptionConnectionState.connecting =>
            'Connecting live transcription…',
          LiveTranscriptionConnectionState.connected =>
            'Connected; waiting for the transcription service…',
          LiveTranscriptionConnectionState.ready =>
            'Live transcription is listening.',
          LiveTranscriptionConnectionState.reconnecting =>
            'Live transcription is reconnecting (attempt ${event.reconnectAttempt}). The WAV is unaffected.',
          LiveTranscriptionConnectionState.offline =>
            'Live transcription is offline. The WAV recording continues locally.',
          LiveTranscriptionConnectionState.stopped =>
            'Live transcription stopped. The audio file remains authoritative.',
        };
      case LiveTranscriptionReadyEvent():
        final local = event.local || event.provider == 'sensevoice';
        recordingTranscriptionStatus = local
            ? 'Local ${event.provider} live transcription is listening.'
            : '${event.provider} live transcription is listening.';
      case LiveTranscriptionDeltaEvent():
        liveTranscriptPreview = '$liveTranscriptPreview${event.delta}';
        if (liveTranscriptPreview.length > 2000) {
          liveTranscriptPreview = liveTranscriptPreview.substring(
            liveTranscriptPreview.length - 2000,
          );
        }
      case LiveTranscriptionCompletedEvent():
        final itemId = event.itemId;
        if (itemId != null && !_completedLiveItemIds.add(itemId)) return;
        final completed = event.transcript.trim();
        if (completed.isEmpty) return;
        final existing = transcript.trimRight();
        transcript = existing.isEmpty ? completed : '$existing\n$completed';
        liveTranscriptPreview = '';
        unawaited(_queueDraftPersist());
        _checkpointDebounce?.cancel();
        _checkpointDebounce = Timer(
          const Duration(milliseconds: 300),
          () => unawaited(_queueCheckpoint()),
        );
        if (!recordingIsCapturing) {
          _scheduleLiveCleanup(const Duration(seconds: 2));
        }
      case LiveTranscriptionErrorEvent():
        recordingTranscriptionStatus =
            'Live transcript notice: ${event.message} The WAV recording is unaffected.';
    }
    if (session.droppedBytes > 0) {
      recordingTranscriptionStatus =
          '$recordingTranscriptionStatus Some live audio was skipped during reconnection; the complete WAV is safe.';
    }
    _safeNotify();
  }

  void _commitLiveTranscription() {
    final session = _liveTranscription;
    if (session == null) return;
    unawaited(_livePcmSubscription?.cancel());
    _livePcmSubscription = null;
    session.commit();
    recordingTranscriptionStatus =
        'Finalizing the last live transcript segments; the WAV is already safe.';
    _scheduleLiveCleanup(const Duration(seconds: 12));
  }

  void _scheduleLiveCleanup(Duration delay) {
    final session = _liveTranscription;
    if (session == null) return;
    _liveCleanupTimer?.cancel();
    _liveCleanupTimer = Timer(delay, () {
      if (identical(session, _liveTranscription)) {
        unawaited(_disposeLiveTranscription());
      }
    });
  }

  Future<void> _disposeLiveTranscription() async {
    _liveCleanupTimer?.cancel();
    _liveCleanupTimer = null;
    final pcmSubscription = _livePcmSubscription;
    final eventSubscription = _liveEventSubscription;
    final session = _liveTranscription;
    _livePcmSubscription = null;
    _liveEventSubscription = null;
    _liveTranscription = null;
    try {
      await pcmSubscription?.cancel();
    } on Object {
      // Live transcription cleanup never controls the recording lifecycle.
    }
    try {
      await eventSubscription?.cancel();
    } on Object {
      // Live transcription cleanup never controls the recording lifecycle.
    }
    try {
      await session?.dispose();
    } on Object {
      // Live transcription cleanup never controls the recording lifecycle.
    }
  }

  Future<void> pauseRecording() async {
    if (recordingPhase != RecordingCapturePhase.recording) return;
    try {
      await _captureDevice.pauseRecording();
      _recordingClock?.cancel();
      recordingPhase = RecordingCapturePhase.paused;
      recordingNotice =
          'Paused. The audio already written on this device remains intact.';
      _safeNotify();
      await _queueCheckpoint();
    } on Object catch (exception) {
      error = exception.toString();
      _safeNotify();
    }
  }

  Future<void> resumeRecording() async {
    if (recordingPhase != RecordingCapturePhase.paused) return;
    try {
      await _captureDevice.resumeRecording();
      recordingPhase = RecordingCapturePhase.recording;
      recordingNotice = 'Recording resumed in the foreground.';
      _startClock();
      _safeNotify();
      await _queueDraftPersist();
    } on Object catch (exception) {
      error = exception.toString();
      _safeNotify();
    }
  }

  void updateTranscript(String value) {
    transcript = value;
    _safeNotify();
    unawaited(_queueDraftPersist());
    _checkpointDebounce?.cancel();
    _checkpointDebounce = Timer(
      const Duration(milliseconds: 800),
      () => unawaited(_queueCheckpoint()),
    );
  }

  void updateRecordingContext(String value) {
    if (!const {'lecture', 'meeting', 'memo'}.contains(value)) return;
    recordingContext = value;
    _safeNotify();
    unawaited(_queueDraftPersist());
    unawaited(_queueCheckpoint());
  }

  void markMoment() {
    if (!recordingIsCapturing) return;
    moments = [
      ...moments,
      RecordingMoment(
        seconds: recordingSeconds,
        label: 'Marked moment ${moments.length + 1}',
      ),
    ];
    _safeNotify();
    unawaited(_queueDraftPersist());
    unawaited(_queueCheckpoint());
  }

  void removeMoment(int index) {
    if (index < 0 || index >= moments.length) return;
    moments = [...moments]..removeAt(index);
    _safeNotify();
    unawaited(_queueDraftPersist());
    unawaited(_queueCheckpoint());
  }

  Future<void> stopRecording() async {
    if (!recordingIsCapturing) return;
    _recordingClock?.cancel();
    _checkpointDebounce?.cancel();
    recordingPhase = RecordingCapturePhase.stopping;
    recordingNotice = 'Finalizing the local audio file before upload.';
    _safeNotify();
    await _queueDraftPersist();
    try {
      localRecording = await _captureDevice.stopRecording();
      final minimumBytes = _recordingPcmFormat == null
          ? 0
          : PcmWavFile.headerBytes;
      if (localRecording!.sizeBytes <= minimumBytes) {
        throw const CaptureDeviceException('The recorded audio file is empty.');
      }
      if (localRecording!.sizeBytes > maxRecordingBytes) {
        throw const CaptureDeviceException(
          'This recording is larger than the 2 GB session limit.',
        );
      }
      _commitLiveTranscription();
      await _queueDraftPersist();
      await _syncRecordingFile();
    } on Object catch (exception) {
      final recoverable = localRecording;
      if (recoverable != null) {
        try {
          final file = File(recoverable.path);
          final length = await file.length();
          localRecording = CapturedAsset(
            path: recoverable.path,
            fileName: recoverable.fileName,
            mediaType: recoverable.mediaType,
            sizeBytes: length,
          );
        } on Object {
          // Keep the original draft pointer; startup recovery will verify it.
        }
        _commitLiveTranscription();
      }
      recordingPhase = localRecording == null
          ? RecordingCapturePhase.failed
          : RecordingCapturePhase.localOnly;
      error = exception.toString();
      recordingNotice = localRecording == null
          ? 'The recorder could not finalize a local audio file.'
          : 'The audio file remains on this device, but server upload did not finish. You can retry.';
      await _queueDraftPersist();
      _safeNotify();
    }
  }

  Future<void> importAudio() async {
    if (recordingIsBusy) return;
    error = null;
    recordingNotice = 'Opening the system audio picker.';
    recordingPhase = RecordingCapturePhase.starting;
    _safeNotify();
    try {
      final asset = await _captureDevice.pickAudio();
      if (asset == null) {
        recordingPhase = RecordingCapturePhase.idle;
        recordingNotice =
            'Audio is saved to an app file first. Live transcription needs a verified compatible PCM stream.';
        _safeNotify();
        return;
      }
      if (asset.sizeBytes > maxRecordingBytes) {
        throw const CaptureDeviceException(
          'This audio file is larger than the 2 GB session limit.',
        );
      }
      final importedTitle = _titleFromFileName(asset.fileName);
      final target = _captureTargetProvider?.call();
      final queued = await _captureOutboxStore.enqueueAsset(
        kind: CaptureOutboxKind.audio,
        asset: asset,
        payload: {
          'workflow': 'recordingImport',
          'title': importedTitle,
          'serverKind': 'recording',
        },
        targetWorkspaceId: target?.workspaceId,
        connectionProfileId: target?.profileId,
      );
      _replaceOutboxEntry(queued);
      final stagedAsset = await _captureOutboxStore.resolveStagedAsset(
        queued.id,
      );
      _prepareNewRecording(importedTitle);
      _replaceRecordingTarget(target);
      _importedAudioOutboxEntryId = queued.id;
      localRecording = stagedAsset;
      await _queueDraftPersist();
      await _syncRecordingFile();
    } on Object catch (exception) {
      recordingPhase = localRecording == null
          ? RecordingCapturePhase.failed
          : RecordingCapturePhase.localOnly;
      error = exception.toString();
      recordingNotice = localRecording == null
          ? 'No audio was imported.'
          : 'The selected audio remains on this device, but server upload did not finish.';
      await _queueDraftPersist();
      _safeNotify();
    }
  }

  Future<void> retryRecordingUpload() async {
    if (localRecording == null || recordingIsBusy) return;
    error = null;
    await _syncRecordingFile();
  }

  Future<bool> saveRecordingToInbox() async {
    final session = recordingSession;
    if (session == null || session.status != 'completed') return false;
    late final KnowledgeRepository repository;
    try {
      // Reuse the lease that owns the completed recording. If this is a
      // restored session, pin its saved target synchronously before any await.
      repository = _recordingRepository();
    } on Object catch (exception) {
      error = exception.toString();
      recordingNotice =
          'Reconnect this recording\'s original workspace before saving its Source.';
      _safeNotify();
      return false;
    }
    final cleanTranscript = transcript.trim();
    final content = cleanTranscript.isNotEmpty
        ? '$cleanTranscript\n\nLocal recording: ${session.id}'
        : 'Audio recording preserved by Gunther.\n'
              'Local recording: ${session.id}\n'
              'Duration: ${_formatDuration(recordingSeconds)}';
    final saved = await _save(
      () => repository.importSource(
        SourceDraft(title: recordingTitle, kind: 'recording', content: content),
      ),
    );
    if (saved) {
      recordingFiledToInbox = true;
      recordingMinimized = false;
      recordingNotice =
          'Recording source saved to Inbox for later organization.';
      // Only the small recovery record is removed. The original audio is not
      // deleted automatically, even after the server and Source are safe.
      await _queueDraftClear();
      final importedEntryId = _importedAudioOutboxEntryId;
      if (importedEntryId != null) {
        try {
          await _captureOutboxStore.confirmServerSuccess(importedEntryId);
          _removeOutboxEntry(importedEntryId);
          _importedAudioOutboxEntryId = null;
        } on Object catch (exception) {
          outboxError =
              'The imported audio is safe, but its device-copy cleanup needs attention: $exception';
        }
      }
      _closeRecordingTargetLease();
      _safeNotify();
    }
    return saved;
  }

  void minimizeRecording() {
    if (recordingPhase == RecordingCapturePhase.idle || recordingMinimized) {
      return;
    }
    recordingMinimized = true;
    _safeNotify();
    unawaited(_queueDraftPersist());
  }

  void expandRecording() {
    if (!recordingMinimized) return;
    recordingMinimized = false;
    _safeNotify();
  }

  void beginAnotherRecording() {
    if (recordingIsBusy) return;
    if (recordingPhase == RecordingCapturePhase.completed &&
        !recordingFiledToInbox) {
      recordingNotice =
          'Keep this completed recording in Inbox before preparing another one.';
      error =
          'This recording is safe on the server but does not have a Source yet.';
      _safeNotify();
      return;
    }
    _prepareNewRecording('Voice memo');
    _safeNotify();
  }

  Future<void> _syncRecordingFile() async {
    final asset = localRecording;
    if (asset == null) return;
    late final KnowledgeRepository repository;
    try {
      // Acquire and retain one immutable transport before draft/outbox writes
      // yield. Every request for this recording, including its final Source,
      // will keep using this repository even if the active profile changes.
      repository = _recordingRepository(allowInitialBinding: true);
    } on Object catch (exception) {
      recordingPhase = RecordingCapturePhase.localOnly;
      error = exception.toString();
      recordingNotice =
          'The complete audio remains on this device. Reconnect its original workspace to continue.';
      await _queueDraftPersist();
      _safeNotify();
      return;
    }
    recordingPhase = RecordingCapturePhase.uploading;
    recordingUploadProgress = 0;
    recordingNotice =
        'Uploading the completed local file in verified 8 MB chunks.';
    _safeNotify();
    await _queueDraftPersist();
    try {
      final importedEntryId = _importedAudioOutboxEntryId;
      if (importedEntryId != null) {
        final uploading = await _captureOutboxStore.markAttemptStarted(
          importedEntryId,
        );
        _replaceOutboxEntry(uploading);
      }
      var current = recordingSession;
      final recoveredId = _recoveredServerRecordingId;
      if (current == null && recoveredId != null) {
        try {
          current = await repository.getRecordingMetadata(recoveredId);
        } on ApiException catch (exception) {
          if (exception.statusCode != 404) rethrow;
          _recoveredServerRecordingId = null;
        }
      }
      if (current != null) {
        current = await repository.getRecordingMetadata(current.id);
      }
      if (current == null || current.status == 'failed') {
        current = await repository.startRecordingSession(
          recordingTitle,
          asset.mediaType,
        );
      }
      recordingSession = current;
      _recoveredServerRecordingId = current.id;
      _recoveredCheckpointRevision = current.checkpointRevision;
      await _queueDraftPersist();
      await _queueCheckpoint(repository: repository);
      current = recordingSession ?? current;
      if (current.status != 'completed') {
        current = await repository.uploadRecordingFile(
          current,
          asset,
          onProgress: (sentBytes, totalBytes) {
            recordingUploadProgress = totalBytes == 0
                ? 0
                : sentBytes / totalBytes;
            _safeNotify();
          },
        );
        current = await repository.completeRecordingSession(current.id);
      }
      recordingSession = current;
      _recoveredServerRecordingId = current.id;
      _recoveredCheckpointRevision = current.checkpointRevision;
      recordingUploadProgress = 1;
      await _queueCheckpoint(repository: repository);
      recordingPhase = RecordingCapturePhase.completed;
      recordingNotice =
          'Complete audio saved in Gunther. Add or edit a transcript, then keep it in Inbox.';
      error = null;
      await _queueDraftPersist();
      if (importedEntryId != null) {
        final awaiting = await _captureOutboxStore.markAwaitingConfirmation(
          importedEntryId,
        );
        _replaceOutboxEntry(awaiting);
      }
    } on Object catch (exception) {
      recordingPhase = RecordingCapturePhase.localOnly;
      error = exception.toString();
      recordingNotice =
          'Upload was interrupted. The complete audio file remains on this device; retry when the backend is reachable.';
      await _queueDraftPersist();
      final importedEntryId = _importedAudioOutboxEntryId;
      if (importedEntryId != null) {
        try {
          final failed = await _captureOutboxStore.markAttemptFailed(
            importedEntryId,
            error: exception.toString(),
          );
          _replaceOutboxEntry(failed);
        } on Object {
          // The recording draft still points at the app-owned copy. Outbox
          // recovery will retry its own state update on the next launch.
        }
      }
    }
    _safeNotify();
  }

  Future<void> _queueCheckpoint({KnowledgeRepository? repository}) {
    _checkpointChain = _checkpointChain.then(
      (_) => _persistCheckpoint(repository: repository),
    );
    return _checkpointChain;
  }

  Future<void> _persistCheckpoint({KnowledgeRepository? repository}) async {
    var session = recordingSession;
    if (session == null || session.status == 'failed') {
      await _queueDraftPersist();
      return;
    }
    KnowledgeRepository targetRepository;
    try {
      // A timer-triggered checkpoint pins synchronously before the first await.
      // Upload flows pass their already-pinned repository through explicitly.
      targetRepository = repository ?? _recordingRepository();
    } on Object {
      await _queueDraftPersist();
      return;
    }
    await _queueDraftPersist();
    final sessionId = session.id;
    final checkpoint = RecordingCheckpointDraft(
      transcript: transcript,
      durationSeconds: recordingSeconds,
      moments: moments,
      recordingContext: recordingContext,
      knowledgeBaseId: recordingKnowledgeBaseId,
    );
    try {
      session = await targetRepository.checkpointRecording(session, checkpoint);
      if (recordingSession?.id == session.id) {
        recordingSession = session;
        _recoveredServerRecordingId = session.id;
        _recoveredCheckpointRevision = session.checkpointRevision;
      }
    } on ApiException catch (exception) {
      if (exception.statusCode != 409) return;
      try {
        final fresh = await targetRepository.getRecordingMetadata(sessionId);
        final updated = await targetRepository.checkpointRecording(
          fresh,
          checkpoint,
        );
        if (recordingSession?.id == updated.id) {
          recordingSession = updated;
          _recoveredServerRecordingId = updated.id;
          _recoveredCheckpointRevision = updated.checkpointRevision;
        }
      } on Object catch (_) {
        // The next timer or edit retries. Audio persistence is independent.
      }
    } on Object catch (_) {
      // The next timer or edit retries. Audio persistence is independent.
    }
    await _queueDraftPersist();
    _safeNotify();
  }

  Future<void> _queueDraftPersist() {
    final asset = localRecording;
    if (asset == null || recordingFiledToInbox) return Future<void>.value();
    final session = recordingSession;
    final pcmFormat = _recordingPcmFormat;
    final target = _recordingTarget;
    final draft = RecordingDraft(
      title: recordingTitle,
      context: recordingContext,
      transcript: transcript,
      durationSeconds: recordingSeconds,
      moments: List<RecordingMoment>.unmodifiable(moments),
      localPath: asset.path,
      fileName: asset.fileName,
      mediaType: asset.mediaType,
      sizeBytes: asset.sizeBytes,
      phase: recordingPhase.name,
      knowledgeBaseId: recordingKnowledgeBaseId,
      serverRecordingId: session?.id ?? _recoveredServerRecordingId,
      checkpointRevision:
          session?.checkpointRevision ?? _recoveredCheckpointRevision,
      updatedAt: DateTime.now().toUtc(),
      encoding: pcmFormat?.encoding,
      sampleRate: pcmFormat?.sampleRate,
      channels: pcmFormat?.channels,
      // Until a runtime connection profile has explicitly bound this capture,
      // null means unassigned. Never infer a destination from whichever
      // server happens to be reachable later.
      targetWorkspaceId: target?.workspaceId,
      connectionProfileId: target?.profileId,
    );
    return _queueDraftWrite(() => _recordingDraftStore.save(draft));
  }

  Future<void> _queueDraftClear() {
    return _queueDraftWrite(_recordingDraftStore.clear);
  }

  Future<void> _queueDraftWrite(Future<void> Function() write) {
    _draftWriteChain = _draftWriteChain
        .catchError((Object _, StackTrace __) {})
        .then((_) => write())
        .catchError((Object exception, StackTrace _) {
          if (!_disposed) {
            error = 'Could not update recording recovery: $exception';
            _safeNotify();
          }
        });
    return _draftWriteChain;
  }

  void _prepareNewRecording(String title) {
    _recordingGeneration += 1;
    unawaited(_disposeLiveTranscription());
    _closeRecordingTargetLease();
    _recordingClock?.cancel();
    _checkpointDebounce?.cancel();
    _checkpointChain = Future<void>.value();
    recordingPhase = RecordingCapturePhase.idle;
    recordingSession = null;
    _recoveredServerRecordingId = null;
    _recoveredCheckpointRevision = 0;
    _importedAudioOutboxEntryId = null;
    _recordingPcmFormat = null;
    _recordingTarget = _captureTargetProvider?.call();
    _completedLiveItemIds.clear();
    localRecording = null;
    recordingTitle = title.trim().isEmpty ? 'Voice memo' : title.trim();
    recordingContext = 'memo';
    recordingKnowledgeBaseId = null;
    transcript = '';
    recordingSeconds = 0;
    moments = const [];
    recordingUploadProgress = 0;
    recordingTranscriptionStatus =
        'Live transcription is not connected. The audio file remains authoritative.';
    liveTranscriptPreview = '';
    recordingMinimized = false;
    recordingFiledToInbox = false;
    error = null;
  }

  KnowledgeRepository _recordingRepository({bool allowInitialBinding = false}) {
    final existing = _recordingTargetLease;
    if (existing != null) return existing.repository;

    final provider = _captureTargetProvider;
    if (provider == null) return _repository;
    final active = provider();
    if (active == null) {
      throw StateError(
        'Connect the recording\'s knowledge workspace before syncing it.',
      );
    }
    var assigned = _recordingTarget;
    if (assigned == null) {
      if (!allowInitialBinding || _recoveredServerRecordingId != null) {
        throw StateError(
          'This recording has no verified workspace destination.',
        );
      }
      _recordingTarget = active;
      assigned = active;
    }
    if (assigned.workspaceId != active.workspaceId ||
        assigned.profileId != active.profileId) {
      throw StateError(
        'This recording belongs to another workspace and was not uploaded.',
      );
    }
    if (_repository is! CaptureTargetRepository) {
      throw StateError(
        'The knowledge repository cannot pin this recording to its saved workspace.',
      );
    }
    final lease = (_repository as CaptureTargetRepository).pinCaptureTarget(
      workspaceId: assigned.workspaceId,
      profileId: assigned.profileId,
    );
    _recordingTargetLease = lease;
    return lease.repository;
  }

  void _replaceRecordingTarget(CaptureTarget? target) {
    _closeRecordingTargetLease();
    _recordingTarget = target;
  }

  void _closeRecordingTargetLease() {
    final lease = _recordingTargetLease;
    _recordingTargetLease = null;
    lease?.close();
  }

  CaptureTarget? _targetFromEntry(CaptureOutboxEntry entry) {
    final workspaceId = entry.targetWorkspaceId;
    final profileId = entry.connectionProfileId;
    if (workspaceId == null || profileId == null) return null;
    return (workspaceId: workspaceId, profileId: profileId);
  }

  void _startClock() {
    _recordingClock?.cancel();
    _recordingClock = Timer.periodic(const Duration(seconds: 1), (_) {
      recordingSeconds += 1;
      _safeNotify();
      if (recordingSeconds % 5 == 0) unawaited(_queueCheckpoint());
    });
  }

  void _safeNotify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _recordingClock?.cancel();
    _checkpointDebounce?.cancel();
    _liveCleanupTimer?.cancel();
    unawaited(_queueDraftPersist());
    _disposed = true;
    _closeRecordingTargetLease();
    unawaited(_disposeLiveTranscription());
    unawaited(_captureDevice.dispose());
    super.dispose();
  }
}

String _titleFromFileName(String fileName) {
  final dot = fileName.lastIndexOf('.');
  final title = dot > 0 ? fileName.substring(0, dot) : fileName;
  return title.trim().isEmpty ? 'Captured material' : title.trim();
}

bool _isRecordingImport(CaptureOutboxEntry entry) {
  return entry.kind == CaptureOutboxKind.audio &&
      entry.payload['workflow'] == 'recordingImport';
}

String _payloadString(CaptureOutboxEntry entry, String key) {
  final value = entry.payload[key];
  if (value is! String || value.isEmpty) {
    throw CaptureOutboxException(
      'Capture ${entry.id} has an invalid "$key" payload.',
    );
  }
  return value;
}

String _friendlyIssue(String value) {
  final normalized = value.toLowerCase();
  if (normalized.contains('another workspace') ||
      normalized.contains('original workspace')) {
    return 'Reconnect the workspace that owns this capture.';
  }
  if (normalized.contains('connect') || normalized.contains('offline')) {
    return 'Workspace is offline or not connected.';
  }
  if (normalized.contains('certificate') || normalized.contains('trust')) {
    return 'Workspace trust must be verified again.';
  }
  if (normalized.contains('auth') || normalized.contains('token')) {
    return 'Workspace authorization needs attention.';
  }
  if (normalized.contains('checksum') || normalized.contains('changed')) {
    return 'The saved original failed its integrity check.';
  }
  if (normalized.contains('missing')) {
    return 'The saved original could not be found.';
  }
  if (normalized.contains('manifest') || normalized.contains('damaged')) {
    return 'The local capture index is damaged; originals were left untouched.';
  }
  if (normalized.contains('too large') || normalized.contains('limit')) {
    return 'The capture exceeds the safe size limit.';
  }
  return 'Sync did not complete. The local capture is retained.';
}

String _diagnosticIssueCode(String value) {
  final normalized = value.toLowerCase();
  if (normalized.contains('another workspace') ||
      normalized.contains('original workspace')) {
    return 'workspace_mismatch';
  }
  if (normalized.contains('certificate')) return 'certificate_changed';
  if (normalized.contains('auth') || normalized.contains('token')) {
    return 'authorization_required';
  }
  if (normalized.contains('checksum') || normalized.contains('changed')) {
    return 'original_integrity_failed';
  }
  if (normalized.contains('missing')) return 'original_missing';
  if (normalized.contains('manifest') || normalized.contains('damaged')) {
    return 'local_index_corrupt';
  }
  if (normalized.contains('too large') || normalized.contains('limit')) {
    return 'size_limit';
  }
  if (normalized.contains('offline') || normalized.contains('connect')) {
    return 'workspace_unavailable';
  }
  if (normalized.contains('image_picker')) return 'image_picker_recovery';
  return 'sync_incomplete';
}

String? _optionalPayloadString(Object? value) {
  if (value == null) return null;
  if (value is! String) {
    throw const CaptureOutboxException(
      'An optional capture payload field is invalid.',
    );
  }
  return value.isEmpty ? null : value;
}

String _formatDuration(int seconds) {
  final hours = seconds ~/ 3600;
  final minutes = (seconds % 3600) ~/ 60;
  final remainder = seconds % 60;
  return '${hours.toString().padLeft(2, '0')}:'
      '${minutes.toString().padLeft(2, '0')}:'
      '${remainder.toString().padLeft(2, '0')}';
}
