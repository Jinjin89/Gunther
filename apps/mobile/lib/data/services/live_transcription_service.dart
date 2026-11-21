import 'dart:async';
import 'dart:collection';
import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';

const int liveTranscriptionSampleRate = 24000;
const int liveTranscriptionBytesPerSample = 2;
const int _bytesPerMs =
    liveTranscriptionSampleRate * liveTranscriptionBytesPerSample ~/ 1000;
const int liveTranscriptionBufferSeconds = 30;
const int defaultLiveTranscriptionBufferBytes =
    liveTranscriptionSampleRate *
    liveTranscriptionBytesPerSample *
    liveTranscriptionBufferSeconds;

enum LiveTranscriptionConnectionState {
  idle,
  connecting,
  connected,
  ready,
  reconnecting,
  offline,
  stopped,
}

sealed class LiveTranscriptionEvent {
  const LiveTranscriptionEvent();
}

class LiveTranscriptionStatusEvent extends LiveTranscriptionEvent {
  const LiveTranscriptionStatusEvent({
    required this.state,
    required this.bufferedBytes,
    this.reconnectAttempt = 0,
    this.retryAfter,
  });

  final LiveTranscriptionConnectionState state;
  final int bufferedBytes;
  final int reconnectAttempt;
  final Duration? retryAfter;
}

class LiveTranscriptionReadyEvent extends LiveTranscriptionEvent {
  const LiveTranscriptionReadyEvent({
    required this.provider,
    required this.model,
    required this.local,
    required this.diarize,
  });

  final String provider;
  final String model;
  final bool local;
  final bool diarize;
}

class LiveTranscriptionPieceEvent extends LiveTranscriptionEvent {
  const LiveTranscriptionPieceEvent({
    required this.id,
    required this.text,
    required this.startMs,
    required this.endMs,
    required this.isFinal,
  });

  final String id;
  final String text;
  final int startMs;
  final int? endMs;
  final bool isFinal;
}

class LiveTranscriptionGapEvent extends LiveTranscriptionEvent {
  const LiveTranscriptionGapEvent(this.startMs, this.endMs);

  final int startMs;
  final int endMs;
}

class LiveTranscriptionFlushedEvent extends LiveTranscriptionEvent {
  const LiveTranscriptionFlushedEvent(this.untilMs);

  final int untilMs;
}

class LiveTranscriptionErrorEvent extends LiveTranscriptionEvent {
  const LiveTranscriptionErrorEvent({
    required this.code,
    required this.message,
    required this.recoverable,
  });

  final String code;
  final String message;
  final bool recoverable;
}

abstract interface class LiveTranscriptionSocket {
  Stream<Object?> get messages;

  void send(String message);

  Future<void> close([int? code, String? reason]);
}

typedef LiveTranscriptionSocketConnector =
    Future<LiveTranscriptionSocket> Function(
      Uri uri,
      Map<String, Object> headers,
    );
typedef LiveTranscriptionReconnectDelay = Duration Function(int attempt);

abstract interface class LiveTranscriptionSession {
  Stream<LiveTranscriptionEvent> get events;
  LiveTranscriptionConnectionState get state;
  int get bufferedBytes;
  int get droppedBytes;
  Future<void> connect();
  void appendPcm(Uint8List pcm);
  Future<bool> commit();
  Future<void> stop();
  Future<void> dispose();
}

typedef LiveTranscriptionSessionFactory =
    LiveTranscriptionSession? Function(String context);

/// A best-effort live transcription companion for an independently durable
/// recording path.
///
/// PCM bytes are never the recording source of truth here. Any network,
/// protocol, or transcription failure is converted into an event and absorbed,
/// so callers can continue writing their original audio without catching this
/// service's transport errors.
class LiveTranscriptionService implements LiveTranscriptionSession {
  LiveTranscriptionService({
    required Uri baseUri,
    String apiToken = '',
    String bearerToken = '',
    String context = '',
    LiveTranscriptionSocketConnector? connector,
    HttpClient? httpClient,
    LiveTranscriptionReconnectDelay? reconnectDelay,
    this.maxBufferedBytes = defaultLiveTranscriptionBufferBytes,
  }) : _apiToken = apiToken,
       _bearerToken = bearerToken,
       _httpClient = httpClient,
       _webSocketUri = _liveTranscriptionUri(baseUri, context),
       _connector =
           connector ??
           ((uri, headers) =>
               _connectDartIoWebSocket(uri, headers, httpClient)),
       _reconnectDelay = reconnectDelay ?? _defaultReconnectDelay {
    if (apiToken.isNotEmpty && bearerToken.isNotEmpty) {
      throw ArgumentError(
        'Sidecar and device credentials cannot be used at the same time.',
      );
    }
    if (maxBufferedBytes <= 0 || maxBufferedBytes.isOdd) {
      throw ArgumentError.value(
        maxBufferedBytes,
        'maxBufferedBytes',
        'must be a positive, even PCM16 byte count',
      );
    }
  }

