import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:gunther_mobile/data/models/connection_profile.dart';
import 'package:gunther_mobile/data/models/pairing_bundle.dart';
import 'package:gunther_mobile/data/services/workspace_connection_service.dart';
import 'package:gunther_mobile/features/connection/presentation/connection_controller.dart';

class ConnectionSettingsSheet extends StatefulWidget {
  const ConnectionSettingsSheet({
    required this.controller,
    this.pairingService = const WorkspacePairingService(),
    this.onConnectionChanged,
    super.key,
  });

  final ConnectionController controller;
  final WorkspacePairingService pairingService;
  final Future<void> Function()? onConnectionChanged;

  @override
  State<ConnectionSettingsSheet> createState() =>
      _ConnectionSettingsSheetState();
}

class _ConnectionSettingsSheetState extends State<ConnectionSettingsSheet> {
  final _serverController = TextEditingController();
  final _pairingIdController = TextEditingController();
  final _pairingCodeController = TextEditingController();
  final _deviceNameController = TextEditingController(text: 'This phone');
  final _fingerprintController = TextEditingController();
  final _certificateController = TextEditingController();
  final _bundleController = TextEditingController();
  bool _showPairing = false;
  bool _privateCertificate = false;
  bool _working = false;
  String? _feedback;

  @override
  void initState() {
    super.initState();
    _showPairing = widget.controller.activeProfile == null;
  }

  @override
  void dispose() {
    _serverController.dispose();
    _pairingIdController.dispose();
    _pairingCodeController.dispose();
    _deviceNameController.dispose();
    _fingerprintController.dispose();
    _certificateController.dispose();
    _bundleController.dispose();
    super.dispose();
  }

  Future<void> _pair() async {
    if (_working) return;
    setState(() {
      _working = true;
      _feedback = null;
    });
    try {
      final paired = await widget.pairingService.exchange(
        baseUri: Uri.parse(_serverController.text.trim()),
        pairingId: _pairingIdController.text,
        pairingCode: _pairingCodeController.text,
        deviceName: _deviceNameController.text,
        platform: Platform.operatingSystem,
        trustMode: _privateCertificate
            ? ConnectionTrustMode.pinnedCertificate
            : ConnectionTrustMode.system,
        caCertificatePem: _privateCertificate
            ? _certificateController.text.trim()
            : null,
        caFingerprint: _privateCertificate
            ? _fingerprintController.text.trim()
            : null,
      );
      await widget.controller.upsertAndSelect(paired.profile, paired.secrets);
      await widget.onConnectionChanged?.call();
      if (!mounted) return;
      _pairingCodeController.clear();
      _certificateController.clear();
      setState(() {
        _showPairing = false;
        _feedback = widget.controller.status == WorkspaceConnectionStatus.online
            ? 'Connected. Captures can now sync to your workspace.'
            : widget.controller.statusMessage;
      });
    } on PairingException catch (error) {
      if (mounted) setState(() => _feedback = error.message);
    } on FormatException {
      if (mounted) {
        setState(() => _feedback = 'Check the address and pairing details.');
      }
    } on Object {
      if (mounted) {
        setState(() {
          _feedback =
              'The connection could not be saved. Your captures were not changed.';
        });
      }
    } finally {
      if (mounted) setState(() => _working = false);
    }
  }

  void _applyPairingBundle() {
    try {
      final bundle = PairingBundle.parse(_bundleController.text);
      _serverController.text = bundle.baseUri.toString();
      _pairingIdController.text = bundle.pairingId;
      _pairingCodeController.text = bundle.pairingCode;
      _fingerprintController.text = bundle.caFingerprint ?? '';
      _certificateController.text = bundle.caCertificatePem ?? '';
      _bundleController.clear();
      setState(() {
        _privateCertificate =
            bundle.trustMode == ConnectionTrustMode.pinnedCertificate;
        _feedback =
            'Pairing details verified on this device. Review the workspace address and device name, then connect.';
      });
    } on Object {
      setState(() {
        _feedback =
            'These pairing details could not be verified. Create a new bundle on the desktop.';
      });
    }
  }

