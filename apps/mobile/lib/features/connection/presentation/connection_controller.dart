import 'package:flutter/foundation.dart';
import 'package:gunther_mobile/data/models/connection_profile.dart';
import 'package:gunther_mobile/data/services/connection_profile_store.dart';

enum WorkspaceConnectionStatus {
  unconfigured,
  checking,
  online,
  offline,
  authExpired,
  certificateChanged,
  incompatible,
}

enum ConnectionProbeOutcome {
  online,
  offline,
  authExpired,
  certificateChanged,
  incompatible,
}

class ConnectionProbeResult {
  const ConnectionProbeResult({
    required this.outcome,
    this.workspaceId,
    this.protocolVersion,
    this.message,
  });

  final ConnectionProbeOutcome outcome;
  final String? workspaceId;
  final int? protocolVersion;
  final String? message;
}

abstract interface class ConnectionProbe {
  Future<ConnectionProbeResult> check(
    ConnectionProfile profile,
    ConnectionSecrets secrets,
  );
}

class ConnectionController extends ChangeNotifier {
  ConnectionController({
    required ConnectionProfileStore profileStore,
    required ConnectionSecretStore secretStore,
    required ConnectionProbe probe,
  }) : _profileStore = profileStore,
       _secretStore = secretStore,
       _probe = probe;

  final ConnectionProfileStore _profileStore;
  final ConnectionSecretStore _secretStore;
  final ConnectionProbe _probe;

  ConnectionProfileCatalog catalog = const ConnectionProfileCatalog();
  WorkspaceConnectionStatus status = WorkspaceConnectionStatus.unconfigured;
  String? statusMessage;
  ConnectionSecrets? _activeSecrets;
  int _generation = 0;
  bool _disposed = false;

  ConnectionProfile? get activeProfile => catalog.activeProfile;
  ConnectionSecrets? get activeSecrets => _activeSecrets;

  Future<void> initialize() async {
    final generation = ++_generation;
    try {
      final loaded = await _profileStore.load();
      if (!_isCurrent(generation)) return;
      catalog = loaded;
      if (loaded.activeProfile == null) {
        _activeSecrets = null;
        status = WorkspaceConnectionStatus.unconfigured;
        statusMessage =
            'Connect a knowledge workspace to sync captured material.';
        _safeNotify();
        return;
      }
      await _checkActive(generation: generation);
    } on Object {
      if (!_isCurrent(generation)) return;
      status = WorkspaceConnectionStatus.offline;
      _activeSecrets = null;
      statusMessage =
          'Saved connection settings could not be read. No workspace was selected.';
      _safeNotify();
    }
  }

  Future<void> upsertAndSelect(
    ConnectionProfile profile,
    ConnectionSecrets secrets,
  ) async {
    final generation = ++_generation;
    final previousSecret = await _secretStore.read(profile.id);
    if (!_isCurrent(generation)) return;
    await _secretStore.write(profile.id, secrets);
    if (!_isCurrent(generation)) return;

    final profiles = [
      for (final existing in catalog.profiles)
        if (existing.id != profile.id) existing,
      profile,
    ];
    final updated = ConnectionProfileCatalog(
      profiles: List<ConnectionProfile>.unmodifiable(profiles),
      activeProfileId: profile.id,
    );
    try {
      await _profileStore.save(updated);
    } on Object {
      if (previousSecret == null) {
        await _secretStore.delete(profile.id);
      } else {
        await _secretStore.write(profile.id, previousSecret);
      }
      rethrow;
    }
    if (!_isCurrent(generation)) return;
    catalog = updated;
    await _checkActive(generation: generation);
  }

  Future<void> select(String profileId) async {
    if (!catalog.profiles.any((profile) => profile.id == profileId)) {
      throw const FormatException(
        'The selected connection profile is missing.',
      );
    }
    final generation = ++_generation;
    _activeSecrets = null;
    final updated = ConnectionProfileCatalog(
      profiles: catalog.profiles,
      activeProfileId: profileId,
    );
    await _profileStore.save(updated);
    if (!_isCurrent(generation)) return;
    catalog = updated;
    await _checkActive(generation: generation);
  }

  Future<void> refresh() async {
    final generation = ++_generation;
    await _checkActive(generation: generation);
  }