  final String _apiToken;
  final String _bearerToken;
  final HttpClient? _httpClient;
  final Uri _webSocketUri;
  final LiveTranscriptionSocketConnector _connector;
  final LiveTranscriptionReconnectDelay _reconnectDelay;
  final int maxBufferedBytes;
  final ListQueue<({int offset, Uint8List pcm})> _retainedPcm = ListQueue();
  final ListQueue<_LiveCommit> _commits = ListQueue<_LiveCommit>();
  final StreamController<LiveTranscriptionEvent> _events =
      StreamController<LiveTranscriptionEvent>.broadcast();

  LiveTranscriptionSocket? _socket;
  StreamSubscription<Object?>? _socketSubscription;
  Future<void>? _connectionAttempt;
  Timer? _reconnectTimer;
  LiveTranscriptionConnectionState _state =
      LiveTranscriptionConnectionState.idle;
  int _droppedBytes = 0;
  int _offeredBytes = 0;
  int _nextSend = 0;
  int _heardThrough = 0;
  int _reportedLostThrough = 0;
  int _socketStartMs = 0;
  int? _firstSentByte;
  int _reconnectAttempt = 0;
  bool _active = false;
  bool _ready = false;
  bool _disposed = false;

  int get _retainedStart => _retainedPcm.firstOrNull?.offset ?? _offeredBytes;

  Uri get webSocketUri => _webSocketUri.replace(
    queryParameters: {
      ..._webSocketUri.queryParameters,
      'offset_ms': '${_nextSend ~/ _bytesPerMs}',
    },
  );
  @override
  Stream<LiveTranscriptionEvent> get events => _events.stream;

  @override
  LiveTranscriptionConnectionState get state => _state;

  @override
  int get bufferedBytes => _offeredBytes - _nextSend;

  @override
  int get droppedBytes => _droppedBytes;

  /// Starts or resumes the best-effort connection. Transport failures complete
  /// normally and are reported through [events].
  @override
  Future<void> connect() async {
    if (_disposed) return;
    _active = true;
    await _ensureConnected();
  }

  /// Offers mono, 24 kHz PCM16 bytes to live transcription without ever
  /// throwing a transport failure back into the recording path.
  @override
  void appendPcm(Uint8List pcm) {
    if (_disposed || pcm.length < liveTranscriptionBytesPerSample) return;
    final alignedLength = pcm.length - (pcm.length % 2);
    final aligned = Uint8List.fromList(pcm.sublist(0, alignedLength));
    final offset = _offeredBytes;
    _offeredBytes += alignedLength;
    _retainedPcm.addLast((offset: offset, pcm: aligned));
    _flushPending();
    _discardBefore(_offeredBytes - maxBufferedBytes);
    if (_nextSend < _retainedStart) _nextSend = _retainedStart;
    if (!_ready) {
      _reportLostAudio();
      _emitStatus();
    }
  }

  /// Returns true when the offered audio is flushed, or false if interrupted.
  @override
  Future<bool> commit() {
    if (_disposed) return Future<bool>.value(false);
    final commit = _LiveCommit(
      _offeredBytes,
      math.max(_heardThrough, _reportedLostThrough),
    );
    _commits.addLast(commit);
    if (!_ready) _reportLostAudio();
    _flushPending();
    return commit.done.future;
  }

  @override
  Future<void> stop() async {
    if (_disposed && _state == LiveTranscriptionConnectionState.stopped) {
      return;
    }
    _active = false;
    _ready = false;
    while (_commits.isNotEmpty) {
      final commit = _commits.removeFirst();
      if (!commit.done.isCompleted) commit.done.complete(false);
    }
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    final subscription = _socketSubscription;
    _socketSubscription = null;
    final socket = _socket;
    _socket = null;
    try {
      await subscription?.cancel();
    } on Object {
      // The recording lifecycle must not depend on socket cleanup.
    }
    try {
      await socket?.close(WebSocketStatus.normalClosure, 'stopped');
    } on Object {
      // The recording lifecycle must not depend on socket cleanup.
    }
    _setState(LiveTranscriptionConnectionState.stopped);
  }

