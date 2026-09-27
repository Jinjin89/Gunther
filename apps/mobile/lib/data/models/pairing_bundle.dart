import 'package:gunther_mobile/data/models/connection_profile.dart';
import 'package:gunther_mobile/data/services/connection_http_client.dart';

class PairingBundle {
  const PairingBundle({
    required this.baseUri,
    required this.pairingId,
    required this.pairingCode,
    required this.trustMode,
    this.caFingerprint,
    this.caCertificatePem,
  });

  factory PairingBundle.parse(String input) {
    if (input.isEmpty ||
        input.length > 300 * 1024 ||
        input.contains('\u0000')) {
      throw const FormatException('Pairing details are empty or too large.');
    }
    final normalized = input.replaceAll('\r\n', '\n').replaceAll('\r', '\n');
    final lines = normalized.split('\n');
    final firstContent = lines.indexWhere((line) => line.trim().isNotEmpty);
    if (firstContent < 0 ||
        lines[firstContent].trim() != 'Gunther device pairing') {
      throw const FormatException('This is not a Gunther pairing bundle.');
    }
    if (RegExp(
      r'^[ \t]*(Access token|Authorization|X-Gunther-Token):',
      caseSensitive: false,
      multiLine: true,
    ).hasMatch(normalized)) {
      throw const FormatException(
        'Pairing details contain a forbidden secret.',
      );
    }

    final fields = <String, String>{};
    var certificateStart = -1;
    for (var index = firstContent + 1; index < lines.length; index += 1) {
      final line = lines[index];
      if (line.trim() == 'CA certificate:') {
        if (certificateStart >= 0) {
          throw const FormatException(
            'Pairing details contain duplicate fields.',
          );
        }
        certificateStart = index + 1;
        break;
      }
      final separator = line.indexOf(':');
      if (separator <= 0) continue;
      final key = line.substring(0, separator).trim();
      if (!const {
        'Connection address',
        'Pairing ID',
        'Pairing code',
        'CA SHA-256',
      }.contains(key)) {
        continue;
      }
      if (fields.containsKey(key)) {
        throw const FormatException(
          'Pairing details contain duplicate fields.',
        );
      }
      fields[key] = line.substring(separator + 1).trim();
    }

    final address = fields['Connection address'];
    final pairingId = fields['Pairing ID'];
    final pairingCode = fields['Pairing code'];
    if (address == null || pairingId == null || pairingCode == null) {
      throw const FormatException('Pairing details are incomplete.');
    }
    final baseUri = ConnectionProfile.normalizeBaseUri(Uri.parse(address));
    if (!RegExp(r'^pair_[a-f0-9]{24,35}$').hasMatch(pairingId) ||
        !RegExp(r'^[A-Za-z0-9_-]{43,128}$').hasMatch(pairingCode)) {
      throw const FormatException('Pairing ID or one-time code is invalid.');
    }

    final fingerprint = fields['CA SHA-256']?.toLowerCase();
    final certificate = certificateStart < 0
        ? null
        : lines.sublist(certificateStart).join('\n').trim();
    if ((fingerprint == null) != (certificate == null)) {
      throw const FormatException(
        'Private certificate details must include both certificate and fingerprint.',
      );
    }
    if (fingerprint != null && certificate != null) {
      if (!RegExp(r'^[a-f0-9]{64}$').hasMatch(fingerprint)) {
        throw const FormatException('CA fingerprint is invalid.');
      }
      verifyPinnedCaFingerprint(
        certificatePem: certificate,
        expectedSha256: fingerprint,
      );
    }

    return PairingBundle(
      baseUri: baseUri,
      pairingId: pairingId,
      pairingCode: pairingCode,
      trustMode: fingerprint == null
          ? ConnectionTrustMode.system
          : ConnectionTrustMode.pinnedCertificate,
      caFingerprint: fingerprint,
      caCertificatePem: certificate,
    );
  }

  final Uri baseUri;
  final String pairingId;
  final String pairingCode;
  final ConnectionTrustMode trustMode;
  final String? caFingerprint;
  final String? caCertificatePem;
}
