import 'dart:io';
import 'dart:typed_data';

class PcmWavException implements Exception {
  const PcmWavException(this.message);

  final String message;

  @override
  String toString() => message;
}

/// Durable PCM16 mono WAV sink used by foreground capture.
///
/// Audio bytes are appended before they are offered to live transcription.
/// The initial header is intentionally recoverable: if the process ends before
/// [finish], [repairHeader] derives the authoritative data size from the file.
class PcmWavFile {
  PcmWavFile._({
    required this.path,
    required RandomAccessFile handle,
    required this.sampleRate,
    required this.channels,
    required int flushIntervalBytes,
  }) : _handle = handle,
       _flushIntervalBytes = flushIntervalBytes;

  static const int headerBytes = 44;
  static const int bitsPerSample = 16;
  static const int maxDataBytes = 0xffffffff - 36;

  final String path;
  final int sampleRate;
  final int channels;
  final RandomAccessFile _handle;
  final int _flushIntervalBytes;

  int _dataBytes = 0;
  int _bytesSinceFlush = 0;
  int? _pendingByte;
  bool _closed = false;

  int get dataBytes => _dataBytes;

  static Future<PcmWavFile> create(
    String path, {
    int sampleRate = 24000,
    int channels = 1,
    int? flushIntervalBytes,
  }) async {
    _validateFormat(sampleRate, channels);
    final file = File(path);
    await file.parent.create(recursive: true);
    final handle = await file.open(mode: FileMode.write);
    final sink = PcmWavFile._(
      path: file.absolute.path,
      handle: handle,
      sampleRate: sampleRate,
      channels: channels,
      flushIntervalBytes:
          flushIntervalBytes ?? sampleRate * channels * (bitsPerSample ~/ 8),
    );
    try {
      await handle.writeFrom(_header(sampleRate, channels, 0));
      await handle.flush();
      return sink;
    } on Object {
      await handle.close();
      rethrow;
    }
  }

  /// Appends complete PCM16 samples and returns the exact bytes accepted.
  ///
  /// A rare odd platform chunk is joined to the next chunk rather than writing
  /// a half sample. A final dangling byte is discarded by [finish].
  Future<Uint8List> append(Uint8List chunk) async {
    if (_closed) {
      throw const PcmWavException('The WAV recording is already finalized.');
    }
    if (chunk.isEmpty) return Uint8List(0);

    final combined = _pendingByte == null
        ? chunk
        : Uint8List.fromList([_pendingByte!, ...chunk]);
    _pendingByte = null;
    final alignedLength = combined.length - (combined.length % 2);
    if (alignedLength == 0) {
      _pendingByte = combined.single;
      return Uint8List(0);
    }
    if (combined.length != alignedLength) {
      _pendingByte = combined.last;
    }
    if (_dataBytes + alignedLength > maxDataBytes) {
      throw const PcmWavException(
        'The PCM recording exceeded the WAV size limit.',
      );
    }

    final accepted = Uint8List.sublistView(combined, 0, alignedLength);
    await _handle.writeFrom(accepted);
    _dataBytes += accepted.length;
    _bytesSinceFlush += accepted.length;
    if (_bytesSinceFlush >= _flushIntervalBytes) {
      await _handle.flush();
      _bytesSinceFlush = 0;
    }
    return Uint8List.fromList(accepted);
  }

  Future<File> finish() async {
    if (_closed) return File(path);
    _closed = true;
    _pendingByte = null;
    try {
      await _handle.setPosition(0);
      await _handle.writeFrom(_header(sampleRate, channels, _dataBytes));
      await _handle.flush();
    } finally {
      await _handle.close();
    }
    return File(path);
  }

