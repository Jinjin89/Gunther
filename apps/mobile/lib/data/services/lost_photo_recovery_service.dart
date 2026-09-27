import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:image_picker/image_picker.dart';
import 'package:path_provider/path_provider.dart';

import 'system_share_ingress_service.dart';

typedef LostPhotoRecoveryDirectoryProvider = Future<Directory> Function();

class LostPhotoCandidate {
  const LostPhotoCandidate({
    required this.path,
    required this.fileName,
    required this.mediaType,
    required this.sizeBytes,
  });

  final String path;
  final String fileName;
  final String mediaType;
  final int sizeBytes;
}

class LostPhotoResult {
  const LostPhotoResult({this.files = const [], this.errorCode});

  final List<LostPhotoCandidate> files;
  final String? errorCode;
}

abstract interface class LostPhotoPickerGateway {
  Future<LostPhotoResult> retrieveLostData();
}

class PluginLostPhotoPickerGateway implements LostPhotoPickerGateway {
  PluginLostPhotoPickerGateway([ImagePicker? picker])
    : _picker = picker ?? ImagePicker();

  final ImagePicker _picker;

  @override
  Future<LostPhotoResult> retrieveLostData() async {
    final response = await _picker.retrieveLostData();
    if (response.isEmpty) return const LostPhotoResult();
    final sourceFiles =
        response.files ?? [if (response.file != null) response.file!];
    final files = <LostPhotoCandidate>[];
    for (final file in sourceFiles) {
      files.add(
        LostPhotoCandidate(
          path: file.path,
          fileName: file.name,
          mediaType: file.mimeType ?? _mediaTypeForPhoto(file.name),
          sizeBytes: await file.length(),
        ),
      );
    }
    return LostPhotoResult(
      files: List.unmodifiable(files),
      errorCode: response.exception?.code,
    );
  }
}

class LostPhotoRecoveryException implements Exception {
  const LostPhotoRecoveryException(this.code);

  final String code;

  @override
  String toString() => 'Lost photo recovery issue: $code';
}

/// Durable Android handoff for image_picker.retrieveLostData().
///
/// image_picker's recovered XFiles are temporary. Every result is first copied
/// into Application Support and committed to this manifest. The copy is kept
/// until the main capture outbox acknowledges its independent durable copy.
class LostPhotoRecoveryService implements SystemShareIngress {
  LostPhotoRecoveryService({
    LostPhotoPickerGateway? picker,
    LostPhotoRecoveryDirectoryProvider? directoryProvider,
    bool? platformSupported,
  }) : _picker = picker ?? PluginLostPhotoPickerGateway(),
       _directoryProvider =
           directoryProvider ??
           (() async => Directory(
             '${(await getApplicationSupportDirectory()).path}/lost-photo-recovery',
           )),
       _platformSupported = platformSupported ?? Platform.isAndroid;

  static const int _manifestVersion = 1;
  static const int _maximumBytes = 512 * 1024 * 1024;

  final LostPhotoPickerGateway _picker;
  final LostPhotoRecoveryDirectoryProvider _directoryProvider;
  final bool _platformSupported;
  bool _retrievedThisProcess = false;
  Future<void> _barrier = Future.value();

  @override
  Future<List<SystemShareItem>> pendingItems() => _serialized(() async {
    final root = await _root();
    // Validate existing durable state before consuming image_picker's one-shot
    // recovery response. A damaged index must never orphan newly recovered data.
    await _readAndVerify(root);
    if (_platformSupported && !_retrievedThisProcess) {
      _retrievedThisProcess = true;
      final result = await _picker.retrieveLostData();
      var stageFailed = false;
      for (final candidate in result.files) {
        try {
          await _stage(root, candidate);
        } on Object {
          // Continue so one unreadable picker result cannot discard siblings.
          // The picker-owned source is never deleted by this service.
          stageFailed = true;
        }
      }
      if (result.errorCode case final code?) {
        throw LostPhotoRecoveryException(_safeIssueCode(code));
      }
      if (stageFailed) {
        throw const LostPhotoRecoveryException('recovered_file_unreadable');
      }
    }
    return _readAndVerify(root);
  });

