import 'dart:io';

abstract final class AppConfig {
  static const apiToken = String.fromEnvironment('GUNTHER_API_TOKEN');

  static String get apiBaseUrl {
    const configured = String.fromEnvironment('GUNTHER_API_URL');
    if (configured.isNotEmpty) return configured;
    return Platform.isAndroid
        ? 'http://10.0.2.2:8787/api/'
        : 'http://127.0.0.1:8787/api/';
  }
}
