import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

class ApiException implements Exception {
  const ApiException(this.message, this.statusCode);

  final String message;
  final int statusCode;

  @override
  String toString() => message;
}

class KnowledgeApiService {
  KnowledgeApiService({
    required String baseUrl,
    String apiToken = '',
    String bearerToken = '',
    HttpClient? client,
  }) : _baseUrl = Uri.parse(baseUrl.endsWith('/') ? baseUrl : '$baseUrl/'),
       _apiToken = apiToken,
       _bearerToken = bearerToken,
       _client =
           client ??
           (HttpClient()..connectionTimeout = const Duration(seconds: 10)) {
    if (apiToken.isNotEmpty && bearerToken.isNotEmpty) {
      throw ArgumentError(
        'Sidecar and device credentials cannot be used at the same time.',
      );
    }
  }

  final Uri _baseUrl;
  final String _apiToken;
  final String _bearerToken;
  final HttpClient _client;

  void _applyCommonHeaders(HttpClientRequest request) {
    request.headers.set(HttpHeaders.acceptHeader, 'application/json');
    if (_apiToken.isNotEmpty) {
      request.headers.set('X-Gunther-Token', _apiToken);
    }
    if (_bearerToken.isNotEmpty) {
      request.headers.set(
        HttpHeaders.authorizationHeader,
        'Bearer $_bearerToken',
      );
    }
  }

  Future<Object?> get(String path) => _request('GET', path);

  Future<Object?> post(String path, Map<String, Object?> body) =>
      _request('POST', path, body: body);

  Future<Object?> patch(String path, Map<String, Object?> body) =>
      _request('PATCH', path, body: body);

  Future<Object?> postEmpty(
    String path, {
    Map<String, String> headers = const {},
  }) => _request('POST', path, headers: headers);

  Future<Object?> putBytes(
    String path,
    Uint8List bytes, {
    Map<String, String> headers = const {},
  }) async {
    final request = await _client.openUrl('PUT', _baseUrl.resolve(path));
    _applyCommonHeaders(request);
    for (final entry in headers.entries) {
      request.headers.set(entry.key, entry.value);
    }
    request.contentLength = bytes.length;
    request.add(bytes);
    return _readResponse(await request.close());
  }

  Future<Object?> uploadFile(
    String path,
    File file, {
    required String contentType,
  }) async {
    final request = await _client.openUrl('POST', _baseUrl.resolve(path));
    _applyCommonHeaders(request);
    request.headers.set(HttpHeaders.contentTypeHeader, contentType);
    request.contentLength = await file.length();
    await request.addStream(file.openRead());
    return _readResponse(await request.close());
  }

  Future<Object?> _request(
    String method,
    String path, {
    Map<String, Object?>? body,
    Map<String, String> headers = const {},
  }) async {
    final request = await _client.openUrl(method, _baseUrl.resolve(path));
    _applyCommonHeaders(request);
    for (final entry in headers.entries) {
      request.headers.set(entry.key, entry.value);
    }
    if (body != null) {
      request.headers.contentType = ContentType.json;
      request.write(jsonEncode(body));
    }

    return _readResponse(await request.close());
  }

  Future<Object?> _readResponse(HttpClientResponse response) async {
    final text = await utf8.decoder.bind(response).join();
    final decoded = text.isEmpty ? null : jsonDecode(text) as Object?;
    if (response.statusCode < 200 || response.statusCode >= 300) {
      final message = decoded is Map<String, Object?>
          ? decoded['detail']?.toString() ?? 'Request failed'
          : 'Request failed';
      throw ApiException(message, response.statusCode);
    }
    return decoded;
  }

  void close() => _client.close(force: true);
}
