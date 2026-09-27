import 'dart:convert';
import 'dart:io';

import 'package:gunther_mobile/data/models/capture.dart';
import 'package:gunther_mobile/data/services/pcm_wav_file.dart';
import 'package:path_provider/path_provider.dart';

/// The durable, device-local handoff between foreground recording and upload.
///
/// A draft points at the original audio file; the store never deletes that
/// file. Cleanup only removes the small JSON recovery record after the server
/// has completed the recording and the corresponding Source has been created.
class RecordingDraft {
  static const currentVersion = 2;
  static const guntherPcm16WavEncoding = 'gunther-pcm16-wav';

  const RecordingDraft({
    required this.title,
    required this.context,
    required this.transcript,
    required this.durationSeconds,
    required this.moments,
    required this.localPath,
    required this.fileName,
    required this.mediaType,
    required this.sizeBytes,
    required this.phase,
    required this.checkpointRevision,
    required this.updatedAt,
    this.knowledgeBaseId,
    this.serverRecordingId,
    this.encoding,
    this.sampleRate,
    this.channels,
    this.targetWorkspaceId,
    this.connectionProfileId,
  });

  factory RecordingDraft.fromJson(Map<String, Object?> json) {
    final version = json['version'];
    if (version != 1 && version != currentVersion) {
      throw const FormatException('Unsupported recording draft version.');
    }
    final rawMoments = json['moments'];
    if (rawMoments is! List<Object?>) {
      throw const FormatException('Recording draft moments are invalid.');
    }
    final encoding = version == currentVersion
        ? _optionalStrictString(json, 'encoding')
        : null;
    final sampleRate = version == currentVersion
        ? _optionalPositiveInt(json, 'sampleRate')
        : null;
    final channels = version == currentVersion
        ? _optionalPositiveInt(json, 'channels')
        : null;
    _validateAudioMetadata(
      encoding: encoding,
      sampleRate: sampleRate,
      channels: channels,
    );

    return RecordingDraft(
      title: _requiredString(json, 'title'),
      context: _requiredString(json, 'context'),
      transcript: json['transcript']?.toString() ?? '',
      durationSeconds: _requiredNonNegativeInt(json, 'durationSeconds'),
      moments: rawMoments
          .map((item) {
            if (item is! Map<String, Object?>) {
              throw const FormatException(
                'A recording draft moment is invalid.',
              );
            }
            return RecordingMoment.fromJson(item);
          })
          .toList(growable: false),
      localPath: _requiredString(json, 'localPath'),
      fileName: _requiredString(json, 'fileName'),
      mediaType: _requiredString(json, 'mediaType'),
      sizeBytes: _requiredNonNegativeInt(json, 'sizeBytes'),
      phase: _requiredString(json, 'phase'),
      knowledgeBaseId: _optionalString(json['knowledgeBaseId']),
      serverRecordingId: _optionalString(json['serverRecordingId']),
      checkpointRevision: _requiredNonNegativeInt(json, 'checkpointRevision'),
      updatedAt: DateTime.parse(_requiredString(json, 'updatedAt')),
      encoding: encoding,
      sampleRate: sampleRate,
      channels: channels,
      targetWorkspaceId: version == currentVersion
          ? _optionalStrictString(json, 'targetWorkspaceId')
          : null,
      connectionProfileId: version == currentVersion
          ? _optionalStrictString(json, 'connectionProfileId')
          : null,
    );
  }

  final String title;
  final String context;
  final String transcript;
  final int durationSeconds;
  final List<RecordingMoment> moments;
  final String localPath;
  final String fileName;
  final String mediaType;
  final int sizeBytes;
  final String phase;
  final String? knowledgeBaseId;
  final String? serverRecordingId;
  final int checkpointRevision;
  final DateTime updatedAt;
  final String? encoding;
  final int? sampleRate;
  final int? channels;

  /// The destination is deliberately unassigned when absent. In particular,
  /// v1 drafts are never silently bound to the current workspace or network.
  final String? targetWorkspaceId;
  final String? connectionProfileId;

  bool get isGuntherPcm16Wav => encoding == guntherPcm16WavEncoding;

  Map<String, Object?> toJson() {
    _validateAudioMetadata(
      encoding: encoding,
      sampleRate: sampleRate,
      channels: channels,
    );
    return {
      'version': currentVersion,
      'title': title,
      'context': context,
      'transcript': transcript,
      'durationSeconds': durationSeconds,
      'moments': moments.map((moment) => moment.toJson()).toList(),
      'localPath': localPath,
      'fileName': fileName,
      'mediaType': mediaType,
      'sizeBytes': sizeBytes,
      'phase': phase,
      'knowledgeBaseId': knowledgeBaseId,
      'serverRecordingId': serverRecordingId,
      'checkpointRevision': checkpointRevision,
      'updatedAt': updatedAt.toUtc().toIso8601String(),
      if (encoding != null) 'encoding': encoding,
      if (sampleRate != null) 'sampleRate': sampleRate,
      if (channels != null) 'channels': channels,
      if (targetWorkspaceId != null) 'targetWorkspaceId': targetWorkspaceId,
      if (connectionProfileId != null)
        'connectionProfileId': connectionProfileId,
    };
  }
}

abstract interface class RecordingDraftStore {
  Future<RecordingDraft?> load();
  Future<CapturedAsset?> resolveLocalAsset(RecordingDraft draft);
  Future<void> save(RecordingDraft draft);
  Future<void> clear();
}

