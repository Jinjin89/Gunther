export type DeviceScope = "api:access" | "transcription:stream";

export interface CreateDevicePairingInput {
  scopes: DeviceScope[];
  expiresInSeconds: number;
}

export interface DevicePairingSession {
  pairingId: string;
  pairingCode: string;
  workspaceId: string;
  workspaceName: string;
  protocolVersion: number;
  scopes: DeviceScope[];
  createdAt: string;
  expiresAt: string;
}

export interface PairedDevice {
  id: string;
  workspaceId: string;
  name: string;
  platform: string;
  scopes: DeviceScope[];
  createdAt: string;
  lastUsedAt: string | null;
  revokedAt: string | null;
}

export interface WorkspaceBootstrap {
  workspaceId: string;
  workspaceName: string;
  protocolVersion: number;
  minimumProtocolVersion: number;
  authKind: "sidecar" | "device" | "development";
  deviceId: string | null;
  scopes: DeviceScope[];
  capabilities: string[];
}

export interface MobileGatewayStatus {
  enabled: boolean;
  running: boolean;
  address: string | null;
  caFingerprint: string | null;
  caCertificatePem: string | null;
  protocolVersion: number;
  error: string | null;
}

export interface MobileConnectionAddress {
  kind: "secure" | "local-only" | "unsafe" | "unavailable";
  displayValue: string;
  connectionAddress: string | null;
  explanation: string;
  caFingerprint: string | null;
  caCertificatePem: string | null;
  protocolVersion: number | null;
}

export const DEFAULT_DEVICE_SCOPES: readonly DeviceScope[] = [
  "api:access",
  "transcription:stream",
];

const isLoopbackHost = (host: string): boolean => (
  host === "localhost"
  || host.endsWith(".localhost")
  || host === "::1"
  || host === "[::1]"
  || /^127(?:\.\d{1,3}){3}$/.test(host)
  || host.startsWith("[::ffff:127.")
);

const parseTime = (value: string): number | null => {
  const milliseconds = Date.parse(value);
  return Number.isFinite(milliseconds) ? milliseconds : null;
};

export function classifyMobileConnectionAddress(
  rawBaseUrl: string | null | undefined,
): MobileConnectionAddress {
  if (!rawBaseUrl) {
    return {
      kind: "unavailable",
      displayValue: "Unavailable · secure mobile gateway is not running",
      connectionAddress: null,
      explanation: "Configure a trusted HTTPS gateway before connecting a phone.",
      caFingerprint: null,
      caCertificatePem: null,
      protocolVersion: null,
    };
  }

  let parsed: URL;
  try {
    parsed = new URL(rawBaseUrl);
  } catch {
    return {
      kind: "unavailable",
      displayValue: "Unavailable · connection address is invalid",
      connectionAddress: null,
      explanation: "Configure a valid HTTPS gateway before connecting a phone.",
      caFingerprint: null,
      caCertificatePem: null,
      protocolVersion: null,
    };
  }

  if (parsed.username || parsed.password || parsed.hash || parsed.search) {
    return {
      kind: "unsafe",
      displayValue: "Blocked · unsafe connection address",
      connectionAddress: null,
      explanation: "Credentials, query parameters, and fragments are not allowed in a mobile connection address.",
      caFingerprint: null,
      caCertificatePem: null,
      protocolVersion: null,
    };
  }

  const host = parsed.hostname.toLowerCase();
  if (isLoopbackHost(host)) {
    return {
      kind: "local-only",
      displayValue: "Unavailable · desktop service is local-only",
      connectionAddress: null,
      explanation:
        "The desktop service is bound to 127.0.0.1. On a phone, 127.0.0.1 means the phone itself, so Gunther will not present it as a pairing address.",
      caFingerprint: null,
      caCertificatePem: null,
      protocolVersion: null,
    };
  }

  if (parsed.protocol !== "https:") {
    return {
      kind: "unsafe",
      displayValue: "Blocked · HTTPS is required",
      connectionAddress: null,
      explanation: "Plaintext network addresses are never offered to a paired device.",
      caFingerprint: null,
      caCertificatePem: null,
      protocolVersion: null,
    };
  }

  const normalized = parsed.toString();
  return {
    kind: "secure",
    displayValue: normalized,
    connectionAddress: normalized,
    explanation: "This HTTPS address can be included in the one-time pairing details.",
    caFingerprint: null,
    caCertificatePem: null,
    protocolVersion: null,
  };
}

