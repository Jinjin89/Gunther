import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gunther_mobile/data/models/capture.dart';
import 'package:gunther_mobile/data/repositories/knowledge_repository.dart';
import 'package:gunther_mobile/data/services/knowledge_api_service.dart';

void main() {
  test(
    'web snapshots use the dedicated JSON endpoint and stable capture id',
    () async {
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final requests = <({String method, String path, Object? body})>[];
      var responseIndex = 0;
      final subscription = server.listen((request) async {
        final bodyText = await utf8.decoder.bind(request).join();
        requests.add((
          method: request.method,
          path: request.uri.path,
          body: jsonDecode(bodyText),
        ));
        request.response.statusCode = HttpStatus.created;
        request.response.headers.contentType = ContentType.json;
        request.response.write(
          jsonEncode(<String, Object?>{
            'asset': {'id': 'ast-web'},
            'importResult': {
              'source': {'id': 'src-web'},
            },
            'snapshot': {
              'originalUrl': 'https://example.com/article',
              'finalUrl': 'https://example.com/article',
              'status': 200,
            },
            'idempotentReplay': responseIndex > 0,
          }),
        );
        responseIndex += 1;
        await request.response.close();
      });
      final api = KnowledgeApiService(
        baseUrl: 'http://127.0.0.1:${server.port}/api/',
        bearerToken: 'paired-device-token',
      );
      final repository = RemoteKnowledgeRepository(apiService: api);
      addTearDown(() async {
        api.close();
        await subscription.cancel();
        await server.close(force: true);
      });
      final draft = WebSnapshotDraft(
        url: ' https://example.com/article ',
        title: 'Evidence article',
        notes: 'Read before the next lecture.',
        knowledgeBaseId: 'bioinformatics',
        clientCaptureId: 'capture_web_00000001',
      );

      final first = await repository.captureWebSnapshot(draft);
      final replay = await repository.captureWebSnapshot(draft);

      expect(first.sourceId, 'src-web');
      expect(first.idempotentReplay, isFalse);
      expect(replay.sourceId, 'src-web');
      expect(replay.idempotentReplay, isTrue);
      expect(requests, hasLength(2));
      expect(requests.map((request) => request.method), everyElement('POST'));
      expect(
        requests.map((request) => request.path),
        everyElement('/api/captures/web'),
      );
      expect(requests[0].body, requests[1].body);
      expect(requests.first.body, {
        'url': 'https://example.com/article',
        'title': 'Evidence article',
        'notes': 'Read before the next lecture.',
        'knowledgeBaseId': 'bioinformatics',
        'clientCaptureId': 'capture_web_00000001',
      });
      expect(requests.first.body, isNot(contains('content')));
    },
  );

  test('web snapshot URL validation accepts only complete http(s) URLs', () {
    expect(
      WebSnapshotDraft(url: 'https://example.com/path').url,
      'https://example.com/path',
    );
    expect(
      WebSnapshotDraft(url: 'http://127.0.0.1:8080/page').url,
      'http://127.0.0.1:8080/page',
    );
    for (final invalid in <String>[
      '',
      'example.com/article',
      'ftp://example.com/article',
      'https://',
      'https://example.com/has space',
    ]) {
      expect(
        () => WebSnapshotDraft(url: invalid),
        throwsA(isA<FormatException>()),
        reason: invalid,
      );
    }
  });

  test('web snapshot receipt depends only on the imported source id', () {
    final receipt = WebSnapshotReceipt.fromJson({
      'importResult': {
        'source': {'id': 'src-minimal'},
      },
      'asset': {'futureField': true},
      'snapshot': {'anotherFutureField': 42},
      'idempotentReplay': true,
      'unrelated': ['forward', 'compatible'],
    });

    expect(receipt.sourceId, 'src-minimal');
    expect(receipt.idempotentReplay, isTrue);
  });

  test(
    'recording upload retries the same chunk from the server offset',
    () async {
      final directory = await Directory.systemTemp.createTemp('recording-test');
      addTearDown(() => directory.delete(recursive: true));
      final file = await File(
        '${directory.path}/lecture.webm',
      ).writeAsBytes([1, 2, 3, 4, 5]);
      final asset = CapturedAsset(
        path: file.path,
        fileName: 'lecture.webm',
        mediaType: 'audio/webm',
        sizeBytes: 5,
      );
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final attempts =
          <({String method, Uri uri, String? checksum, List<int> bytes})>[];
      final subscription = server.listen((request) async {
        final bytes = await request.expand((chunk) => chunk).toList();
        attempts.add((
          method: request.method,
          uri: request.uri,
          checksum: request.headers.value('x-chunk-sha256'),
          bytes: bytes,
        ));
        request.response.headers.contentType = ContentType.json;
        if (attempts.length == 1) {
          request.response.statusCode = HttpStatus.serviceUnavailable;
          request.response.write('{"detail":"Chunk receipt unavailable"}');
        } else {
          request.response.write(
            jsonEncode(_recordingJson(sizeBytes: 5, sequence: 2)),
          );
        }
        await request.response.close();
      });
      final api = KnowledgeApiService(
        baseUrl: 'http://127.0.0.1:${server.port}/api/',
      );
      final repository = RemoteKnowledgeRepository(apiService: api);
      addTearDown(() async {
        api.close();
        await subscription.cancel();
        await server.close(force: true);
      });
      final partial = RecordingSession.fromJson(
        _recordingJson(sizeBytes: 2, sequence: 1),
      );

      await expectLater(
        repository.uploadRecordingFile(partial, asset),
        throwsA(
          isA<ApiException>().having(
            (error) => error.statusCode,
            'status',
            503,
          ),
        ),
      );
      final uploaded = await repository.uploadRecordingFile(partial, asset);
      expect(uploaded.sizeBytes, 5);
      expect(uploaded.nextExpectedSequence, 2);
      await repository.uploadRecordingFile(uploaded, asset);

      const suffix = [3, 4, 5];
      expect(attempts, hasLength(2));
      for (final attempt in attempts) {
        expect(attempt.method, 'PUT');
        expect(attempt.uri.path, '/api/recordings/recording-1/chunks');
        expect(attempt.uri.queryParameters, {'sequence': '1'});
        expect(attempt.bytes, suffix);
        expect(attempt.checksum, sha256.convert(suffix).toString());
      }

      await file.writeAsBytes([1, 2]);
      await expectLater(
        repository.uploadRecordingFile(uploaded, asset),
        throwsA(
          isA<ApiException>().having(
            (error) => error.statusCode,
            'status',
            409,
          ),
        ),
      );
      expect(attempts, hasLength(2));
    },
  );

  test(
    'recording checkpoints use the saved revision and optional fields',
    () async {
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final requests = <({String method, String path, Object? body})>[];
      final subscription = server.listen((request) async {
        requests.add((
          method: request.method,
          path: request.uri.path,
          body: jsonDecode(await utf8.decoder.bind(request).join()),
        ));
        request.response.headers.contentType = ContentType.json;
        request.response.write(
          jsonEncode(
            _recordingJson(sizeBytes: 5, sequence: 2, checkpointRevision: 4),
          ),
        );
        await request.response.close();
      });
      final api = KnowledgeApiService(
        baseUrl: 'http://127.0.0.1:${server.port}/api/',
      );
      final repository = RemoteKnowledgeRepository(apiService: api);
      addTearDown(() async {
        api.close();
        await subscription.cancel();
        await server.close(force: true);
      });
      final session = RecordingSession.fromJson(
        _recordingJson(sizeBytes: 5, sequence: 2, checkpointRevision: 3),
      );
      final saved = await repository.checkpointRecording(
        session,
        const RecordingCheckpointDraft(
          transcript: 'Checkpoint text',
          durationSeconds: 12,
          moments: [RecordingMoment(seconds: 5, label: 'A key detail')],
          recordingContext: 'memo',
        ),
      );

      final request = requests.single;
      expect(request.method, 'PATCH');
      expect(request.path, '/api/recordings/recording-1/checkpoint');
      expect(request.body, {
        'expectedRevision': 3,
        'transcript': 'Checkpoint text',
        'durationSeconds': 12,
        'moments': [
          {'seconds': 5, 'label': 'A key detail'},
        ],
        'recordingContext': 'memo',
        'knowledgeBaseId': null,
      });
      expect(saved.checkpointRevision, 4);
      expect(saved.knowledgeBaseId, isNull);
      expect(saved.recovery.checkpointedAt, isNull);
    },
  );
}

Map<String, Object?> _recordingJson({
  required int sizeBytes,
  required int sequence,
  int checkpointRevision = 0,
}) => {
  'id': 'recording-1',
  'title': 'Lecture',
  'status': 'capturing',
  'fileName': 'lecture.webm',
  'contentType': 'audio/webm',
  'sizeBytes': sizeBytes,
  'nextExpectedSequence': sequence,
  'checkpointRevision': checkpointRevision,
  'recovery': {
    'canResume': true,
    'audioAvailable': sizeBytes > 0,
    'nextExpectedSequence': sequence,
    'checkpointRevision': checkpointRevision,
  },
  'createdAt': '2026-01-01T00:00:00Z',
  'updatedAt': '2026-01-01T00:00:00Z',
  'storedAt': '2026-01-01T00:00:00Z',
};
