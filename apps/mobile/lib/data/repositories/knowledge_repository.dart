import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:gunther_mobile/data/models/assertion.dart';
import 'package:gunther_mobile/data/models/capture.dart';
import 'package:gunther_mobile/data/models/inbox_item.dart';
import 'package:gunther_mobile/data/models/knowledge_graph.dart';
import 'package:gunther_mobile/data/models/knowledge_base.dart';
import 'package:gunther_mobile/data/models/overview.dart';
import 'package:gunther_mobile/data/models/source.dart';
import 'package:gunther_mobile/data/models/web_search.dart';
import 'package:gunther_mobile/data/services/knowledge_api_service.dart';

abstract interface class KnowledgeRepository {
  Future<Overview> getOverview();
  Future<KnowledgeGraph> getGraph();
  Future<List<KnowledgeBaseSummary>> getKnowledgeBases();
  Future<KnowledgeBaseSummary> createKnowledgeBase(KnowledgeBaseDraft draft);
  Future<List<InboxItem>> getInbox({String? state});
  Future<List<SourceSummary>> getSources();
  Future<List<Assertion>> getProvisionalAssertions();
  Future<void> importSource(SourceDraft draft);
  Future<void> createQuickNote(QuickNoteDraft draft);
  Future<WebSnapshotReceipt> captureWebSnapshot(WebSnapshotDraft draft);
  Future<void> uploadAsset(
    CapturedAsset asset, {
    required String title,
    required String kind,
    String? knowledgeBaseId,
    String notes = '',
  });
  Future<RecordingSession> startRecordingSession(
    String title,
    String contentType,
  );
  Future<RecordingSession> getRecordingMetadata(String recordingId);
  Future<RecordingSession> uploadRecordingFile(
    RecordingSession session,
    CapturedAsset asset, {
    void Function(int sentBytes, int totalBytes)? onProgress,
  });
  Future<RecordingSession> completeRecordingSession(String recordingId);
  Future<RecordingSession> checkpointRecording(
    RecordingSession session,
    RecordingCheckpointDraft checkpoint,
  );
  Future<void> fileSource(String sourceId, String knowledgeBaseId);
  Future<void> fileQuickNote(String noteId, String knowledgeBaseId);
  Future<WebSearchResult> searchWeb(String query);
  Future<void> updateAssertionStatus(String id, String status);
}

/// One connection lease pinned to the workspace/profile that owns a durable
/// capture. The repository must not consult mutable "active profile" state
/// again while this lease is in use.
abstract interface class CaptureTargetLease {
  KnowledgeRepository get repository;
  void close();
}

/// Opens a synchronous, target-pinned transport before an outbox upload does
/// any asynchronous work. This closes the race where the active connection can
/// change after an entry is checked but before its request is sent.
abstract interface class CaptureTargetRepository {
  CaptureTargetLease pinCaptureTarget({
    required String workspaceId,
    required String profileId,
  });
}

class RemoteKnowledgeRepository implements KnowledgeRepository {
  const RemoteKnowledgeRepository({required KnowledgeApiService apiService})
    : _apiService = apiService;

  final KnowledgeApiService _apiService;

  @override
  Future<Overview> getOverview() async {
    final json = await _apiService.get('overview') as Map<String, Object?>;
    return Overview.fromJson(json);
  }

  @override
  Future<KnowledgeGraph> getGraph() async {
    final json = await _apiService.get('graph') as Map<String, Object?>;
    return KnowledgeGraph.fromJson(json);
  }

  @override
  Future<List<KnowledgeBaseSummary>> getKnowledgeBases() async {
    final json = await _apiService.get('knowledge-bases') as List<Object?>;
    return json
        .map(
          (item) =>
              KnowledgeBaseSummary.fromJson(item! as Map<String, Object?>),
        )
        .toList(growable: false);
  }

  @override
  Future<KnowledgeBaseSummary> createKnowledgeBase(
    KnowledgeBaseDraft draft,
  ) async {
    final json =
        await _apiService.post('knowledge-bases', draft.toJson())
            as Map<String, Object?>;
    return KnowledgeBaseSummary.fromJson(json);
  }

  @override
  Future<List<InboxItem>> getInbox({String? state}) async {
    final path = state == null
        ? 'inbox'
        : 'inbox?state=${Uri.encodeQueryComponent(state)}';
    final json = await _apiService.get(path) as List<Object?>;
    return json
        .map((item) => InboxItem.fromJson(item! as Map<String, Object?>))
        .toList(growable: false);
  }

  @override
  Future<List<SourceSummary>> getSources() async {
    final json = await _apiService.get('sources') as List<Object?>;
    return json
        .map((item) => SourceSummary.fromJson(item! as Map<String, Object?>))
        .toList(growable: false);
  }

  @override
  Future<List<Assertion>> getProvisionalAssertions() async {
    final json =
        await _apiService.get('assertions?status=provisional') as List<Object?>;
    return json
        .map((item) => Assertion.fromJson(item! as Map<String, Object?>))
        .toList(growable: false);
  }