  Future<void> remove(String profileId) async {
    final generation = ++_generation;
    final profiles = catalog.profiles
        .where((profile) => profile.id != profileId)
        .toList(growable: false);
    final updated = ConnectionProfileCatalog(
      profiles: List<ConnectionProfile>.unmodifiable(profiles),
      activeProfileId: catalog.activeProfileId == profileId
          ? null
          : catalog.activeProfileId,
    );
    await _profileStore.save(updated);
    await _secretStore.delete(profileId);
    if (!_isCurrent(generation)) return;
    catalog = updated;
    if (updated.activeProfile == null) {
      _activeSecrets = null;
      status = WorkspaceConnectionStatus.unconfigured;
      statusMessage = 'No knowledge workspace is connected.';
      _safeNotify();
    }
  }

  Future<void> _checkActive({required int generation}) async {
    if (!_isCurrent(generation)) return;
    final profile = catalog.activeProfile;
    if (profile == null) {
      _activeSecrets = null;
      status = WorkspaceConnectionStatus.unconfigured;
      statusMessage = 'No knowledge workspace is connected.';
      _safeNotify();
      return;
    }
    status = WorkspaceConnectionStatus.checking;
    statusMessage = 'Checking ${profile.label}…';
    _safeNotify();
    final secrets = await _secretStore.read(profile.id);
    if (!_isCurrent(generation)) return;
    if (secrets == null) {
      _activeSecrets = null;
      status = WorkspaceConnectionStatus.authExpired;
      statusMessage = 'This workspace needs to be paired again.';
      _safeNotify();
      return;
    }
    _activeSecrets = secrets;

    ConnectionProbeResult result;
    try {
      result = await _probe.check(profile, secrets);
    } on Object {
      if (!_isCurrent(generation)) return;
      status = WorkspaceConnectionStatus.offline;
      statusMessage =
          'The workspace is unavailable. Captures must stay on this device.';
      _safeNotify();
      return;
    }
    if (!_isCurrent(generation)) return;

    if (result.outcome == ConnectionProbeOutcome.online &&
        (result.workspaceId != profile.workspaceId ||
            result.protocolVersion != profile.protocolVersion)) {
      status = WorkspaceConnectionStatus.incompatible;
      statusMessage =
          'The server identity or protocol does not match this saved workspace.';
      _safeNotify();
      return;
    }
    status = _statusFor(result.outcome);
    statusMessage = result.message ?? _defaultMessage(status, profile.label);
    if (status == WorkspaceConnectionStatus.online) {
      final connected = profile.copyWith(
        lastConnectedAt: DateTime.now().toUtc(),
      );
      final profiles = [
        for (final existing in catalog.profiles)
          if (existing.id == connected.id) connected else existing,
      ];
      final updated = ConnectionProfileCatalog(
        profiles: List<ConnectionProfile>.unmodifiable(profiles),
        activeProfileId: connected.id,
      );
      try {
        await _profileStore.save(updated);
        if (_isCurrent(generation)) catalog = updated;
      } on Object {
        // A failed timestamp update does not invalidate the authenticated
        // connection; the next refresh can persist it again.
      }
    }
    if (_isCurrent(generation)) _safeNotify();
  }

  bool _isCurrent(int generation) => !_disposed && generation == _generation;

  void _safeNotify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _activeSecrets = null;
    _generation += 1;
    super.dispose();
  }
}

WorkspaceConnectionStatus _statusFor(ConnectionProbeOutcome outcome) {
  return switch (outcome) {
    ConnectionProbeOutcome.online => WorkspaceConnectionStatus.online,
    ConnectionProbeOutcome.offline => WorkspaceConnectionStatus.offline,
    ConnectionProbeOutcome.authExpired => WorkspaceConnectionStatus.authExpired,
    ConnectionProbeOutcome.certificateChanged =>
      WorkspaceConnectionStatus.certificateChanged,
    ConnectionProbeOutcome.incompatible =>
      WorkspaceConnectionStatus.incompatible,
  };
}

String _defaultMessage(WorkspaceConnectionStatus status, String label) {
  return switch (status) {
    WorkspaceConnectionStatus.unconfigured =>
      'No knowledge workspace is connected.',
    WorkspaceConnectionStatus.checking => 'Checking $label…',
    WorkspaceConnectionStatus.online => '$label is connected.',
    WorkspaceConnectionStatus.offline =>
      '$label is offline. Captures must stay on this device.',
    WorkspaceConnectionStatus.authExpired => '$label needs to be paired again.',
    WorkspaceConnectionStatus.certificateChanged =>
      '$label presented a different certificate. Connection was stopped.',
    WorkspaceConnectionStatus.incompatible =>
      '$label is not compatible with this version of Gunther.',
  };
}
