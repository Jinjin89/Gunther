import 'dart:async';

import 'package:flutter/services.dart';
import 'package:gunther_mobile/data/models/capture.dart';
import 'package:gunther_mobile/data/models/capture_outbox.dart';
import 'package:gunther_mobile/data/services/capture_outbox_service.dart';

const String systemShareMethodChannelName = 'com.gunther.mobile/share_ingress';

enum SystemShareKind { text, url, file }

class SystemShareItem {
  const SystemShareItem({
    required this.id,
    required this.kind,
    this.text,
    this.path,
    this.fileName,
    this.mediaType,
    this.sizeBytes,
  });

  factory SystemShareItem.fromPlatform(Object? value) {
    if (value is! Map<Object?, Object?>) {
      throw const FormatException('A shared item was not an object.');
    }
    final id = _requiredString(value, 'id');
    if (!RegExp(r'^[A-Za-z0-9._-]{8,180}$').hasMatch(id)) {
      throw const FormatException('A shared item id was invalid.');
    }
    final kind = switch (_requiredString(value, 'kind')) {
      'text' => SystemShareKind.text,
      'url' => SystemShareKind.url,
      'file' => SystemShareKind.file,
      _ => throw const FormatException('A shared item kind was invalid.'),
    };
    final text = _optionalString(value['text']);
    final path = _optionalString(value['path']);
    final fileName = _optionalString(value['fileName']);
    final mediaType = _optionalString(value['mediaType']);
    final sizeBytes = value['sizeBytes'];
    if ((kind == SystemShareKind.text || kind == SystemShareKind.url) &&
        (text == null || text.trim().isEmpty)) {
      throw const FormatException('Shared text was empty.');
    }
    if (kind == SystemShareKind.file &&
        (path == null ||
            path.isEmpty ||
            fileName == null ||
            fileName.isEmpty ||
            mediaType == null ||
            !mediaType.contains('/') ||
            sizeBytes is! int ||
            sizeBytes < 0)) {
      throw const FormatException('Shared file metadata was invalid.');
    }
    return SystemShareItem(
      id: id,
      kind: kind,
      text: text,
      path: path,
      fileName: fileName,
      mediaType: mediaType,
      sizeBytes: sizeBytes as int?,
    );
  }

  final String id;
  final SystemShareKind kind;
  final String? text;
  final String? path;
  final String? fileName;
  final String? mediaType;
  final int? sizeBytes;
}

abstract interface class SystemShareIngress {
  Future<List<SystemShareItem>> pendingItems();
  Future<void> acknowledge(List<String> ids);
  void setItemsAvailableHandler(Future<void> Function()? handler);
}

abstract interface class SystemShareIngressStatusSource {
  Future<String> currentStagingState();
  void setStagingStateHandler(void Function(String state)? handler);
}

class SystemShareMaintenanceSnapshot {
  const SystemShareMaintenanceSnapshot({
    required this.healthy,
    required this.pendingCount,
    required this.quarantinedQueueCount,
    this.issueCode,
  });

  factory SystemShareMaintenanceSnapshot.fromPlatform(Object? value) {
    if (value is! Map<Object?, Object?> ||
        value['healthy'] is! bool ||
        value['pendingCount'] is! int ||
        value['quarantinedQueueCount'] is! int) {
      throw const FormatException(
        'Share maintenance diagnostics were invalid.',
      );
    }
    return SystemShareMaintenanceSnapshot(
      healthy: value['healthy']! as bool,
      pendingCount: value['pendingCount']! as int,
      quarantinedQueueCount: value['quarantinedQueueCount']! as int,
      issueCode: value['issueCode'] as String?,
    );
  }

  final bool healthy;
  final int pendingCount;
  final int quarantinedQueueCount;
  final String? issueCode;
}

abstract interface class SystemShareIngressMaintenanceSource {
  Future<SystemShareMaintenanceSnapshot> inspectShareMaintenance();
  Future<void> quarantineDamagedShareQueue({required bool userConfirmed});
}

