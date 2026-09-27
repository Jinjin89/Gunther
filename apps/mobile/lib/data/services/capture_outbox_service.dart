import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:crypto/crypto.dart';
import 'package:gunther_mobile/data/models/capture.dart';
import 'package:gunther_mobile/data/models/capture_outbox.dart';
import 'package:path_provider/path_provider.dart';

typedef CaptureOutboxDirectoryProvider = Future<Directory> Function();

abstract interface class CaptureOutboxStore {
  Future<List<CaptureOutboxEntry>> listEntries();
  Future<CaptureOutboxEntry?> loadEntry(String id);
  Future<CaptureOutboxEntry> enqueuePayload({
    required CaptureOutboxKind kind,
    required Map<String, Object?> payload,
    String? targetWorkspaceId,
    String? connectionProfileId,
  });
  Future<CaptureOutboxEntry> enqueueAsset({
    required CaptureOutboxKind kind,
    required CapturedAsset asset,
    Map<String, Object?> payload = const <String, Object?>{},
    String? targetWorkspaceId,
    String? connectionProfileId,
  });
  Future<CaptureOutboxEntry> bindUnassignedEntry(
    String id, {
    required String workspaceId,
    required String profileId,
  });
  Future<CapturedAsset> resolveStagedAsset(String id);
  Future<CaptureOutboxEntry> markAttemptStarted(String id);
  Future<CaptureOutboxEntry> markAttemptFailed(
    String id, {
    required String error,
  });
  Future<CaptureOutboxEntry> markAwaitingConfirmation(String id);
  Future<List<CaptureOutboxEntry>> recoverInterruptedAttempts();
  Future<void> confirmServerSuccess(String id);
}

abstract interface class CaptureOutboxMaintenanceStore {
  Future<CaptureOutboxMaintenanceSnapshot> inspectMaintenance();
  Future<int> cleanConfirmedFiles();
  Future<String> quarantineDamagedManifest({required bool userConfirmed});
  Future<String> quarantineDamagedCleanupJournal({required bool userConfirmed});
}

class CaptureOutboxMaintenanceSnapshot {
  const CaptureOutboxMaintenanceSnapshot({
    required this.manifestHealthy,
    required this.confirmedFilesReadyForCleanup,
    required this.quarantinedManifestCount,
    this.issueCode,
  });

  final bool manifestHealthy;
  final int confirmedFilesReadyForCleanup;
  final int quarantinedManifestCount;
  final String? issueCode;
}

class CaptureOutboxException implements Exception {
  const CaptureOutboxException(this.message);

  final String message;

  @override
  String toString() => message;
}

class CaptureOutboxCorruptManifestException extends CaptureOutboxException {
  const CaptureOutboxCorruptManifestException(super.message);
}

class CaptureOutboxCorruptCleanupException extends CaptureOutboxException {
  const CaptureOutboxCorruptCleanupException(super.message);
}

class CaptureOutboxEntryNotFoundException extends CaptureOutboxException {
  const CaptureOutboxEntryNotFoundException(String id)
    : super('Capture outbox entry "$id" was not found.');
}

