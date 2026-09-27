enum CaptureKind { quickNote, document, photo, link, recording, web }

enum PhotoCaptureSource { camera, gallery }

enum RecordingCapturePhase {
  idle,
  starting,
  recording,
  paused,
  stopping,
  uploading,
  completed,
  localOnly,
  failed,
}

class CapturedAsset {
  const CapturedAsset({
    required this.path,
    required this.fileName,
    required this.mediaType,
    required this.sizeBytes,
  });

  final String path;
  final String fileName;
  final String mediaType;
  final int sizeBytes;
}

class RecordingMoment {
  const RecordingMoment({required this.seconds, required this.label});

  factory RecordingMoment.fromJson(Map<String, Object?> json) {
    return RecordingMoment(
      seconds: json['seconds']! as int,
      label: json['label']! as String,
    );
  }

  final int seconds;
  final String label;

  Map<String, Object?> toJson() => {'seconds': seconds, 'label': label};
}

class RecordingRecovery {
  const RecordingRecovery({
    required this.canResume,
    required this.audioAvailable,
    required this.nextExpectedSequence,
    required this.checkpointRevision,
    this.checkpointedAt,
  });

  factory RecordingRecovery.fromJson(Map<String, Object?> json) {
    return RecordingRecovery(
      canResume: json['canResume']! as bool,
      audioAvailable: json['audioAvailable']! as bool,
      nextExpectedSequence: json['nextExpectedSequence']! as int,
      checkpointRevision: json['checkpointRevision']! as int,
      checkpointedAt: _optionalDate(json['checkpointedAt']),
    );
  }

  final bool canResume;
  final bool audioAvailable;
  final int nextExpectedSequence;
  final int checkpointRevision;
  final DateTime? checkpointedAt;
}

class RecordingSession {
  const RecordingSession({
    required this.id,
    required this.title,
    required this.status,
    required this.fileName,
    required this.contentType,
    required this.sizeBytes,
    required this.nextExpectedSequence,
    required this.transcript,
    required this.durationSeconds,
    required this.moments,
    required this.recordingContext,
    required this.checkpointRevision,
    required this.recovery,
    required this.createdAt,
    required this.updatedAt,
    required this.storedAt,
    this.knowledgeBaseId,
    this.checkpointedAt,
    this.completedAt,
  });

  factory RecordingSession.fromJson(Map<String, Object?> json) {
    final rawMoments = json['moments'] as List<Object?>? ?? const [];
    return RecordingSession(
      id: json['id']! as String,
      title: json['title']! as String,
      status: json['status']! as String,
      fileName: json['fileName']! as String,
      contentType: json['contentType']! as String,
      sizeBytes: json['sizeBytes']! as int,
      nextExpectedSequence: json['nextExpectedSequence']! as int,
      transcript: json['transcript']?.toString() ?? '',
      durationSeconds: json['durationSeconds'] as int? ?? 0,
      moments: rawMoments
          .map(
            (item) => RecordingMoment.fromJson(item! as Map<String, Object?>),
          )
          .toList(growable: false),
      recordingContext: json['recordingContext']?.toString() ?? 'memo',
      knowledgeBaseId: json['knowledgeBaseId'] as String?,
      checkpointRevision: json['checkpointRevision'] as int? ?? 0,
      checkpointedAt: _optionalDate(json['checkpointedAt']),
      recovery: RecordingRecovery.fromJson(
        json['recovery']! as Map<String, Object?>,
      ),
      createdAt: DateTime.parse(json['createdAt']! as String),
      updatedAt: DateTime.parse(json['updatedAt']! as String),
      completedAt: _optionalDate(json['completedAt']),
      storedAt: DateTime.parse(json['storedAt']! as String),
    );
  }