/// Method-channel bridge to Android's SEND/SEND_MULTIPLE intents and the iOS
/// Share Extension app-group queue.
///
/// Platform code owns the incoming bytes until [acknowledge] succeeds. Dart
/// acknowledges only after [CaptureOutboxStore] has made its own durable copy.
class MethodChannelSystemShareIngress
    implements
        SystemShareIngress,
        SystemShareIngressStatusSource,
        SystemShareIngressMaintenanceSource {
  MethodChannelSystemShareIngress({MethodChannel? channel})
    : _channel = channel ?? const MethodChannel(systemShareMethodChannelName) {
    _channel.setMethodCallHandler(_handleMethodCall);
  }

  final MethodChannel _channel;
  Future<void> Function()? _itemsAvailableHandler;
  void Function(String state)? _stagingStateHandler;

  @override
  Future<List<SystemShareItem>> pendingItems() async {
    try {
      final raw = await _channel.invokeMethod<Object?>('getPendingShares');
      if (raw == null) return const [];
      if (raw is! List<Object?>) {
        throw const FormatException('The platform share queue was invalid.');
      }
      final items = <SystemShareItem>[];
      for (final value in raw) {
        // Fail closed. A malformed platform item cannot be acknowledged
        // safely, and valid siblings must not be used to rewrite its queue.
        items.add(SystemShareItem.fromPlatform(value));
      }
      return List<SystemShareItem>.unmodifiable(items);
    } on MissingPluginException {
      return const [];
    }
  }

  @override
  Future<void> acknowledge(List<String> ids) async {
    if (ids.isEmpty) return;
    await _channel.invokeMethod<void>('acknowledgeShares', ids);
  }

  @override
  void setItemsAvailableHandler(Future<void> Function()? handler) {
    _itemsAvailableHandler = handler;
  }

  @override
  Future<String> currentStagingState() async {
    try {
      final value = await _channel.invokeMethod<String>('getShareStagingState');
      return _validStagingState(value) ? value! : 'idle';
    } on MissingPluginException {
      return 'idle';
    }
  }

  @override
  void setStagingStateHandler(void Function(String state)? handler) {
    _stagingStateHandler = handler;
  }

  @override
  Future<SystemShareMaintenanceSnapshot> inspectShareMaintenance() async {
    try {
      final value = await _channel.invokeMethod<Object?>('getShareDiagnostics');
      return SystemShareMaintenanceSnapshot.fromPlatform(value);
    } on MissingPluginException {
      return const SystemShareMaintenanceSnapshot(
        healthy: true,
        pendingCount: 0,
        quarantinedQueueCount: 0,
      );
    }
  }

  @override
  Future<void> quarantineDamagedShareQueue({
    required bool userConfirmed,
  }) async {
    if (!userConfirmed) {
      throw const FormatException(
        'Share queue quarantine requires explicit user confirmation.',
      );
    }
    await _channel.invokeMethod<void>('quarantineDamagedShareQueue', {
      'userConfirmed': true,
    });
  }

  Future<void> _handleMethodCall(MethodCall call) async {
    if (call.method == 'sharedItemsAvailable') {
      await _itemsAvailableHandler?.call();
    } else if (call.method == 'shareStagingState') {
      final arguments = call.arguments;
      final state = arguments is Map<Object?, Object?>
          ? arguments['state']
          : null;
      if (state is String && _validStagingState(state)) {
        _stagingStateHandler?.call(state);
      }
    }
  }

  void dispose() {
    _itemsAvailableHandler = null;
    _stagingStateHandler = null;
    _channel.setMethodCallHandler(null);
  }
}

bool _validStagingState(String? value) =>
    value == 'idle' ||
    value == 'processing' ||
    value == 'completed' ||
    value == 'partial_failure' ||
    value == 'failed';

typedef SystemShareTarget = ({String workspaceId, String profileId});

/// Moves native share items into the same durable outbox used by in-app
/// capture. It deliberately does not perform network I/O.
class SystemShareIngressCoordinator {
  SystemShareIngressCoordinator({
    required SystemShareIngress ingress,
    required CaptureOutboxStore outbox,
    SystemShareTarget? Function()? targetProvider,
  }) : _ingress = ingress,
       _outbox = outbox,
       _targetProvider = targetProvider;

  final SystemShareIngress _ingress;
  final CaptureOutboxStore _outbox;
  final SystemShareTarget? Function()? _targetProvider;
  Future<List<CaptureOutboxEntry>> _barrier = Future.value(const []);
  Object? lastError;

  Future<List<CaptureOutboxEntry>> drain() {
    final result = _barrier
        .catchError((Object _, StackTrace __) => <CaptureOutboxEntry>[])
        .then((_) => _drain());
    _barrier = result;
    return result;
  }

