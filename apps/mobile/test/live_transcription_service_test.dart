import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:gunther_mobile/data/services/live_transcription_service.dart';

import 'support/mobile_live_fixture.dart';

const _bytesPerMs =
    liveTranscriptionSampleRate * liveTranscriptionBytesPerSample ~/ 1000;
const _ready = {
  'type': 'service.ready',
  'provider': 'sensevoice',
  'model': 'local',
};

void main() {
  group('live transcription endpoint policy', () {
    test('maps secure and exact-loopback API bases without query tokens', () {
      final secure = LiveTranscriptionService(
        baseUri: Uri.parse('https://knowledge.example/api/'),
        apiToken: 'must-not-appear-in-uri',
        context: 'Bioinformatics lecture',
      );
      final ipv4 = LiveTranscriptionService(
        baseUri: Uri.parse('http://127.0.0.1:8787/api'),
      );
      final localhost = LiveTranscriptionService(
        baseUri: Uri.parse('http://localhost:8787/api/'),
      );
      final ipv6 = LiveTranscriptionService(
        baseUri: Uri.parse('http://[::1]:8787/api/'),
      );

      expect(
        secure.webSocketUri.toString(),
        'wss://knowledge.example/api/recordings/live?context=Bioinformatics+lecture&protocol=2&sample_rate=24000&offset_ms=0',
      );
      expect(secure.webSocketUri.queryParameters, isNot(contains('token')));
      expect(
        ipv4.webSocketUri.toString(),
        'ws://127.0.0.1:8787/api/recordings/live?protocol=2&sample_rate=24000&offset_ms=0',
      );
      expect(localhost.webSocketUri.scheme, 'ws');
      expect(ipv6.webSocketUri.scheme, 'ws');
    });

    test(
      'rejects unsafe cleartext, credentials, fragments, and other schemes',
      () {
        for (final value in <String>[
          'http://192.168.1.50:8787/api/',
          'http://127.0.0.2:8787/api/',
          'https://user:secret@knowledge.example/api/',
          'https://knowledge.example/api/#token',
          'ws://127.0.0.1:8787/api/',
        ]) {
          expect(
            () => LiveTranscriptionService(baseUri: Uri.parse(value)),
            throwsArgumentError,
            reason: value,
          );
        }
      },
    );
  });

  test(
    'uses dart:io WebSocket headers and parses the backend protocol',
    () async {
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final requestSeen = Completer<HttpRequest>();
      final clientMessages = <Map<String, Object?>>[];
      final appendSeen = Completer<void>();
      final commitSeen = Completer<void>();
      final serverSubscription = server.listen((request) async {
        if (!requestSeen.isCompleted) requestSeen.complete(request);
        final socket = await WebSocketTransformer.upgrade(request);
        socket.add(
          jsonEncode(<String, Object>{
            'type': 'service.ready',
            'provider': 'sensevoice',
            'model': 'sensevoice-small',
            'local': true,
            'diarize': false,
          }),
        );
        socket.listen((message) {
          final decoded = jsonDecode(message as String) as Map<String, Object?>;
          clientMessages.add(decoded);
          if (decoded['type'] == 'input_audio_buffer.append') {
            if (!appendSeen.isCompleted) appendSeen.complete();
            socket.add(
              jsonEncode({
                'type': 'service.error',
                'code': 'sensevoice_segment_failed',
                'message': 'One segment was missed.',
              }),
            );
          } else if (decoded['type'] == 'input_audio_buffer.commit') {
            if (!commitSeen.isCompleted) commitSeen.complete();
          }
        });
      });
      final service = LiveTranscriptionService(
        baseUri: Uri.parse('http://127.0.0.1:${server.port}/api/'),
        apiToken: 'header-only-token',
        context: 'Course recording',
      );
      final events = <LiveTranscriptionEvent>[];
      final eventSubscription = service.events.listen(events.add);

      try {
        await service.connect();
        await _waitUntil(
          () => events.whereType<LiveTranscriptionReadyEvent>().isNotEmpty,
        );
        service.appendPcm(Uint8List.fromList(<int>[1, 2, 3, 4]));
        service.commit();
        await Future.wait(<Future<void>>[appendSeen.future, commitSeen.future]);
        await _waitUntil(
          () => events.whereType<LiveTranscriptionErrorEvent>().isNotEmpty,
        );

        final request = await requestSeen.future;
        expect(request.uri.path, '/api/recordings/live');
        expect(request.uri.queryParameters, <String, String>{
          'context': 'Course recording',
          'protocol': '2',
          'sample_rate': '24000',
          'offset_ms': '0',
        });
        expect(request.headers.value('X-Gunther-Token'), 'header-only-token');
        expect(request.uri.queryParameters, isNot(contains('token')));
        expect(base64Decode(clientMessages.first['audio']! as String), <int>[
          1,
          2,
          3,
          4,
        ]);
        expect(
          events.whereType<LiveTranscriptionErrorEvent>().single.code,
          'sensevoice_segment_failed',
        );
      } finally {
        await service.dispose();
        await eventSubscription.cancel();
        await serverSubscription.cancel();
        await server.close(force: true);
      }
    },
  );

  test('sends 24 kHz PCM and decodes the full v2 event trace', () async {
    final fixture = await loadMobileLiveV2();
    final socket = _FakeLiveTranscriptionSocket();
    final service = LiveTranscriptionService(
      baseUri: Uri.parse('https://knowledge.example/api/'),
      connector: (_, _) async => socket,
    );
    final events = <LiveTranscriptionEvent>[];
    final subscription = service.events.listen(events.add);
    addTearDown(() async {
      await service.dispose();
      await subscription.cancel();
      await socket.dispose();
    });

    await service.connect();
    socket.receive(fixture['ready']! as Map<String, Object?>);
    await _waitUntil(
      () => events.whereType<LiveTranscriptionReadyEvent>().isNotEmpty,
    );
    final rate = fixture['sample_rate']! as int;
    expect(rate, liveTranscriptionSampleRate);
    final sentAudio = <Uint8List>[];
    final chunks = (fixture['chunks']! as List).cast<Map<String, Object?>>();
    for (final chunk in chunks) {
      final duration = chunk['duration_ms']! as int;
      final byteCount = rate * 2 * duration ~/ 1000;
      final pcm = Uint8List(byteCount)
        ..fillRange(0, byteCount, sentAudio.length + 1);
      service.appendPcm(pcm);
      sentAudio.add(pcm);
      for (final frame
          in (chunk['events']! as List).cast<Map<String, Object?>>()) {
        socket.receive(frame);
      }
      await Future<void>.delayed(Duration.zero);
    }
    service.commit();
    socket.receive(fixture['commit']! as Map<String, Object?>);
    await Future<void>.delayed(Duration.zero);

    final sent = socket.sent.map(
      (message) => jsonDecode(message) as Map<String, Object?>,
    );
    expect(sent.map((message) => message['type']), [
      'input_audio_buffer.append',
      'input_audio_buffer.append',
      'input_audio_buffer.commit',
    ]);
    expect(
      sent.take(2).map((message) => base64Decode(message['audio'] as String)),
      sentAudio,
    );
    expect(sent.take(2).map((message) => message['offset_ms']), [0, 600]);
    expect(
      events
          .map(
            (event) => switch (event) {
              LiveTranscriptionPieceEvent() => [
                event.id,
                event.text,
                event.startMs,
                event.endMs,
                event.isFinal,
              ],
              LiveTranscriptionGapEvent() => [event.startMs, event.endMs],
              LiveTranscriptionFlushedEvent() => [event.untilMs],
              _ => null,
            },
          )
          .nonNulls,
      [
        ['piece-1', "We'll use", 60000, null, false],
        ['piece-1', "We'll use it.", 60000, 60300, true],
        [60600, 75000],
        [60600],
        ['piece-2', "We'll use", 75000, null, false],
        ['piece-2', "We'll use it.", 75000, 75300, true],
        [75600],
      ],
    );
    expect(events.whereType<LiveTranscriptionErrorEvent>(), isEmpty);
  });

  test(
    'Stop flush uses retained frame timestamps when the handshake outlives eviction',
    () async {
      final initial = _FakeLiveTranscriptionSocket();
      final socket = _FakeLiveTranscriptionSocket();
      final reconnect = Completer<LiveTranscriptionSocket>();
      var connections = 0;
      final requestedUris = <Uri>[];
      final requestedHeaders = <Map<String, Object>>[];
      final service = LiveTranscriptionService(
        baseUri: Uri.parse('https://knowledge.example/api/'),
        bearerToken: 'device-token',
        maxBufferedBytes: 200 * _bytesPerMs,
        reconnectDelay: (_) => Duration.zero,
        connector: (uri, headers) async {
          requestedUris.add(uri);
          requestedHeaders.add(headers);
          connections += 1;
          return connections == 1 ? initial : reconnect.future;
        },
      );
      final events = <LiveTranscriptionEvent>[];
      final subscription = service.events.listen(events.add);

      try {
        await service.connect();
        initial.receive(_ready);
        await _waitUntil(
          () => service.state == LiveTranscriptionConnectionState.ready,
        );
        service.appendPcm(Uint8List(500 * _bytesPerMs));
        final firstFlush = service.commit();
        initial.flush(500);
        expect(await firstFlush, isTrue);
        await initial.dispose();
        service.appendPcm(Uint8List(100 * _bytesPerMs));
        await _waitUntil(() => connections == 2);

        final pausedAudio = Uint8List.fromList(
          List.generate(100 * _bytesPerMs, (index) => index % 256),
        );
        final resumedAudio = Uint8List(100 * _bytesPerMs);
        service.appendPcm(pausedAudio);
        final pauseFlush = service.commit();
        service.appendPcm(resumedAudio);
        expect(await pauseFlush, isFalse);
        final stopFlush = service.commit();
        bool? stopped;
        unawaited(stopFlush.then((value) => stopped = value));
        reconnect.complete(socket);
        await _waitUntil(
          () => service.state == LiveTranscriptionConnectionState.connected,
        );
        socket.receive(_ready);
        await _waitUntil(() => socket.sent.length == 4);

        expect(service.bufferedBytes, 0);
        expect(service.droppedBytes, 100 * _bytesPerMs);
        expect(requestedUris.map((uri) => uri.queryParameters['offset_ms']), [
          '0',
          '500',
        ]);
        expect(
          requestedHeaders,
          everyElement(<String, Object>{
            HttpHeaders.authorizationHeader: 'Bearer device-token',
          }),
        );
        final sent = socket.sent
            .map((message) => jsonDecode(message) as Map)
            .toList();
        expect(sent.map((message) => message['type']), [
          'input_audio_buffer.append',
          'input_audio_buffer.commit',
          'input_audio_buffer.append',
          'input_audio_buffer.commit',
        ]);
        expect(sent.map((message) => message['offset_ms']), [
          600,
          null,
          700,
          null,
        ]);
        expect(base64Decode(sent[0]['audio'] as String), pausedAudio);
        expect(base64Decode(sent[2]['audio'] as String), resumedAudio);
        expect(
          events.whereType<LiveTranscriptionStatusEvent>().map(
            (event) => event.state,
          ),
          contains(LiveTranscriptionConnectionState.reconnecting),
        );
        socket.flush(700);
        await _waitUntil(
          () =>
              events.whereType<LiveTranscriptionFlushedEvent>().last.untilMs ==
              700,
        );
        expect(stopped, isNull);
        socket.flush(800);
        expect(await stopFlush, isTrue);
      } finally {
        await service.dispose();
        await subscription.cancel();
        await initial.dispose();
        await socket.dispose();
      }
    },
  );

  test('replays unheard audio and a commit sent before a disconnect', () async {
    final initial = _FakeLiveTranscriptionSocket();
    final resumed = _FakeLiveTranscriptionSocket();
    final offsets = <String?>[];
    final service = LiveTranscriptionService(
      baseUri: Uri.parse('https://knowledge.example/api/'),
      reconnectDelay: (_) => Duration.zero,
      connector: (uri, _) async {
        offsets.add(uri.queryParameters['offset_ms']);
        return offsets.length == 1 ? initial : resumed;
      },
    );
    addTearDown(resumed.dispose);
    addTearDown(initial.dispose);
    addTearDown(service.dispose);
    await service.connect();
    initial.receive(_ready);
    await _waitUntil(
      () => service.state == LiveTranscriptionConnectionState.ready,
    );
    service.appendPcm(
      Uint8List(100 * _bytesPerMs + liveTranscriptionBytesPerSample),
    );
    final pause = service.commit();
    initial.flush(100);
    expect(await pause, isTrue);
    const tailBytes = 100 * _bytesPerMs - liveTranscriptionBytesPerSample;
    final tail = Uint8List(tailBytes)..fillRange(0, tailBytes, 7);
    service.appendPcm(tail);
    final stop = service.commit();
    expect(initial.sent.length, 4);
    bool? result;
    unawaited(stop.then((value) => result = value));
    await initial.dispose();
    await _waitUntil(
      () => service.state == LiveTranscriptionConnectionState.connected,
    );
    expect(offsets, ['0', '100']);
    expect(result, isNull);
    resumed.receive(_ready);
    await _waitUntil(() => resumed.sent.length == 2);
    expect(resumed.sent, initial.sent.skip(2).toList());
    resumed.flush(198);
    await Future<void>.delayed(Duration.zero);
    expect(result, isNull);
    // The first retained sample was stamped at 100 ms, rounding its fractional origin down.
    resumed.flush(199);
    expect(await stop, isTrue);
  });

  test(
    'interrupts a sent commit when its unheard prefix can no longer replay',
    () async {
      final socket = _FakeLiveTranscriptionSocket();
      final service = LiveTranscriptionService(
        baseUri: Uri.parse('https://knowledge.example/api/'),
        maxBufferedBytes: 100 * _bytesPerMs,
        reconnectDelay: (_) => const Duration(hours: 1),
        connector: (_, _) async => socket,
      );
      addTearDown(socket.dispose);
      addTearDown(service.dispose);
      await service.connect();
      socket.receive(_ready);
      await _waitUntil(
        () => service.state == LiveTranscriptionConnectionState.ready,
      );
      service.appendPcm(Uint8List(200 * _bytesPerMs));
      final stop = service.commit();
      expect(socket.sent.length, 2);
      await socket.dispose();
      expect(await stop.timeout(const Duration(seconds: 1)), isFalse);
      expect(service.droppedBytes, 100 * _bytesPerMs);
    },
  );

  test(
    'absorbs connector and socket failures away from recording code',
    () async {
      final socket = _FakeLiveTranscriptionSocket(throwOnSend: true);
      var useFailingConnector = true;
      final service = LiveTranscriptionService(
        baseUri: Uri.parse('https://knowledge.example/api/'),
        reconnectDelay: (_) => const Duration(seconds: 8),
        connector: (_, _) async {
          if (useFailingConnector) {
            useFailingConnector = false;
            throw const SocketException('network unavailable');
          }
          return socket;
        },
      );

      try {
        await expectLater(service.connect(), completes);
        expect(
          () => service.appendPcm(Uint8List.fromList(<int>[1, 2, 3, 4])),
          returnsNormally,
        );
        expect(service.commit, returnsNormally);
        expect(service.bufferedBytes, 4);
      } finally {
        await service.dispose();
        await socket.dispose();
      }
    },
  );

  test('default buffer is thirty seconds of 24 kHz mono PCM16', () {
    expect(defaultLiveTranscriptionBufferBytes, 30 * 24000 * 2);
  });

  test('rejects mixed sidecar and paired-device credentials', () {
    expect(
      () => LiveTranscriptionService(
        baseUri: Uri.parse('https://knowledge.example/api/'),
        apiToken: 'sidecar',
        bearerToken: 'device',
      ),
      throwsArgumentError,
    );
  });

  test(
    'default reconnect delay backs off exponentially and caps at eight seconds',
    () {
      expect(
        liveTranscriptionReconnectDelay(1),
        const Duration(milliseconds: 500),
      );
      expect(liveTranscriptionReconnectDelay(2), const Duration(seconds: 1));
      expect(liveTranscriptionReconnectDelay(3), const Duration(seconds: 2));
      expect(liveTranscriptionReconnectDelay(4), const Duration(seconds: 4));
      expect(liveTranscriptionReconnectDelay(5), const Duration(seconds: 8));
      expect(liveTranscriptionReconnectDelay(50), const Duration(seconds: 8));
    },
  );
}

class _FakeLiveTranscriptionSocket implements LiveTranscriptionSocket {
  _FakeLiveTranscriptionSocket({this.throwOnSend = false});

  final bool throwOnSend;
  final StreamController<Object?> _messages =
      StreamController<Object?>.broadcast();
  final List<String> sent = <String>[];

  @override
  Stream<Object?> get messages => _messages.stream;

  @override
  void send(String message) {
    if (throwOnSend) throw const SocketException('send failed');
    sent.add(message);
  }

  void receive(Map<String, Object?> message) {
    _messages.add(jsonEncode(message));
  }

  void flush(int untilMs) =>
      receive({'type': 'transcript.flushed', 'until_ms': untilMs});

  @override
  Future<void> close([int? code, String? reason]) async {}

  Future<void> dispose() async {
    if (!_messages.isClosed) await _messages.close();
  }
}

Future<void> _waitUntil(
  bool Function() condition, {
  Duration timeout = const Duration(seconds: 2),
}) async {
  final deadline = DateTime.now().add(timeout);
  while (!condition()) {
    if (DateTime.now().isAfter(deadline)) {
      throw TimeoutException('Condition was not reached before timeout.');
    }
    await Future<void>.delayed(const Duration(milliseconds: 5));
  }
}
