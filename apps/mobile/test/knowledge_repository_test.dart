import 'dart:convert';
import 'dart:io';

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
}
