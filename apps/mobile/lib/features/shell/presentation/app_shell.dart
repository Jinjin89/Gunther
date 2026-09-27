import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:gunther_mobile/data/models/capture.dart';
import 'package:gunther_mobile/data/models/capture_outbox.dart';
import 'package:gunther_mobile/data/repositories/knowledge_repository.dart';
import 'package:gunther_mobile/data/services/capture_device_service.dart';
import 'package:gunther_mobile/data/services/capture_outbox_service.dart';
import 'package:gunther_mobile/data/services/live_transcription_service.dart';
import 'package:gunther_mobile/data/services/lost_photo_recovery_service.dart';
import 'package:gunther_mobile/data/services/recording_draft_store.dart';
import 'package:gunther_mobile/data/services/system_share_ingress_service.dart';
import 'package:gunther_mobile/features/capture/presentation/capture_launcher.dart';
import 'package:gunther_mobile/features/capture/presentation/capture_pages.dart';
import 'package:gunther_mobile/features/capture/presentation/capture_view_model.dart';
import 'package:gunther_mobile/features/connection/presentation/connection_controller.dart';
import 'package:gunther_mobile/features/connection/presentation/connection_settings_sheet.dart';
import 'package:gunther_mobile/features/home/presentation/home_view.dart';
import 'package:gunther_mobile/features/home/presentation/home_view_model.dart';
import 'package:gunther_mobile/features/inbox/presentation/inbox_view.dart';
import 'package:gunther_mobile/features/inbox/presentation/inbox_view_model.dart';
import 'package:gunther_mobile/features/libraries/presentation/libraries_view.dart';
import 'package:gunther_mobile/features/libraries/presentation/libraries_view_model.dart';

class AppShell extends StatefulWidget {
  const AppShell({
    required this.repository,
    this.captureDevice,
    this.recordingDraftStore,
    this.captureOutboxStore,
    this.liveTranscriptionFactory,
    this.connectionController,
    this.systemShareIngress,
    this.lostPhotoIngress,
    super.key,
  });