class FileRecordingDraftStore implements RecordingDraftStore {
  FileRecordingDraftStore({Future<Directory> Function()? directoryProvider})
    : _directoryProvider =
          directoryProvider ??
          (() async {
            final support = await getApplicationSupportDirectory();
            return Directory('${support.path}/recording-recovery');
          });

  static const _fileName = 'active-recording.json';
  final Future<Directory> Function() _directoryProvider;

  @override
  Future<RecordingDraft?> load() async {
    final file = await _draftFile(createDirectory: false);
    if (!await file.exists()) return null;
    try {
      final decoded = jsonDecode(await file.readAsString());
      if (decoded is! Map<String, Object?>) return null;
      final draft = RecordingDraft.fromJson(decoded);
      if (draft.isGuntherPcm16Wav) {
        final audio = await _resolveVerifiedFile(draft);
        if (audio == null) return null;
        await PcmWavFile.repairHeader(
          audio,
          sampleRate: draft.sampleRate!,
          channels: draft.channels!,
        );
      }
      return draft;
    } on Object {
      // A damaged draft must not crash app startup or erase the audio it points
      // to. A later support flow can inspect the JSON and original file.
      return null;
    }
  }

  @override
  Future<CapturedAsset?> resolveLocalAsset(RecordingDraft draft) async {
    final file = await _resolveVerifiedFile(draft);
    if (file == null) return null;
    return CapturedAsset(
      path: draft.localPath,
      fileName: draft.fileName,
      mediaType: draft.mediaType,
      sizeBytes: await file.length(),
    );
  }

  @override
  Future<void> save(RecordingDraft draft) async {
    final file = await _draftFile(createDirectory: true);
    final temporary = File('${file.path}.tmp');
    await temporary.writeAsString(jsonEncode(draft.toJson()), flush: true);
    await temporary.rename(file.path);
  }

  @override
  Future<void> clear() async {
    final file = await _draftFile(createDirectory: false);
    if (await file.exists()) await file.delete();
    final temporary = File('${file.path}.tmp');
    if (await temporary.exists()) await temporary.delete();
  }

  Future<File> _draftFile({required bool createDirectory}) async {
    final directory = await _directoryProvider();
    if (createDirectory) await directory.create(recursive: true);
    return File('${directory.path}/$_fileName');
  }

  Future<File?> _resolveVerifiedFile(RecordingDraft draft) async {
    try {
      if (!_isSafeAbsolutePath(draft.localPath, draft.fileName)) return null;
      final type = await FileSystemEntity.type(
        draft.localPath,
        followLinks: false,
      );
      if (type != FileSystemEntityType.file) return null;
      final file = File(draft.localPath);
      final length = await file.length();
      if (!_lengthMatchesDraft(draft, length)) return null;
      return file;
    } on FileSystemException {
      return null;
    } on ArgumentError {
      return null;
    }
  }
}

String _requiredString(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value is! String || value.isEmpty) {
    throw FormatException('Recording draft field "$key" is invalid.');
  }
  return value;
}

int _requiredNonNegativeInt(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value is! int || value < 0) {
    throw FormatException('Recording draft field "$key" is invalid.');
  }
  return value;
}

String? _optionalString(Object? value) {
  final text = value?.toString();
  return text == null || text.isEmpty ? null : text;
}

String? _optionalStrictString(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value == null) return null;
  if (value is! String || value.trim().isEmpty) {
    throw FormatException('Recording draft field "$key" is invalid.');
  }
  return value;
}

int? _optionalPositiveInt(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value == null) return null;
  if (value is! int || value <= 0) {
    throw FormatException('Recording draft field "$key" is invalid.');
  }
  return value;
}

void _validateAudioMetadata({
  required String? encoding,
  required int? sampleRate,
  required int? channels,
}) {
  if (encoding == null) {
    if (sampleRate != null || channels != null) {
      throw const FormatException(
        'Recording draft sample metadata requires an explicit encoding.',
      );
    }
    return;
  }
  if (sampleRate != null && (sampleRate < 8000 || sampleRate > 192000)) {
    throw const FormatException('Recording draft sample rate is invalid.');
  }
  if (channels != null && (channels < 1 || channels > 2)) {
    throw const FormatException('Recording draft channel count is invalid.');
  }
  if (encoding == RecordingDraft.guntherPcm16WavEncoding &&
      (sampleRate == null || channels == null)) {
    throw const FormatException(
      'Gunther PCM16 WAV metadata requires sample rate and channels.',
    );
  }
}

bool _isSafeAbsolutePath(String localPath, String fileName) {
  final uri = Uri.file(localPath);
  if (!uri.isAbsolute || uri.pathSegments.isEmpty) return false;
  if (fileName.isEmpty || fileName == '.' || fileName == '..') return false;
  if (fileName.contains('/') || fileName.contains('\\')) return false;
  if (uri.pathSegments.any((segment) => segment == '.' || segment == '..')) {
    return false;
  }
  return uri.pathSegments.last == fileName;
}

bool _lengthMatchesDraft(RecordingDraft draft, int actualLength) {
  if (actualLength < 0) return false;
  final appendOnlyPhase = switch (draft.phase) {
    'starting' || 'recording' || 'paused' || 'stopping' => true,
    _ => false,
  };
  if (appendOnlyPhase) return actualLength >= draft.sizeBytes;
  return actualLength == draft.sizeBytes;
}
