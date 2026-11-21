import 'dart:convert';
import 'dart:io';

Future<Map<String, Object?>> loadMobileLiveV2() async =>
    jsonDecode(await File('test/fixtures/mobile_live_v2.json').readAsString())
        as Map<String, Object?>;
