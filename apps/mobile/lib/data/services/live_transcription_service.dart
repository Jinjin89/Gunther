import 'dart:async';
import 'dart:collection';
import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';

const int liveTranscriptionSampleRate = 24000;
const int liveTranscriptionBytesPerSample = 2;
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

class LiveTranscriptionDeltaEvent extends LiveTranscriptionEvent {
  const LiveTranscriptionDeltaEvent({required this.delta, this.itemId});

  final String delta;
  final String? itemId;
}

class LiveTranscriptionCompletedEvent extends LiveTranscriptionEvent {
  const LiveTranscriptionCompletedEvent({
    required this.transcript,
    this.itemId,
    this.provider,
    this.startSeconds,
  });

  final String transcript;
  final String? itemId;
  final String? provider;
  final double? startSeconds;
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
  void commit();
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
  final ListQueue<Uint8List> _offlineAudio = ListQueue<Uint8List>();
  final StreamController<LiveTranscriptionEvent> _events =
      StreamController<LiveTranscriptionEvent>.broadcast();

  LiveTranscriptionSocket? _socket;
  StreamSubscription<Object?>? _socketSubscription;
  Future<void>? _connectionAttempt;
  Timer? _reconnectTimer;
  LiveTranscriptionConnectionState _state =
      LiveTranscriptionConnectionState.idle;
  int _bufferedBytes = 0;
  int _droppedBytes = 0;
  int _reconnectAttempt = 0;
  bool _active = false;
  bool _ready = false;
  bool _commitPending = false;
  bool _disposed = false;

  Uri get webSocketUri => _webSocketUri;
  @override
  Stream<LiveTranscriptionEvent> get events => _events.stream;

  @override
  LiveTranscriptionConnectionState get state => _state;

  @override
  int get bufferedBytes => _bufferedBytes;

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
    if (alignedLength == 0) return;
    final aligned = Uint8List.fromList(pcm.sublist(0, alignedLength));
    if (_ready && _sendAppend(aligned)) return;
    _bufferAudio(aligned);
    _emitStatus();
  }

  /// Commits all audio offered so far. If offline, the commit follows the
  /// bounded buffer after a successful reconnect.
  @override
  void commit() {
    if (_disposed) return;
    _commitPending = true;
    _flushPending();
  }

  @override
  Future<void> stop() async {
    if (_disposed && _state == LiveTranscriptionConnectionState.stopped) {
      return;
    }
    _active = false;
    _ready = false;
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
      final socket = await _connector(_webSocketUri, headers);
      if (!_active || _disposed) {
        try {
          await socket.close(WebSocketStatus.normalClosure, 'inactive');
        } on Object {
          // Connection teardown remains best effort.
        }
        return;
      }
      _socket = socket;
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

    switch (payload['type']) {
      case 'service.ready':
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
      case 'conversation.item.input_audio_transcription.delta':
        final delta = _string(payload['delta']);
        if (delta != null && delta.isNotEmpty) {
          _emit(
            LiveTranscriptionDeltaEvent(
              delta: delta,
              itemId: _string(payload['item_id']),
            ),
          );
        }
      case 'conversation.item.input_audio_transcription.completed':
        final transcript = _string(payload['transcript']);
        if (transcript != null && transcript.isNotEmpty) {
          _emit(
            LiveTranscriptionCompletedEvent(
              transcript: transcript,
              itemId: _string(payload['item_id']),
              provider: _string(payload['provider']),
              startSeconds: _number(payload['start_seconds']),
            ),
          );
        }
      case 'service.error':
      case 'error':
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
    if (!_ready || _socket == null) return;
    while (_offlineAudio.isNotEmpty && _ready) {
      final chunk = _offlineAudio.removeFirst();
      _bufferedBytes -= chunk.length;
      if (!_sendAppend(chunk)) {
        _offlineAudio.addFirst(chunk);
        _bufferedBytes += chunk.length;
        _emitStatus();
        return;
      }
    }
    if (_commitPending &&
        _ready &&
        _sendJson(<String, Object>{'type': 'input_audio_buffer.commit'})) {
      _commitPending = false;
    }
    _emitStatus();
  }

  bool _sendAppend(Uint8List pcm) => _sendJson(<String, Object>{
    'type': 'input_audio_buffer.append',
    'audio': base64Encode(pcm),
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

  void _bufferAudio(Uint8List pcm) {
    var retained = pcm;
    if (retained.length > maxBufferedBytes) {
      final dropped = retained.length - maxBufferedBytes;
      _droppedBytes += dropped;
      retained = Uint8List.fromList(retained.sublist(dropped));
    }

    var overflow = _bufferedBytes + retained.length - maxBufferedBytes;
    while (overflow > 0 && _offlineAudio.isNotEmpty) {
      final oldest = _offlineAudio.removeFirst();
      if (oldest.length <= overflow) {
        overflow -= oldest.length;
        _bufferedBytes -= oldest.length;
        _droppedBytes += oldest.length;
      } else {
        _offlineAudio.addFirst(Uint8List.fromList(oldest.sublist(overflow)));
        _bufferedBytes -= overflow;
        _droppedBytes += overflow;
        overflow = 0;
      }
    }
    _offlineAudio.addLast(retained);
    _bufferedBytes += retained.length;
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
        bufferedBytes: _bufferedBytes,
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
    queryParameters: normalizedContext.isEmpty
        ? null
        : <String, String>{'context': normalizedContext},
  );
}

bool _isExactLoopback(String host) {
  final normalized = host.toLowerCase();
  return normalized == '127.0.0.1' ||
      normalized == 'localhost' ||
      normalized == '::1';
}

String? _string(Object? value) => value is String ? value : null;

double? _number(Object? value) => value is num ? value.toDouble() : null;