  @override
  Future<void> acknowledge(List<String> ids) => _serialized(() async {
    if (ids.any((id) => !_safeId.hasMatch(id))) {
      throw const LostPhotoRecoveryException('invalid_acknowledgement');
    }
    final root = await _root();
    final current = await _readAndVerify(root);
    final idSet = ids.toSet();
    final kept = current.where((item) => !idSet.contains(item.id)).toList();
    final files = current
        .where((item) => idSet.contains(item.id))
        .map((item) => File(item.path!))
        .toList();
    // Manifest acknowledgement precedes cleanup; never leave a live record
    // pointing at a deleted recovered original.
    await _write(root, kept);
    for (final file in files) {
      if (_isOwnedFile(root, file) && await file.exists()) await file.delete();
    }
  });

  @override
  void setItemsAvailableHandler(Future<void> Function()? handler) {}

  Future<void> _stage(Directory root, LostPhotoCandidate candidate) async {
    final source = File(candidate.path);
    final type = await FileSystemEntity.type(source.path, followLinks: false);
    if (type != FileSystemEntityType.file ||
        candidate.sizeBytes < 0 ||
        candidate.sizeBytes > _maximumBytes) {
      throw const LostPhotoRecoveryException('invalid_recovered_file');
    }
    final safeName = _safeFileName(candidate.fileName);
    final mediaType = candidate.mediaType.startsWith('image/')
        ? candidate.mediaType
        : _mediaTypeForPhoto(safeName);
    final files = Directory('${root.path}/files');
    await files.create(recursive: true);
    final temporary = File(
      '${files.path}/.incoming-${DateTime.now().microsecondsSinceEpoch}.part',
    );
    final sink = temporary.openWrite();
    final digestSink = _SingleDigestSink();
    final converter = sha256.startChunkedConversion(digestSink);
    var sinkClosed = false;
    var converterClosed = false;
    var length = 0;
    try {
      await for (final chunk in source.openRead()) {
        length += chunk.length;
        if (length > _maximumBytes) {
          throw const LostPhotoRecoveryException('recovered_file_too_large');
        }
        converter.add(chunk);
        sink.add(chunk);
      }
      await sink.flush();
      await sink.close();
      sinkClosed = true;
      converter.close();
      converterClosed = true;
      if (length != candidate.sizeBytes) {
        throw const LostPhotoRecoveryException('recovered_file_changed');
      }
      final checksum = digestSink.value!.toString();
      final idSeed = utf8.encode('$checksum\u0000$safeName\u0000$mediaType');
      final id =
          'lost-photo-${sha256.convert(idSeed).toString().substring(0, 40)}';
      final extension = _safeExtension(safeName);
      final destination = File('${files.path}/$id$extension');
      if (!await destination.exists()) {
        await temporary.rename(destination.path);
      }
      final current = await _readAndVerify(root);
      if (current.any((item) => item.id == id)) return;
      await _write(root, [
        ...current,
        SystemShareItem(
          id: id,
          kind: SystemShareKind.file,
          path: destination.path,
          fileName: safeName,
          mediaType: mediaType,
          sizeBytes: length,
        ),
      ]);
    } finally {
      if (!converterClosed) converter.close();
      if (!sinkClosed) await sink.close();
      if (await temporary.exists()) await temporary.delete();
    }
  }

