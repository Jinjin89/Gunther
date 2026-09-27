import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:gunther_mobile/data/models/capture.dart';
import 'package:gunther_mobile/data/services/recording_draft_store.dart';

void main() {
  test(
    'v2 store atomically round-trips and never deletes original audio',
    () async {
      final root = await _temporaryDirectory();
      final audio = File('${root.path}/lecture.m4a');
      await audio.writeAsBytes(List<int>.filled(32, 4), flush: true);
      final recoveryDirectory = Directory('${root.path}/recovery');
      final store = _store(recoveryDirectory);
      final draft = RecordingDraft(
        title: 'Systems lecture',
        context: 'lecture',
        transcript: 'First checkpoint',
        durationSeconds: 90,
        moments: const [RecordingMoment(seconds: 45, label: 'Important')],
        localPath: audio.path,
        fileName: 'lecture.m4a',
        mediaType: 'audio/mp4',
        sizeBytes: 32,
        phase: 'localOnly',
        serverRecordingId: 'rec-1',
        checkpointRevision: 7,
        updatedAt: DateTime.utc(2026, 8, 30),
      );

      await store.save(draft);

      final active = File('${recoveryDirectory.path}/active-recording.json');
      expect(await active.exists(), isTrue);
      expect(await File('${active.path}.tmp').exists(), isFalse);
      final persisted = jsonDecode(await active.readAsString());
      expect(persisted, isA<Map<String, Object?>>());
      expect((persisted as Map<String, Object?>)['version'], 2);

      final restored = await store.load();
      expect(restored?.title, 'Systems lecture');
      expect(restored?.serverRecordingId, 'rec-1');
      expect(restored?.checkpointRevision, 7);
      expect(restored?.moments.single.seconds, 45);
      expect(restored?.encoding, isNull);
      expect(restored?.targetWorkspaceId, isNull);
      expect(restored?.connectionProfileId, isNull);
      expect((await store.resolveLocalAsset(restored!))?.sizeBytes, 32);

      await store.clear();

      expect(await active.exists(), isFalse);
      expect(await audio.exists(), isTrue);
    },
  );

  test('v1 remains readable and its destination stays unassigned', () async {
    final root = await _temporaryDirectory();
    final audio = File('${root.path}/legacy.m4a');
    await audio.writeAsBytes(List<int>.filled(18, 7), flush: true);
    final recoveryDirectory = Directory('${root.path}/recovery');
    await recoveryDirectory.create();
    final legacyJson = _draftJson(
      version: 1,
      audio: audio,
      fileName: 'legacy.m4a',
      mediaType: 'audio/mp4',
      sizeBytes: 18,
      extra: {
        'knowledgeBaseId': 'legacy-library',
        // Unknown v2 fields injected into v1 must never silently bind it.
        'targetWorkspaceId': 'must-not-bind',
        'connectionProfileId': 'must-not-bind',
      },
    );
    await File(
      '${recoveryDirectory.path}/active-recording.json',
    ).writeAsString(jsonEncode(legacyJson), flush: true);

    final store = _store(recoveryDirectory);
    final restored = await store.load();

    expect(restored, isNotNull);
    expect(restored?.knowledgeBaseId, 'legacy-library');
    expect(restored?.encoding, isNull);
    expect(restored?.sampleRate, isNull);
    expect(restored?.channels, isNull);
    expect(restored?.targetWorkspaceId, isNull);
    expect(restored?.connectionProfileId, isNull);
    expect((await store.resolveLocalAsset(restored!))?.sizeBytes, 18);
  });

  test('v2 repairs only an explicitly marked Gunther PCM16 WAV', () async {
    final root = await _temporaryDirectory();
    final audio = File('${root.path}/interrupted.wav');
    final interrupted = _interruptedPcm16Wav(dataBytes: 12);
    await audio.writeAsBytes(interrupted, flush: true);
    final recoveryDirectory = Directory('${root.path}/recovery');
    final store = _store(recoveryDirectory);
    final draft = RecordingDraft(
      title: 'Interrupted lecture',
      context: 'lecture',
      transcript: '',
      durationSeconds: 1,
      moments: const [],
      localPath: audio.path,
      fileName: 'interrupted.wav',
      mediaType: 'audio/wav',
      // An in-progress checkpoint may precede already-flushed append-only PCM.
      sizeBytes: 44,
      phase: 'recording',
      checkpointRevision: 1,
      updatedAt: DateTime.utc(2026, 8, 30),
      encoding: RecordingDraft.guntherPcm16WavEncoding,
      sampleRate: 24000,
      channels: 1,
      targetWorkspaceId: 'workspace-local',
      connectionProfileId: 'profile-local',
    );

    await store.save(draft);
    final restored = await store.load();

    expect(restored, isNotNull);
    expect(restored?.isGuntherPcm16Wav, isTrue);
    expect(restored?.sampleRate, 24000);
    expect(restored?.channels, 1);
    expect(restored?.targetWorkspaceId, 'workspace-local');
    expect(restored?.connectionProfileId, 'profile-local');
    final repaired = Uint8List.fromList(await audio.readAsBytes());
    final header = ByteData.sublistView(repaired);
    expect(repaired.length, interrupted.length);
    expect(header.getUint32(4, Endian.little), repaired.length - 8);
    expect(header.getUint32(40, Endian.little), repaired.length - 44);
  });

  test('an imported WAV without Gunther metadata is never rewritten', () async {
    final root = await _temporaryDirectory();
    final audio = File('${root.path}/imported.wav');
    final original = _interruptedPcm16Wav(dataBytes: 10);
    await audio.writeAsBytes(original, flush: true);
    final recoveryDirectory = Directory('${root.path}/recovery');
    final store = _store(recoveryDirectory);
    final draft = RecordingDraft(
      title: 'Imported interview',
      context: 'meeting',
      transcript: '',
      durationSeconds: 0,
      moments: const [],
      localPath: audio.path,
      fileName: 'imported.wav',
      mediaType: 'audio/wav',
      sizeBytes: original.length,
      phase: 'localOnly',
      checkpointRevision: 0,
      updatedAt: DateTime.utc(2026, 8, 30),
    );

    await store.save(draft);
    expect(await store.load(), isNotNull);

    expect(await audio.readAsBytes(), orderedEquals(original));
  });

  test('Gunther WAV recovery fails closed on a symbolic-link path', () async {
    final root = await _temporaryDirectory();
    final original = File('${root.path}/original.wav');
    final originalBytes = _interruptedPcm16Wav(dataBytes: 8);
    await original.writeAsBytes(originalBytes, flush: true);
    final linked = Link('${root.path}/linked.wav');
    await linked.create(original.path);
    final recoveryDirectory = Directory('${root.path}/recovery');
    final store = _store(recoveryDirectory);
    final draft = RecordingDraft(
      title: 'Linked recording',
      context: 'memo',
      transcript: '',
      durationSeconds: 0,
      moments: const [],
      localPath: linked.path,
      fileName: 'linked.wav',
      mediaType: 'audio/wav',
      sizeBytes: originalBytes.length,
      phase: 'localOnly',
      checkpointRevision: 0,
      updatedAt: DateTime.utc(2026, 8, 30),
      encoding: RecordingDraft.guntherPcm16WavEncoding,
      sampleRate: 24000,
      channels: 1,
    );

    await store.save(draft);

    expect(await store.load(), isNull);
    expect(await store.resolveLocalAsset(draft), isNull);
    expect(await original.readAsBytes(), orderedEquals(originalBytes));
  });

  test(
    'finalized draft resolution fails closed on a length mismatch',
    () async {
      final root = await _temporaryDirectory();
      final audio = File('${root.path}/changed.m4a');
      await audio.writeAsBytes(List<int>.filled(12, 9), flush: true);
      final store = _store(Directory('${root.path}/recovery'));
      final draft = RecordingDraft(
        title: 'Changed recording',
        context: 'memo',
        transcript: '',
        durationSeconds: 0,
        moments: const [],
        localPath: audio.path,
        fileName: 'changed.m4a',
        mediaType: 'audio/mp4',
        sizeBytes: 11,
        phase: 'localOnly',
        checkpointRevision: 0,
        updatedAt: DateTime.utc(2026, 8, 30),
      );

      expect(await store.resolveLocalAsset(draft), isNull);
    },
  );

  test('incomplete Gunther PCM metadata is rejected', () {
    final json = _draftJson(
      version: 2,
      audio: File('/tmp/incomplete.wav'),
      fileName: 'incomplete.wav',
      mediaType: 'audio/wav',
      sizeBytes: 44,
      extra: {'encoding': RecordingDraft.guntherPcm16WavEncoding},
    );

    expect(() => RecordingDraft.fromJson(json), throwsFormatException);
  });
}

