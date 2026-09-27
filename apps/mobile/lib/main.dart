import 'package:flutter/material.dart';
import 'package:gunther_mobile/app.dart';
import 'package:gunther_mobile/core/config/app_config.dart';
import 'package:gunther_mobile/data/repositories/connection_aware_knowledge_repository.dart';
import 'package:gunther_mobile/data/repositories/knowledge_repository.dart';
import 'package:gunther_mobile/data/services/connection_http_client.dart';
import 'package:gunther_mobile/data/services/connection_profile_store.dart';
import 'package:gunther_mobile/data/services/knowledge_api_service.dart';
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

  final fallbackApiService = KnowledgeApiService(
    baseUrl: AppConfig.apiBaseUrl,
    apiToken: AppConfig.apiToken,
  );
  final repository = ConnectionAwareKnowledgeRepository(
    connections: connectionController,
    fallback: RemoteKnowledgeRepository(apiService: fallbackApiService),
  );
  runApp(
    GuntherApp(
      repository: repository,
      connectionController: connectionController,
      liveTranscriptionFactory: (context) {
        try {
          final profile = connectionController.activeProfile;
          final secrets = connectionController.activeSecrets;
          final mayUsePairedWorkspace = switch (connectionController.status) {
            WorkspaceConnectionStatus.checking ||
            WorkspaceConnectionStatus.online ||
            WorkspaceConnectionStatus.offline => true,
            WorkspaceConnectionStatus.unconfigured ||
            WorkspaceConnectionStatus.authExpired ||
            WorkspaceConnectionStatus.certificateChanged ||
            WorkspaceConnectionStatus.incompatible => false,
          };
          if (profile != null && secrets != null && mayUsePairedWorkspace) {
            return LiveTranscriptionService(
              baseUri: profile.baseUri,
              bearerToken: secrets.accessToken,
              httpClient: createConnectionHttpClient(profile, secrets),
              context: context,
            );
          }
          return LiveTranscriptionService(
            baseUri: Uri.parse(AppConfig.apiBaseUrl),
            apiToken: AppConfig.apiToken,
            context: context,
          );
        } on ArgumentError {
          // Cleartext non-loopback endpoints are intentionally unavailable to
          // live audio. A runtime HTTPS workspace profile will replace this
          // compile-time bridge without weakening recording persistence.
          return null;
        } on FormatException {
          return null;
        }
      },
    ),
  );
}
