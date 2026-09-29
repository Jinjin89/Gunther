const DATABASE_NAME = "gunther-recording-spool";
const DATABASE_VERSION = 2;
const CHUNK_STORE = "chunks";
const SESSION_INDEX = "by-session";

export type RecordingSpoolSource = "live" | "import";

export interface RecordingSpoolChunk {
  key: string;
  sessionId: string;
  sequence: number;
  blob: Blob;
  mimeType: string;
  sizeBytes: number;
  checksum: string;
  workspaceId: string;
  source: RecordingSpoolSource;
  createdAt: string;
}

interface StoredRecordingSpoolChunk extends Partial<RecordingSpoolChunk> {
  key: string;
  sessionId: string;
  sequence: number;
  blob: Blob;
  mimeType: string;
  createdAt: string;
}

let databasePromise: Promise<IDBDatabase> | null = null;

function chunkKey(sessionId: string, sequence: number) {
  return `${sessionId}:${sequence.toString().padStart(12, "0")}`;
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB request failed."));
  });
}

function transactionFinished(transaction: IDBTransaction): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = () => reject(transaction.error ?? new Error("IndexedDB transaction was aborted."));
    transaction.onerror = () => reject(transaction.error ?? new Error("IndexedDB transaction failed."));
  });
}

async function blobBytes(blob: Blob): Promise<Uint8Array> {
  if (typeof blob.arrayBuffer === "function") {
    return new Uint8Array(await blob.arrayBuffer());
  }
  return new Promise<Uint8Array>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error ?? new Error("Recording chunk could not be read."));
    reader.readAsArrayBuffer(blob);
  });
}

export async function recordingBlobChecksum(blob: Blob): Promise<string> {
  if (!globalThis.crypto?.subtle) {
    throw new Error("SHA-256 is unavailable in this web view.");
  }
  const bytes = await blobBytes(blob);
  const digest = await globalThis.crypto.subtle.digest(
    "SHA-256",
    bytes as Uint8Array<ArrayBuffer>,
  );
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function normalizedStoredChunk(chunk: StoredRecordingSpoolChunk): RecordingSpoolChunk {
  if (
    !chunk.sessionId ||
    !Number.isSafeInteger(chunk.sequence) ||
    chunk.sequence < 0 ||
    !(chunk.blob instanceof Blob)
  ) {
    throw new Error("A durable recording chunk has invalid metadata.");
  }
  return {
    key: chunk.key,
    sessionId: chunk.sessionId,
    sequence: chunk.sequence,
    blob: chunk.blob,
    mimeType: chunk.mimeType || chunk.blob.type || "audio/webm",
    sizeBytes: typeof chunk.sizeBytes === "number" ? chunk.sizeBytes : chunk.blob.size,
    checksum: typeof chunk.checksum === "string" ? chunk.checksum : "",
    workspaceId: typeof chunk.workspaceId === "string" ? chunk.workspaceId : "",
    source: chunk.source === "import" ? "import" : "live",
    createdAt: chunk.createdAt,
  };
}

async function verifyStoredChunk(chunk: RecordingSpoolChunk): Promise<RecordingSpoolChunk> {
  if (chunk.sizeBytes !== chunk.blob.size) {
    throw new Error(`Durable recording chunk ${chunk.sequence} has a size mismatch.`);
  }
  const checksum = await recordingBlobChecksum(chunk.blob);
  if (chunk.checksum && chunk.checksum !== checksum) {
    throw new Error(`Durable recording chunk ${chunk.sequence} failed SHA-256 verification.`);
  }
  return { ...chunk, checksum };
}

/** Exact idempotency guard: retries must contain the same bytes, not only the same length. */
export async function recordingChunkMatches(
  existing: RecordingSpoolChunk,
  candidate: Blob,
): Promise<boolean> {
  if (
    existing.blob.size !== candidate.size ||
    existing.mimeType !== (candidate.type || existing.mimeType)
  ) {
    return false;
  }
  if (existing.checksum && existing.checksum !== await recordingBlobChecksum(candidate)) {
    return false;
  }
  const [storedBytes, candidateBytes] = await Promise.all([
    blobBytes(existing.blob),
    blobBytes(candidate),
  ]);
  return storedBytes.every((value, index) => value === candidateBytes[index]);
}

function openDatabase(): Promise<IDBDatabase> {
  if (!window.indexedDB) {
    return Promise.reject(new Error("IndexedDB is unavailable in this web view."));
  }
  databasePromise ??= new Promise<IDBDatabase>((resolve, reject) => {
    const request = window.indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(CHUNK_STORE)) {
        const store = database.createObjectStore(CHUNK_STORE, { keyPath: "key" });
        store.createIndex(SESSION_INDEX, "sessionId", { unique: false });
      }
    };
    request.onsuccess = () => {
      const database = request.result;
      database.onversionchange = () => {
        database.close();
        databasePromise = null;
      };
      resolve(database);
    };
    request.onerror = () => {
      databasePromise = null;
      reject(request.error ?? new Error("The recording spool could not be opened."));
    };
    request.onblocked = () => {
      databasePromise = null;
      reject(new Error("The recording spool is blocked by another Gunther window."));
    };
  });
  return databasePromise;
}