  @override
  Future<void> dispose() async {
    if (_disposed) return;
    _disposed = true;
    await stop();
    _httpClient?.close(force: true);
    await _events.close();
  }

  Future<void> _ensureConnected() {
    if (!_active || _disposed || _socket != null) return Future<void>.value();
    final existing = _connectionAttempt;
    if (existing != null) return existing;

    late final Future<void> attempt;
    attempt = _openConnection().whenComplete(() {
      if (identical(_connectionAttempt, attempt)) _connectionAttempt = null;
    });
    _connectionAttempt = attempt;
    return attempt;
  }

  Future<void> _openConnection() async {
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _setState(
      _reconnectAttempt == 0
          ? LiveTranscriptionConnectionState.connecting
          : LiveTranscriptionConnectionState.reconnecting,
    );
    try {
      final headers = <String, Object>{
        if (_apiToken.isNotEmpty) 'X-Gunther-Token': _apiToken,
        if (_bearerToken.isNotEmpty)
          HttpHeaders.authorizationHeader: 'Bearer $_bearerToken',
      };
      final startMs = _nextSend ~/ _bytesPerMs;
      final socket = await _connector(webSocketUri, headers);
      if (!_active || _disposed) {
        try {
          await socket.close(WebSocketStatus.normalClosure, 'inactive');
        } on Object {
          // Connection teardown remains best effort.
        }
        return;
      }
      _socket = socket;
      _socketStartMs = startMs;
      _firstSentByte = null;
      _ready = false;
      _setState(LiveTranscriptionConnectionState.connected);
      _socketSubscription = socket.messages.listen(
        (message) => _handleMessage(socket, message),
        onError: (Object error, StackTrace stackTrace) {
          _handleTransportFailure(socket, error);
        },
        onDone: () => _handleTransportClosed(socket),
        cancelOnError: false,
      );
    } on Object {
      _emitError(
        code: 'connection_failed',
        message: 'Live transcription is offline; the recording remains local.',
        recoverable: true,
      );
      _scheduleReconnect();
      // Deliberately absorb connector details, which can contain platform
      // networking information and are not required by the recording path.
    }
  }

  void _handleMessage(LiveTranscriptionSocket socket, Object? rawMessage) {
    if (!identical(_socket, socket)) return;
    if (rawMessage is! String) {
      _emitError(
        code: 'invalid_event',
        message: 'Live transcription returned an unreadable event.',
        recoverable: true,
      );
      return;
    }

    Map<String, Object?> payload;
    try {
      final decoded = jsonDecode(rawMessage);
      if (decoded is! Map<String, Object?>) {
        throw const FormatException('event must be an object');
      }
      payload = decoded;
    } on Object {
      _emitError(
        code: 'invalid_event',
        message: 'Live transcription returned an unreadable event.',
        recoverable: true,
      );
      return;
    }

    switch (payload) {
      case {'type': 'service.ready'}:
        _ready = true;
        _reconnectAttempt = 0;
        _emit(
          LiveTranscriptionReadyEvent(
            provider: _string(payload['provider']) ?? 'unknown',
            model: _string(payload['model']) ?? 'unknown',
            local: payload['local'] == true,
            diarize: payload['diarize'] == true,
          ),
        );
        _setState(LiveTranscriptionConnectionState.ready);
        _flushPending();
        _emitStatus();
      case {
        'type': 'transcript.piece',
        'id': String id,
        'text': String text,
        'start_ms': int startMs,
        'end_ms': int? endMs,
        'final': bool isFinal,
      }:
        if (isFinal && endMs != null) _confirmThrough(endMs * _bytesPerMs);
        _emit(
          LiveTranscriptionPieceEvent(
            id: id,
            text: text,
            startMs: startMs,
            endMs: endMs,
            isFinal: isFinal,
          ),
        );
      case {
            'type': 'transcript.gap',
            'start_ms': int startMs,
            'end_ms': int endMs,
          }
          when endMs > startMs:
        _emit(LiveTranscriptionGapEvent(startMs, endMs));
      case {'type': 'transcript.flushed', 'until_ms': int untilMs}:
        _acknowledgeFlush(untilMs);
        _emit(LiveTranscriptionFlushedEvent(untilMs));
      case {'type': 'service.error'}:
        _emitError(
          code: _string(payload['code']) ?? 'transcription_error',
          message:
              _string(payload['message']) ??
              'Live transcription failed; the recording remains local.',
          recoverable: true,
        );
    }
  }

