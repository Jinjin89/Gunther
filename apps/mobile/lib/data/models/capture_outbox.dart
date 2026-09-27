enum CaptureOutboxKind { quickNote, source, link, web, document, photo, audio }

enum CaptureOutboxState { pending, uploading, retryable, awaitingConfirmation }

extension CaptureOutboxKindPolicy on CaptureOutboxKind {
  bool get carriesAsset => switch (this) {
    CaptureOutboxKind.document ||
    CaptureOutboxKind.photo ||
    CaptureOutboxKind.audio => true,
    CaptureOutboxKind.quickNote ||
    CaptureOutboxKind.source ||
    CaptureOutboxKind.link ||
    CaptureOutboxKind.web => false,
  };
}

class CaptureOutboxFile {
  const CaptureOutboxFile({
    required this.relativePath,
    required this.fileName,
    required this.mediaType,
    required this.sizeBytes,
    required this.sha256,
  });

  factory CaptureOutboxFile.fromJson(Map<String, Object?> json) {
    final relativePath = _requiredString(json, 'relativePath');
    final segments = relativePath.split('/');
    if (segments.length != 2 ||
        segments.first != 'files' ||
        !_safeStoredFileName.hasMatch(segments.last)) {
      throw const FormatException('Outbox staged-file path is invalid.');
    }
    final fileName = _requiredString(json, 'fileName');
    if (!_validOriginalFileName(fileName)) {
      throw const FormatException('Outbox original file name is invalid.');
    }
    final mediaType = _requiredString(json, 'mediaType');
    if (mediaType.length > 255 ||
        !mediaType.contains('/') ||
        mediaType.contains('\r') ||
        mediaType.contains('\n')) {
      throw const FormatException('Outbox media type is invalid.');
    }
    final sha256 = _requiredString(json, 'sha256');
    if (!_sha256Digest.hasMatch(sha256)) {
      throw const FormatException('Outbox staged-file checksum is invalid.');
    }
    return CaptureOutboxFile(
      relativePath: relativePath,
      fileName: fileName,
      mediaType: mediaType,
      sizeBytes: _requiredNonNegativeInt(json, 'sizeBytes'),
      sha256: sha256,
    );
  }

  final String relativePath;
  final String fileName;
  final String mediaType;
  final int sizeBytes;
  final String sha256;

  Map<String, Object?> toJson() => {
    'relativePath': relativePath,
    'fileName': fileName,
    'mediaType': mediaType,
    'sizeBytes': sizeBytes,
    'sha256': sha256,
  };
}

class CaptureOutboxEntry {
  CaptureOutboxEntry({
    required this.id,
    required this.kind,
    required this.payload,
    required this.state,
    required this.attemptCount,
    required this.createdAt,
    required this.updatedAt,
    this.stagedFile,
    this.lastAttemptAt,
    this.lastError,
    this.targetWorkspaceId,
    this.connectionProfileId,
  }) {
    validateTargetBinding(
      targetWorkspaceId: targetWorkspaceId,
      connectionProfileId: connectionProfileId,
    );
  }

  factory CaptureOutboxEntry.fromJson(Map<String, Object?> json) {
    final id = _requiredString(json, 'id');
    if (!_safeEntryId.hasMatch(id)) {
      throw const FormatException('Outbox entry id is invalid.');
    }
    final kind = _enumByName(
      CaptureOutboxKind.values,
      _requiredString(json, 'kind'),
      'kind',
    );
    final state = _enumByName(
      CaptureOutboxState.values,
      _requiredString(json, 'state'),
      'state',
    );
    final payload = json['payload'];
    if (payload is! Map<String, Object?>) {
      throw const FormatException('Outbox payload is invalid.');
    }
    final rawStagedFile = json['stagedFile'];
    final CaptureOutboxFile? stagedFile;
    if (rawStagedFile == null) {
      stagedFile = null;
    } else if (rawStagedFile is Map<String, Object?>) {
      stagedFile = CaptureOutboxFile.fromJson(rawStagedFile);
    } else {
      throw const FormatException('Outbox staged-file metadata is invalid.');
    }
    if (kind.carriesAsset != (stagedFile != null)) {
      throw const FormatException(
        'Outbox entry kind and staged-file metadata do not match.',
      );
    }
    return CaptureOutboxEntry(
      id: id,
      kind: kind,
      payload: Map<String, Object?>.unmodifiable(payload),
      stagedFile: stagedFile,
      state: state,
      attemptCount: _requiredNonNegativeInt(json, 'attemptCount'),
      createdAt: _requiredDate(json, 'createdAt'),
      updatedAt: _requiredDate(json, 'updatedAt'),
      lastAttemptAt: _optionalDate(json['lastAttemptAt']),
      lastError: _optionalString(json['lastError']),
      targetWorkspaceId: _optionalString(json['targetWorkspaceId']),
      connectionProfileId: _optionalString(json['connectionProfileId']),
    );
  }