  Future<List<SystemShareItem>> _readAndVerify(Directory root) async {
    final manifest = File('${root.path}/manifest.json');
    if (!await manifest.exists()) return const [];
    try {
      final decoded = jsonDecode(await manifest.readAsString());
      if (decoded is! Map<String, Object?> ||
          decoded['version'] != _manifestVersion) {
        throw const FormatException('header');
      }
      final raw = decoded['entries'];
      if (raw is! List<Object?>) throw const FormatException('entries');
      final result = <SystemShareItem>[];
      for (final value in raw) {
        final item = SystemShareItem.fromPlatform(value);
        if (item.kind != SystemShareKind.file)
          throw const FormatException('kind');
        final file = File(item.path!);
        final type = await FileSystemEntity.type(file.path, followLinks: false);
        if (!_isOwnedFile(root, file) ||
            type != FileSystemEntityType.file ||
            await file.length() != item.sizeBytes) {
          throw const FormatException('original');
        }
        final checksum = await sha256.bind(file.openRead()).first;
        final expectedId =
            'lost-photo-${sha256.convert(utf8.encode('${checksum.toString()}\u0000${item.fileName}\u0000${item.mediaType}')).toString().substring(0, 40)}';
        if (item.id != expectedId) throw const FormatException('checksum');
        result.add(item);
      }
      return List.unmodifiable(result);
    } on Object catch (error) {
      throw LostPhotoRecoveryException('manifest_corrupt_${error.runtimeType}');
    }
  }

  Future<void> _write(Directory root, List<SystemShareItem> items) async {
    final manifest = File('${root.path}/manifest.json');
    final temporary = File(
      '${manifest.path}.${DateTime.now().microsecondsSinceEpoch}.tmp',
    );
    final entries = items
        .map(
          (item) => {
            'id': item.id,
            'kind': 'file',
            'path': item.path,
            'fileName': item.fileName,
            'mediaType': item.mediaType,
            'sizeBytes': item.sizeBytes,
          },
        )
        .toList();
    try {
      await temporary.writeAsString(
        jsonEncode({'version': _manifestVersion, 'entries': entries}),
        flush: true,
      );
      await temporary.rename(manifest.path);
    } finally {
      if (await temporary.exists()) await temporary.delete();
    }
  }

  Future<Directory> _root() async {
    final root = (await _directoryProvider()).absolute;
    await root.create(recursive: true);
    return root;
  }

  Future<T> _serialized<T>(Future<T> Function() action) {
    final result = _barrier.then((_) => action());
    _barrier = result.then<void>((_) {}, onError: (_, _) {});
    return result;
  }

  bool _isOwnedFile(Directory root, File file) {
    final filesRoot = Directory('${root.path}/files').absolute.path;
    return file.absolute.path.startsWith('$filesRoot${Platform.pathSeparator}');
  }
}

final RegExp _safeId = RegExp(r'^lost-photo-[a-f0-9]{40}$');

String _safeFileName(String value) {
  final leaf = value.split(RegExp(r'[/\\]')).last;
  final safe = leaf.replaceAll(RegExp(r'[\x00-\x1f\x7f]'), '_');
  if (safe.trim().isEmpty) return 'recovered-photo.jpg';
  return safe.length <= 180 ? safe : safe.substring(0, 180);
}

String _safeExtension(String fileName) {
  final dot = fileName.lastIndexOf('.');
  if (dot <= 0) return '';
  final extension = fileName.substring(dot).toLowerCase();
  return RegExp(r'^\.[a-z0-9]{1,12}$').hasMatch(extension) ? extension : '';
}

String _mediaTypeForPhoto(String fileName) {
  final name = fileName.toLowerCase();
  if (name.endsWith('.png')) return 'image/png';
  if (name.endsWith('.gif')) return 'image/gif';
  if (name.endsWith('.webp')) return 'image/webp';
  if (name.endsWith('.heic')) return 'image/heic';
  return 'image/jpeg';
}

String _safeIssueCode(String value) {
  final safe = value.toLowerCase().replaceAll(RegExp(r'[^a-z0-9_-]+'), '_');
  if (safe.isEmpty) return 'image_picker_error';
  return safe.length <= 80 ? safe : safe.substring(0, 80);
}

class _SingleDigestSink implements Sink<Digest> {
  Digest? value;

  @override
  void add(Digest data) => value = data;

  @override
  void close() {}
}