/** Verify that this web view can durably transact before microphone capture starts. */
export async function assertRecordingSpoolAvailable(): Promise<void> {
  const database = await openDatabase();
  const probeKey = `__probe__:${Date.now()}:${Math.random().toString(36).slice(2)}`;
  const transaction = database.transaction(CHUNK_STORE, "readwrite");
  const store = transaction.objectStore(CHUNK_STORE);
  store.put({
    key: probeKey,
    sessionId: "__probe__",
    sequence: 0,
    blob: new Blob(["ok"], { type: "text/plain" }),
    mimeType: "text/plain",
    sizeBytes: 2,
    checksum: "probe",
    workspaceId: "__probe__",
    source: "live",
    createdAt: new Date().toISOString(),
  } satisfies RecordingSpoolChunk);
  store.delete(probeKey);
  await transactionFinished(transaction);
}

/** Store a chunk idempotently. A sequence can never be replaced by different bytes. */
export async function spoolRecordingChunk(
  sessionId: string,
  sequence: number,
  blob: Blob,
  workspaceId: string,
  source: RecordingSpoolSource = "live",
): Promise<void> {
  if (!sessionId || !workspaceId || !Number.isSafeInteger(sequence) || sequence < 0) {
    throw new Error("A durable recording chunk requires a session, workspace, and sequence.");
  }
  if (!blob.size) throw new Error("An empty recording chunk cannot be preserved.");
  const database = await openDatabase();
  const key = chunkKey(sessionId, sequence);
  const readTransaction = database.transaction(CHUNK_STORE, "readonly");
  const readStore = readTransaction.objectStore(CHUNK_STORE);
  const existing = await requestResult(
    readStore.get(key) as IDBRequest<StoredRecordingSpoolChunk | undefined>,
  );
  await transactionFinished(readTransaction);
  if (existing) {
    const normalized = normalizedStoredChunk(existing);
    if (normalized.workspaceId && normalized.workspaceId !== workspaceId) {
      throw new Error(`Recording chunk ${sequence} belongs to another workspace.`);
    }
    if (normalized.source !== source) {
      throw new Error(`Recording chunk ${sequence} belongs to another capture path.`);
    }
    if (!(await recordingChunkMatches(normalized, blob))) {
      throw new Error(`Recording chunk ${sequence} already exists with different content.`);
    }
    return;
  }

  const checksum = await recordingBlobChecksum(blob);
  const chunk = {
    key,
    sessionId,
    sequence,
    blob,
    mimeType: blob.type || "audio/webm",
    sizeBytes: blob.size,
    checksum,
    workspaceId,
    source,
    createdAt: new Date().toISOString(),
  } satisfies RecordingSpoolChunk;
  const writeTransaction = database.transaction(CHUNK_STORE, "readwrite");
  writeTransaction.objectStore(CHUNK_STORE).add(chunk);
  try {
    await transactionFinished(writeTransaction);
  } catch (error) {
    // Another window can win the add between the readonly check and this write.
    // Treat that race as idempotent only after comparing the authoritative bytes.
    const retryTransaction = database.transaction(CHUNK_STORE, "readonly");
    const raced = await requestResult(
      retryTransaction.objectStore(CHUNK_STORE).get(key) as IDBRequest<
        StoredRecordingSpoolChunk | undefined
      >,
    );
    await transactionFinished(retryTransaction);
    if (raced) {
      const normalized = normalizedStoredChunk(raced);
      if (
        (!normalized.workspaceId || normalized.workspaceId === workspaceId) &&
        normalized.source === source &&
        await recordingChunkMatches(normalized, blob)
      ) return;
    }
    if (raced) {
      throw new Error(`Recording chunk ${sequence} already exists with different content.`);
    }
    throw error;
  }
}

