import 'package:gunther_mobile/data/models/assertion.dart';
import 'package:gunther_mobile/data/models/capture.dart';
import 'package:gunther_mobile/data/models/inbox_item.dart';
import 'package:gunther_mobile/data/models/knowledge_base.dart';
import 'package:gunther_mobile/data/models/knowledge_graph.dart';
import 'package:gunther_mobile/data/models/overview.dart';
import 'package:gunther_mobile/data/models/source.dart';
import 'package:gunther_mobile/data/models/web_search.dart';
import 'package:gunther_mobile/data/repositories/knowledge_repository.dart';
import 'package:gunther_mobile/data/services/connection_http_client.dart';
import 'package:gunther_mobile/data/services/knowledge_api_service.dart';
import 'package:gunther_mobile/features/connection/presentation/connection_controller.dart';

/// Routes every operation to the currently paired workspace while keeping the
/// compile-time endpoint as a development fallback. Credentials remain in the
/// platform secret store/controller and are never copied into capture payloads.
class ConnectionAwareKnowledgeRepository
    implements KnowledgeRepository, CaptureTargetRepository {
  ConnectionAwareKnowledgeRepository({
    required ConnectionController connections,
    required KnowledgeRepository fallback,
  }) : _connections = connections,
       _fallback = fallback;

  final ConnectionController _connections;
  final KnowledgeRepository _fallback;
  String? _activeKey;
  KnowledgeApiService? _activeApi;
  RemoteKnowledgeRepository? _activeRepository;

  KnowledgeRepository get _delegate {
    final profile = _connections.activeProfile;
    final secrets = _connections.activeSecrets;
    final unsafeStatus = switch (_connections.status) {
      WorkspaceConnectionStatus.unconfigured ||
      WorkspaceConnectionStatus.authExpired ||
      WorkspaceConnectionStatus.certificateChanged ||
      WorkspaceConnectionStatus.incompatible => true,
      WorkspaceConnectionStatus.checking ||
      WorkspaceConnectionStatus.online ||
      WorkspaceConnectionStatus.offline => false,
    };
    if (profile == null) return _fallback;
    if (secrets == null || unsafeStatus) {
      throw StateError(
        'The selected knowledge workspace is not trusted for sync right now.',
      );
    }

    final key =
        '${profile.id}\u0000${profile.deviceId}\u0000${profile.baseUri}\u0000${profile.caFingerprint ?? ''}';
    if (_activeKey != key || _activeRepository == null) {
      _activeApi?.close();
      final api = KnowledgeApiService(
        baseUrl: profile.baseUri.toString(),
        bearerToken: secrets.accessToken,
        client: createConnectionHttpClient(profile, secrets),
      );
      _activeKey = key;
      _activeApi = api;
      _activeRepository = RemoteKnowledgeRepository(apiService: api);
    }
    return _activeRepository!;
  }

  void dispose() {
    _activeApi?.close();
    _activeApi = null;
    _activeRepository = null;
    _activeKey = null;
  }

  @override
  CaptureTargetLease pinCaptureTarget({
    required String workspaceId,
    required String profileId,
  }) {
    final profile = _connections.activeProfile;
    final secrets = _connections.activeSecrets;
    final unsafeStatus = switch (_connections.status) {
      WorkspaceConnectionStatus.unconfigured ||
      WorkspaceConnectionStatus.authExpired ||
      WorkspaceConnectionStatus.certificateChanged ||
      WorkspaceConnectionStatus.incompatible => true,
      WorkspaceConnectionStatus.checking ||
      WorkspaceConnectionStatus.online ||
      WorkspaceConnectionStatus.offline => false,
    };
    if (profile == null ||
        profile.id != profileId ||
        profile.workspaceId != workspaceId) {
      throw StateError(
        'The saved capture target is no longer the active knowledge workspace.',
      );
    }
    if (secrets == null || unsafeStatus) {
      throw StateError(
        'The selected knowledge workspace is not trusted for sync right now.',
      );
    }

    // This client owns immutable profile/secrets values and is deliberately
    // separate from the mutable active-repository cache. Switching profiles can
    // neither redirect nor close an upload already using this lease.
    final api = KnowledgeApiService(
      baseUrl: profile.baseUri.toString(),
      bearerToken: secrets.accessToken,
      client: createConnectionHttpClient(profile, secrets),
    );
    return _PinnedCaptureTargetLease(
      api,
      RemoteKnowledgeRepository(apiService: api),
    );
  }

  @override
  Future<Overview> getOverview() => _delegate.getOverview();

  @override
  Future<KnowledgeGraph> getGraph() => _delegate.getGraph();

  @override
  Future<List<KnowledgeBaseSummary>> getKnowledgeBases() =>
      _delegate.getKnowledgeBases();

  @override
  Future<KnowledgeBaseSummary> createKnowledgeBase(KnowledgeBaseDraft draft) =>
      _delegate.createKnowledgeBase(draft);

  @override
  Future<List<InboxItem>> getInbox({String? state}) =>
      _delegate.getInbox(state: state);

  @override
  Future<List<SourceSummary>> getSources() => _delegate.getSources();

  @override
  Future<List<Assertion>> getProvisionalAssertions() =>
      _delegate.getProvisionalAssertions();

  @override
  Future<void> importSource(SourceDraft draft) => _delegate.importSource(draft);

  @override
  Future<void> createQuickNote(QuickNoteDraft draft) =>
      _delegate.createQuickNote(draft);

  @override
  Future<WebSnapshotReceipt> captureWebSnapshot(WebSnapshotDraft draft) =>
      _delegate.captureWebSnapshot(draft);

  @override
  Future<void> uploadAsset(
    CapturedAsset asset, {
    required String title,
    required String kind,
    String? knowledgeBaseId,
    String notes = '',
  }) => _delegate.uploadAsset(
    asset,
    title: title,
    kind: kind,
    knowledgeBaseId: knowledgeBaseId,
    notes: notes,
  );

  @override
  Future<RecordingSession> startRecordingSession(
    String title,
    String contentType,
  ) => _delegate.startRecordingSession(title, contentType);

  @override
  Future<RecordingSession> getRecordingMetadata(String recordingId) =>
      _delegate.getRecordingMetadata(recordingId);

  @override
  Future<RecordingSession> uploadRecordingFile(
    RecordingSession session,
    CapturedAsset asset, {
    void Function(int sentBytes, int totalBytes)? onProgress,
  }) => _delegate.uploadRecordingFile(session, asset, onProgress: onProgress);

  @override
  Future<RecordingSession> completeRecordingSession(String recordingId) =>
      _delegate.completeRecordingSession(recordingId);

  @override
  Future<RecordingSession> checkpointRecording(
    RecordingSession session,
    RecordingCheckpointDraft checkpoint,
  ) => _delegate.checkpointRecording(session, checkpoint);

  @override
  Future<void> fileSource(String sourceId, String knowledgeBaseId) =>
      _delegate.fileSource(sourceId, knowledgeBaseId);

  @override
  Future<void> fileQuickNote(String noteId, String knowledgeBaseId) =>
      _delegate.fileQuickNote(noteId, knowledgeBaseId);

  @override
  Future<WebSearchResult> searchWeb(String query) => _delegate.searchWeb(query);

  @override
  Future<void> updateAssertionStatus(String id, String status) =>
      _delegate.updateAssertionStatus(id, status);
}

class _PinnedCaptureTargetLease implements CaptureTargetLease {
  _PinnedCaptureTargetLease(this._api, this.repository);

  final KnowledgeApiService _api;

  @override
  final KnowledgeRepository repository;

  bool _closed = false;

  @override
  void close() {
    if (_closed) return;
    _closed = true;
    _api.close();
  }
}
