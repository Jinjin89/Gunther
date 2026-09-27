import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gunther_mobile/data/models/connection_profile.dart';
import 'package:gunther_mobile/data/models/pairing_bundle.dart';

const _desktopGoldenCa = '''-----BEGIN CERTIFICATE-----
MIICsDCCAZgCCQDtnQ5zO1s4QjANBgkqhkiG9w0BAQsFADAaMRgwFgYDVQQDDA9H
dW50aGVyLVRlc3QtQ0EwHhcNMjYwODI5MjEyNzE4WhcNMzYwODI2MjEyNzE4WjAa
MRgwFgYDVQQDDA9HdW50aGVyLVRlc3QtQ0EwggEiMA0GCSqGSIb3DQEBAQUAA4IB
DwAwggEKAoIBAQDJwPM0GCeBycxlBeHsjiuj2xn1TqtgOWTeo78J8MWjdPqaksz3
x/Ck0G9ZYH3T1W8MTko7StTRJ31irttqhy43bObHytCQY3HmQ0z9iLwvlyxqtxg5
f2gnC3Sn0kkLF/k9pIcS0Mp1tZC0q/m7QGPxd82EGNPF2sHY196gkJLFKz7msdG7
vhI8zvEz4n75ALmUmxRSpxtYyRn6RpIuAOzTASP6zo8g0+lcfBkgAnq+Rz2Dpc1v
CphoNHecl6/fB5ra6sgu2znl3Bix6uGQqgj/pWcMOEvjfSVK6tafnttRc9AleRSs
7+Fk32WZyfpNb+Y0KhhJBS0yYCP6bx2SrZhZAgMBAAEwDQYJKoZIhvcNAQELBQAD
ggEBAKwopQQoIyTM40rdYLzXh9STcknP8F4wXjWzY+TQA277ZOSpCwDNnD16CPI3
K1yf0e5aN6ny7J4HphDn0R4vmu8RLZDk+DW0upnjfRsiWGw6f+Vr8Wo/pshFviVd
y4+EDjqF8Kuk04F+0PhW2IOJymOuRTwROhI032UADdaFHJDMOASPY/plqv4OhJGL
9x7KXRK4/xdCbFqOlxVfBkHdNUk8L4auMFFDq9ZCqVC/w2ZdcTb7VgRIBjVzKaXb
j4wkkcpapblwNlAEKhhKVc3YdKFsU6gvwhu2EnI3kjpQSzgF7yWliE/o+3xSP1LS
1m8ngQlYjsQb/kJWck+kTZJVxDw=
-----END CERTIFICATE-----''';

void main() {
  test('parses and verifies a pinned HTTPS pairing bundle', () {
    final der = <int>[1, 2, 3, 4, 5, 6];
    final fingerprint = sha256.convert(der).toString();
    final certificate =
        '-----BEGIN CERTIFICATE-----\n${base64Encode(der)}\n-----END CERTIFICATE-----';

    final bundle = PairingBundle.parse('''
Gunther device pairing
Workspace: Bioinformatics
Connection address: https://gunther-mac.local:8788/api/
Pairing ID: pair_0123456789abcdef01234567
Pairing code: abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ
CA SHA-256: $fingerprint
CA certificate:
$certificate
''');

    expect(bundle.baseUri.toString(), 'https://gunther-mac.local:8788/api/');
    expect(bundle.trustMode, ConnectionTrustMode.pinnedCertificate);
    expect(bundle.caFingerprint, fingerprint);
    expect(bundle.caCertificatePem, certificate);
  });

  test('accepts a system-trusted bundle without private CA material', () {
    final bundle = PairingBundle.parse('''
Gunther device pairing
Connection address: https://knowledge.example/api/
Pairing ID: pair_0123456789abcdef01234567
Pairing code: abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ
''');

    expect(bundle.trustMode, ConnectionTrustMode.system);
    expect(bundle.caCertificatePem, isNull);
  });

  test('accepts the desktop golden bundle with a real X.509 CA', () {
    const fingerprint =
        'e19ef71aa2305ccf5e6c41494bcceaaaad1d57cc07706025f2bd15f1a2196e27';
    final bundle = PairingBundle.parse('''
Gunther device pairing
Workspace: My knowledge
Workspace ID: workspace_123
Protocol version: 1
Connection address: https://192.168.1.12:8788/api/
Pairing ID: pair_1234567890abcdef12345678
Pairing code: pairing-code-that-is-long-enough-to-be-a-secret-value
CA SHA-256: $fingerprint
Expires at: 2026-08-30T01:02:00Z
Use this one-time code only inside Gunther mobile.
CA certificate:
$_desktopGoldenCa
''');

    expect(bundle.caFingerprint, fingerprint);
    expect(bundle.caCertificatePem, _desktopGoldenCa);
    expect(bundle.baseUri.toString(), 'https://192.168.1.12:8788/api/');
  });

  test('rejects changed certificates, cleartext LAN, and leaked tokens', () {
    final cases = <String>[
      '''
Gunther device pairing
Connection address: https://gunther.local:8788/api/
Pairing ID: pair_0123456789abcdef01234567
Pairing code: abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ
CA SHA-256: ${List<String>.filled(64, '0').join()}
CA certificate:
-----BEGIN CERTIFICATE-----
AQID
-----END CERTIFICATE-----
''',
      '''
Gunther device pairing
Connection address: http://192.168.1.8:8788/api/
Pairing ID: pair_0123456789abcdef01234567
Pairing code: abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ
''',
      '''
Gunther device pairing
Connection address: https://knowledge.example/api/
Pairing ID: pair_0123456789abcdef01234567
Pairing code: abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ
Authorization: Bearer forbidden
''',
      '''
Gunther device pairing
Connection address: https://knowledge.example/api/
Pairing ID: pair_0123456789abcdef01234567
Pairing code: abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ
   X-Gunther-Token: forbidden-even-when-indented
''',
    ];

    for (final value in cases) {
      expect(() => PairingBundle.parse(value), throwsFormatException);
    }
  });
}