export async function listRecordingSpool(
  sessionId: string,
  expectedWorkspaceId?: string,
): Promise<RecordingSpoolChunk[]> {
  const database = await openDatabase();
  const transaction = database.transaction(CHUNK_STORE, "readonly");
  const index = transaction.objectStore(CHUNK_STORE).index(SESSION_INDEX);
  const chunks = await requestResult(
    index.getAll(IDBKeyRange.only(sessionId)) as IDBRequest<StoredRecordingSpoolChunk[]>,
  );
  await transactionFinished(transaction);
  const verified: RecordingSpoolChunk[] = [];
  for (const stored of chunks.sort((left, right) => left.sequence - right.sequence)) {
    const chunk = await verifyStoredChunk(normalizedStoredChunk(stored));
    if (expectedWorkspaceId && chunk.workspaceId !== expectedWorkspaceId) {
      throw new Error(
        chunk.workspaceId
          ? `Durable recording chunk ${chunk.sequence} belongs to another workspace.`
          : `Durable recording chunk ${chunk.sequence} predates workspace binding. Verify its server session before migrating it.`,
      );
    }
    verified.push(chunk);
  }
  return verified;
}

/**
 * Bind legacy v1 spool entries only after the caller has verified the server
 * session through the expected workspace. Existing foreign bindings fail closed.
 */
export async function bindRecordingSpoolWorkspace(
  sessionId: string,
  verifiedWorkspaceId: string,
): Promise<void> {
  if (!verifiedWorkspaceId) throw new Error("A verified workspace is required.");
  const database = await openDatabase();
  const readTransaction = database.transaction(CHUNK_STORE, "readonly");
  const stored = await requestResult(
    readTransaction.objectStore(CHUNK_STORE).index(SESSION_INDEX).getAll(
      IDBKeyRange.only(sessionId),
    ) as IDBRequest<StoredRecordingSpoolChunk[]>,
  );
  await transactionFinished(readTransaction);

  const migrated: RecordingSpoolChunk[] = [];
  for (const value of stored) {
    const chunk = await verifyStoredChunk(normalizedStoredChunk(value));
    if (chunk.workspaceId && chunk.workspaceId !== verifiedWorkspaceId) {
      throw new Error(`Durable recording chunk ${chunk.sequence} belongs to another workspace.`);
    }
    migrated.push({ ...chunk, workspaceId: verifiedWorkspaceId });
  }
  if (!migrated.length) return;
  const writeTransaction = database.transaction(CHUNK_STORE, "readwrite");
  const store = writeTransaction.objectStore(CHUNK_STORE);
  for (const chunk of migrated) store.put(chunk);
  await transactionFinished(writeTransaction);
}

export async function deleteRecordingSpoolChunk(
  sessionId: string,
  sequence: number,
): Promise<void> {
  const database = await openDatabase();
  const transaction = database.transaction(CHUNK_STORE, "readwrite");
  transaction.objectStore(CHUNK_STORE).delete(chunkKey(sessionId, sequence));
  await transactionFinished(transaction);
}

/** Remove only chunks the server has already committed. Unacknowledged bytes remain durable. */
export async function deleteAcknowledgedRecordingSpool(
  sessionId: string,
  nextExpectedSequence: number,
  expectedWorkspaceId?: string,
): Promise<void> {
  const chunks = await listRecordingSpool(sessionId, expectedWorkspaceId);
  const acknowledged = chunks.filter((chunk) => chunk.sequence < nextExpectedSequence);
  if (!acknowledged.length) return;
  const database = await openDatabase();
  const transaction = database.transaction(CHUNK_STORE, "readwrite");
  const store = transaction.objectStore(CHUNK_STORE);
  for (const chunk of acknowledged) store.delete(chunk.key);
  await transactionFinished(transaction);
}