/// Durable handoff between mobile capture and an eventually successful upload.
///
/// Asset originals are copied into Application Support before this service
/// returns an entry. Network code may then call [markAttemptStarted], upload
/// from [resolveStagedAsset], and finally [confirmServerSuccess]. No staged
/// original is deleted by a failed attempt or while reading a damaged manifest.
class CaptureOutboxService
    implements CaptureOutboxStore, CaptureOutboxMaintenanceStore {
  CaptureOutboxService({
    CaptureOutboxDirectoryProvider? directoryProvider,
    String Function()? idProvider,
    DateTime Function()? clock,
    this.maxAssetBytes = defaultMaxAssetBytes,
    this.maxAudioBytes = defaultMaxAudioBytes,
  }) : _directoryProvider =
           directoryProvider ??
           (() async {
             final support = await getApplicationSupportDirectory();
             return Directory(_join(support.path, 'capture-outbox'));
           }),
       _idProvider = idProvider ?? _randomId,
       _clock = clock ?? DateTime.now {
    if (maxAssetBytes <= 0 || maxAudioBytes < maxAssetBytes) {
      throw ArgumentError(
        'Outbox limits require a positive asset limit and an audio limit at least as large.',
      );
    }
  }

  static const int manifestVersion = 3;
  static const int _legacyManifestVersion = 1;
  static const int _unassignedManifestVersion = 2;
  static const String _legacyChecksumPlaceholder =
      '0000000000000000000000000000000000000000000000000000000000000000';
  static const int defaultMaxAssetBytes = 512 * 1024 * 1024;
  static const int defaultMaxAudioBytes = 2 * 1024 * 1024 * 1024;
  static const int _maxManifestBytes = 16 * 1024 * 1024;
  static const String _manifestFileName = 'manifest.json';
  static const String _lockFileName = '.manifest.lock';
  static const String _filesDirectoryName = 'files';
  static const String _confirmedCleanupFileName = 'confirmed-cleanup.json';
  static const String _quarantineDirectoryName = 'quarantine';

  static final Map<String, Future<void>> _directoryBarriers =
      <String, Future<void>>{};

  final CaptureOutboxDirectoryProvider _directoryProvider;
  final String Function() _idProvider;
  final DateTime Function() _clock;
  final int maxAssetBytes;
  final int maxAudioBytes;
  Future<void> _instanceBarrier = Future<void>.value();

  @override
  Future<List<CaptureOutboxEntry>> listEntries() {
    return _serialized((root) async {
      final manifest = await _readManifest(root);
      final entries = [...manifest.entries]
        ..sort((left, right) => left.createdAt.compareTo(right.createdAt));
      return List<CaptureOutboxEntry>.unmodifiable(entries);
    });
  }

  @override
  Future<CaptureOutboxEntry?> loadEntry(String id) {
    _validateEntryId(id);
    return _serialized((root) async {
      final manifest = await _readManifest(root);
      return _findEntry(manifest.entries, id);
    });
  }

  @override
  Future<CaptureOutboxEntry> enqueuePayload({
    required CaptureOutboxKind kind,
    required Map<String, Object?> payload,
    String? targetWorkspaceId,
    String? connectionProfileId,
  }) {
    _validateTargetBindingInput(targetWorkspaceId, connectionProfileId);
    if (kind.carriesAsset) {
      throw const CaptureOutboxException(
        'Document, photo, and audio entries require a staged original.',
      );
    }
    return _serialized((root) async {
      final manifest = await _readManifest(root);
      final id = await _availableId(root, manifest.entries);
      final now = _clock().toUtc();
      final entry = CaptureOutboxEntry(
        id: id,
        kind: kind,
        payload: _jsonPayloadCopy(payload),
        state: CaptureOutboxState.pending,
        attemptCount: 0,
        createdAt: now,
        updatedAt: now,
        targetWorkspaceId: targetWorkspaceId,
        connectionProfileId: connectionProfileId,
      );
      await _writeManifest(root, [...manifest.entries, entry]);
      return entry;
    });
  }

  @override
  Future<CaptureOutboxEntry> enqueueAsset({
    required CaptureOutboxKind kind,
    required CapturedAsset asset,
    Map<String, Object?> payload = const <String, Object?>{},
    String? targetWorkspaceId,
    String? connectionProfileId,
  }) {
    _validateTargetBindingInput(targetWorkspaceId, connectionProfileId);
    if (!kind.carriesAsset) {
      throw const CaptureOutboxException(
        'Only document, photo, and audio entries can stage an original file.',
      );
    }
    return _serialized((root) async {
      final manifest = await _readManifest(root);
      final safePayload = _jsonPayloadCopy(payload);
      final source = await _validatedSource(asset);
      final limit = kind == CaptureOutboxKind.audio
          ? maxAudioBytes
          : maxAssetBytes;
      final sourceLength = await source.length();
      if (sourceLength > limit) throw _sizeLimitException(kind, limit);

      final id = await _availableId(root, manifest.entries);
      final storedName = '$id${_safeExtension(asset.fileName)}';
      final relativePath = '$_filesDirectoryName/$storedName';
      final filesDirectory = Directory(_join(root.path, _filesDirectoryName));
      await filesDirectory.create(recursive: true);
      final stagedFile = File(_join(filesDirectory.path, storedName));
      final partialFile = File('${stagedFile.path}.part');
      if (await stagedFile.exists() || await partialFile.exists()) {
        throw const CaptureOutboxException(
          'The generated outbox file path is already in use.',
        );
      }

      ({int bytes, String sha256}) copied;
      try {
        copied = await _copyStreaming(source, partialFile, maximumBytes: limit);
        await partialFile.rename(stagedFile.path);
      } on Object {
        if (await partialFile.exists()) await partialFile.delete();
        rethrow;
      }

      final now = _clock().toUtc();
      final entry = CaptureOutboxEntry(
        id: id,
        kind: kind,
        payload: safePayload,
        stagedFile: CaptureOutboxFile(
          relativePath: relativePath,
          fileName: asset.fileName,
          mediaType: asset.mediaType,
          sizeBytes: copied.bytes,
          sha256: copied.sha256,
        ),
        state: CaptureOutboxState.pending,
        attemptCount: 0,
        createdAt: now,
        updatedAt: now,
        targetWorkspaceId: targetWorkspaceId,
        connectionProfileId: connectionProfileId,
      );

      // If the atomic manifest replacement fails, retain the fully copied file
      // for support/recovery rather than risking deletion of captured bytes.
      await _writeManifest(root, [...manifest.entries, entry]);
      return entry;
    });
  }

  @override
  Future<CaptureOutboxEntry> bindUnassignedEntry(
    String id, {
    required String workspaceId,
    required String profileId,
  }) {
    _validateEntryId(id);
    _validateTargetBindingInput(workspaceId, profileId);
    return _serialized((root) async {
      final manifest = await _readManifest(root);
      final index = _entryIndex(manifest.entries, id);
      if (index < 0) throw CaptureOutboxEntryNotFoundException(id);
      final current = manifest.entries[index];
      if (current.isTargetAssigned) {
        throw CaptureOutboxException(
          'Capture outbox entry "$id" already has an assigned target.',
        );
      }
      if (current.state != CaptureOutboxState.pending &&
          current.state != CaptureOutboxState.retryable) {
        throw CaptureOutboxException(
          'Capture outbox entry "$id" cannot be bound while ${current.state.name}.',
        );
      }
      if (current.attemptCount != 0 || current.lastAttemptAt != null) {
        throw CaptureOutboxException(
          'Capture outbox entry "$id" cannot be bound after an upload attempt.',
        );
      }
      final updated = current.copyWith(
        updatedAt: _clock().toUtc(),
        targetWorkspaceId: workspaceId,
        connectionProfileId: profileId,
      );
      final entries = [...manifest.entries]..[index] = updated;
      await _writeManifest(root, entries);
      return updated;
    });
  }

  @override
  Future<CapturedAsset> resolveStagedAsset(String id) {
    _validateEntryId(id);
    return _serialized((root) async {
      final manifest = await _readManifest(root);
      final entry = _findEntry(manifest.entries, id);
      if (entry == null) throw CaptureOutboxEntryNotFoundException(id);
      final metadata = entry.stagedFile;
      if (metadata == null) {
        throw CaptureOutboxException(
          'Capture outbox entry "$id" does not contain a staged file.',
        );
      }
      final file = _resolveStagedFile(root, metadata);
      if (!await file.exists()) {
        throw CaptureOutboxException(
          'The staged original for capture "$id" is missing.',
        );
      }
      final length = await file.length();
      if (length != metadata.sizeBytes) {
        throw CaptureOutboxException(
          'The staged original for capture "$id" changed after it was saved.',
        );
      }
      final checksum = await _sha256File(file);
      if (checksum != metadata.sha256) {
        throw CaptureOutboxException(
          'The staged original for capture "$id" failed checksum verification.',
        );
      }
      return CapturedAsset(
        path: file.path,
        fileName: metadata.fileName,
        mediaType: metadata.mediaType,
        sizeBytes: length,
      );
    });
  }

  @override
  Future<CaptureOutboxEntry> markAttemptStarted(String id) {
    _validateEntryId(id);
    return _serialized((root) async {
      final manifest = await _readManifest(root);
      final index = _entryIndex(manifest.entries, id);
      if (index < 0) throw CaptureOutboxEntryNotFoundException(id);
      final current = manifest.entries[index];
      final now = _clock().toUtc();
      final updated = current.copyWith(
        state: CaptureOutboxState.uploading,
        attemptCount: current.attemptCount + 1,
        updatedAt: now,
        lastAttemptAt: now,
        clearLastError: true,
      );
      final entries = [...manifest.entries]..[index] = updated;
      await _writeManifest(root, entries);
      return updated;
    });
  }

  @override
  Future<CaptureOutboxEntry> markAttemptFailed(
    String id, {
    required String error,
  }) {
    _validateEntryId(id);
    return _serialized((root) async {
      final manifest = await _readManifest(root);
      final index = _entryIndex(manifest.entries, id);
      if (index < 0) throw CaptureOutboxEntryNotFoundException(id);
      final updated = manifest.entries[index].copyWith(
        state: CaptureOutboxState.retryable,
        updatedAt: _clock().toUtc(),
        lastError: _boundedError(error),
      );
      final entries = [...manifest.entries]..[index] = updated;
      await _writeManifest(root, entries);
      return updated;
    });
  }

  @override
  Future<CaptureOutboxEntry> markAwaitingConfirmation(String id) {
    _validateEntryId(id);
    return _serialized((root) async {
      final manifest = await _readManifest(root);
      final index = _entryIndex(manifest.entries, id);
      if (index < 0) throw CaptureOutboxEntryNotFoundException(id);
      final updated = manifest.entries[index].copyWith(
        state: CaptureOutboxState.awaitingConfirmation,
        updatedAt: _clock().toUtc(),
        clearLastError: true,
      );
      final entries = [...manifest.entries]..[index] = updated;
      await _writeManifest(root, entries);
      return updated;
    });
  }

  /// Converts attempts left in `uploading` by a terminated process into an
  /// explicit retry state. Call once during startup before retrying entries.
  @override
  Future<List<CaptureOutboxEntry>> recoverInterruptedAttempts() {
    return _serialized((root) async {
      final manifest = await _readManifest(root);
      final now = _clock().toUtc();
      var changed = false;
      final entries = manifest.entries
          .map((entry) {
            if (entry.state != CaptureOutboxState.uploading) return entry;
            changed = true;
            return entry.copyWith(
              state: CaptureOutboxState.retryable,
              updatedAt: now,
              lastError:
                  entry.lastError ??
                  'The previous upload ended before the server confirmed success.',
            );
          })
          .toList(growable: false);
      if (changed) await _writeManifest(root, entries);
      return List<CaptureOutboxEntry>.unmodifiable(entries);
    });
  }

  /// Explicit server-success acknowledgement. This is the only API that
  /// removes a durable entry or its staged original.
  @override
  Future<void> confirmServerSuccess(String id) {
    _validateEntryId(id);
    return _serialized((root) async {
      final manifest = await _readManifest(root);
      final index = _entryIndex(manifest.entries, id);
      if (index < 0) throw CaptureOutboxEntryNotFoundException(id);
      final entry = manifest.entries[index];
      final remaining = [...manifest.entries]..removeAt(index);
      final metadata = entry.stagedFile;

      // Journal server-confirmed cleanup before changing the manifest. If the
      // process ends at any later instruction, maintenance can distinguish
      // this file from an unconfirmed orphan and never guesses about deletion.
      if (metadata != null) {
        await _addConfirmedCleanup(root, metadata.relativePath);
      }

      // Commit manifest removal before file cleanup. A crash can leave a
      // harmless orphan, but can never leave a manifest pointing at deleted
      // captured bytes.
      await _writeManifest(root, remaining);
      if (metadata == null) return;
      final stagedFile = _resolveStagedFile(root, metadata);
      if (await stagedFile.exists()) await stagedFile.delete();
      await _removeConfirmedCleanup(root, metadata.relativePath);
    });
  }

  @override
  Future<CaptureOutboxMaintenanceSnapshot> inspectMaintenance() {
    return _serialized((root) async {
      final quarantine = Directory(_join(root.path, _quarantineDirectoryName));
      final quarantineCount = await quarantine.exists()
          ? await quarantine
                .list(followLinks: false)
                .where((entity) => entity is File)
                .length
          : 0;
      try {
        final manifest = await _readManifest(root);
        final referenced = manifest.entries
            .map((entry) => entry.stagedFile?.relativePath)
            .whereType<String>()
            .toSet();
        List<String> cleanup;
        try {
          cleanup = await _readConfirmedCleanup(root);
        } on CaptureOutboxCorruptCleanupException {
          return CaptureOutboxMaintenanceSnapshot(
            manifestHealthy: true,
            confirmedFilesReadyForCleanup: 0,
            quarantinedManifestCount: quarantineCount,
            issueCode: 'cleanup_journal_corrupt',
          );
        }
        return CaptureOutboxMaintenanceSnapshot(
          manifestHealthy: true,
          confirmedFilesReadyForCleanup: cleanup
              .where((path) => !referenced.contains(path))
              .length,
          quarantinedManifestCount: quarantineCount,
        );
      } on CaptureOutboxCorruptManifestException {
        return CaptureOutboxMaintenanceSnapshot(
          manifestHealthy: false,
          confirmedFilesReadyForCleanup: 0,
          quarantinedManifestCount: quarantineCount,
          issueCode: 'manifest_corrupt',
        );
      }
    });
  }

  @override
  Future<int> cleanConfirmedFiles() {
    return _serialized((root) async {
      final manifest = await _readManifest(root);
      final referenced = manifest.entries
          .map((entry) => entry.stagedFile?.relativePath)
          .whereType<String>()
          .toSet();
      final cleanup = await _readConfirmedCleanup(root);
      var deleted = 0;
      final retained = <String>[];
      for (final path in cleanup) {
        if (referenced.contains(path)) {
          retained.add(path);
          continue;
        }
        final file = _resolveCleanupPath(root, path);
        if (await file.exists()) {
          await file.delete();
          deleted += 1;
        }
      }
      await _writeConfirmedCleanup(root, retained);
      return deleted;
    });
  }

  @override
  Future<String> quarantineDamagedManifest({required bool userConfirmed}) {
    if (!userConfirmed) {
      throw const CaptureOutboxException(
        'Damaged capture data requires explicit user confirmation.',
      );
    }
    return _serialized((root) async {
      try {
        await _readManifest(root);
      } on CaptureOutboxCorruptManifestException {
        final manifest = File(_join(root.path, _manifestFileName));
        if (!await manifest.exists()) {
          throw const CaptureOutboxException(
            'The damaged capture manifest no longer exists.',
          );
        }
        final quarantine = Directory(
          _join(root.path, _quarantineDirectoryName),
        );
        await quarantine.create(recursive: true);
        final name =
            'manifest-${_clock().toUtc().microsecondsSinceEpoch}-${_randomId()}.json';
        final destination = File(_join(quarantine.path, name));
        await manifest.rename(destination.path);
        // Only the index is reset. Every staged original remains untouched and
        // the exact damaged index is retained for support/export recovery.
        await _writeManifest(root, const []);
        return destination.path;
      }
      throw const CaptureOutboxException(
        'The capture manifest is healthy and must not be quarantined.',
      );
    });
  }

  @override
  Future<String> quarantineDamagedCleanupJournal({
    required bool userConfirmed,
  }) {
    if (!userConfirmed) {
      throw const CaptureOutboxException(
        'Damaged cleanup data requires explicit user confirmation.',
      );
    }
    return _serialized((root) async {
      await _readManifest(root);
      try {
        await _readConfirmedCleanup(root);
      } on CaptureOutboxCorruptCleanupException {
        final journal = File(_join(root.path, _confirmedCleanupFileName));
        if (!await journal.exists()) {
          throw const CaptureOutboxException(
            'The damaged cleanup journal no longer exists.',
          );
        }
        final quarantine = Directory(
          _join(root.path, _quarantineDirectoryName),
        );
        await quarantine.create(recursive: true);
        final destination = File(
          _join(
            quarantine.path,
            'confirmed-cleanup-${_clock().toUtc().microsecondsSinceEpoch}-${_randomId()}.json',
          ),
        );
        await journal.rename(destination.path);
        return destination.path;
      }
      throw const CaptureOutboxException(
        'The confirmed-file cleanup journal is healthy.',
      );
    });
  }

  Future<T> _serialized<T>(Future<T> Function(Directory root) action) {
    final result = _instanceBarrier.then((_) async {
      final root = (await _directoryProvider()).absolute;
      return _withDirectoryBarrier(root, action);
    });
    _instanceBarrier = result.then<void>(
      (_) {},
      onError: (Object _, StackTrace __) {},
    );
    return result;
  }

  Future<T> _withDirectoryBarrier<T>(
    Directory root,
    Future<T> Function(Directory root) action,
  ) {
    final key = root.path;
    final previous = _directoryBarriers[key] ?? Future<void>.value();
    final result = previous.then((_) async {
      await root.create(recursive: true);
      final lock = await File(
        _join(root.path, _lockFileName),
      ).open(mode: FileMode.append);
      try {
        await lock.lock(FileLock.exclusive);
        return await action(root);
      } finally {
        try {
          await lock.unlock();
        } on FileSystemException {
          // Closing releases the lock even when the platform reports that an
          // interrupted lock was already released.
        }
        await lock.close();
      }
    });
    _directoryBarriers[key] = result.then<void>(
      (_) {},
      onError: (Object _, StackTrace __) {},
    );
    return result;
  }

  Future<_CaptureOutboxManifest> _readManifest(Directory root) async {
    final file = File(_join(root.path, _manifestFileName));
    if (!await file.exists()) return const _CaptureOutboxManifest([]);
    try {
      if (await file.length() > _maxManifestBytes) {
        throw const FormatException('Outbox manifest is unexpectedly large.');
      }
      final Object? decoded = jsonDecode(await file.readAsString());
      if (decoded is! Map<String, Object?>) {
        throw const FormatException('Outbox manifest is invalid.');
      }
      final version = decoded['version'];
      if (version != manifestVersion &&
          version != _unassignedManifestVersion &&
          version != _legacyManifestVersion) {
        throw const FormatException('Outbox manifest version is invalid.');
      }
      final rawEntries = decoded['entries'];
      if (rawEntries is! List<Object?>) {
        throw const FormatException('Outbox manifest entries are invalid.');
      }
      final entries = <CaptureOutboxEntry>[];
      for (final raw in rawEntries) {
        if (raw is! Map<String, Object?>) {
          throw const FormatException('An outbox manifest entry is invalid.');
        }
        if (version == _legacyManifestVersion) {
          entries.add(await _migrateLegacyEntry(root, raw));
        } else if (version == _unassignedManifestVersion) {
          // v2 had no destination contract. Unknown injected fields must not
          // silently bind an old capture to whichever workspace is active.
          entries.add(CaptureOutboxEntry.fromJson(_asUnassigned(raw)));
        } else {
          entries.add(CaptureOutboxEntry.fromJson(raw));
        }
      }
      final ids = <String>{};
      for (final entry in entries) {
        if (!ids.add(entry.id)) {
          throw const FormatException('Outbox manifest has duplicate ids.');
        }
      }
      if (version != manifestVersion) {
        // _readManifest is only reached while _withDirectoryBarrier holds the
        // process-wide file lock. Rewrite only after every old entry (and each
        // v1 staged original) has passed validation, so a failed migration
        // leaves the exact old manifest and every original untouched.
        await _writeManifest(root, entries);
      }
      return _CaptureOutboxManifest(entries);
    } on CaptureOutboxCorruptManifestException {
      rethrow;
    } on Object catch (error) {
      throw CaptureOutboxCorruptManifestException(
        'The capture outbox manifest is damaged and was left untouched: $error',
      );
    }
  }

  Future<CaptureOutboxEntry> _migrateLegacyEntry(
    Directory root,
    Map<String, Object?> raw,
  ) async {
    final rawStagedFile = raw['stagedFile'];
    if (rawStagedFile == null) {
      return CaptureOutboxEntry.fromJson(_asUnassigned(raw));
    }
    if (rawStagedFile is! Map<String, Object?>) {
      throw const FormatException('Outbox staged-file metadata is invalid.');
    }

    final suppliedChecksum = rawStagedFile['sha256'];
    final stagedForValidation = <String, Object?>{
      ...rawStagedFile,
      'sha256': suppliedChecksum ?? _legacyChecksumPlaceholder,
    };
    final entryForValidation = CaptureOutboxEntry.fromJson({
      ..._asUnassigned(raw),
      'stagedFile': stagedForValidation,
    });
    final metadata = entryForValidation.stagedFile!;
    final maximumBytes = entryForValidation.kind == CaptureOutboxKind.audio
        ? maxAudioBytes
        : maxAssetBytes;
    if (metadata.sizeBytes > maximumBytes) {
      throw const FormatException(
        'A legacy outbox staged file exceeds the current size limit.',
      );
    }

    final stagedFile = _resolveStagedFile(root, metadata);
    final type = await FileSystemEntity.type(
      stagedFile.path,
      followLinks: false,
    );
    if (type != FileSystemEntityType.file) {
      throw const FormatException(
        'A legacy outbox staged file is missing or is not a regular file.',
      );
    }
    if (await stagedFile.length() != metadata.sizeBytes) {
      throw const FormatException(
        'A legacy outbox staged file does not match its recorded length.',
      );
    }

    final checksum = await _sha256File(stagedFile);
    if (suppliedChecksum != null && suppliedChecksum != checksum) {
      throw const FormatException(
        'A legacy outbox staged file failed checksum verification.',
      );
    }
    return CaptureOutboxEntry.fromJson({
      ..._asUnassigned(raw),
      'stagedFile': <String, Object?>{...rawStagedFile, 'sha256': checksum},
    });
  }

  Future<void> _writeManifest(
    Directory root,
    List<CaptureOutboxEntry> entries,
  ) async {
    await root.create(recursive: true);
    final manifest = File(_join(root.path, _manifestFileName));
    final temporary = File(
      '${manifest.path}.${_clock().toUtc().microsecondsSinceEpoch}.${_randomId()}.tmp',
    );
    final encoded = jsonEncode({
      'version': manifestVersion,
      'entries': entries.map((entry) => entry.toJson()).toList(),
    });
    if (utf8.encode(encoded).length > _maxManifestBytes) {
      throw const CaptureOutboxException(
        'The capture outbox manifest exceeded its safe size.',
      );
    }
    try {
      await temporary.writeAsString(encoded, flush: true);
      await temporary.rename(manifest.path);
    } on Object {
      if (await temporary.exists()) await temporary.delete();
      rethrow;
    }
  }

  Future<List<String>> _readConfirmedCleanup(Directory root) async {
    final file = File(_join(root.path, _confirmedCleanupFileName));
    if (!await file.exists()) return const [];
    try {
      final decoded = jsonDecode(await file.readAsString());
      if (decoded is! Map<String, Object?> || decoded['version'] != 1) {
        throw const FormatException('Cleanup journal header is invalid.');
      }
      final paths = decoded['paths'];
      if (paths is! List<Object?>) {
        throw const FormatException('Cleanup journal paths are invalid.');
      }
      final validated = <String>[];
      for (final path in paths) {
        if (path is! String) {
          throw const FormatException('Cleanup journal path is invalid.');
        }
        _resolveCleanupPath(root, path);
        if (!validated.contains(path)) validated.add(path);
      }
      return validated;
    } on Object catch (error) {
      throw CaptureOutboxCorruptCleanupException(
        'The confirmed-file cleanup journal is damaged and was left untouched: $error',
      );
    }
  }

  Future<void> _addConfirmedCleanup(Directory root, String path) async {
    final paths = await _readConfirmedCleanup(root);
    if (paths.contains(path)) return;
    await _writeConfirmedCleanup(root, [...paths, path]);
  }

  Future<void> _removeConfirmedCleanup(Directory root, String path) async {
    final paths = await _readConfirmedCleanup(root);
    await _writeConfirmedCleanup(
      root,
      paths.where((candidate) => candidate != path).toList(),
    );
  }

  Future<void> _writeConfirmedCleanup(
    Directory root,
    List<String> paths,
  ) async {
    final journal = File(_join(root.path, _confirmedCleanupFileName));
    if (paths.isEmpty) {
      if (await journal.exists()) await journal.delete();
      return;
    }
    for (final path in paths) {
      _resolveCleanupPath(root, path);
    }
    final temporary = File(
      '${journal.path}.${_clock().toUtc().microsecondsSinceEpoch}.${_randomId()}.tmp',
    );
    try {
      await temporary.writeAsString(
        jsonEncode({'version': 1, 'paths': paths}),
        flush: true,
      );
      await temporary.rename(journal.path);
    } on Object {
      if (await temporary.exists()) await temporary.delete();
      rethrow;
    }
  }

  File _resolveCleanupPath(Directory root, String relativePath) {
    final segments = relativePath.split('/');
    if (segments.length != 2 ||
        segments.first != _filesDirectoryName ||
        !_safeStoredName.hasMatch(segments.last)) {
      throw const CaptureOutboxCorruptManifestException(
        'The confirmed-file cleanup path is invalid.',
      );
    }
    return File(_join(root.path, relativePath));
  }

  Future<File> _validatedSource(CapturedAsset asset) async {
    _validateOriginalFileName(asset.fileName);
    _validateMediaType(asset.mediaType);
    if (asset.path.isEmpty || asset.path.contains('\u0000')) {
      throw const CaptureOutboxException('The selected file path is invalid.');
    }
    if (asset.sizeBytes < 0) {
      throw const CaptureOutboxException('The selected file size is invalid.');
    }
    final source = File(asset.path);
    if (source.path != source.absolute.path) {
      throw const CaptureOutboxException(
        'The selected file must have an absolute device path.',
      );
    }
    final type = await FileSystemEntity.type(source.path, followLinks: false);
    if (type != FileSystemEntityType.file) {
      throw const CaptureOutboxException(
        'The selected path is not a readable regular file.',
      );
    }
    return source;
  }

  Future<({int bytes, String sha256})> _copyStreaming(
    File source,
    File destination, {
    required int maximumBytes,
  }) async {
    final sink = destination.openWrite(mode: FileMode.writeOnly);
    final digestSink = _DigestSink();
    final digestInput = sha256.startChunkedConversion(digestSink);
    var digestClosed = false;
    var copied = 0;
    try {
      await for (final chunk in source.openRead()) {
        copied += chunk.length;
        if (copied > maximumBytes) {
          throw const CaptureOutboxException(
            'The selected file grew beyond the outbox size limit while it was copied.',
          );
        }
        digestInput.add(chunk);
        sink.add(chunk);
      }
      digestInput.close();
      digestClosed = true;
      await sink.flush();
      await sink.close();
      final digest = digestSink.value;
      if (digest == null) {
        throw const CaptureOutboxException(
          'The staged original checksum could not be calculated.',
        );
      }
      return (bytes: copied, sha256: digest.toString());
    } on Object {
      if (!digestClosed) digestInput.close();
      await sink.close();
      rethrow;
    }
  }

  Future<String> _sha256File(File file) async {
    final digestSink = _DigestSink();
    final digestInput = sha256.startChunkedConversion(digestSink);
    await for (final chunk in file.openRead()) {
      digestInput.add(chunk);
    }
    digestInput.close();
    final digest = digestSink.value;
    if (digest == null) {
      throw const CaptureOutboxException(
        'The staged original checksum could not be verified.',
      );
    }
    return digest.toString();
  }

  File _resolveStagedFile(Directory root, CaptureOutboxFile metadata) {
    final segments = metadata.relativePath.split('/');
    if (segments.length != 2 ||
        segments.first != _filesDirectoryName ||
        !_safeStoredName.hasMatch(segments.last)) {
      throw const CaptureOutboxCorruptManifestException(
        'A staged-file path escaped the capture outbox and was not opened.',
      );
    }
    return File(_join(_join(root.path, _filesDirectoryName), segments.last));
  }

  Future<String> _availableId(
    Directory root,
    List<CaptureOutboxEntry> entries,
  ) async {
    for (var attempt = 0; attempt < 32; attempt += 1) {
      final id = _idProvider();
      _validateEntryId(id);
      if (_entryIndex(entries, id) >= 0) continue;
      final fileDirectory = Directory(_join(root.path, _filesDirectoryName));
      final collisions = fileDirectory.existsSync()
          ? fileDirectory.listSync().whereType<File>().any(
              (file) => _baseName(file.path).startsWith('$id.'),
            )
          : false;
      if (!collisions) return id;
    }
    throw const CaptureOutboxException(
      'Gunther could not allocate a unique capture outbox id.',
    );
  }
}