  final KnowledgeRepository repository;
  final CaptureDevice? captureDevice;
  final RecordingDraftStore? recordingDraftStore;
  final CaptureOutboxStore? captureOutboxStore;
  final LiveTranscriptionSessionFactory? liveTranscriptionFactory;
  final ConnectionController? connectionController;
  final SystemShareIngress? systemShareIngress;
  final SystemShareIngress? lostPhotoIngress;

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> with WidgetsBindingObserver {
  int _selectedIndex = 0;
  late final HomeViewModel _homeViewModel;
  late final LibrariesViewModel _librariesViewModel;
  late final InboxViewModel _inboxViewModel;
  late final CaptureViewModel _captureViewModel;
  late final CaptureOutboxStore _captureOutboxStore;
  late final SystemShareIngress _systemShareIngress;
  late final SystemShareIngressCoordinator _shareIngressCoordinator;
  late final SystemShareIngress _lostPhotoIngress;
  late final SystemShareIngressCoordinator _lostPhotoCoordinator;
  late final bool _ownsSystemShareIngress;

  static const titles = ['Home', 'Libraries', 'Inbox'];

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _homeViewModel = HomeViewModel(widget.repository)..load();
    _librariesViewModel = LibrariesViewModel(widget.repository)..load();
    _inboxViewModel = InboxViewModel(widget.repository)..load();
    _captureOutboxStore = widget.captureOutboxStore ?? CaptureOutboxService();
    _ownsSystemShareIngress = widget.systemShareIngress == null;
    _systemShareIngress =
        widget.systemShareIngress ?? MethodChannelSystemShareIngress();
    SystemShareTarget? currentCaptureTarget() {
      final profile = widget.connectionController?.activeProfile;
      if (profile == null) return null;
      return (workspaceId: profile.workspaceId, profileId: profile.id);
    }

    _shareIngressCoordinator = SystemShareIngressCoordinator(
      ingress: _systemShareIngress,
      outbox: _captureOutboxStore,
      targetProvider: widget.connectionController == null
          ? null
          : currentCaptureTarget,
    );
    _lostPhotoIngress = widget.lostPhotoIngress ?? LostPhotoRecoveryService();
    _lostPhotoCoordinator = SystemShareIngressCoordinator(
      ingress: _lostPhotoIngress,
      outbox: _captureOutboxStore,
      targetProvider: widget.connectionController == null
          ? null
          : currentCaptureTarget,
    );
    _captureViewModel = CaptureViewModel(
      widget.repository,
      widget.captureDevice ?? FlutterCaptureDevice(),
      recordingDraftStore: widget.recordingDraftStore,
      captureOutboxStore: _captureOutboxStore,
      liveTranscriptionFactory: widget.liveTranscriptionFactory,
      onOutboxUploaded: _refreshPrimaryViews,
      captureTargetProvider: widget.connectionController == null
          ? null
          : currentCaptureTarget,
      shareIngressMaintenanceSource:
          _systemShareIngress is SystemShareIngressMaintenanceSource
          ? _systemShareIngress as SystemShareIngressMaintenanceSource
          : null,
    );
    _systemShareIngress.setItemsAvailableHandler(_drainSystemShares);
    if (_systemShareIngress case final SystemShareIngressStatusSource status) {
      status.setStagingStateHandler(
        _captureViewModel.reportSystemShareStagingState,
      );
    }
    // ChangeNotifier updates are intentionally deferred until after the first
    // frame, avoiding a build-phase notification during app startup.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      unawaited(() async {
        await _captureViewModel.restoreRecordingDraft();
        await _captureViewModel.restoreCaptureOutbox();
        await _drainLostPhotos();
        await _drainSystemShares();
      }());
    });
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      unawaited(() async {
        await _drainLostPhotos();
        await _drainSystemShares();
        await _captureViewModel.flushCaptureOutbox();
      }());
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _homeViewModel.dispose();
    _librariesViewModel.dispose();
    _inboxViewModel.dispose();
    _captureViewModel.dispose();
    _systemShareIngress.setItemsAvailableHandler(null);
    if (_systemShareIngress case final SystemShareIngressStatusSource status) {
      status.setStagingStateHandler(null);
    }
    final ingress = _systemShareIngress;
    if (_ownsSystemShareIngress && ingress is MethodChannelSystemShareIngress) {
      ingress.dispose();
    }
    super.dispose();
  }

  Future<void> _drainSystemShares() async {
    try {
      if (_systemShareIngress
          case final SystemShareIngressStatusSource status) {
        _captureViewModel.reportSystemShareStagingState(
          await status.currentStagingState(),
        );
      }
      final accepted = await _shareIngressCoordinator.drain();
      if (accepted.isNotEmpty) {
        await _captureViewModel.acceptSystemShareEntries(accepted);
      }
      if (_shareIngressCoordinator.lastError case final error?) {
        _captureViewModel.reportSystemShareError(error);
      }
    } on Object catch (exception) {
      _captureViewModel.reportSystemShareError(exception);
    }
  }

  Future<void> _drainLostPhotos() async {
    try {
      final accepted = await _lostPhotoCoordinator.drain();
      if (accepted.isNotEmpty) {
        await _captureViewModel.acceptSystemShareEntries(accepted);
      }
      if (_lostPhotoCoordinator.lastError case final error?) {
        _captureViewModel.reportSystemShareError(error);
      }
    } on Object catch (exception) {
      _captureViewModel.reportSystemShareError(exception);
    }
  }

  Future<void> _refreshPrimaryViews() async {
    await Future.wait([
      _homeViewModel.load(),
      _librariesViewModel.load(),
      _inboxViewModel.load(),
    ]);
  }

  Future<void> _openCapture() async {
    final kind = await showModalBottomSheet<CaptureKind>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      showDragHandle: true,
      builder: (context) => CaptureLauncher(
        onSelected: (kind) => Navigator.of(context).pop(kind),
      ),
    );
    if (kind == null || !mounted) return;
    await _openCaptureFlow(kind);
  }

  Future<void> _openCaptureFlow(CaptureKind kind) async {
    _captureViewModel.resetFeedback();
    if (kind == CaptureKind.recording) _captureViewModel.expandRecording();
    await Navigator.of(context).push<void>(
      MaterialPageRoute(
        builder: (context) => CaptureFlowPage(
          kind: kind,
          viewModel: _captureViewModel,
          onCaptured: _refreshPrimaryViews,
          onMinimizeRecording: () => Navigator.of(context).pop(),
        ),
      ),
    );
  }

  Future<void> _expandRecording() async {
    if (!mounted) return;
    await _openCaptureFlow(CaptureKind.recording);
  }

  Future<void> _showPendingCaptures() async {
    unawaited(_captureViewModel.refreshOutboxMaintenance());
    await showModalBottomSheet<void>(
      context: context,
      useSafeArea: true,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (context) => _PendingCapturesSheet(
        viewModel: _captureViewModel,
        onOpenRecording: () async {
          Navigator.of(context).pop();
          await _expandRecording();
        },
      ),
    );
  }

  Future<void> _showConnectionSettings() async {
    final controller = widget.connectionController;
    if (controller == null) return;
    await showModalBottomSheet<void>(
      context: context,
      useSafeArea: true,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (context) => ConnectionSettingsSheet(
        controller: controller,
        onConnectionChanged: () async {
          await _captureViewModel.flushCaptureOutbox();
          await _refreshPrimaryViews();
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final pages = [
      HomeView(
        viewModel: _homeViewModel,
        onOpenLibraries: () => setState(() => _selectedIndex = 1),
        onOpenInbox: () => setState(() => _selectedIndex = 2),
      ),
      LibrariesView(
        viewModel: _librariesViewModel,
        onChanged: _refreshPrimaryViews,
      ),
      InboxView(viewModel: _inboxViewModel, onChanged: _refreshPrimaryViews),
    ];

    return AnimatedBuilder(
      animation: _captureViewModel,
      builder: (context, _) => Scaffold(
        appBar: AppBar(
          title: Text(
            titles[_selectedIndex],
            style: const TextStyle(fontWeight: FontWeight.w700),
          ),
          actions: [
            if (widget.connectionController case final controller?)
              AnimatedBuilder(
                animation: controller,
                builder: (context, _) => IconButton(
                  key: const Key('workspace-connection-button'),
                  tooltip: controller.statusMessage ?? 'Knowledge workspace',
                  onPressed: _showConnectionSettings,
                  icon: Icon(_connectionIcon(controller.status)),
                ),
              ),
            if (_captureViewModel.hasCaptureAttention)
              IconButton(
                key: const Key('pending-captures-button'),
                tooltip: 'Captures saved on this device',
                onPressed: _showPendingCaptures,
                icon: Badge(
                  label: Text(
                    _captureViewModel.pendingCaptureCount > 0
                        ? '${_captureViewModel.pendingCaptureCount}'
                        : '!',
                  ),
                  child: _captureViewModel.outboxSyncing
                      ? const SizedBox.square(
                          dimension: 20,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.cloud_upload_outlined),
                ),
              ),
          ],
        ),
        body: SafeArea(
          top: false,
          child: Stack(
            children: [
              IndexedStack(index: _selectedIndex, children: pages),
              if (_captureViewModel.hasMinimizedRecording)
                Positioned(
                  left: 12,
                  right: 12,
                  bottom: 12,
                  child: _MiniRecordingBar(
                    viewModel: _captureViewModel,
                    onExpand: _expandRecording,
                  ),
                ),
            ],
          ),
        ),
        floatingActionButtonLocation: FloatingActionButtonLocation.centerFloat,
        floatingActionButton: _captureViewModel.hasMinimizedRecording
            ? null
            : FloatingActionButton.extended(
                key: const Key('global-capture-button'),
                // This button can be inserted while a minimized recording
                // route is opening. Disabling the implicit default Hero avoids
                // duplicate-tag transitions during that state handoff.
                heroTag: null,
                onPressed: _openCapture,
                icon: const Icon(Icons.add),
                label: const Text('Capture'),
              ),
        bottomNavigationBar: NavigationBar(
          selectedIndex: _selectedIndex,
          onDestinationSelected: (index) =>
              setState(() => _selectedIndex = index),
          destinations: const [
            NavigationDestination(
              key: Key('nav-home'),
              icon: Icon(Icons.home_outlined),
              label: 'Home',
            ),
            NavigationDestination(
              key: Key('nav-libraries'),
              icon: Icon(Icons.collections_bookmark_outlined),
              label: 'Libraries',
            ),
            NavigationDestination(
              key: Key('nav-inbox'),
              icon: Icon(Icons.inbox_outlined),
              label: 'Inbox',
            ),
          ],
        ),
      ),
    );
  }
}

IconData _connectionIcon(WorkspaceConnectionStatus status) => switch (status) {
  WorkspaceConnectionStatus.online => Icons.cloud_done_outlined,
  WorkspaceConnectionStatus.checking => Icons.sync,
  WorkspaceConnectionStatus.unconfigured => Icons.cloud_off_outlined,
  WorkspaceConnectionStatus.offline => Icons.cloud_off_outlined,
  WorkspaceConnectionStatus.authExpired => Icons.lock_clock_outlined,
  WorkspaceConnectionStatus.certificateChanged => Icons.gpp_bad_outlined,
  WorkspaceConnectionStatus.incompatible => Icons.update_outlined,
};

class _PendingCapturesSheet extends StatelessWidget {
  const _PendingCapturesSheet({
    required this.viewModel,
    required this.onOpenRecording,
  });

  final CaptureViewModel viewModel;
  final Future<void> Function() onOpenRecording;

  @override
  Widget build(BuildContext context) {
    return FractionallySizedBox(
      heightFactor: 0.72,
      child: AnimatedBuilder(
        animation: viewModel,
        builder: (context, _) => Padding(
          padding: const EdgeInsets.fromLTRB(20, 4, 20, 20),
          child: Column(
            key: const Key('pending-captures-sheet'),
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                'Saved on this device',
                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 6),
              Text(
                'Gunther keeps these captures until the server confirms them.',
                style: Theme.of(context).textTheme.bodyMedium,
              ),
              if (viewModel.outboxError != null) ...[
                const SizedBox(height: 10),
                Text(
                  viewModel.outboxError!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ],
              if (viewModel.systemIngressIssueCode != null) ...[
                const SizedBox(height: 8),
                Text(
                  'Incoming capture needs attention · ${viewModel.systemIngressIssueCode}',
                  key: const Key('system-ingress-issue'),
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ],
              if (viewModel.systemIngressStagingState == 'processing') ...[
                const SizedBox(height: 8),
                const Row(
                  key: Key('system-share-processing'),
                  children: [
                    SizedBox.square(
                      dimension: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                    SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        'Copying shared originals into protected app storage…',
                      ),
                    ),
                  ],
                ),
              ],
              if (viewModel.systemIngressStagingState == 'partial_failure') ...[
                const SizedBox(height: 8),
                Text(
                  'Some shared items were saved, but at least one could not be copied. The uncopied original remains in the source app; share it again or use Capture inside Gunther.',
                  key: const Key('system-share-partial-failure'),
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ],
              if (viewModel.outboxMaintenance?.manifestHealthy == false) ...[
                const SizedBox(height: 10),
                Card(
                  color: Theme.of(context).colorScheme.errorContainer,
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text(
                          'Local capture index is damaged',
                          style: TextStyle(fontWeight: FontWeight.w700),
                        ),
                        const SizedBox(height: 4),
                        const Text(
                          'Gunther has not deleted any captured originals. You can copy diagnostics, then quarantine only the damaged index to resume capture.',
                        ),
                        const SizedBox(height: 8),
                        OutlinedButton(
                          key: const Key('quarantine-damaged-outbox'),
                          onPressed: viewModel.maintenanceBusy
                              ? null
                              : () => _confirmQuarantine(context),
                          child: const Text('Quarantine damaged index'),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
              if (viewModel.shareIngressMaintenance?.healthy == false) ...[
                const SizedBox(height: 10),
                Card(
                  color: Theme.of(context).colorScheme.errorContainer,
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text(
                          'Incoming-share index is damaged',
                          style: TextStyle(fontWeight: FontWeight.w700),
                        ),
                        const SizedBox(height: 4),
                        const Text(
                          'Shared originals remain in protected storage. Quarantine only the damaged index to resume Share Sheet capture.',
                        ),
                        const SizedBox(height: 8),
                        OutlinedButton(
                          key: const Key('quarantine-damaged-share-queue'),
                          onPressed: viewModel.maintenanceBusy
                              ? null
                              : () => _confirmShareQueueQuarantine(context),
                          child: const Text('Quarantine incoming index'),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
              if (viewModel.outboxMaintenance?.issueCode ==
                  'cleanup_journal_corrupt') ...[
                const SizedBox(height: 10),
                Card(
                  color: Theme.of(context).colorScheme.errorContainer,
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text(
                          'Cleanup journal needs repair',
                          style: TextStyle(fontWeight: FontWeight.w700),
                        ),
                        const SizedBox(height: 4),
                        const Text(
                          'Pending captures are intact. Quarantining this maintenance journal deletes no capture files.',
                        ),
                        const SizedBox(height: 8),
                        OutlinedButton(
                          key: const Key('quarantine-cleanup-journal'),
                          onPressed: viewModel.maintenanceBusy
                              ? null
                              : () => _confirmCleanupJournalQuarantine(context),
                          child: const Text('Quarantine cleanup journal'),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
              if (viewModel.maintenanceNotice != null) ...[
                const SizedBox(height: 8),
                Text(viewModel.maintenanceNotice!),
              ],
              const SizedBox(height: 12),
              Expanded(
                child: viewModel.outboxEntries.isEmpty
                    ? const Center(child: Text('Everything is synced.'))
                    : ListView.separated(
                        itemCount: viewModel.outboxEntries.length,
                        separatorBuilder: (_, _) => const Divider(height: 1),
                        itemBuilder: (context, index) {
                          final entry = viewModel.outboxEntries[index];
                          return ListTile(
                            contentPadding: EdgeInsets.zero,
                            leading: Icon(_outboxIcon(entry.kind)),
                            title: Text(_outboxTitle(entry)),
                            subtitle: Text(viewModel.outboxUserStatus(entry)),
                            trailing: entry.kind == CaptureOutboxKind.audio
                                ? TextButton(
                                    key: Key('open-capture-${entry.id}'),
                                    onPressed: () =>
                                        unawaited(onOpenRecording()),
                                    child: const Text('Open'),
                                  )
                                : viewModel.canRetryOutboxEntry(entry)
                                ? TextButton(
                                    key: Key('retry-capture-${entry.id}'),
                                    onPressed: viewModel.outboxSyncing
                                        ? null
                                        : () => unawaited(
                                            viewModel.retryCapture(entry.id),
                                          ),
                                    child: const Text('Retry'),
                                  )
                                : const SizedBox.square(
                                    dimension: 20,
                                    child: CircularProgressIndicator(
                                      strokeWidth: 2,
                                    ),
                                  ),
                          );
                        },
                      ),
              ),
              const SizedBox(height: 12),
              FilledButton.icon(
                key: const Key('retry-all-captures'),
                onPressed:
                    viewModel.outboxSyncing || !viewModel.hasRetryableCaptureNow
                    ? null
                    : () => unawaited(viewModel.flushCaptureOutbox()),
                icon: const Icon(Icons.sync),
                label: Text(viewModel.outboxSyncing ? 'Syncing…' : 'Retry all'),
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      key: const Key('copy-capture-diagnostics'),
                      onPressed: () => _copyDiagnostics(context),
                      icon: const Icon(Icons.content_copy),
                      label: const Text('Copy diagnostics'),
                    ),
                  ),
                  if ((viewModel
                              .outboxMaintenance
                              ?.confirmedFilesReadyForCleanup ??
                          0) >
                      0) ...[
                    const SizedBox(width: 8),
                    Expanded(
                      child: OutlinedButton.icon(
                        key: const Key('clean-confirmed-files'),
                        onPressed: viewModel.maintenanceBusy
                            ? null
                            : () => _confirmCleanup(context),
                        icon: const Icon(Icons.cleaning_services_outlined),
                        label: const Text('Clean confirmed'),
                      ),
                    ),
                  ],
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _copyDiagnostics(BuildContext context) async {
    final diagnostics = await viewModel.sanitizedOutboxDiagnostics();
    await Clipboard.setData(ClipboardData(text: diagnostics));
    if (!context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Sanitized diagnostics copied.')),
    );
  }

  Future<void> _confirmCleanup(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Clean confirmed leftovers?'),
        content: const Text(
          'Only files recorded in the server-confirmed cleanup journal will be removed. Pending and failed captures are never selected.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const Key('confirm-clean-confirmed-files'),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Clean'),
          ),
        ],
      ),
    );
    if (confirmed == true) await viewModel.cleanConfirmedOutboxFiles();
  }

  Future<void> _confirmQuarantine(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Quarantine damaged index?'),
        content: const Text(
          'The exact damaged index will be retained for recovery and no captured original will be deleted. Existing damaged records will no longer appear in the active queue.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const Key('confirm-quarantine-damaged-outbox'),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Quarantine index'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await viewModel.quarantineDamagedOutbox(userConfirmed: true);
    }
  }

  Future<void> _confirmCleanupJournalQuarantine(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Quarantine cleanup journal?'),
        content: const Text(
          'The damaged journal will be retained for support. Pending records and every captured original remain untouched.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const Key('confirm-quarantine-cleanup-journal'),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Quarantine journal'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await viewModel.quarantineDamagedCleanupJournal(userConfirmed: true);
    }
  }

  Future<void> _confirmShareQueueQuarantine(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Quarantine incoming-share index?'),
        content: const Text(
          'The exact damaged index is retained for support. No shared original or pending app capture is deleted.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const Key('confirm-quarantine-damaged-share-queue'),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Quarantine index'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await viewModel.quarantineDamagedShareQueue(userConfirmed: true);
    }
  }
}

class _MiniRecordingBar extends StatelessWidget {
  const _MiniRecordingBar({required this.viewModel, required this.onExpand});

  final CaptureViewModel viewModel;
  final VoidCallback onExpand;

  @override
  Widget build(BuildContext context) {
    return Card(
      key: const Key('mini-recording-bar'),
      color: Theme.of(context).colorScheme.inverseSurface,
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: onExpand,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          child: Row(
            children: [
              Icon(
                viewModel.recordingPhase == RecordingCapturePhase.completed
                    ? Icons.check_circle_outline
                    : Icons.graphic_eq,
                color: Theme.of(context).colorScheme.onInverseSurface,
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      viewModel.recordingTitle,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.onInverseSurface,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    Text(
                      _miniStatus(viewModel),
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.onInverseSurface,
                      ),
                    ),
                  ],
                ),
              ),
              if (viewModel.recordingPhase == RecordingCapturePhase.recording)
                IconButton(
                  key: const Key('mini-pause-recording'),
                  tooltip: 'Pause recording',
                  onPressed: () => unawaited(viewModel.pauseRecording()),
                  color: Theme.of(context).colorScheme.onInverseSurface,
                  icon: const Icon(Icons.pause),
                )
              else if (viewModel.recordingPhase == RecordingCapturePhase.paused)
                IconButton(
                  key: const Key('mini-resume-recording'),
                  tooltip: 'Resume recording',
                  onPressed: () => unawaited(viewModel.resumeRecording()),
                  color: Theme.of(context).colorScheme.onInverseSurface,
                  icon: const Icon(Icons.play_arrow),
                ),
              Icon(
                Icons.open_in_full,
                color: Theme.of(context).colorScheme.onInverseSurface,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

IconData _outboxIcon(CaptureOutboxKind kind) => switch (kind) {
  CaptureOutboxKind.quickNote => Icons.note_alt_outlined,
  CaptureOutboxKind.source => Icons.text_snippet_outlined,
  CaptureOutboxKind.link || CaptureOutboxKind.web => Icons.link,
  CaptureOutboxKind.document => Icons.description_outlined,
  CaptureOutboxKind.photo => Icons.photo_outlined,
  CaptureOutboxKind.audio => Icons.audio_file_outlined,
};

String _outboxTitle(CaptureOutboxEntry entry) {
  final title = entry.payload['title'];
  return title is String && title.trim().isNotEmpty
      ? title.trim()
      : switch (entry.kind) {
          CaptureOutboxKind.quickNote => 'Quick note',
          CaptureOutboxKind.source => 'Text source',
          CaptureOutboxKind.link => 'Webpage snapshot',
          CaptureOutboxKind.web => 'Web research',
          CaptureOutboxKind.document => 'Document',
          CaptureOutboxKind.photo => 'Photo',
          CaptureOutboxKind.audio => 'Imported audio',
        };
}

String _miniStatus(CaptureViewModel viewModel) {
  final minutes = viewModel.recordingSeconds ~/ 60;
  final seconds = viewModel.recordingSeconds % 60;
  final time =
      '${minutes.toString().padLeft(2, '0')}:'
      '${seconds.toString().padLeft(2, '0')}';
  return switch (viewModel.recordingPhase) {
    RecordingCapturePhase.recording => '$time · Recording in app',
    RecordingCapturePhase.paused => '$time · Paused',
    RecordingCapturePhase.uploading =>
      '${(viewModel.recordingUploadProgress * 100).round()}% · Uploading safely',
    RecordingCapturePhase.completed => 'Saved · Tap to review',
    RecordingCapturePhase.localOnly => 'On device · Tap to retry',
    _ => 'Tap to reopen',
  };
}
