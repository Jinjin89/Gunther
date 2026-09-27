import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:gunther_mobile/data/models/capture.dart';
import 'package:image_picker/image_picker.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

import 'pcm_wav_file.dart';

abstract interface class CaptureDevice {
  Future<CapturedAsset?> pickDocument();
  Future<CapturedAsset?> pickAudio();
  Future<CapturedAsset?> pickPhoto(PhotoCaptureSource source);
  Future<String> startRecording(String title);
  Future<void> pauseRecording();
  Future<void> resumeRecording();
  Future<CapturedAsset> stopRecording();
  Future<void> dispose();
}

/// Optional capability implemented by capture devices that expose the same
/// PCM16 bytes that have already been durably appended to their local file.
///
/// Consumers may use the stream for best-effort live transcription. They must
/// never make recording persistence depend on the stream being connected.
abstract interface class LivePcmCaptureDevice {
  static const String encoding = 'pcm_s16le';

  Stream<Uint8List>? get livePcmStream;
  int get livePcmSampleRate;
  int get livePcmChannels;
  String get liveRecordingMediaType;
}

/// Small seam around the recording plugin so durable streaming can be tested
/// without a physical microphone.
abstract interface class CaptureAudioRecorder {
  Future<bool> hasPermission();
  Future<bool> isEncoderSupported(AudioEncoder encoder);
  Future<Stream<Uint8List>> startStream(RecordConfig config);
  Future<String?> stop();
  Future<void> pause();
  Future<void> resume();
  Future<void> dispose();
}

class PluginCaptureAudioRecorder implements CaptureAudioRecorder {
  PluginCaptureAudioRecorder([AudioRecorder? recorder])
    : _recorder = recorder ?? AudioRecorder();

  final AudioRecorder _recorder;

  @override
  Future<bool> hasPermission() => _recorder.hasPermission();

  @override
  Future<bool> isEncoderSupported(AudioEncoder encoder) =>
      _recorder.isEncoderSupported(encoder);

  @override
  Future<Stream<Uint8List>> startStream(RecordConfig config) =>
      _recorder.startStream(config);

  @override
  Future<String?> stop() => _recorder.stop();

  @override
  Future<void> pause() => _recorder.pause();

  @override
  Future<void> resume() => _recorder.resume();

  @override
  Future<void> dispose() => _recorder.dispose();
}

class CaptureDeviceException implements Exception {
  const CaptureDeviceException(this.message);

  final String message;

  @override
  String toString() => message;
}

class FlutterCaptureDevice implements CaptureDevice, LivePcmCaptureDevice {
  FlutterCaptureDevice({
    CaptureAudioRecorder? recorder,
    ImagePicker? imagePicker,
    Future<Directory> Function()? documentsDirectoryProvider,
  }) : _recorder = recorder ?? PluginCaptureAudioRecorder(),
       _imagePicker = imagePicker ?? ImagePicker(),
       _documentsDirectoryProvider =
           documentsDirectoryProvider ?? getApplicationDocumentsDirectory;

  static const _sampleRate = 24000;
  static const _channels = 1;

  final CaptureAudioRecorder _recorder;
  final ImagePicker _imagePicker;
  final Future<Directory> Function() _documentsDirectoryProvider;
  String? _recordingPath;
  PcmWavFile? _wavWriter;
  StreamController<Uint8List>? _persistedPcmController;
  StreamSubscription<Uint8List>? _sourceSubscription;
  Completer<void>? _streamDone;
  Object? _streamError;
  StackTrace? _streamErrorStack;

  @override
  Stream<Uint8List>? get livePcmStream => _persistedPcmController?.stream;

  @override
  int get livePcmSampleRate => _sampleRate;

  @override
  int get livePcmChannels => _channels;

  @override
  String get liveRecordingMediaType => 'audio/wav';

  @override
  Future<CapturedAsset?> pickDocument() async {
    final file = await FilePicker.pickFile(type: FileType.any);
    if (file == null) return null;
    return _platformFile(file);
  }

  @override
  Future<CapturedAsset?> pickAudio() async {
    final file = await FilePicker.pickFile(type: FileType.audio);
    if (file == null) return null;
    return _platformFile(file, fallbackMediaType: 'audio/mp4');
  }

  @override
  Future<CapturedAsset?> pickPhoto(PhotoCaptureSource source) async {
    final image = await _imagePicker.pickImage(
      source: source == PhotoCaptureSource.camera
          ? ImageSource.camera
          : ImageSource.gallery,
      requestFullMetadata: false,
    );
    if (image == null) return null;
    return CapturedAsset(
      path: image.path,
      fileName: image.name,
      mediaType: image.mimeType ?? _mediaTypeFor(image.name, 'image/jpeg'),
      sizeBytes: await image.length(),
    );
  }

