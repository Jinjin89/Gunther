import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gunther_mobile/data/services/connection_http_client.dart';

void main() {
  test('accepts exactly one certificate with the expected DER fingerprint', () {
    final der = List<int>.generate(64, (index) => index);
    final pem = _pem(der);

    expect(
      () => verifyPinnedCaFingerprint(
        certificatePem: pem,
        expectedSha256: sha256.convert(der).toString().toUpperCase(),
      ),
      returnsNormally,
    );
  });

  test('rejects a changed, malformed, or multi-certificate CA bundle', () {
    final der = List<int>.generate(32, (index) => index + 1);
    final pem = _pem(der);
    expect(
      () => verifyPinnedCaFingerprint(
        certificatePem: pem,
        expectedSha256: List<String>.filled(32, '00').join(),
      ),
      throwsFormatException,
    );
    expect(
      () => verifyPinnedCaFingerprint(
        certificatePem: '-----BEGIN CERTIFICATE-----\nnot-base64!\n',
        expectedSha256: sha256.convert(der).toString(),
      ),
      throwsFormatException,
    );
    expect(
      () => verifyPinnedCaFingerprint(
        certificatePem: '$pem\n$pem',
        expectedSha256: sha256.convert(der).toString(),
      ),
      throwsFormatException,
    );
    expect(
      () => verifyPinnedCaFingerprint(
        certificatePem: '$pem\nnot-a-certificate',
        expectedSha256: sha256.convert(der).toString(),
      ),
      throwsFormatException,
    );
  });
}

String _pem(List<int> der) {
  return '-----BEGIN CERTIFICATE-----\n${base64Encode(der)}\n'
      '-----END CERTIFICATE-----';
}