class _CaptureOutboxManifest {
  const _CaptureOutboxManifest(this.entries);

  final List<CaptureOutboxEntry> entries;
}

class _DigestSink implements Sink<Digest> {
  Digest? value;

  @override
  void add(Digest data) {
    value = data;
  }

  @override
  void close() {}
}

final RegExp _safeId = RegExp(r'^[A-Za-z0-9_-]{8,128}$');
final RegExp _safeStoredName = RegExp(
  r'^[A-Za-z0-9_-]{8,128}(?:\.[A-Za-z0-9]{1,10}|\.asset)$',
);

CaptureOutboxEntry? _findEntry(List<CaptureOutboxEntry> entries, String id) {
  final index = _entryIndex(entries, id);
  return index < 0 ? null : entries[index];
}

int _entryIndex(List<CaptureOutboxEntry> entries, String id) {
  return entries.indexWhere((entry) => entry.id == id);
}

void _validateEntryId(String id) {
  if (!_safeId.hasMatch(id)) {
    throw const CaptureOutboxException('Capture outbox id is invalid.');
  }
}

void _validateTargetBindingInput(
  String? targetWorkspaceId,
  String? connectionProfileId,
) {
  try {
    CaptureOutboxEntry.validateTargetBinding(
      targetWorkspaceId: targetWorkspaceId,
      connectionProfileId: connectionProfileId,
    );
  } on FormatException catch (error) {
    throw CaptureOutboxException(error.message.toString());
  }
}