  /// Repairs only a PCM16 WAV created with the expected capture format.
  /// Audio bytes are never rewritten or truncated.
  static Future<int> repairHeader(
    File file, {
    int sampleRate = 24000,
    int channels = 1,
  }) async {
    _validateFormat(sampleRate, channels);
    final type = await FileSystemEntity.type(file.path, followLinks: false);
    if (type != FileSystemEntityType.file) {
      throw const PcmWavException('The recoverable WAV file is missing.');
    }
    final length = await file.length();
    if (length < headerBytes) {
      throw const PcmWavException('The recoverable WAV header is incomplete.');
    }
    final dataBytes = length - headerBytes;
    if (dataBytes > maxDataBytes) {
      throw const PcmWavException(
        'The recoverable WAV exceeds the format limit.',
      );
    }
    if (dataBytes.isOdd) {
      throw const PcmWavException(
        'The recoverable WAV ends with a partial PCM16 sample.',
      );
    }

    final header = await file
        .openRead(0, headerBytes)
        .fold<List<int>>(<int>[], (buffer, chunk) => buffer..addAll(chunk));
    if (header.length != headerBytes) {
      throw const PcmWavException('The recoverable WAV header is incomplete.');
    }
    final headerBytesView = Uint8List.fromList(header);
    final headerData = ByteData.sublistView(headerBytesView);
    final expectedBlockAlign = channels * (bitsPerSample ~/ 8);
    if (!_hasAscii(headerBytesView, 0, 'RIFF') ||
        !_hasAscii(headerBytesView, 8, 'WAVE') ||
        !_hasAscii(headerBytesView, 12, 'fmt ') ||
        !_hasAscii(headerBytesView, 36, 'data') ||
        headerData.getUint32(16, Endian.little) != 16 ||
        headerData.getUint16(20, Endian.little) != 1 ||
        headerData.getUint16(22, Endian.little) != channels ||
        headerData.getUint32(24, Endian.little) != sampleRate ||
        headerData.getUint16(32, Endian.little) != expectedBlockAlign ||
        headerData.getUint16(34, Endian.little) != bitsPerSample) {
      throw const PcmWavException(
        'The recoverable file is not Gunther PCM16 WAV audio.',
      );
    }

    final handle = await file.open(mode: FileMode.writeOnlyAppend);
    try {
      await handle.setPosition(0);
      await handle.writeFrom(_header(sampleRate, channels, dataBytes));
      await handle.flush();
    } finally {
      await handle.close();
    }
    return dataBytes;
  }

  static void _validateFormat(int sampleRate, int channels) {
    if (sampleRate < 8000 || sampleRate > 192000) {
      throw const PcmWavException(
        'The PCM sample rate is outside the supported range.',
      );
    }
    if (channels < 1 || channels > 2) {
      throw const PcmWavException(
        'The PCM channel count is outside the supported range.',
      );
    }
  }

  static Uint8List _header(int sampleRate, int channels, int dataBytes) {
    final bytes = Uint8List(headerBytes);
    final data = ByteData.sublistView(bytes);
    _ascii(bytes, 0, 'RIFF');
    data.setUint32(4, 36 + dataBytes, Endian.little);
    _ascii(bytes, 8, 'WAVE');
    _ascii(bytes, 12, 'fmt ');
    data.setUint32(16, 16, Endian.little);
    data.setUint16(20, 1, Endian.little);
    data.setUint16(22, channels, Endian.little);
    data.setUint32(24, sampleRate, Endian.little);
    final blockAlign = channels * (bitsPerSample ~/ 8);
    data.setUint32(28, sampleRate * blockAlign, Endian.little);
    data.setUint16(32, blockAlign, Endian.little);
    data.setUint16(34, bitsPerSample, Endian.little);
    _ascii(bytes, 36, 'data');
    data.setUint32(40, dataBytes, Endian.little);
    return bytes;
  }

  static void _ascii(Uint8List target, int offset, String value) {
    for (var index = 0; index < value.length; index += 1) {
      target[offset + index] = value.codeUnitAt(index);
    }
  }

  static bool _hasAscii(Uint8List source, int offset, String value) {
    for (var index = 0; index < value.length; index += 1) {
      if (source[offset + index] != value.codeUnitAt(index)) return false;
    }
    return true;
  }
}