  void _handleTransportFailure(LiveTranscriptionSocket socket, Object _error) {
    if (!identical(_socket, socket)) return;
    _emitError(
      code: 'connection_interrupted',
      message:
          'Live transcription was interrupted; the recording remains local.',
      recoverable: true,
    );
    _detachSocket(socket);
  }

  void _handleTransportClosed(LiveTranscriptionSocket socket) {
    if (!identical(_socket, socket)) return;
    _detachSocket(socket);
  }

  void _detachSocket(LiveTranscriptionSocket socket) {
    if (!identical(_socket, socket)) return;
    _socket = null;
    _socketSubscription = null;
    _ready = false;
    _nextSend = _retainedStart;
    for (final commit in _commits) {
      commit.flushMs = null;
    }
    _reportLostAudio();
    if (_active && !_disposed) {
      _setState(LiveTranscriptionConnectionState.offline);
      _scheduleReconnect();
    }
  }

  void _scheduleReconnect() {
    if (!_active || _disposed || _reconnectTimer != null) return;
    _reconnectAttempt += 1;
    final retryAfter = _reconnectDelay(_reconnectAttempt);
    _setState(
      LiveTranscriptionConnectionState.reconnecting,
      reconnectAttempt: _reconnectAttempt,
      retryAfter: retryAfter,
    );
    _reconnectTimer = Timer(retryAfter, () {
      _reconnectTimer = null;
      if (_active && !_disposed) unawaited(_ensureConnected());
    });
  }

  void _flushPending() {
    if (!_ready) return;
    for (final frame in _retainedPcm) {
      final end = frame.offset + frame.pcm.length;
      if (end <= _nextSend) continue;
      if (!_sendCommits() || !_sendAppend(frame.pcm, frame.offset)) return;
      _firstSentByte ??= frame.offset;
      _nextSend = end;
    }
    _sendCommits();
  }

  bool _sendCommits() {
    for (final commit in _commits) {
      if (commit.offset > _nextSend) break;
      if (commit.flushMs != null) continue;
      if (!_sendJson({'type': 'input_audio_buffer.commit'})) return false;
      final first = _firstSentByte;
      commit.flushMs = first == null
          ? _socketStartMs
          : first ~/ _bytesPerMs + (_nextSend - first) ~/ _bytesPerMs;
    }
    return true;
  }

  void _acknowledgeFlush(int untilMs) {
    final commit = _commits.firstOrNull;
    if (commit != null && commit.flushMs == untilMs) {
      _commits.removeFirst();
      _confirmThrough(commit.offset);
      if (!commit.done.isCompleted) commit.done.complete(true);
    } else {
      _confirmThrough(untilMs * _bytesPerMs);
    }
  }

  void _confirmThrough(int offset) {
    _heardThrough = math.max(_heardThrough, math.min(offset, _nextSend));
    _discardBefore(_heardThrough);
  }

  void _reportLostAudio() {
    final start = _retainedStart;
    if (start <= _heardThrough) return;
    _droppedBytes += math.max(
      0,
      start - math.max(_heardThrough, _reportedLostThrough),
    );
    _reportedLostThrough = start;
    for (final commit in _commits) {
      final neededFrom = math.max(_heardThrough, commit.replayFrom);
      if (commit.flushMs == null &&
          start > neededFrom &&
          commit.offset > neededFrom &&
          !commit.done.isCompleted) {
        commit.done.complete(false);
      }
    }
    _commits.removeWhere(
      (commit) =>
          commit.flushMs == null &&
          commit.done.isCompleted &&
          commit.offset <= start,
    );
  }

  bool _sendAppend(Uint8List pcm, int offset) => _sendJson(<String, Object>{
    'type': 'input_audio_buffer.append',
    'audio': base64Encode(pcm),
    'offset_ms': offset ~/ _bytesPerMs,
  });

  bool _sendJson(Map<String, Object> payload) {
    final socket = _socket;
    if (!_ready || socket == null) return false;
    try {
      socket.send(jsonEncode(payload));
      return true;
    } on Object catch (error) {
      _handleTransportFailure(socket, error);
      return false;
    }
  }

