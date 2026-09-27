import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:gunther_mobile/data/services/pcm_wav_file.dart';

void main() {
  test('writes a valid PCM16 WAV and joins odd stream chunks', () async {
    final root = await Directory.systemTemp.createTemp('gunther-pcm-wav-');
    addTearDown(() => root.delete(recursive: true));
    final file = File('${root.path}/lecture.wav');
    final writer = await PcmWavFile.create(file.path, flushIntervalBytes: 2);

    expect(await writer.append(Uint8List.fromList([1])), isEmpty);
    expect(await writer.append(Uint8List.fromList([2, 3, 4])), [1, 2, 3, 4]);
    expect(await writer.append(Uint8List.fromList([5, 6, 7])), [5, 6]);
    expect(await writer.append(Uint8List.fromList([8])), [7, 8]);
    await writer.finish();

    final bytes = await file.readAsBytes();
    final header = ByteData.sublistView(bytes, 0, PcmWavFile.headerBytes);
    expect(String.fromCharCodes(bytes.sublist(0, 4)), 'RIFF');
    expect(String.fromCharCodes(bytes.sublist(8, 12)), 'WAVE');
    expect(header.getUint32(4, Endian.little), 44);
    expect(header.getUint16(20, Endian.little), 1);
    expect(header.getUint16(22, Endian.little), 1);
    expect(header.getUint32(24, Endian.little), 24000);
    expect(header.getUint16(34, Endian.little), 16);
    expect(header.getUint32(40, Endian.little), 8);
    expect(bytes.sublist(PcmWavFile.headerBytes), [1, 2, 3, 4, 5, 6, 7, 8]);
  });

  test(
    'repairs an interrupted provisional header without changing PCM',
    () async {
      final root = await Directory.systemTemp.createTemp('gunther-pcm-repair-');
      addTearDown(() => root.delete(recursive: true));
      final file = File('${root.path}/interrupted.wav');
      final writer = await PcmWavFile.create(file.path);
      await writer.append(Uint8List.fromList([9, 8, 7, 6]));

      // Simulate the durable bytes of a process-ended stream: the provisional
      // header still reports zero even though PCM has reached the file.
      await writer.finish();
      final bytes = await file.readAsBytes();
      final provisional = Uint8List.fromList(bytes)
        ..fillRange(4, 8, 0)
        ..fillRange(40, 44, 0);
      await file.writeAsBytes(provisional, flush: true);

      final repaired = await PcmWavFile.repairHeader(file);
      final recovered = await file.readAsBytes();
      final header = ByteData.sublistView(recovered, 0, PcmWavFile.headerBytes);
      expect(repaired, 4);
      expect(header.getUint32(4, Endian.little), 40);
      expect(header.getUint32(40, Endian.little), 4);
      expect(recovered.sublist(PcmWavFile.headerBytes), [9, 8, 7, 6]);
    },
  );

  test('fails closed for a partial PCM16 sample during recovery', () async {
    final root = await Directory.systemTemp.createTemp('gunther-pcm-invalid-');
    addTearDown(() => root.delete(recursive: true));
    final file = File('${root.path}/invalid.wav');
    final writer = await PcmWavFile.create(file.path);
    await writer.finish();
    await file.writeAsBytes([1], mode: FileMode.append, flush: true);

    await expectLater(
      PcmWavFile.repairHeader(file),
      throwsA(isA<PcmWavException>()),
    );
    expect(await file.length(), PcmWavFile.headerBytes + 1);
  });

  test('does not rewrite an unrelated same-sized file', () async {
    final root = await Directory.systemTemp.createTemp('gunther-pcm-foreign-');
    addTearDown(() => root.delete(recursive: true));
    final file = File('${root.path}/foreign.wav');
    final original = Uint8List.fromList(
      List<int>.generate(48, (index) => index),
    );
    await file.writeAsBytes(original, flush: true);

    await expectLater(
      PcmWavFile.repairHeader(file),
      throwsA(isA<PcmWavException>()),
    );
    expect(await file.readAsBytes(), original);
  });

  test(
    'repairs the sparse boundary of a four-hour lecture recording',
    () async {
      final root = await Directory.systemTemp.createTemp('gunther-pcm-4h-');
      addTearDown(() => root.delete(recursive: true));
      final file = File('${root.path}/four-hours.wav');
      final writer = await PcmWavFile.create(file.path);
      await writer.finish();
      const dataBytes = 24000 * 2 * 4 * 60 * 60;
      final handle = await file.open(mode: FileMode.writeOnlyAppend);
      await handle.truncate(PcmWavFile.headerBytes + dataBytes);
      await handle.close();

      expect(await PcmWavFile.repairHeader(file), dataBytes);
      final headerBytes = await file
          .openRead(0, PcmWavFile.headerBytes)
          .fold(<int>[], (buffer, chunk) => buffer..addAll(chunk));
      final header = ByteData.sublistView(Uint8List.fromList(headerBytes));
      expect(header.getUint32(40, Endian.little), dataBytes);
      expect(header.getUint32(4, Endian.little), dataBytes + 36);
      expect(await file.length(), PcmWavFile.headerBytes + dataBytes);
    },
  );

  test(
    'rejects a sparse WAV beyond the RIFF limit without rewriting it',
    () async {
      final root = await Directory.systemTemp.createTemp('gunther-pcm-limit-');
      addTearDown(() => root.delete(recursive: true));
      final file = File('${root.path}/over-limit.wav');
      final writer = await PcmWavFile.create(file.path);
      await writer.finish();
      final originalHeader = await file.readAsBytes();
      final handle = await file.open(mode: FileMode.writeOnlyAppend);
      await handle.truncate(
        PcmWavFile.headerBytes + PcmWavFile.maxDataBytes + 1,
      );
      await handle.close();

      await expectLater(
        PcmWavFile.repairHeader(file),
        throwsA(isA<PcmWavException>()),
      );
      final header = await file
          .openRead(0, PcmWavFile.headerBytes)
          .fold<List<int>>(<int>[], (buffer, chunk) => buffer..addAll(chunk));
      expect(header, originalHeader);
      expect(
        await file.length(),
        PcmWavFile.headerBytes + PcmWavFile.maxDataBytes + 1,
      );
    },
  );
}
