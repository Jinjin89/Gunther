import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:gunther_mobile/data/services/knowledge_api_service.dart';

void main() {
  test('applies the configured token to every request transport', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final temporary = await Directory.systemTemp.createTemp(
      'gunther-mobile-api-test-',
    );
    final upload = File('${temporary.path}/sample.bin');
    await upload.writeAsBytes([7, 8, 9], flush: true);
    final requests = <({String method, String path, String? token})>[];
    final customHeaders = <String?>[];
    final bodies = <List<int>>[];
    final subscription = server.listen((request) async {
      requests.add((
        method: request.method,
        path: request.uri.path,
        token: request.headers.value('X-Gunther-Token'),
      ));
      customHeaders.add(request.headers.value('X-Chunk-Sha256'));
      bodies.add(
        await request.fold<List<int>>(
          <int>[],
          (buffer, chunk) => buffer..addAll(chunk),
        ),
      );
      request.response.headers.contentType = ContentType.json;
      request.response.write('{}');
      await request.response.close();
    });
    final service = KnowledgeApiService(
      baseUrl: 'http://127.0.0.1:${server.port}/api/',
      apiToken: 'test-token-123',
    );

    try {
      await service.get('health');
      await service.post('notes', const {'title': 'Captured'});
      await service.patch('notes/note-1', const {'status': 'filed'});
      await service.postEmpty('recordings/recording-1/complete');
      await service.putBytes(
        'recordings/recording-1/chunks?seq=0',
        Uint8List.fromList([1, 2, 3]),
        headers: const {'X-Chunk-Sha256': 'abc123'},
      );
      await service.uploadFile(
        'captures/assets?title=Sample',
        upload,
        contentType: 'application/octet-stream',
      );

      expect(requests, hasLength(6));
      expect(requests.map((request) => request.token).toSet(), {
        'test-token-123',
      });
      expect(requests.map((request) => request.method), [
        'GET',
        'POST',
        'PATCH',
        'POST',
        'PUT',
        'POST',
      ]);
      expect(requests.map((request) => request.path), [
        '/api/health',
        '/api/notes',
        '/api/notes/note-1',
        '/api/recordings/recording-1/complete',
        '/api/recordings/recording-1/chunks',
        '/api/captures/assets',
      ]);
      expect(customHeaders[4], 'abc123');
      expect(utf8.decode(bodies[1]), jsonEncode({'title': 'Captured'}));
      expect(bodies[4], [1, 2, 3]);
      expect(bodies[5], [7, 8, 9]);
    } finally {
      service.close();
      await subscription.cancel();
      await server.close(force: true);
      await temporary.delete(recursive: true);
    }
  });

  test('sends device credentials only in the Authorization header', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final requestSeen = Completer<HttpRequest>();
    final subscription = server.listen((request) async {
      if (!requestSeen.isCompleted) requestSeen.complete(request);
      request.response.headers.contentType = ContentType.json;
      request.response.write('{}');
      await request.response.close();
    });
    final service = KnowledgeApiService(
      baseUrl: 'http://127.0.0.1:${server.port}/api/',
      bearerToken: 'paired-device-token',
    );

    try {
      await service.get('health');
      final request = await requestSeen.future;
      expect(
        request.headers.value(HttpHeaders.authorizationHeader),
        'Bearer paired-device-token',
      );
      expect(request.headers.value('X-Gunther-Token'), isNull);
      expect(request.uri.queryParameters, isNot(contains('token')));
    } finally {
      service.close();
      await subscription.cancel();
      await server.close(force: true);
    }
  });

  test('rejects mixed sidecar and device credentials', () {
    expect(
      () => KnowledgeApiService(
        baseUrl: 'https://knowledge.example/api/',
        apiToken: 'sidecar',
        bearerToken: 'device',
      ),
      throwsArgumentError,
    );
  });
}