export function mobileConnectionFromGateway(
  status: MobileGatewayStatus | null,
): MobileConnectionAddress {
  if (!status) return classifyMobileConnectionAddress(null);
  if (!status.enabled) {
    return {
      ...classifyMobileConnectionAddress(null),
      displayValue: "Unavailable · secure mobile gateway is disabled",
      explanation: status.error ?? "Enable the HTTPS gateway before connecting a phone.",
    };
  }
  if (!status.running) {
    return {
      ...classifyMobileConnectionAddress(null),
      displayValue: "Unavailable · secure mobile gateway is not running",
      explanation: status.error ?? "The HTTPS gateway is still starting. Refresh in a moment.",
    };
  }

  const connection = classifyMobileConnectionAddress(status.address);
  const hasApiPath = connection.connectionAddress
    ? new URL(connection.connectionAddress).pathname.endsWith("/api/")
    : false;
  const normalizedFingerprint = status.caFingerprint?.replaceAll(":", "").toLowerCase()
    ?? null;
  const fingerprintLooksValid = normalizedFingerprint !== null
    && /^[a-f0-9]{64}$/.test(normalizedFingerprint);
  const certificateLooksValid = status.caCertificatePem?.startsWith(
    "-----BEGIN CERTIFICATE-----",
  ) && status.caCertificatePem.trimEnd().endsWith("-----END CERTIFICATE-----");
  if (
    connection.kind !== "secure"
    || !connection.connectionAddress
    || !hasApiPath
    || !fingerprintLooksValid
    || !certificateLooksValid
  ) {
    return {
      ...classifyMobileConnectionAddress(null),
      kind: "unsafe",
      displayValue: "Blocked · gateway identity is incomplete",
      explanation: "Gunther will not pair until the HTTPS address, CA fingerprint, and CA certificate are all available.",
    };
  }

  return {
    ...connection,
    caFingerprint: normalizedFingerprint,
    caCertificatePem: status.caCertificatePem,
    protocolVersion: status.protocolVersion,
    explanation: `TLS listener ready · keep both devices on the same network and allow Gunther in macOS Local Network settings · CA SHA-256 ${normalizedFingerprint}`,
  };
}

export function isPairingExpired(expiresAt: string, nowMilliseconds = Date.now()): boolean {
  const expiry = parseTime(expiresAt);
  return expiry === null || expiry <= nowMilliseconds;
}

export function formatPairingExpiry(
  expiresAt: string,
  nowMilliseconds = Date.now(),
): string {
  const expiry = parseTime(expiresAt);
  if (expiry === null) return "Expiry unavailable";

  const seconds = Math.ceil((expiry - nowMilliseconds) / 1_000);
  if (seconds <= 0) return "Expired";

  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `Expires in ${minutes}m ${remainder.toString().padStart(2, "0")}s`;
}

export function formatAbsoluteTime(value: string): string {
  const milliseconds = parseTime(value);
  if (milliseconds === null) return "Time unavailable";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(milliseconds);
}

export function formatDeviceLastUsed(
  lastUsedAt: string | null,
  nowMilliseconds = Date.now(),
): string {
  if (!lastUsedAt) return "Never connected";
  const lastUsed = parseTime(lastUsedAt);
  if (lastUsed === null) return "Last use unavailable";

  const elapsedSeconds = Math.max(0, Math.floor((nowMilliseconds - lastUsed) / 1_000));
  if (elapsedSeconds < 60) return "Used just now";
  if (elapsedSeconds < 3_600) {
    const minutes = Math.floor(elapsedSeconds / 60);
    return `Used ${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  }
  if (elapsedSeconds < 86_400) {
    const hours = Math.floor(elapsedSeconds / 3_600);
    return `Used ${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  }
  const days = Math.floor(elapsedSeconds / 86_400);
  return `Used ${days} ${days === 1 ? "day" : "days"} ago`;
}

export function buildPairingCopyText(
  pairing: DevicePairingSession,
  connection: MobileConnectionAddress,
): string {
  if (
    connection.kind !== "secure"
    || !connection.connectionAddress
    || !connection.caFingerprint
    || !connection.caCertificatePem
  ) {
    throw new Error("Secure mobile gateway details are incomplete");
  }
  return [
    "Gunther device pairing",
    `Workspace: ${pairing.workspaceName}`,
    `Workspace ID: ${pairing.workspaceId}`,
    `Protocol version: ${connection.protocolVersion ?? pairing.protocolVersion}`,
    `Connection address: ${connection.connectionAddress}`,
    `Pairing ID: ${pairing.pairingId}`,
    `Pairing code: ${pairing.pairingCode}`,
    `CA SHA-256: ${connection.caFingerprint}`,
    `Expires at: ${pairing.expiresAt}`,
    "Use this one-time code only inside Gunther mobile.",
    "CA certificate:",
    connection.caCertificatePem.trim(),
  ].join("\n");
}

export function sortPairedDevices(devices: readonly PairedDevice[]): PairedDevice[] {
  return [...devices].sort((left, right) => {
    if (Boolean(left.revokedAt) !== Boolean(right.revokedAt)) return left.revokedAt ? 1 : -1;
    return (parseTime(right.lastUsedAt ?? right.createdAt) ?? 0)
      - (parseTime(left.lastUsedAt ?? left.createdAt) ?? 0);
  });
}

export function findDevicePairedForSession(
  pairing: DevicePairingSession,
  devices: readonly PairedDevice[],
): PairedDevice | null {
  const pairingCreatedAt = parseTime(pairing.createdAt);
  if (pairingCreatedAt === null) return null;

  const candidates = devices.filter((device) => {
    if (device.revokedAt || device.workspaceId !== pairing.workspaceId) return false;
    const deviceCreatedAt = parseTime(device.createdAt);
    return deviceCreatedAt !== null && deviceCreatedAt >= pairingCreatedAt;
  });
  return candidates.sort(
    (left, right) => (parseTime(right.createdAt) ?? 0) - (parseTime(left.createdAt) ?? 0),
  )[0] ?? null;
}