FileRecordingDraftStore _store(Directory directory) {
  return FileRecordingDraftStore(directoryProvider: () async => directory);
}

Future<Directory> _temporaryDirectory() async {
  final directory = await Directory.systemTemp.createTemp(
    'gunther-draft-store-',
  );
  addTearDown(() async {
    if (await directory.exists()) await directory.delete(recursive: true);
  });
  return directory;
}

Map<String, Object?> _draftJson({
  required int version,
  required File audio,
  required String fileName,
  required String mediaType,
  required int sizeBytes,
  Map<String, Object?> extra = const {},
}) {
  return {
    'version': version,
    'title': 'Recovered recording',
    'context': 'memo',
    'transcript': '',
    'durationSeconds': 0,
    'moments': <Object?>[],
    'localPath': audio.path,
    'fileName': fileName,
    'mediaType': mediaType,
    'sizeBytes': sizeBytes,
    'phase': 'localOnly',
    'knowledgeBaseId': null,
    'serverRecordingId': null,
    'checkpointRevision': 0,
    'updatedAt': DateTime.utc(2026, 8, 30).toIso8601String(),
    ...extra,
  };
}

Uint8List _interruptedPcm16Wav({
  int sampleRate = 24000,
  int channels = 1,
  required int dataBytes,
}) {
  final bytes = Uint8List(44 + dataBytes);
  final data = ByteData.sublistView(bytes);
  _writeAscii(bytes, 0, 'RIFF');
  data.setUint32(4, 36, Endian.little);
  _writeAscii(bytes, 8, 'WAVE');
  _writeAscii(bytes, 12, 'fmt ');
  data.setUint32(16, 16, Endian.little);
  data.setUint16(20, 1, Endian.little);
  data.setUint16(22, channels, Endian.little);
  data.setUint32(24, sampleRate, Endian.little);
  final blockAlign = channels * 2;
  data.setUint32(28, sampleRate * blockAlign, Endian.little);
  data.setUint16(32, blockAlign, Endian.little);
  data.setUint16(34, 16, Endian.little);
  _writeAscii(bytes, 36, 'data');
  data.setUint32(40, 0, Endian.little);
  for (var index = 44; index < bytes.length; index += 1) {
    bytes[index] = index & 0xff;
  }
  return bytes;
}

void _writeAscii(Uint8List target, int offset, String value) {
  for (var index = 0; index < value.length; index += 1) {
    target[offset + index] = value.codeUnitAt(index);
  }
}
