import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:gunther_mobile/data/services/capture_device_service.dart';
import 'package:gunther_mobile/data/services/live_transcription_service.dart';
import 'package:gunther_mobile/data/services/pcm_wav_file.dart';
import 'package:record/record.dart';

void main() {
  test('persists PCM before publishing it and finalizes a WAV', () async {
    final root = await Directory.systemTemp.createTemp('gunther-capture-');
    addTearDown(() => root.delete(recursive: true));
    final recorder = FakeCaptureAudioRecorder();
    final device = FlutterCaptureDevice(
      recorder: recorder,
      documentsDirectoryProvider: () async => root,
    );
    addTearDown(device.dispose);

    final path = await device.startRecording('Biology lesson');
    expect(path, endsWith('.wav'));
    expect(recorder.config?.encoder, AudioEncoder.pcm16bits);
    expect(recorder.config?.sampleRate, 24000);
    expect(recorder.config?.numChannels, 1);

    final published = <int>[];
    final liveDone = Completer<void>();
    device.livePcmStream!.listen(published.addAll, onDone: liveDone.complete);
    recorder.add([1, 2, 3]);
    recorder.add([4, 5, 6]);
    await recorder.waitForEvents();

    final growing = await File(path).readAsBytes();
    expect(growing.sublist(44), [1, 2, 3, 4, 5, 6]);
    expect(published, [1, 2, 3, 4, 5, 6]);

    final asset = await device.stopRecording();
    await liveDone.future;
    expect(asset.mediaType, 'audio/wav');
    expect(asset.sizeBytes, 50);
    final complete = await File(path).readAsBytes();
    final header = ByteData.sublistView(complete, 0, 44);
    expect(header.getUint32(40, Endian.little), 6);
  });

  test(
    'a transcription listener is optional and never gates local audio',
    () async {
      final root = await Directory.systemTemp.createTemp('gunther-capture-');
      addTearDown(() => root.delete(recursive: true));
      final recorder = FakeCaptureAudioRecorder();
      final device = FlutterCaptureDevice(
        recorder: recorder,
        documentsDirectoryProvider: () async => root,
      );
      addTearDown(device.dispose);

      await device.startRecording('Offline');
      recorder.add([9, 8, 7, 6]);
      await recorder.waitForEvents();
      final asset = await device.stopRecording();

      expect((await File(asset.path).readAsBytes()).sublist(44), [9, 8, 7, 6]);
    },
  );

  test('an offline STT connection never prevents WAV persistence', () async {
    final root = await Directory.systemTemp.createTemp('gunther-capture-');
    addTearDown(() => root.delete(recursive: true));
    final recorder = FakeCaptureAudioRecorder();
    final device = FlutterCaptureDevice(
      recorder: recorder,
      documentsDirectoryProvider: () async => root,
    );
    final transcription = LiveTranscriptionService(
      baseUri: Uri.parse('https://offline.example/api/'),
      reconnectDelay: (_) => const Duration(hours: 1),
      connector: (_, _) async => throw const SocketException('STT offline'),
    );
    addTearDown(device.dispose);
    addTearDown(transcription.dispose);

    final path = await device.startRecording('Offline STT');
    final subscription = device.livePcmStream!.listen(transcription.appendPcm);
    addTearDown(subscription.cancel);
    await expectLater(transcription.connect(), completes);
    recorder.add([11, 12, 13, 14]);
    await recorder.waitForEvents();

    final asset = await device.stopRecording();
    final bytes = await File(path).readAsBytes();
    expect(asset.path, path);
    expect(bytes.sublist(PcmWavFile.headerBytes), [11, 12, 13, 14]);
    expect(ByteData.sublistView(bytes).getUint32(40, Endian.little), 4);
    expect(transcription.bufferedBytes, 4);
  });

  test(
    'a microphone stream error still finalizes recoverable WAV bytes',
    () async {
      final root = await Directory.systemTemp.createTemp('gunther-capture-');
      addTearDown(() => root.delete(recursive: true));
      final recorder = FakeCaptureAudioRecorder();
      final device = FlutterCaptureDevice(
        recorder: recorder,
        documentsDirectoryProvider: () async => root,
      );
      addTearDown(device.dispose);

      final path = await device.startRecording('Interrupted microphone');
      recorder.add([21, 22, 23, 24]);
      await recorder.waitForEvents();
      recorder.addError(const SocketException('microphone stream ended'));
      await recorder.waitForEvents();

      await expectLater(
        device.stopRecording(),
        throwsA(
          isA<CaptureDeviceException>().having(
            (error) => error.message,
            'message',
            contains('recoverable WAV file remains'),
          ),
        ),
      );
      final bytes = await File(path).readAsBytes();
      expect(bytes.sublist(PcmWavFile.headerBytes), [21, 22, 23, 24]);
      expect(ByteData.sublistView(bytes).getUint32(40, Endian.little), 4);
    },
  );

  test('fails before creating a file when PCM is unsupported', () async {
    final root = await Directory.systemTemp.createTemp('gunther-capture-');
    addTearDown(() => root.delete(recursive: true));
    final recorder = FakeCaptureAudioRecorder()..encoderSupported = false;
    final device = FlutterCaptureDevice(
      recorder: recorder,
      documentsDirectoryProvider: () async => root,
    );
    addTearDown(device.dispose);

    await expectLater(
      device.startRecording('Unsupported'),
      throwsA(isA<CaptureDeviceException>()),
    );
    expect(await root.list(recursive: true).toList(), isEmpty);
  });
}

class FakeCaptureAudioRecorder implements CaptureAudioRecorder {
  final StreamController<Uint8List> _controller =
      StreamController<Uint8List>.broadcast();
  bool permissionGranted = true;
  bool encoderSupported = true;
  bool disposed = false;
  RecordConfig? config;

  void add(List<int> bytes) => _controller.add(Uint8List.fromList(bytes));

  void addError(Object error) => _controller.addError(error);

  Future<void> waitForEvents() async {
    await Future<void>.delayed(Duration.zero);
    await Future<void>.delayed(Duration.zero);
  }

  @override
  Future<bool> hasPermission() async => permissionGranted;

  @override
  Future<bool> isEncoderSupported(AudioEncoder encoder) async =>
      encoderSupported;

  @override
  Future<Stream<Uint8List>> startStream(RecordConfig value) async {
    config = value;
    return _controller.stream;
  }

  @override
  Future<String?> stop() async {
    await _controller.close();
    return null;
  }

  @override
  Future<void> pause() async {}

  @override
  Future<void> resume() async {}

  @override
  Future<void> dispose() async {
    disposed = true;
    if (!_controller.isClosed) await _controller.close();
  }
}