  final String id;
  final String title;
  final String status;
  final String fileName;
  final String contentType;
  final int sizeBytes;
  final int nextExpectedSequence;
  final String transcript;
  final int durationSeconds;
  final List<RecordingMoment> moments;
  final String recordingContext;
  final String? knowledgeBaseId;
  final int checkpointRevision;
  final DateTime? checkpointedAt;
  final RecordingRecovery recovery;
  final DateTime createdAt;
  final DateTime updatedAt;
  final DateTime? completedAt;
  final DateTime storedAt;
}

class RecordingCheckpointDraft {
  const RecordingCheckpointDraft({
    required this.transcript,
    required this.durationSeconds,
    required this.moments,
    required this.recordingContext,
    this.knowledgeBaseId,
  });

  final String transcript;
  final int durationSeconds;
  final List<RecordingMoment> moments;
  final String recordingContext;
  final String? knowledgeBaseId;

  Map<String, Object?> toJson(int expectedRevision) => {
    'expectedRevision': expectedRevision,
    'transcript': transcript,
    'durationSeconds': durationSeconds,
    'moments': moments.map((moment) => moment.toJson()).toList(),
    'recordingContext': recordingContext,
    'knowledgeBaseId': knowledgeBaseId,
  };
}

DateTime? _optionalDate(Object? value) {
  final text = value as String?;
  return text == null ? null : DateTime.parse(text);
}

class QuickNoteDraft {
  const QuickNoteDraft({
    required this.title,
    required this.content,
    this.clientCaptureId,
  });

  final String title;
  final String content;
  final String? clientCaptureId;

  Map<String, Object?> toJson() => {
    'title': title,
    'content': content,
    'pinned': false,
    if (clientCaptureId != null) 'clientCaptureId': clientCaptureId,
  };
}

class WebSnapshotDraft {
  WebSnapshotDraft({
    required String url,
    this.title,
    this.notes = '',
    this.knowledgeBaseId,
    this.clientCaptureId,
  }) : url = normalizeWebSnapshotUrl(url);

  final String url;
  final String? title;
  final String notes;
  final String? knowledgeBaseId;
  final String? clientCaptureId;

  Map<String, Object?> toJson({bool includeClientCaptureId = true}) => {
    'url': url,
    if (title?.trim().isNotEmpty ?? false) 'title': title!.trim(),
    if (notes.trim().isNotEmpty) 'notes': notes.trim(),
    if (knowledgeBaseId?.trim().isNotEmpty ?? false)
      'knowledgeBaseId': knowledgeBaseId!.trim(),
    if (includeClientCaptureId && (clientCaptureId?.trim().isNotEmpty ?? false))
      'clientCaptureId': clientCaptureId!.trim(),
  };
}

class WebSnapshotReceipt {
  const WebSnapshotReceipt({
    required this.sourceId,
    required this.idempotentReplay,
  });

  factory WebSnapshotReceipt.fromJson(Map<String, Object?> json) {
    final importResult = json['importResult'];
    if (importResult is! Map<String, Object?>) {
      throw const FormatException(
        'The web snapshot response does not contain an import result.',
      );
    }
    final source = importResult['source'];
    if (source is! Map<String, Object?>) {
      throw const FormatException(
        'The web snapshot response does not contain a source.',
      );
    }
    final sourceId = source['id'];
    if (sourceId is! String || sourceId.trim().isEmpty) {
      throw const FormatException(
        'The web snapshot response source is missing its id.',
      );
    }
    return WebSnapshotReceipt(
      sourceId: sourceId,
      idempotentReplay: json['idempotentReplay'] == true,
    );
  }

  final String sourceId;
  final bool idempotentReplay;
}

String normalizeWebSnapshotUrl(String value) {
  final text = value.trim();
  if (text.isEmpty || RegExp(r'\s').hasMatch(text)) {
    throw const FormatException('Enter a complete http or https URL.');
  }
  final uri = Uri.tryParse(text);
  if (uri == null ||
      (uri.scheme.toLowerCase() != 'https' &&
          uri.scheme.toLowerCase() != 'http') ||
      uri.host.isEmpty) {
    throw const FormatException('Enter a complete http or https URL.');
  }
  return uri.toString();
}
