import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:gunther_mobile/data/services/live_transcription_service.dart';

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
        'wss://knowledge.example/api/recordings/live?context=Bioinformatics+lecture',
      );
      expect(secure.webSocketUri.queryParameters, isNot(contains('token')));
      expect(
        ipv4.webSocketUri.toString(),
        'ws://127.0.0.1:8787/api/recordings/live',
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
            socket
              ..add(
                jsonEncode(<String, Object>{
                  'type': 'conversation.item.input_audio_transcription.delta',
                  'item_id': 'speech-1',
                  'delta': 'Hello',
                }),
              )
              ..add(
                jsonEncode(<String, Object>{
                  'type':
                      'conversation.item.input_audio_transcription.completed',
                  'item_id': 'speech-1',
                  'transcript': 'Hello Gunther',
                  'provider': 'sensevoice',
                  'start_seconds': 1.25,
                }),
              )
              ..add(
                jsonEncode(<String, Object>{
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
          events.whereType<LiveTranscriptionDeltaEvent>().single.delta,
          'Hello',
        );
        final completed = events
            .whereType<LiveTranscriptionCompletedEvent>()
            .single;
        expect(completed.transcript, 'Hello Gunther');
        expect(completed.startSeconds, 1.25);
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

  test(
    'bounds offline PCM and flushes it before commit after reconnect',
    () async {
      final socket = _FakeLiveTranscriptionSocket();
      var connections = 0;
      final requestedUris = <Uri>[];
      final requestedHeaders = <Map<String, Object>>[];
      final service = LiveTranscriptionService(
        baseUri: Uri.parse('https://knowledge.example/api/'),
        bearerToken: 'device-token',
        maxBufferedBytes: 8,
        reconnectDelay: (_) => Duration.zero,
        connector: (uri, headers) async {
          requestedUris.add(uri);
          requestedHeaders.add(headers);
          connections += 1;
          if (connections == 1) throw const SocketException('offline');
          return socket;
        },
      );
      final events = <LiveTranscriptionEvent>[];
      final subscription = service.events.listen(events.add);

      try {
        service.appendPcm(Uint8List.fromList(List<int>.generate(12, (i) => i)));
        service.commit();
        await expectLater(service.connect(), completes);
        await _waitUntil(() => connections == 2);
        socket.receive(<String, Object>{
          'type': 'service.ready',
          'provider': 'sensevoice',
          'model': 'local',
        });
        await _waitUntil(() => socket.sent.length == 2);

        expect(service.bufferedBytes, 0);
        expect(service.droppedBytes, 4);
        expect(requestedUris, everyElement(service.webSocketUri));
        expect(
          requestedHeaders,
          everyElement(<String, Object>{
            HttpHeaders.authorizationHeader: 'Bearer device-token',
          }),
        );
        final append = jsonDecode(socket.sent[0]) as Map<String, Object?>;
        final commit = jsonDecode(socket.sent[1]) as Map<String, Object?>;
        expect(base64Decode(append['audio']! as String), <int>[
          4,
          5,
          6,
          7,
          8,
          9,
          10,
          11,
        ]);
        expect(commit['type'], 'input_audio_buffer.commit');
        expect(
          events.whereType<LiveTranscriptionStatusEvent>().map(
            (event) => event.state,
          ),
          contains(LiveTranscriptionConnectionState.reconnecting),
        );
      } finally {
        await service.dispose();
        await subscription.cancel();
        await socket.dispose();
      }
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

  void receive(Map<String, Object> message) {
    _messages.add(jsonEncode(message));
  }

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
