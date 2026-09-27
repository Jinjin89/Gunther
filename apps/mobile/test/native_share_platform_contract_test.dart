import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('Android reports partial staging and syncs fallback copies', () async {
    final source = await File(
      'android/app/src/main/kotlin/com/gunther/gunther_mobile/MainActivity.kt',
    ).readAsString();

    expect(source, contains('failedAny = true'));
    expect(source, contains('"partial_failure"'));
    expect(source, contains('FileOutputStream(destination, true).use'));
    expect(source, contains('it.fd.sync()'));
  });

  test(
    'iOS refuses nil, unsupported, empty, oversized, and timed-out shares',
    () async {
      final controller = await File(
        'ios/ShareExtension/ShareViewController.swift',
      ).readAsString();
      final queue = await File(
        'ios/Shared/ShareIngressQueue.swift',
      ).readAsString();

      expect(
        controller,
        contains('providerError ?? GuntherShareIngressError.invalidSource'),
      );
      expect(controller, contains('staged.isEmpty && stagingError == nil'));
      expect(controller, contains('cancelRequest(withError: stagingError)'));
      expect(
        queue,
        contains('maximumExtensionBytes: Int64 = 100 * 1024 * 1024'),
      );
      expect(queue, contains('maximumExtensionSeconds: TimeInterval = 20'));
      expect(queue, contains('GuntherShareIngressError.stagingTimedOut'));
    },
  );
}