Map<String, Object?> _asUnassigned(Map<String, Object?> json) {
  return <String, Object?>{
    ...json,
    'targetWorkspaceId': null,
    'connectionProfileId': null,
  };
}

void _validateOriginalFileName(String fileName) {
  if (fileName.isEmpty ||
      fileName == '.' ||
      fileName == '..' ||
      fileName.length > 240 ||
      fileName.contains('/') ||
      fileName.contains('\\') ||
      fileName.codeUnits.any((unit) => unit < 32 || unit == 127)) {
    throw const CaptureOutboxException('The selected file name is invalid.');
  }
}

void _validateMediaType(String mediaType) {
  if (mediaType.isEmpty ||
      mediaType.length > 255 ||
      !mediaType.contains('/') ||
      mediaType.contains('\r') ||
      mediaType.contains('\n')) {
    throw const CaptureOutboxException('The selected media type is invalid.');
  }
}

String _safeExtension(String fileName) {
  final dot = fileName.lastIndexOf('.');
  if (dot <= 0 || dot == fileName.length - 1) return '.asset';
  final extension = fileName.substring(dot + 1).toLowerCase();
  return RegExp(r'^[a-z0-9]{1,10}$').hasMatch(extension)
      ? '.$extension'
      : '.asset';
}

Map<String, Object?> _jsonPayloadCopy(Map<String, Object?> payload) {
  try {
    final Object? decoded = jsonDecode(jsonEncode(payload));
    if (decoded is! Map<String, Object?>) {
      throw const FormatException('Payload is not a JSON object.');
    }
    return Map<String, Object?>.unmodifiable(decoded);
  } on Object catch (error) {
    throw CaptureOutboxException(
      'Capture payload cannot be persisted as JSON: $error',
    );
  }
}