  @override
  Future<void> importSource(SourceDraft draft) async {
    await _apiService.post('sources', draft.toJson());
  }

  @override
  Future<void> createQuickNote(QuickNoteDraft draft) async {
    await _apiService.post('notes', draft.toJson());
  }

  @override
  Future<WebSnapshotReceipt> captureWebSnapshot(WebSnapshotDraft draft) async {
    final json =
        await _apiService.post('captures/web', draft.toJson())
            as Map<String, Object?>;
    return WebSnapshotReceipt.fromJson(json);
  }

  @override
  Future<void> uploadAsset(
    CapturedAsset asset, {
    required String title,
    required String kind,
    String? knowledgeBaseId,
    String notes = '',
  }) async {
    final query = Uri(
      queryParameters: {
        'title': title,
        'fileName': asset.fileName,
        'kind': kind,
        if (knowledgeBaseId != null) 'knowledgeBaseId': knowledgeBaseId,
        if (notes.trim().isNotEmpty) 'notes': notes.trim(),
      },
    ).query;
    await _apiService.uploadFile(
      'captures/assets?$query',
      File(asset.path),
      contentType: asset.mediaType,
    );
  }

  @override
  Future<RecordingSession> startRecordingSession(
    String title,
    String contentType,
  ) async {
    final query = Uri(queryParameters: {'title': title}).query;
    final json =
        await _apiService.postEmpty(
              'recordings/sessions?$query',
              headers: {'Content-Type': contentType},
            )
            as Map<String, Object?>;
    return RecordingSession.fromJson(json);
  }

  @override
  Future<RecordingSession> getRecordingMetadata(String recordingId) async {
    final json =
        await _apiService.get('recordings/$recordingId/metadata')
            as Map<String, Object?>;
    return RecordingSession.fromJson(json);
  }

  @override
  Future<RecordingSession> uploadRecordingFile(
    RecordingSession session,
    CapturedAsset asset, {
    void Function(int sentBytes, int totalBytes)? onProgress,
  }) async {
    const chunkSize = 8 * 1024 * 1024;
    final file = File(asset.path);
    final totalBytes = await file.length();
    if (session.sizeBytes > totalBytes) {
      throw const ApiException(
        'The server has more recording bytes than the selected local file.',
        409,
      );
    }

    var current = session;
    var sentBytes = session.sizeBytes;
    var sequence = session.nextExpectedSequence;
    final reader = await file.open();
    try {
      await reader.setPosition(sentBytes);
      onProgress?.call(sentBytes, totalBytes);
      while (sentBytes < totalBytes) {
        final remaining = totalBytes - sentBytes;
        final bytes = await reader.read(
          remaining < chunkSize ? remaining : chunkSize,
        );
        if (bytes.isEmpty) break;
        final payload = Uint8List.fromList(bytes);
        final checksum = sha256.convert(payload).toString();
        final json =
            await _apiService.putBytes(
                  'recordings/${session.id}/chunks?sequence=$sequence',
                  payload,
                  headers: {
                    'Content-Type': asset.mediaType,
                    'X-Chunk-SHA256': checksum,
                  },
                )
                as Map<String, Object?>;
        current = RecordingSession.fromJson(json);
        sentBytes += payload.length;
        sequence = current.nextExpectedSequence;
        onProgress?.call(sentBytes, totalBytes);
      }
    } finally {
      await reader.close();
    }
    if (sentBytes != totalBytes) {
      throw const ApiException(
        'The local recording ended before upload completed.',
        422,
      );
    }
    return current;
  }

  @override
  Future<RecordingSession> completeRecordingSession(String recordingId) async {
    final json =
        await _apiService.postEmpty('recordings/$recordingId/complete')
            as Map<String, Object?>;
    return RecordingSession.fromJson(json);
  }

  @override
  Future<RecordingSession> checkpointRecording(
    RecordingSession session,
    RecordingCheckpointDraft checkpoint,
  ) async {
    final json =
        await _apiService.patch(
              'recordings/${session.id}/checkpoint',
              checkpoint.toJson(session.checkpointRevision),
            )
            as Map<String, Object?>;
    return RecordingSession.fromJson(json);
  }

  @override
  Future<void> fileSource(String sourceId, String knowledgeBaseId) async {
    await _apiService.post('sources/$sourceId/file', {
      'knowledgeBaseId': knowledgeBaseId,
    });
  }

  @override
  Future<void> fileQuickNote(String noteId, String knowledgeBaseId) async {
    await _apiService.post('notes/$noteId/file', {
      'knowledgeBaseId': knowledgeBaseId,
    });
  }

  @override
  Future<WebSearchResult> searchWeb(String query) async {
    final encoded = Uri.encodeQueryComponent(query);
    final json =
        await _apiService.get('search/web?q=$encoded') as Map<String, Object?>;
    return WebSearchResult.fromJson(json);
  }

  @override
  Future<void> updateAssertionStatus(String id, String status) async {
    await _apiService.patch('assertions/$id/status', {
      'status': status,
      'reason': 'Reviewed in Gunther mobile',
    });
  }
}
