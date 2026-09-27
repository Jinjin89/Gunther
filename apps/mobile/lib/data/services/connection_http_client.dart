import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:gunther_mobile/data/models/connection_profile.dart';
import 'package:gunther_mobile/data/services/connection_profile_store.dart';

HttpClient createConnectionHttpClient(
  ConnectionProfile profile,
  ConnectionSecrets secrets,
) {
  return createTrustedHttpClient(
    trustMode: profile.trustMode,
    caCertificatePem: secrets.caCertificatePem,
    expectedSha256: profile.caFingerprint,
  );
}

/// Creates the same bounded TLS client before a pairing exchange has returned
/// a workspace or device identity. Pairing never weakens certificate checks:
/// private workspaces must provide one CA certificate whose DER fingerprint
/// exactly matches the value the user approved out of band.
HttpClient createTrustedHttpClient({
  required ConnectionTrustMode trustMode,
  String? caCertificatePem,
  String? expectedSha256,
}) {
  final HttpClient client;
  if (trustMode == ConnectionTrustMode.pinnedCertificate) {
    final certificate = _verifiedPinnedCertificate(
      caCertificatePem: caCertificatePem,
      expectedSha256: expectedSha256,
    );
    final context = SecurityContext(withTrustedRoots: false);
    context.setTrustedCertificatesBytes(utf8.encode(certificate));
    client = HttpClient(context: context);
  } else {
    client = HttpClient();
  }
  client.connectionTimeout = const Duration(seconds: 10);
  client.idleTimeout = const Duration(seconds: 30);
  return client;
}

/// Verifies the one-certificate private CA bundle before it reaches the TLS
/// trust store. Exposed for deterministic policy testing without network I/O.
void verifyPinnedCaFingerprint({
  required String certificatePem,
  required String expectedSha256,
}) {
  final certificates = _pemCertificates(certificatePem);
  if (certificates.length != 1) {
    throw const FormatException(
      'A pinned workspace must provide exactly one CA certificate.',
    );
  }
  final actual = sha256.convert(certificates.single.der).toString();
  if (actual != expectedSha256.toLowerCase()) {
    throw const FormatException(
      'The workspace CA certificate does not match its saved fingerprint.',
    );
  }
}

String _verifiedPinnedCertificate({
  required String? caCertificatePem,
  required String? expectedSha256,
}) {
  final certificate = caCertificatePem;
  final fingerprint = expectedSha256;
  if (certificate == null || fingerprint == null) {
    throw const FormatException(
      'The pinned workspace is missing its CA certificate.',
    );
  }
  verifyPinnedCaFingerprint(
    certificatePem: certificate,
    expectedSha256: fingerprint,
  );
  return certificate;
}

List<({String pem, Uint8List der})> _pemCertificates(String bundle) {
  final matches = RegExp(
    r'-----BEGIN CERTIFICATE-----\s*([A-Za-z0-9+/=\s]+?)\s*-----END CERTIFICATE-----',
    multiLine: true,
  ).allMatches(bundle);
  final certificates = <({String pem, Uint8List der})>[];
  var consumed = 0;
  for (final match in matches) {
    if (bundle.substring(consumed, match.start).trim().isNotEmpty) {
      throw const FormatException(
        'The workspace CA certificate contains unexpected data.',
      );
    }
    final encoded = match.group(1)!.replaceAll(RegExp(r'\s'), '');
    try {
      final der = base64Decode(encoded);
      if (der.isEmpty) throw const FormatException('empty certificate');
      certificates.add((pem: match.group(0)!, der: der));
    } on FormatException {
      throw const FormatException('The workspace CA certificate is invalid.');
    }
    consumed = match.end;
  }
  if (bundle.substring(consumed).trim().isNotEmpty) {
    throw const FormatException(
      'The workspace CA certificate contains unexpected data.',
    );
  }
  return certificates;
}