  final String id;
  final CaptureOutboxKind kind;
  final Map<String, Object?> payload;
  final CaptureOutboxFile? stagedFile;
  final CaptureOutboxState state;
  final int attemptCount;
  final DateTime createdAt;
  final DateTime updatedAt;
  final DateTime? lastAttemptAt;
  final String? lastError;
  final String? targetWorkspaceId;
  final String? connectionProfileId;

  bool get isTargetAssigned => targetWorkspaceId != null;

  bool get canRetry =>
      state == CaptureOutboxState.pending ||
      state == CaptureOutboxState.retryable;

  CaptureOutboxEntry copyWith({
    CaptureOutboxState? state,
    int? attemptCount,
    DateTime? updatedAt,
    DateTime? lastAttemptAt,
    String? lastError,
    bool clearLastError = false,
    String? targetWorkspaceId,
    String? connectionProfileId,
  }) {
    final updatesTarget =
        targetWorkspaceId != null || connectionProfileId != null;
    if (updatesTarget) {
      validateTargetBinding(
        targetWorkspaceId: targetWorkspaceId,
        connectionProfileId: connectionProfileId,
      );
      if (isTargetAssigned &&
          (targetWorkspaceId != this.targetWorkspaceId ||
              connectionProfileId != this.connectionProfileId)) {
        throw const FormatException(
          'An assigned outbox target cannot be replaced by copyWith.',
        );
      }
    }
    return CaptureOutboxEntry(
      id: id,
      kind: kind,
      payload: payload,
      stagedFile: stagedFile,
      state: state ?? this.state,
      attemptCount: attemptCount ?? this.attemptCount,
      createdAt: createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
      lastAttemptAt: lastAttemptAt ?? this.lastAttemptAt,
      lastError: clearLastError ? null : lastError ?? this.lastError,
      targetWorkspaceId: targetWorkspaceId ?? this.targetWorkspaceId,
      connectionProfileId: connectionProfileId ?? this.connectionProfileId,
    );
  }

  Map<String, Object?> toJson() => {
    'id': id,
    'kind': kind.name,
    'payload': payload,
    'stagedFile': stagedFile?.toJson(),
    'state': state.name,
    'attemptCount': attemptCount,
    'createdAt': createdAt.toUtc().toIso8601String(),
    'updatedAt': updatedAt.toUtc().toIso8601String(),
    'lastAttemptAt': lastAttemptAt?.toUtc().toIso8601String(),
    'lastError': lastError,
    'targetWorkspaceId': targetWorkspaceId,
    'connectionProfileId': connectionProfileId,
  };

  static void validateTargetBinding({
    required String? targetWorkspaceId,
    required String? connectionProfileId,
  }) {
    if ((targetWorkspaceId == null) != (connectionProfileId == null)) {
      throw const FormatException(
        'Outbox target workspace and connection profile must be assigned together.',
      );
    }
    if (targetWorkspaceId == null) return;
    if (!_safeTargetId.hasMatch(targetWorkspaceId) ||
        !_safeTargetId.hasMatch(connectionProfileId!)) {
      throw const FormatException('Outbox target binding is invalid.');
    }
  }
}

final RegExp _safeEntryId = RegExp(r'^[A-Za-z0-9_-]{8,128}$');
final RegExp _safeTargetId = RegExp(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$');
final RegExp _safeStoredFileName = RegExp(
  r'^[A-Za-z0-9_-]{8,128}(?:\.[A-Za-z0-9]{1,10}|\.asset)$',
);
final RegExp _sha256Digest = RegExp(r'^[a-f0-9]{64}$');

bool _validOriginalFileName(String fileName) {
  return fileName.isNotEmpty &&
      fileName != '.' &&
      fileName != '..' &&
      fileName.length <= 240 &&
      !fileName.contains('/') &&
      !fileName.contains('\\') &&
      !fileName.codeUnits.any((unit) => unit < 32 || unit == 127);
}

String _requiredString(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value is! String || value.isEmpty) {
    throw FormatException('Outbox field "$key" is invalid.');
  }
  return value;
}

int _requiredNonNegativeInt(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value is! int || value < 0) {
    throw FormatException('Outbox field "$key" is invalid.');
  }
  return value;
}

DateTime _requiredDate(Map<String, Object?> json, String key) {
  final value = _requiredString(json, key);
  try {
    return DateTime.parse(value).toUtc();
  } on FormatException {
    throw FormatException('Outbox field "$key" is invalid.');
  }
}

DateTime? _optionalDate(Object? value) {
  if (value == null) return null;
  if (value is! String || value.isEmpty) {
    throw const FormatException('Outbox optional date is invalid.');
  }
  try {
    return DateTime.parse(value).toUtc();
  } on FormatException {
    throw const FormatException('Outbox optional date is invalid.');
  }
}

String? _optionalString(Object? value) {
  if (value == null) return null;
  if (value is! String || value.isEmpty) {
    throw const FormatException('Outbox optional string is invalid.');
  }
  return value;
}

T _enumByName<T extends Enum>(List<T> values, String name, String field) {
  for (final value in values) {
    if (value.name == name) return value;
  }
  throw FormatException('Outbox field "$field" is invalid.');
}