  Future<void> _disconnect() async {
    final profile = widget.controller.activeProfile;
    if (profile == null || _working) return;
    setState(() {
      _working = true;
      _feedback = null;
    });
    try {
      await widget.controller.remove(profile.id);
      await widget.onConnectionChanged?.call();
      if (!mounted) return;
      setState(() {
        _showPairing = true;
        _feedback =
            'Disconnected. Captures will remain safely on this device until you connect again.';
      });
    } on Object {
      if (mounted) {
        setState(
          () => _feedback = 'The saved connection could not be removed.',
        );
      }
    } finally {
      if (mounted) setState(() => _working = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return FractionallySizedBox(
      heightFactor: 0.9,
      child: AnimatedBuilder(
        animation: widget.controller,
        builder: (context, _) {
          final profile = widget.controller.activeProfile;
          return ListView(
            key: const Key('connection-settings-sheet'),
            padding: const EdgeInsets.fromLTRB(20, 4, 20, 28),
            children: [
              Text(
                'Knowledge workspace',
                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 6),
              const Text(
                'Your phone captures first. A trusted workspace connection decides where those captures can sync.',
              ),
              const SizedBox(height: 18),
              _ConnectionStatusCard(controller: widget.controller),
              if (profile != null) ...[
                const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                      child: OutlinedButton.icon(
                        key: const Key('refresh-workspace-connection'),
                        onPressed: _working
                            ? null
                            : () => unawaited(widget.controller.refresh()),
                        icon: const Icon(Icons.refresh),
                        label: const Text('Check connection'),
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: OutlinedButton.icon(
                        key: const Key('disconnect-workspace'),
                        onPressed: _working
                            ? null
                            : () => unawaited(_disconnect()),
                        icon: const Icon(Icons.link_off),
                        label: const Text('Disconnect'),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                TextButton(
                  onPressed: _working
                      ? null
                      : () => setState(() => _showPairing = !_showPairing),
                  child: Text(
                    _showPairing
                        ? 'Hide pairing form'
                        : 'Connect another workspace',
                  ),
                ),
              ],
              if (_showPairing) ...[
                const SizedBox(height: 12),
                Text(
                  'Pair this phone',
                  style: Theme.of(
                    context,
                  ).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 4),
                const Text(
                  'Create a one-time code in Gunther on your computer. Paste all details for the safest setup, or enter the fields manually.',
                ),
                const SizedBox(height: 14),
                TextField(
                  key: const Key('pairing-bundle'),
                  controller: _bundleController,
                  enabled: !_working,
                  minLines: 3,
                  maxLines: 6,
                  autocorrect: false,
                  enableSuggestions: false,
                  decoration: const InputDecoration(
                    labelText: 'Paste pairing details from desktop',
                    hintText: 'Gunther device pairing…',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 8),
                OutlinedButton.icon(
                  key: const Key('apply-pairing-bundle'),
                  onPressed: _working ? null : _applyPairingBundle,
                  icon: const Icon(Icons.content_paste_go_outlined),
                  label: const Text('Verify and fill details'),
                ),
                const SizedBox(height: 16),
                TextField(
                  key: const Key('workspace-address'),
                  controller: _serverController,
                  enabled: !_working,
                  keyboardType: TextInputType.url,
                  autocorrect: false,
                  enableSuggestions: false,
                  decoration: const InputDecoration(
                    labelText: 'Workspace address',
                    hintText: 'https://knowledge.example/api/',
                    helperText: 'HTTPS is required outside local development.',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  key: const Key('pairing-id'),
                  controller: _pairingIdController,
                  enabled: !_working,
                  autocorrect: false,
                  enableSuggestions: false,
                  decoration: const InputDecoration(
                    labelText: 'Pairing ID',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  key: const Key('pairing-code'),
                  controller: _pairingCodeController,
                  enabled: !_working,
                  autocorrect: false,
                  enableSuggestions: false,
                  obscureText: true,
                  decoration: const InputDecoration(
                    labelText: 'One-time code',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  key: const Key('paired-device-name'),
                  controller: _deviceNameController,
                  enabled: !_working,
                  maxLength: 120,
                  decoration: const InputDecoration(
                    labelText: 'Name shown on your computer',
                    border: OutlineInputBorder(),
                  ),
                ),
                SwitchListTile.adaptive(
                  key: const Key('private-certificate-toggle'),
                  contentPadding: EdgeInsets.zero,
                  value: _privateCertificate,
                  onChanged: _working
                      ? null
                      : (value) => setState(() => _privateCertificate = value),
                  title: const Text('My workspace uses a private certificate'),
                  subtitle: const Text(
                    'Use only the certificate and fingerprint shown by your workspace administrator.',
                  ),
                ),
                if (_privateCertificate) ...[
                  TextField(
                    key: const Key('certificate-fingerprint'),
                    controller: _fingerprintController,
                    enabled: !_working,
                    autocorrect: false,
                    enableSuggestions: false,
                    decoration: const InputDecoration(
                      labelText: 'SHA-256 certificate fingerprint',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    key: const Key('certificate-pem'),
                    controller: _certificateController,
                    enabled: !_working,
                    minLines: 3,
                    maxLines: 6,
                    autocorrect: false,
                    enableSuggestions: false,
                    decoration: const InputDecoration(
                      labelText: 'CA certificate',
                      hintText: '-----BEGIN CERTIFICATE-----',
                      border: OutlineInputBorder(),
                    ),
                  ),
                ],
                const SizedBox(height: 14),
                FilledButton.icon(
                  key: const Key('pair-workspace'),
                  onPressed: _working ? null : () => unawaited(_pair()),
                  icon: _working
                      ? const SizedBox.square(
                          dimension: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.phonelink_lock),
                  label: Text(_working ? 'Connecting…' : 'Connect securely'),
                ),
              ],
              if (_feedback != null) ...[
                const SizedBox(height: 12),
                Text(
                  _feedback!,
                  key: const Key('connection-feedback'),
                  style: TextStyle(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
                ),
              ],
              const SizedBox(height: 18),
              const ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Icon(Icons.shield_outlined),
                title: Text('Credentials stay in secure device storage'),
                subtitle: Text(
                  'Gunther never puts the access token in a link, capture, transcript, or connection profile file.',
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class _ConnectionStatusCard extends StatelessWidget {
  const _ConnectionStatusCard({required this.controller});

  final ConnectionController controller;

  @override
  Widget build(BuildContext context) {
    final profile = controller.activeProfile;
    final status = controller.status;
    final (icon, color, label) = switch (status) {
      WorkspaceConnectionStatus.online => (
        Icons.cloud_done_outlined,
        Colors.green.shade700,
        'Connected',
      ),
      WorkspaceConnectionStatus.checking => (
        Icons.sync,
        Theme.of(context).colorScheme.primary,
        'Checking',
      ),
      WorkspaceConnectionStatus.unconfigured => (
        Icons.cloud_off_outlined,
        Theme.of(context).colorScheme.outline,
        'Not connected',
      ),
      WorkspaceConnectionStatus.offline => (
        Icons.cloud_off_outlined,
        Colors.orange.shade800,
        'Offline',
      ),
      WorkspaceConnectionStatus.authExpired => (
        Icons.lock_clock_outlined,
        Colors.orange.shade800,
        'Pair again',
      ),
      WorkspaceConnectionStatus.certificateChanged => (
        Icons.gpp_bad_outlined,
        Theme.of(context).colorScheme.error,
        'Security check stopped',
      ),
      WorkspaceConnectionStatus.incompatible => (
        Icons.update_outlined,
        Theme.of(context).colorScheme.error,
        'Update needed',
      ),
    };
    return Card(
      key: const Key('workspace-connection-status'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, color: color),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    profile?.label ?? label,
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: 3),
                  Text(controller.statusMessage ?? label),
                  if (profile != null) ...[
                    const SizedBox(height: 5),
                    Text(
                      profile.baseUri.host,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ],
                ],
              ),
            ),
            Text(
              label,
              style: TextStyle(color: color, fontWeight: FontWeight.w700),
            ),
          ],
        ),
      ),
    );
  }
}
