export const LOCAL_CAPTURE_QUEUE_KEY = "gunther:local-captures";
export const LOCAL_CAPTURE_QUARANTINE_KEY = "gunther:local-captures-quarantine";

export interface StoredCapture {
  id: string;
  title: string;
  content: string;
  kind: string;
  baseId: string | null;
  workspaceId: string | null;
  url?: string;
  createdAt?: string;
}

interface QuarantineEntry {
  quarantinedAt: string;
  reason: string;
  payload: unknown;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const normalizeStoredCapture = (value: unknown): StoredCapture | null => {
  if (!isRecord(value)) return null;
  if (
    typeof value.id !== "string"
    || typeof value.title !== "string"
    || typeof value.content !== "string"
    || typeof value.kind !== "string"
    || !(value.baseId === null || typeof value.baseId === "string")
    || !(value.workspaceId === undefined || value.workspaceId === null || typeof value.workspaceId === "string")
    || !(value.url === undefined || typeof value.url === "string")
    || !(value.createdAt === undefined || typeof value.createdAt === "string")
  ) return null;

  return {
    id: value.id,
    title: value.title,
    content: value.content,
    kind: value.kind,
    baseId: value.baseId,
    // Captures created before workspace binding was introduced remain
    // preserved, but null makes them ineligible for automatic delivery.
    workspaceId: value.workspaceId ?? null,
    ...(value.url === undefined ? {} : { url: value.url }),
    ...(value.createdAt === undefined ? {} : { createdAt: value.createdAt }),
  };
};

const quarantine = (storage: Storage, reason: string, payload: unknown) => {
  const entry: QuarantineEntry = {
    quarantinedAt: new Date().toISOString(),
    reason,
    payload,
  };
  const existingRaw = storage.getItem(LOCAL_CAPTURE_QUARANTINE_KEY);
  let existing: unknown[] = [];
  if (existingRaw) {
    try {
      const parsed = JSON.parse(existingRaw) as unknown;
      existing = Array.isArray(parsed) ? parsed : [{ payload: existingRaw }];
    } catch {
      existing = [{ payload: existingRaw }];
    }
  }
  storage.setItem(LOCAL_CAPTURE_QUARANTINE_KEY, JSON.stringify([...existing, entry]));
};

export const loadStoredCaptures = (storage: Storage = window.localStorage): StoredCapture[] => {
  const raw = storage.getItem(LOCAL_CAPTURE_QUEUE_KEY);
  if (!raw) return [];

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw) as unknown;
  } catch {
    quarantine(storage, "invalid-json", raw);
    storage.setItem(LOCAL_CAPTURE_QUEUE_KEY, "[]");
    return [];
  }
  if (!Array.isArray(parsed)) {
    quarantine(storage, "queue-is-not-an-array", parsed);
    storage.setItem(LOCAL_CAPTURE_QUEUE_KEY, "[]");
    return [];
  }

  const captures: StoredCapture[] = [];
  const invalid: unknown[] = [];
  for (const candidate of parsed) {
    const capture = normalizeStoredCapture(candidate);
    if (capture) captures.push(capture);
    else invalid.push(candidate);
  }
  if (invalid.length) quarantine(storage, "invalid-queue-elements", invalid);

  // Persist the normalized list so malformed elements cannot poison a later
  // successful removal, and legacy entries acquire an explicit null binding.
  storage.setItem(LOCAL_CAPTURE_QUEUE_KEY, JSON.stringify(captures));
  return captures;
};

export const appendStoredCapture = (
  capture: StoredCapture,
  storage: Storage = window.localStorage,
) => {
  const captures = loadStoredCaptures(storage);
  storage.setItem(LOCAL_CAPTURE_QUEUE_KEY, JSON.stringify([...captures, capture]));
};

export const removeStoredCapture = (
  captureId: string,
  storage: Storage = window.localStorage,
) => {
  storage.setItem(
    LOCAL_CAPTURE_QUEUE_KEY,
    JSON.stringify(loadStoredCaptures(storage).filter((capture) => capture.id !== captureId)),
  );
};
