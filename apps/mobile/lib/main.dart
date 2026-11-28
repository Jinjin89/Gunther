import 'package:flutter/material.dart';
import 'package:gunther_mobile/app.dart';
import 'package:gunther_mobile/data/repositories/connection_aware_knowledge_repository.dart';
import 'package:gunther_mobile/data/services/connection_http_client.dart';
import 'package:gunther_mobile/data/services/connection_profile_store.dart';
import 'package:gunther_mobile/data/services/live_transcription_service.dart';
import 'package:gunther_mobile/data/services/platform_connection_secret_store.dart';
import 'package:gunther_mobile/data/services/workspace_connection_service.dart';
import 'package:gunther_mobile/features/connection/presentation/connection_controller.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final profileStore = FileConnectionProfileStore();
  final secretStore = PlatformConnectionSecretStore();
  final connectionController = ConnectionController(
    profileStore: profileStore,
    secretStore: secretStore,
    probe: const WorkspaceConnectionProbe(),
  );
  await connectionController.initialize();

  final repository = ConnectionAwareKnowledgeRepository(
    connections: connectionController,
  );
  runApp(
    GuntherApp(
      repository: repository,
      connectionController: connectionController,
      liveTranscriptionFactory: (context) {
        try {
          final profile = connectionController.activeProfile;
          final secrets = connectionController.activeSecrets;
          if (profile != null &&
              secrets != null &&
              connectionController.canUseWorkspace) {
            return LiveTranscriptionService(
              baseUri: profile.baseUri,
              bearerToken: secrets.accessToken,
              httpClient: createConnectionHttpClient(profile, secrets),
              context: context,
            );
          }
          return null;
        } on ArgumentError {
          // Local recording remains available when the profile cannot start STT.
          return null;
        } on FormatException {
          return null;
        }
      },
    ),
  );
}
