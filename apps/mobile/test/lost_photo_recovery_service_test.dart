import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:gunther_mobile/data/services/lost_photo_recovery_service.dart';

void main() {
  test(
    'retrieveLostData results become app-owned before picker files disappear',
    () async {
      final root = await Directory.systemTemp.createTemp('gunther-lost-photo-');
      final pickerRoot = await Directory.systemTemp.createTemp(
        'gunther-picker-temp-',
      );
      addTearDown(() => root.delete(recursive: true));
      addTearDown(() => pickerRoot.delete(recursive: true));
      final first = File('${pickerRoot.path}/camera.jpg');
      final second = File('${pickerRoot.path}/gallery.png');
      await first.writeAsBytes([1, 2, 3], flush: true);
      await second.writeAsBytes([4, 5, 6, 7], flush: true);
      final gateway = _MemoryLostPhotoPicker(
        LostPhotoResult(
          files: [
            LostPhotoCandidate(
              path: first.path,
              fileName: 'camera.jpg',
              mediaType: 'image/jpeg',
              sizeBytes: 3,
            ),
            LostPhotoCandidate(
              path: second.path,
              fileName: 'gallery.png',
              mediaType: 'image/png',
              sizeBytes: 4,
            ),
          ],
        ),
      );
      final service = LostPhotoRecoveryService(
        picker: gateway,
        directoryProvider: () async => root,
        platformSupported: true,
      );

      final recovered = await service.pendingItems();
      await first.delete();
      await second.writeAsBytes([9], flush: true);

      expect(gateway.calls, 1);
      expect(recovered, hasLength(2));
      expect(await File(recovered[0].path!).readAsBytes(), [1, 2, 3]);
      expect(await File(recovered[1].path!).readAsBytes(), [4, 5, 6, 7]);
      expect(
        recovered.every((item) => item.mediaType!.startsWith('image/')),
        isTrue,
      );
      expect(await service.pendingItems(), hasLength(2));
      expect(gateway.calls, 1);

      final acknowledgedPath = recovered.first.path!;
      await service.acknowledge([recovered.first.id]);
      expect(await File(acknowledgedPath).exists(), isFalse);
      expect(await service.pendingItems(), hasLength(1));
    },
  );

  test(
    'damaged recovery manifest fails closed before consuming picker data',
    () async {
      final root = await Directory.systemTemp.createTemp(
        'gunther-lost-corrupt-',
      );
      addTearDown(() => root.delete(recursive: true));
      await File(
        '${root.path}/manifest.json',
      ).writeAsString('{not-json', flush: true);
      final gateway = _MemoryLostPhotoPicker(const LostPhotoResult());
      final service = LostPhotoRecoveryService(
        picker: gateway,
        directoryProvider: () async => root,
        platformSupported: true,
      );

      await expectLater(
        service.pendingItems(),
        throwsA(isA<LostPhotoRecoveryException>()),
      );

      expect(gateway.calls, 0);
      expect(
        await File('${root.path}/manifest.json').readAsString(),
        '{not-json',
      );
    },
  );

  test('picker exception is surfaced as a bounded diagnostic code', () async {
    final root = await Directory.systemTemp.createTemp('gunther-lost-error-');
    addTearDown(() => root.delete(recursive: true));
    final gateway = _MemoryLostPhotoPicker(
      const LostPhotoResult(errorCode: 'camera_access_denied SECRET detail'),
    );
    final service = LostPhotoRecoveryService(
      picker: gateway,
      directoryProvider: () async => root,
      platformSupported: true,
    );

    await expectLater(
      service.pendingItems(),
      throwsA(
        isA<LostPhotoRecoveryException>().having(
          (error) => error.code,
          'code',
          'camera_access_denied_secret_detail',
        ),
      ),
    );
  });
}

class _MemoryLostPhotoPicker implements LostPhotoPickerGateway {
  _MemoryLostPhotoPicker(this.result);

  final LostPhotoResult result;
  int calls = 0;

  @override
  Future<LostPhotoResult> retrieveLostData() async {
    calls += 1;
    return result;
  }
}
