import 'package:flutter/material.dart';
import 'package:gunther_mobile/core/theme/app_theme.dart';
import 'package:gunther_mobile/data/repositories/knowledge_repository.dart';
import 'package:gunther_mobile/data/services/capture_device_service.dart';
import 'package:gunther_mobile/data/services/capture_outbox_service.dart';
import 'package:gunther_mobile/data/services/live_transcription_service.dart';
import 'package:gunther_mobile/data/services/recording_draft_store.dart';
import 'package:gunther_mobile/data/services/system_share_ingress_service.dart';
import 'package:gunther_mobile/features/shell/presentation/app_shell.dart';
import 'package:gunther_mobile/features/connection/presentation/connection_controller.dart';

class GuntherApp extends StatelessWidget {
  const GuntherApp({
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
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Gunther',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light,
      home: AppShell(
        repository: repository,
        captureDevice: captureDevice,
        recordingDraftStore: recordingDraftStore,
        captureOutboxStore: captureOutboxStore,
        liveTranscriptionFactory: liveTranscriptionFactory,
        connectionController: connectionController,
        systemShareIngress: systemShareIngress,
        lostPhotoIngress: lostPhotoIngress,
      ),
    );
  }
}