CaptureOutboxException _sizeLimitException(CaptureOutboxKind kind, int limit) {
  final label = kind == CaptureOutboxKind.audio ? 'audio' : 'asset';
  final gibibytes = limit / (1024 * 1024 * 1024);
  final mebibytes = limit / (1024 * 1024);
  final String formatted;
  if (gibibytes >= 1) {
    formatted =
        '${gibibytes.toStringAsFixed(gibibytes == gibibytes.round() ? 0 : 1)} GB';
  } else if (mebibytes >= 1) {
    formatted = '${mebibytes.toStringAsFixed(0)} MB';
  } else {
    formatted = '$limit B';
  }
  return CaptureOutboxException(
    'This $label is larger than the $formatted capture limit.',
  );
}

String _boundedError(String error) {
  final normalized = error.trim().isEmpty ? 'Upload failed.' : error.trim();
  return normalized.length <= 2000 ? normalized : normalized.substring(0, 2000);
}

String _randomId() {
  final random = Random.secure();
  final buffer = StringBuffer();
  for (var index = 0; index < 16; index += 1) {
    buffer.write(random.nextInt(256).toRadixString(16).padLeft(2, '0'));
  }
  return buffer.toString();
}

String _join(String parent, String child) {
  if (parent.endsWith(Platform.pathSeparator)) return '$parent$child';
  return '$parent${Platform.pathSeparator}$child';
}

String _baseName(String path) {
  final separator = path.lastIndexOf(Platform.pathSeparator);
  return separator < 0 ? path : path.substring(separator + 1);
}