  Future<List<CaptureOutboxEntry>> _drain() async {
    lastError = null;
    final pending = await _ingress.pendingItems();
    if (pending.isEmpty) return const [];
    final existing = await _outbox.listEntries();
    final byShareId = <String, CaptureOutboxEntry>{
      for (final entry in existing)
        if (entry.payload['systemShareId'] case final String shareId)
          shareId: entry,
    };
    final accepted = <CaptureOutboxEntry>[];
    for (final item in pending) {
      try {
        final alreadyStaged = byShareId[item.id];
        if (alreadyStaged != null) {
          await _ingress.acknowledge([item.id]);
          accepted.add(alreadyStaged);
          continue;
        }

        // Capture the identity synchronously. Once written, an outbox target
        // is immutable and profile switching can never redirect this item.
        final target = _targetProvider?.call();
        final entry = await _stage(item, target);
        byShareId[item.id] = entry;
        // Native may delete its app-owned copy only after the outbox copy and
        // manifest commit have both completed.
        await _ingress.acknowledge([item.id]);
        accepted.add(entry);
      } on Object catch (exception) {
        // Previously acknowledged siblings can sync now. This item stays in
        // native storage, and a staged duplicate is recognized on the retry.
        lastError = exception;
        break;
      }
    }
    return List<CaptureOutboxEntry>.unmodifiable(accepted);
  }

  Future<CaptureOutboxEntry> _stage(
    SystemShareItem item,
    SystemShareTarget? target,
  ) {
    final binding = (
      targetWorkspaceId: target?.workspaceId,
      connectionProfileId: target?.profileId,
    );
    switch (item.kind) {
      case SystemShareKind.url:
        final url = normalizeWebSnapshotUrl(item.text!);
        return _outbox.enqueuePayload(
          kind: CaptureOutboxKind.link,
          payload: {'url': url, 'systemShareId': item.id},
          targetWorkspaceId: binding.targetWorkspaceId,
          connectionProfileId: binding.connectionProfileId,
        );
      case SystemShareKind.text:
        final content = item.text!.trim();
        final title = _titleFromText(content);
        return _outbox.enqueuePayload(
          kind: CaptureOutboxKind.quickNote,
          payload: {
            'title': title,
            'content': content,
            'pinned': false,
            'systemShareId': item.id,
          },
          targetWorkspaceId: binding.targetWorkspaceId,
          connectionProfileId: binding.connectionProfileId,
        );
      case SystemShareKind.file:
        final mediaType = item.mediaType!;
        final outboxKind = mediaType.startsWith('image/')
            ? CaptureOutboxKind.photo
            : mediaType.startsWith('audio/')
            ? CaptureOutboxKind.audio
            : CaptureOutboxKind.document;
        final serverKind = mediaType.startsWith('image/')
            ? 'image'
            : mediaType.startsWith('audio/')
            ? 'audio'
            : 'file';
        return _outbox.enqueueAsset(
          kind: outboxKind,
          asset: CapturedAsset(
            path: item.path!,
            fileName: item.fileName!,
            mediaType: mediaType,
            sizeBytes: item.sizeBytes!,
          ),
          payload: {
            'title': _titleFromFileName(item.fileName!),
            'serverKind': serverKind,
            'systemShareId': item.id,
          },
          targetWorkspaceId: binding.targetWorkspaceId,
          connectionProfileId: binding.connectionProfileId,
        );
    }
  }
}

String _titleFromText(String content) {
  final firstLine = content.split(RegExp(r'[\r\n]')).first.trim();
  if (firstLine.isEmpty) return 'Shared note';
  return firstLine.length <= 80
      ? firstLine
      : '${firstLine.substring(0, 77)}...';
}

String _titleFromFileName(String fileName) {
  final dot = fileName.lastIndexOf('.');
  final title = (dot > 0 ? fileName.substring(0, dot) : fileName).trim();
  return title.isEmpty ? 'Shared file' : title;
}

String _requiredString(Map<Object?, Object?> value, String key) {
  final result = value[key];
  if (result is! String || result.isEmpty) {
    throw FormatException('Shared item field "$key" was invalid.');
  }
  return result;
}

String? _optionalString(Object? value) {
  if (value == null) return null;
  if (value is! String || value.isEmpty) {
    throw const FormatException('A shared item string was invalid.');
  }
  return value;
}