  @override
  Future<String> startRecording(String title) async {
    if (_wavWriter != null) {
      throw const CaptureDeviceException('A recording is already in progress.');
    }
    if (!await _recorder.hasPermission()) {
      throw const CaptureDeviceException(
        'Microphone permission was not granted. Nothing was recorded.',
      );
    }
    if (!await _recorder.isEncoderSupported(AudioEncoder.pcm16bits)) {
      throw const CaptureDeviceException(
        'PCM16 recording is not supported on this device. Nothing was recorded.',
      );
    }
    final directory = Directory(
      '${(await _documentsDirectoryProvider()).path}/recordings',
    );
    await directory.create(recursive: true);
    final safeTitle = title
        .trim()
        .replaceAll(RegExp(r'[^a-zA-Z0-9_-]+'), '-')
        .replaceAll(RegExp(r'-+'), '-')
        .replaceAll(RegExp(r'^-|-$'), '');
    final stamp = DateTime.now().toUtc().microsecondsSinceEpoch;
    final path =
        '${directory.path}/${safeTitle.isEmpty ? 'recording' : safeTitle}-$stamp.wav';
    final writer = await PcmWavFile.create(
      path,
      sampleRate: _sampleRate,
      channels: _channels,
    );
    try {
      final source = await _recorder.startStream(
        const RecordConfig(
          encoder: AudioEncoder.pcm16bits,
          sampleRate: _sampleRate,
          numChannels: _channels,
          streamBufferSize: 4800,
        ),
      );
      final controller = StreamController<Uint8List>.broadcast();
      final done = Completer<void>();
      _recordingPath = writer.path;
      _wavWriter = writer;
      _persistedPcmController = controller;
      _streamDone = done;
      _streamError = null;
      _streamErrorStack = null;
      _sourceSubscription = source
          .asyncMap(writer.append)
          .listen(
            (accepted) {
              if (accepted.isNotEmpty && !controller.isClosed) {
                // These bytes are emitted only after their local file append has
                // completed. A slow or absent network can never block the writer.
                controller.add(accepted);
              }
            },
            onError: (Object exception, StackTrace stackTrace) {
              _streamError = exception;
              _streamErrorStack = stackTrace;
              if (!controller.isClosed) {
                controller.addError(exception, stackTrace);
              }
              if (!done.isCompleted) done.complete();
            },
            onDone: () {
              if (!done.isCompleted) done.complete();
            },
            cancelOnError: true,
          );
      return writer.path;
    } on Object {
      await writer.finish();
      final emptyFile = File(writer.path);
      if (await emptyFile.exists() &&
          await emptyFile.length() == PcmWavFile.headerBytes) {
        await emptyFile.delete();
      }
      rethrow;
    }
  }

  @override
  Future<void> pauseRecording() => _recorder.pause();

  @override
  Future<void> resumeRecording() => _recorder.resume();

  @override
  Future<CapturedAsset> stopRecording() async {
    final writer = _wavWriter;
    final path = _recordingPath;
    if (writer == null || path == null) {
      throw const CaptureDeviceException(
        'There is no active audio recording to stop.',
      );
    }

    Object? stopError;
    StackTrace? stopStack;
    try {
      await _recorder.stop().timeout(const Duration(seconds: 10));
      await _streamDone?.future.timeout(const Duration(seconds: 5));
    } on Object catch (exception, stackTrace) {
      stopError = exception;
      stopStack = stackTrace;
      await _sourceSubscription?.cancel();
    }
    final streamError = _streamError;
    final streamErrorStack = _streamErrorStack;
    final controller = _persistedPcmController;
    try {
      final file = await writer.finish();
      if (!await file.exists()) {
        throw const CaptureDeviceException(
          'The recorded audio file is missing.',
        );
      }
      if (stopError != null) {
        Error.throwWithStackTrace(
          CaptureDeviceException(
            'The microphone did not stop cleanly, but its recoverable WAV file remains on this device: $stopError',
          ),
          stopStack!,
        );
      }
      if (streamError != null) {
        Error.throwWithStackTrace(
          CaptureDeviceException(
            'The microphone stream ended unexpectedly, but its recoverable WAV file remains on this device: $streamError',
          ),
          streamErrorStack!,
        );
      }
      return CapturedAsset(
        path: writer.path,
        fileName: Uri.file(writer.path).pathSegments.last,
        mediaType: liveRecordingMediaType,
        sizeBytes: await file.length(),
      );
    } finally {
      await controller?.close();
      _recordingPath = null;
      _wavWriter = null;
      _persistedPcmController = null;
      _sourceSubscription = null;
      _streamDone = null;
      _streamError = null;
      _streamErrorStack = null;
    }
  }

  @override
  Future<void> dispose() async {
    if (_wavWriter != null) {
      try {
        await stopRecording();
      } on Object {
        // stopRecording finalizes the recoverable WAV before surfacing a
        // recorder error. App teardown must still release the plugin.
      }
    }
    await _recorder.dispose();
  }

  Future<CapturedAsset> _platformFile(
    PlatformFile file, {
    String fallbackMediaType = 'application/octet-stream',
  }) async {
    final path = file.path;
    if (path == null || path.isEmpty) {
      throw const CaptureDeviceException(
        'The selected file could not be read from this device.',
      );
    }
    return CapturedAsset(
      path: path,
      fileName: file.name,
      mediaType: _mediaTypeFor(file.name, fallbackMediaType),
      sizeBytes: await file.length(),
    );
  }
}

String _mediaTypeFor(String fileName, String fallback) {
  final normalized = fileName.toLowerCase();
  const types = <String, String>{
    '.aac': 'audio/aac',
    '.csv': 'text/csv',
    '.docx':
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.epub': 'application/epub+zip',
    '.gif': 'image/gif',
    '.heic': 'image/heic',
    '.html': 'text/html',
    '.jpeg': 'image/jpeg',
    '.jpg': 'image/jpeg',
    '.json': 'application/json',
    '.m4a': 'audio/mp4',
    '.md': 'text/markdown',
    '.mp3': 'audio/mpeg',
    '.mp4': 'audio/mp4',
    '.ogg': 'audio/ogg',
    '.pdf': 'application/pdf',
    '.png': 'image/png',
    '.txt': 'text/plain',
    '.wav': 'audio/wav',
    '.webm': 'audio/webm',
    '.webp': 'image/webp',
  };
  for (final entry in types.entries) {
    if (normalized.endsWith(entry.key)) return entry.value;
  }
  return fallback;
}