  void _discardBefore(int offset) {
    while (_retainedPcm.isNotEmpty && _retainedPcm.first.offset < offset) {
      final frame = _retainedPcm.removeFirst();
      final dropped = offset - frame.offset;
      if (dropped < frame.pcm.length) {
        _retainedPcm.addFirst((
          offset: offset,
          pcm: Uint8List.fromList(Uint8List.sublistView(frame.pcm, dropped)),
        ));
        break;
      }
    }
  }

  void _setState(
    LiveTranscriptionConnectionState state, {
    int reconnectAttempt = 0,
    Duration? retryAfter,
  }) {
    _state = state;
    _emit(
      LiveTranscriptionStatusEvent(
        state: state,
        bufferedBytes: bufferedBytes,
        reconnectAttempt: reconnectAttempt,
        retryAfter: retryAfter,
      ),
    );
  }

  void _emitStatus() => _setState(_state);

  void _emitError({
    required String code,
    required String message,
    required bool recoverable,
  }) => _emit(
    LiveTranscriptionErrorEvent(
      code: code,
      message: message,
      recoverable: recoverable,
    ),
  );

  void _emit(LiveTranscriptionEvent event) {
    if (!_events.isClosed) _events.add(event);
  }
}

class _LiveCommit {
  _LiveCommit(this.offset, this.replayFrom);

  final int offset;
  final int replayFrom;
  final done = Completer<bool>();
  int? flushMs;
}

class _DartIoLiveTranscriptionSocket implements LiveTranscriptionSocket {
  const _DartIoLiveTranscriptionSocket(this._socket);

  final WebSocket _socket;

  @override
  Stream<Object?> get messages => _socket;

  @override
  void send(String message) => _socket.add(message);

  @override
  Future<void> close([int? code, String? reason]) async {
    await _socket.close(code, reason);
  }
}

Future<LiveTranscriptionSocket> _connectDartIoWebSocket(
  Uri uri,
  Map<String, Object> headers,
  HttpClient? httpClient,
) async {
  final socket = await WebSocket.connect(
    uri.toString(),
    headers: headers,
    customClient: httpClient,
  );
  socket.pingInterval = const Duration(seconds: 20);
  return _DartIoLiveTranscriptionSocket(socket);
}

Duration _defaultReconnectDelay(int attempt) =>
    liveTranscriptionReconnectDelay(attempt);

Duration liveTranscriptionReconnectDelay(int attempt) {
  final exponent = math.min(math.max(attempt - 1, 0), 4);
  return Duration(milliseconds: math.min(8000, 500 * (1 << exponent)));
}

Uri _liveTranscriptionUri(Uri baseUri, String context) {
  if (!baseUri.hasScheme || baseUri.host.isEmpty) {
    throw ArgumentError.value(baseUri, 'baseUri', 'must be an absolute URI');
  }
  if (baseUri.userInfo.isNotEmpty) {
    throw ArgumentError.value(baseUri, 'baseUri', 'must not contain user info');
  }
  if (baseUri.hasFragment) {
    throw ArgumentError.value(
      baseUri,
      'baseUri',
      'must not contain a fragment',
    );
  }
  if (baseUri.hasQuery) {
    throw ArgumentError.value(baseUri, 'baseUri', 'must not contain a query');
  }

  final scheme = baseUri.scheme.toLowerCase();
  final webSocketScheme = switch (scheme) {
    'https' => 'wss',
    'http' when _isExactLoopback(baseUri.host) => 'ws',
    'http' => throw ArgumentError.value(
      baseUri,
      'baseUri',
      'cleartext is only allowed for an exact loopback host',
    ),
    _ => throw ArgumentError.value(
      baseUri,
      'baseUri',
      'must use https, or loopback http for local development',
    ),
  };
  final basePath = baseUri.path.endsWith('/')
      ? baseUri.path
      : '${baseUri.path}/';
  final endpoint = baseUri
      .replace(scheme: webSocketScheme, path: basePath)
      .resolve('recordings/live');
  final normalizedContext = context.trim();
  return endpoint.replace(
    queryParameters: {
      if (normalizedContext.isNotEmpty) 'context': normalizedContext,
      'protocol': '2',
      'sample_rate': '$liveTranscriptionSampleRate',
    },
  );
}

bool _isExactLoopback(String host) {
  final normalized = host.toLowerCase();
  return normalized == '127.0.0.1' ||
      normalized == 'localhost' ||
      normalized == '::1';
}

String? _string(Object? value) => value is String ? value : null;
