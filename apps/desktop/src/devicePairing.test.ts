import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import {
  buildPairingCopyText,
  classifyMobileConnectionAddress,
  findDevicePairedForSession,
  formatDeviceLastUsed,
  formatPairingExpiry,
  isPairingExpired,
  mobileConnectionFromGateway,
  sortPairedDevices,
  type DevicePairingSession,
  type PairedDevice,
} from "./devicePairing";

const caCertificate = `-----BEGIN CERTIFICATE-----
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
-----END CERTIFICATE-----\n`;
const caFingerprint = "E1:9E:F7:1A:A2:30:5C:CF:5E:6C:41:49:4B:CC:EA:AA:AD:1D:57:CC:07:70:60:25:F2:BD:15:F1:A2:19:6E:27";
const normalizedCaFingerprint = caFingerprint.replaceAll(":", "").toLowerCase();

const pairing: DevicePairingSession = {
  pairingId: "pair_1234567890abcdef1234567890abcdef",
  pairingCode: "pairing-code-that-is-long-enough-to-be-a-secret-value",
  workspaceId: "workspace_123",
  workspaceName: "My knowledge",
  protocolVersion: 1,
  scopes: ["api:access", "transcription:stream"],
  createdAt: "2026-08-30T01:00:00Z",
  expiresAt: "2026-08-30T01:02:00Z",
};

const device = (overrides: Partial<PairedDevice>): PairedDevice => ({
  id: "device_1",
  workspaceId: "workspace_123",
  name: "Keke's phone",
  platform: "ios",
  scopes: ["api:access"],
  createdAt: "2026-08-29T00:00:00Z",
  lastUsedAt: null,
  revokedAt: null,
  ...overrides,
});

describe("mobile connection address policy", () => {
  it("offers only an HTTPS address to a mobile device", () => {
    const secure = classifyMobileConnectionAddress("https://knowledge.example.test/");
    const local = classifyMobileConnectionAddress("http://127.0.0.1:8787");
    const localTls = classifyMobileConnectionAddress("https://127.0.0.1:8788/api/");
    const plaintextLan = classifyMobileConnectionAddress("http://192.168.1.5:8787");

    expect(secure).toMatchObject({
      kind: "secure",
      connectionAddress: "https://knowledge.example.test/",
    });
    expect(local).toMatchObject({ kind: "local-only", connectionAddress: null });
    expect(localTls).toMatchObject({ kind: "local-only", connectionAddress: null });
    expect(plaintextLan).toMatchObject({ kind: "unsafe", connectionAddress: null });
  });

  it("never turns loopback or a URL token into pairing details", () => {
    const local = classifyMobileConnectionAddress("http://127.0.0.1:8787");
    const tokenInUrl = classifyMobileConnectionAddress(
      "https://knowledge.example.test/api/?token=sidecar-launch-token",
    );

    expect(() => buildPairingCopyText(pairing, local)).toThrow(
      "Secure mobile gateway details are incomplete",
    );
    expect(tokenInUrl).toMatchObject({ kind: "unsafe", connectionAddress: null });
  });

  it("builds the strict mobile bundle with the complete public CA last", () => {
    const certificateDer = Buffer.from(
      caCertificate
        .replace("-----BEGIN CERTIFICATE-----", "")
        .replace("-----END CERTIFICATE-----", "")
        .replaceAll(/\s/g, ""),
      "base64",
    );
    expect(createHash("sha256").update(certificateDer).digest("hex"))
      .toBe(normalizedCaFingerprint);

    const gateway = mobileConnectionFromGateway({
      enabled: true,
      running: true,
      address: "https://192.168.1.8:8788/api/",
      caFingerprint,
      caCertificatePem: caCertificate,
      protocolVersion: 1,
      error: null,
    });
    const text = buildPairingCopyText(pairing, gateway);

    expect(text).toContain("Connection address: https://192.168.1.8:8788/api/");
    expect(text).toContain(`Pairing ID: ${pairing.pairingId}`);
    expect(text).toContain(`Pairing code: ${pairing.pairingCode}`);
    expect(text).toContain(`CA SHA-256: ${normalizedCaFingerprint}`);
    expect(text).toContain("CA certificate:\n-----BEGIN CERTIFICATE-----");
    expect(text.endsWith(caCertificate.trim())).toBe(true);
    expect(text).not.toContain("sidecar-launch-token");
  });
});

describe("pairing and device presentation", () => {
  it("formats a deterministic expiry countdown", () => {
    const now = Date.parse("2026-08-30T01:00:01Z");
    expect(formatPairingExpiry(pairing.expiresAt, now)).toBe("Expires in 1m 59s");
    expect(isPairingExpired(pairing.expiresAt, Date.parse(pairing.expiresAt))).toBe(true);
  });

  it("formats recent use without exposing raw timestamps", () => {
    const now = Date.parse("2026-08-30T02:00:00Z");
    expect(formatDeviceLastUsed(null, now)).toBe("Never connected");
    expect(formatDeviceLastUsed("2026-08-30T01:45:00Z", now)).toBe("Used 15 minutes ago");
  });

  it("keeps active devices before revoked devices and orders by activity", () => {
    const sorted = sortPairedDevices([
      device({ id: "revoked", revokedAt: "2026-08-30T00:00:00Z" }),
      device({ id: "older", lastUsedAt: "2026-08-29T01:00:00Z" }),
      device({ id: "newer", lastUsedAt: "2026-08-30T01:00:00Z" }),
    ]);

    expect(sorted.map((item) => item.id)).toEqual(["newer", "older", "revoked"]);
  });

  it("detects only a new active device created for the current pairing session", () => {
    const beforePairing = device({
      id: "before",
      createdAt: "2026-08-30T00:59:59Z",
    });
    const revokedAfterPairing = device({
      id: "revoked-after",
      createdAt: pairing.createdAt,
      revokedAt: "2026-08-30T01:00:01Z",
    });
    const otherWorkspace = device({
      id: "other-workspace",
      workspaceId: "workspace_456",
      createdAt: "2026-08-30T01:00:01Z",
    });
    const connected = device({
      id: "connected",
      createdAt: "2026-08-30T01:00:02Z",
    });

    expect(findDevicePairedForSession(pairing, [
      beforePairing,
      revokedAfterPairing,
      otherWorkspace,
    ])).toBeNull();
    expect(findDevicePairedForSession(pairing, [
      beforePairing,
      connected,
      revokedAfterPairing,
      otherWorkspace,
    ])?.id).toBe("connected");
  });
});
