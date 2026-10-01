import type {
  AgentStep,
  ConversationContext,
  Artifact,
  ArtifactSummary,
  Assertion,
  AssetCaptureResult,
  ConversationTurn,
  CreateArtifactInput,
  CreateKnowledgeBaseInput,
  CreateKnowledgeProposalInput,
  CreateKnowledgeSessionInput,
  CreateNotebookNoteInput,
  CreateSessionMessageInput,
  CreateSourceInput,
  ImportResult,
  InboxItem,
  InboxItemState,
  InboxItemType,
  FileNotebookNoteResult,
  FileSourceResult,
  KnowledgeGraph,
  KnowledgeBaseMetadata,
  KnowledgeProposal,
  KnowledgeSearchResult,
  KnowledgeSession,
  KnowledgeSessionSummary,
  KnowledgeUnit,
  LectureSummary,
  CreateLectureSummaryInput,
  Overview,
  NotebookNote,
  RecordingCheckpointInput,
  RecordingSession,
  SourceSummary,
  SourceDetail,
  UpdateAssertionStatusInput,
  UpdateKnowledgeBaseInput,
  UpdateKnowledgeProposalInput,
  UpdateKnowledgeSessionInput,
  UpdateNotebookNoteInput,
  WebCaptureInput,
  WebCaptureResult,
  WebSearchResult,
  KnowledgeTopic,
  TopicInput,
  SourceStructure,
  SourceProcessing,
  RetrievalStatus,
  SourceDigestState,
  SourcePaper,
  TopicOverview,
  TopicSuggestions,
  StorageStatus,
  ServiceSettings,
  ServiceSettingsList,
  Effort,
  ModelMenu,
  ModelsOverview,
  ModelRole,
  ProviderInput,
  ProviderTestResult,
  SpeechOverview,
  SpeechProviderInput,
  SpeechRole,
  SpeechTestResult,
  SpeechClip,
  SpeechStart,
  TtsProviderInput,
  TtsRoleInput,
  TtsSampleInput,
  TtsOverview,
  TtsRole,
  AnswerTrace,
  BackgroundJob,
  DeveloperSettings,
  LogLine,
  ServiceSettingValue,
  ServiceTestResult,
  TrashItem,
  TrashItemKind,
} from "@gunther/contracts";
import { readServerEvents } from "./sse";
import { createArtifactSchema, webCaptureSchema } from "@gunther/contracts";
import { invoke } from "@tauri-apps/api/core";
import { log } from "./log";
import type {
  CreateDevicePairingInput,
  DevicePairingSession,
  MobileGatewayStatus,
  PairedDevice,
  WorkspaceBootstrap,
} from "./devicePairing";

/** What the agent reports while answering (see the service's message stream). */
export type AnswerEvent =
  | { type: "step"; state: "running" | "done"; tool: AgentStep["tool"]; label: string; query?: string; found?: number; error?: string | null }
  | { type: "text"; text: string }
  /** Following an answer again after leaving: the question it answers. */
  | { type: "resumed"; question: string; startedAt: number };

interface Health {
  status: "ok";
  extractionMode: "local" | "model";
  webSearchMode: "tavily" | "not_configured";
  transcriptionMode: "sensevoice_local" | "qwen" | "compatible" | "not_configured";
  transcriptionProvider: "sensevoice" | "qwen" | "compatible" | "none";
  transcriptionModel: string;
  /** The model that writes recording summaries ("DeepSeek · Flash"), or "off". */
  summaryMode: string;
  /** The model that writes each capture's summary, or "off". */
  digestMode?: string;
  analysisModel?: string | null;
  askModel?: string | null;
  digestImages?: boolean;
}

interface BackendConnection {
  baseUrl: string;
  authToken: string;
}

const tauriRuntime = "__TAURI_INTERNALS__" in window;
// The installed app's service listens on this computer only, on 28787 unless another
// program holds it (see src-tauri/src/lib.rs); development's backend is on 8787.
const isLocalServiceAddress = (url: string) => {
  const port = /^http:\/\/127\.0\.0\.1:(\d{1,5})$/.exec(url)?.[1];
  return port !== undefined && Number(port) > 0 && Number(port) <= 65535;
};
let configuredBase = tauriRuntime ? undefined : (import.meta.env.VITE_API_URL as string | undefined);
let apiBase = configuredBase ? `${configuredBase.replace(/\/$/, "")}/api` : "/api";
let authToken = "";
let connectionInitialization: Promise<void> | null = null;
let desktopBackendReady: Promise<void> | null = null;

const wait = (milliseconds: number) => new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));

async function initializeBackendConnection(): Promise<void> {
  connectionInitialization ??= (async () => {
    if (!tauriRuntime) return;
    let connection: BackendConnection;
    try {
      connection = await invoke<BackendConnection>("backend_connection");
    } catch (reason) {
      // The shell says why: the service stopped (and what it said), or it took too long.
      const why = typeof reason === "string" && reason ? reason : "Gunther's knowledge service did not start.";
      log.error("startup", why);
      throw new Error(why);
    }
    if (!isLocalServiceAddress(connection.baseUrl)) {
      throw new Error("Gunther refused an unsafe local knowledge service address.");
    }
    configuredBase = connection.baseUrl;
    apiBase = `${connection.baseUrl}/api`;
    authToken = connection.authToken;
  })();
  return connectionInitialization;
}

function authenticatedHeaders(headers?: HeadersInit): Headers {
  const authenticated = new Headers(headers);
  if (authToken) authenticated.set("X-Gunther-Token", authToken);
  return authenticated;
}

async function authenticatedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  await initializeBackendConnection();
  return fetch(input, { ...init, headers: authenticatedHeaders(init?.headers) });
}

class RecordingChunkRequestError extends Error {
  constructor(message: string, readonly retryable: boolean) {
    super(message);
    this.name = "RecordingChunkRequestError";
  }
}

async function waitForDesktopBackend(): Promise<void> {
  await initializeBackendConnection();
  if (!tauriRuntime) return;
  desktopBackendReady ??= (async () => {
    let last = "no answer";
    for (let attempt = 0; attempt < 40; attempt += 1) {
      try {
        const response = await authenticatedFetch(`${apiBase}/health`, { cache: "no-store" });
        if (response.ok) {
          const health = (await response.json().catch(() => null)) as Partial<Health> | null;
          if (health?.status === "ok" && typeof health.extractionMode === "string") return;
          last = "an answer that was not Gunther's";
        } else {
          last = `status ${response.status}`;
        }
      } catch (reason) {
        // The frozen Python sidecar may still be unpacking on first launch.
        last = reason instanceof Error ? reason.message : "no answer";
      }
      await wait(250);
    }
    log.error("startup", `The knowledge service was ready but its health check failed (${last})`);
    throw new Error(
      `Gunther's knowledge service started but did not answer its health check (${last}). No local data was sent.`,
    );
  })();
  return desktopBackendReady;
}

export const ensureBackendReady = (): Promise<void> => waitForDesktopBackend();

/** Connect again after the app restarted its local service, e.g. to move the libraries. */
export async function reconnectBackend(): Promise<void> {
  connectionInitialization = null;
  desktopBackendReady = null;
  await waitForDesktopBackend();
}

async function backendFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  await waitForDesktopBackend();
  return authenticatedFetch(input, init);
}

function withQueryToken(url: string): string {
  if (!authToken) return url;
  return `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(authToken)}`;
}

export const recordingSocketUrl = (context: string, purpose: "recording" | "dictation" = "recording") => {
  const origin = configuredBase
    ? configuredBase.replace(/^http/, "ws").replace(/\/$/, "")
    : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`;
  return withQueryToken(`${origin}/api/recordings/live?context=${encodeURIComponent(context)}${purpose === "dictation" ? "&purpose=dictation" : ""}`);
};

export const recordingAssetUrl = (id: string) =>
  withQueryToken(`${apiBase}/recordings/${encodeURIComponent(id)}`);
export const sourceAssetUrl = (id: string) =>
  withQueryToken(`${apiBase}/assets/${encodeURIComponent(id)}`);

async function uploadRecording(blob: Blob, title: string, expectedWorkspaceId?: string): Promise<RecordingSession> {
  await waitForDesktopBackend();
  const response = await backendFetch(`${apiBase}/recordings?title=${encodeURIComponent(title)}`, {
    method: "POST",
    headers: {
      "Content-Type": blob.type || "audio/webm",
      ...(expectedWorkspaceId ? { "X-Gunther-Workspace-Id": expectedWorkspaceId } : {}),
    },
    body: blob,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Recording upload failed with status ${response.status}`);
  }
  return response.json() as Promise<RecordingSession>;
}

async function uploadAsset(
  file: File,
  title: string,
  kind: CreateSourceInput["kind"],
  knowledgeBaseId: string | null,
  notes: string,
  expectedWorkspaceId?: string,
): Promise<AssetCaptureResult> {
  await waitForDesktopBackend();
  const params = new URLSearchParams({
    title,
    fileName: file.name,
    kind,
    deferProcessing: "true",
  });
  if (knowledgeBaseId) params.set("knowledgeBaseId", knowledgeBaseId);
  if (notes.trim()) params.set("notes", notes.trim().slice(0, 5_000));
  const response = await backendFetch(`${apiBase}/captures/assets?${params.toString()}`, {
    method: "POST",
    headers: {
      "Content-Type": file.type || "application/octet-stream",
      ...(expectedWorkspaceId ? { "X-Gunther-Workspace-Id": expectedWorkspaceId } : {}),
    },
    body: file,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `File upload failed with status ${response.status}`);
  }
  return response.json() as Promise<AssetCaptureResult>;
}

async function startRecordingSession(title: string, contentType: string, expectedWorkspaceId?: string): Promise<RecordingSession> {
  await waitForDesktopBackend();
  const response = await backendFetch(`${apiBase}/recordings/sessions?title=${encodeURIComponent(title)}`, {
    method: "POST",
    headers: {
      "Content-Type": contentType || "audio/webm",
      ...(expectedWorkspaceId ? { "X-Gunther-Workspace-Id": expectedWorkspaceId } : {}),
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Recording session failed with status ${response.status}`);
  }
  return response.json() as Promise<RecordingSession>;
}

async function recordingChunkChecksum(blob: Blob): Promise<string> {
  const digest = await window.crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function appendRecordingChunk(
  id: string,
  blob: Blob,
  sequence: number,
  expectedWorkspaceId?: string,
  verifiedChecksum?: string,
): Promise<RecordingSession> {
  await waitForDesktopBackend();
  if (verifiedChecksum && !/^[0-9a-f]{64}$/.test(verifiedChecksum)) {
    throw new Error("The recording chunk SHA-256 checksum is invalid.");
  }
  const checksum = verifiedChecksum ?? await recordingChunkChecksum(blob);
  let lastFailure: unknown = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await backendFetch(`${apiBase}/recordings/${encodeURIComponent(id)}/chunks?sequence=${sequence}`, {
        method: "PUT",
        headers: {
          "Content-Type": blob.type || "audio/webm",
          "X-Chunk-SHA256": checksum,
          ...(expectedWorkspaceId ? { "X-Gunther-Workspace-Id": expectedWorkspaceId } : {}),
        },
        body: blob,
      });
      if (response.ok) return response.json() as Promise<RecordingSession>;
      const body = (await response.json().catch(() => null)) as { detail?: string } | null;
      const error = new RecordingChunkRequestError(
        body?.detail ?? `Recording chunk failed with status ${response.status}`,
        response.status >= 500 || response.status === 408 || response.status === 429,
      );
      if (response.status < 500 || attempt === 2) throw error;
      lastFailure = error;
    } catch (reason) {
      lastFailure = reason;
      if (attempt === 2 || (reason instanceof RecordingChunkRequestError && !reason.retryable)) throw reason;
    }
    await wait(180 * 2 ** attempt);
  }
  throw lastFailure instanceof Error ? lastFailure : new Error("Recording chunk could not be saved.");
}

async function completeRecordingSession(id: string, expectedWorkspaceId?: string): Promise<RecordingSession> {
  await waitForDesktopBackend();
  return request<RecordingSession>(`/recordings/${encodeURIComponent(id)}/complete`, { method: "POST" }, expectedWorkspaceId);
}

async function checkpointRecordingSession(
  id: string,
  payload: RecordingCheckpointInput,
  expectedWorkspaceId?: string,
): Promise<RecordingSession> {
  await waitForDesktopBackend();
  let lastFailure: unknown = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await backendFetch(
        `${apiBase}/recordings/${encodeURIComponent(id)}/checkpoint`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            ...(expectedWorkspaceId ? { "X-Gunther-Workspace-Id": expectedWorkspaceId } : {}),
          },
          body: JSON.stringify(payload),
        },
      );
      if (response.ok) return response.json() as Promise<RecordingSession>;
      const body = (await response.json().catch(() => null)) as { detail?: string } | null;
      const error = new RecordingChunkRequestError(
        body?.detail ?? `Recording checkpoint failed with status ${response.status}`,
        response.status >= 500 || response.status === 408 || response.status === 429,
      );
      if (!error.retryable || attempt === 2) throw error;
      lastFailure = error;
    } catch (reason) {
      lastFailure = reason;
      if (attempt === 2 || (reason instanceof RecordingChunkRequestError && !reason.retryable)) {
        throw reason;
      }
    }
    await wait(180 * 2 ** attempt);
  }
  throw lastFailure instanceof Error
    ? lastFailure
    : new Error("Recording checkpoint could not be saved.");
}

/** The local knowledge service could not be reached at all (as opposed to answering with an error). */
export class ServiceUnavailableError extends Error {
  constructor() {
    super("Gunther’s local service isn’t responding. Your work is safe — try again in a moment.");
    this.name = "ServiceUnavailableError";
  }
}

/** Someone pressed Stop; the question stays in the conversation, marked. */
export class AnswerStoppedError extends Error {
  constructor() {
    super("Stopped. Your question stays in the conversation.");
    this.name = "AnswerStoppedError";
  }
}

/** The events of an answer until it is saved, fails or is stopped. */
async function readAnswer(body: ReadableStream<Uint8Array>, onEvent: (event: AnswerEvent) => void): Promise<ConversationTurn> {
  for await (const message of readServerEvents(body)) {
    const data = JSON.parse(message.data) as Record<string, unknown>;
    if (message.event === "done") return data as unknown as ConversationTurn;
    if (message.event === "stopped") throw new AnswerStoppedError();
    if (message.event === "error") throw new Error(typeof data.detail === "string" ? data.detail : "The answer could not be written.");
    onEvent(data as unknown as AnswerEvent);
  }
  throw new Error("The connection closed before the answer was finished.");
}

/** A failed call, in the log: method, path without its query, and what the service said. */
function logFailure(init: RequestInit | undefined, path: string, what: string) {
  log.warn("api", `${init?.method ?? "GET"} ${path.split("?")[0]} → ${what}`);
}

async function request<T>(path: string, init?: RequestInit, expectedWorkspaceId?: string): Promise<T> {
  await waitForDesktopBackend();
  let response: Response;
  try {
    response = await backendFetch(`${apiBase}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...init?.headers,
        ...(expectedWorkspaceId ? { "X-Gunther-Workspace-Id": expectedWorkspaceId } : {}),
      },
    });
  } catch (reason) {
    // fetch rejects with a TypeError when the service is unreachable; aborts keep their own error.
    if (reason instanceof TypeError) {
      logFailure(init, path, `unreachable (${reason.message})`);
      throw new ServiceUnavailableError();
    }
    throw reason;
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    logFailure(init, path, `${response.status} ${body?.detail ?? ""}`.trim());
    throw new Error(body?.detail ?? `Request failed with status ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function requestBlob(path: string, init?: RequestInit): Promise<Blob> {
  await waitForDesktopBackend();
  let response: Response;
  try {
    response = await backendFetch(`${apiBase}${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  } catch (reason) {
    if (reason instanceof TypeError) {
      logFailure(init, path, `unreachable (${reason.message})`);
      throw new ServiceUnavailableError();
    }
    throw reason;
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    logFailure(init, path, `${response.status} ${body?.detail ?? ""}`.trim());
    throw new Error(body?.detail ?? `Request failed with status ${response.status}`);
  }
  return response.blob();
}

export const knowledgeApi = {
  health: () => request<Health>("/health"),
  mobileGatewayStatus: () => request<MobileGatewayStatus>("/mobile-gateway/status"),
  workspaceBootstrap: () => request<WorkspaceBootstrap>("/workspace/bootstrap"),
  createDevicePairing: (payload: CreateDevicePairingInput) =>
    request<DevicePairingSession>("/pairing/sessions", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  pairedDevices: () => request<PairedDevice[]>("/devices"),
  revokePairedDevice: (id: string) =>
    request<PairedDevice>(`/devices/${encodeURIComponent(id)}/revoke`, {
      method: "POST",
    }),
  /** Search local knowledge, optionally only inside the given libraries. */
  search: (query: string, limit = 20, knowledgeBaseIds: readonly string[] = []) =>
    request<KnowledgeSearchResult[]>(
      `/search?q=${encodeURIComponent(query)}&limit=${limit}${knowledgeBaseIds.map((id) => `&knowledgeBaseId=${encodeURIComponent(id)}`).join("")}`,
    ),
  webSearch: (query: string) =>
    request<WebSearchResult>(`/search/web?q=${encodeURIComponent(query)}`),
  summarizeLecture: (payload: CreateLectureSummaryInput) =>
    request<LectureSummary>("/lectures/summarize", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  saveRecording: uploadRecording,
  captureAsset: uploadAsset,
  sourceStructure: (id: string, offset = 0, revisionId?: string | null, blockId?: string | null, limit?: number) =>
    request<SourceStructure>(`/sources/${encodeURIComponent(id)}/structure?offset=${offset}${revisionId ? `&revision_id=${encodeURIComponent(revisionId)}` : ""}${blockId ? `&block_id=${encodeURIComponent(blockId)}` : ""}${limit ? `&limit=${limit}` : ""}`),
  reprocessSource: (id: string) => request<SourceProcessing>(`/sources/${encodeURIComponent(id)}/reprocess`, { method: "POST" }),
  cancelSourceProcessing: (id: string) => request<SourceProcessing>(`/sources/${encodeURIComponent(id)}/processing/cancel`, { method: "POST" }),
  topics: (baseId: string) => request<KnowledgeTopic[]>(`/knowledge-bases/${encodeURIComponent(baseId)}/topics`),
  saveTopic: (baseId: string, payload: TopicInput, id?: string) => request<KnowledgeTopic>(
    `/knowledge-bases/${encodeURIComponent(baseId)}/topics${id ? `/${encodeURIComponent(id)}` : ""}`,
    { method: id ? "PATCH" : "POST", body: JSON.stringify(payload) },
  ),
  fileTopicSources: (baseId: string, topicId: string, sourceIds: string[]) => request<KnowledgeTopic>(
    `/knowledge-bases/${encodeURIComponent(baseId)}/topics/${encodeURIComponent(topicId)}/sources`,
    { method: "POST", body: JSON.stringify({ sourceIds }) },
  ),
  topicSuggestions: (baseId: string) => request<TopicSuggestions>(`/knowledge-bases/${encodeURIComponent(baseId)}/topic-suggestions`),
  topicOverview: (baseId: string, topicId: string) => request<TopicOverview>(`/knowledge-bases/${encodeURIComponent(baseId)}/topics/${encodeURIComponent(topicId)}/overview`),
  writeTopicOverview: (baseId: string, topicId: string) => request<TopicOverview>(
    `/knowledge-bases/${encodeURIComponent(baseId)}/topics/${encodeURIComponent(topicId)}/overview`,
    { method: "POST" },
  ),
  sourcePaper: (id: string) => request<SourcePaper>(`/sources/${encodeURIComponent(id)}/paper`),
  sourceDigest: (id: string) => request<SourceDigestState>(`/sources/${encodeURIComponent(id)}/digest`),
  writeSourceDigest: (id: string) => request<SourceDigestState>(`/sources/${encodeURIComponent(id)}/digest`, { method: "POST" }),
  linkTopicEvidence: (baseId: string, topicId: string, blockId: string) => request<KnowledgeTopic>(
    `/knowledge-bases/${encodeURIComponent(baseId)}/topics/${encodeURIComponent(topicId)}/evidence`,
    { method: "POST", body: JSON.stringify({ blockId }) },
  ),
  captureWeb: (payload: WebCaptureInput, expectedWorkspaceId?: string) =>
    request<WebCaptureResult>("/captures/web", {
      method: "POST",
      body: JSON.stringify(webCaptureSchema.parse(payload)),
    }, expectedWorkspaceId),
  startRecordingSession,
  appendRecordingChunk,
  checkpointRecordingSession,
  completeRecordingSession,
  recordings: (expectedWorkspaceId?: string) => request<RecordingSession[]>("/recordings", undefined, expectedWorkspaceId),
  recordingMetadata: (id: string, expectedWorkspaceId?: string) => request<RecordingSession>(`/recordings/${encodeURIComponent(id)}/metadata`, undefined, expectedWorkspaceId),
  inbox: (filters: { state?: InboxItemState; itemType?: InboxItemType } = {}) => {
    const params = new URLSearchParams();
    if (filters.state) params.set("state", filters.state);
    if (filters.itemType) params.set("itemType", filters.itemType);
    return request<InboxItem[]>(`/inbox${params.size ? `?${params.toString()}` : ""}`);
  },
  notes: (status?: NotebookNote["status"], query?: string) => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (query?.trim()) params.set("q", query.trim());
    const suffix = params.size ? `?${params.toString()}` : "";
    return request<NotebookNote[]>(`/notes${suffix}`);
  },
  createNote: (payload: CreateNotebookNoteInput = {}, expectedWorkspaceId?: string) =>
    request<NotebookNote>("/notes", {
      method: "POST",
      body: JSON.stringify(payload),
    }, expectedWorkspaceId),
  updateNote: (id: string, payload: UpdateNotebookNoteInput) =>
    request<NotebookNote>(`/notes/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  fileNote: (id: string, knowledgeBaseId: string, expectedWorkspaceId?: string) =>
    request<FileNotebookNoteResult>(`/notes/${encodeURIComponent(id)}/file`, {
      method: "POST",
      body: JSON.stringify({ knowledgeBaseId }),
    }, expectedWorkspaceId),
  knowledgeBases: () => request<KnowledgeBaseMetadata[]>("/knowledge-bases"),
  createKnowledgeBase: (payload: CreateKnowledgeBaseInput) =>
    request<KnowledgeBaseMetadata>("/knowledge-bases", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateKnowledgeBase: (id: string, payload: UpdateKnowledgeBaseInput) =>
    request<KnowledgeBaseMetadata>(`/knowledge-bases/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  overview: () => request<Overview>("/overview"),
  graph: () => request<KnowledgeGraph>("/graph"),
  sources: (knowledgeBaseId?: string) => request<SourceSummary[]>(knowledgeBaseId ? `/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/sources` : "/sources"),
  source: (id: string) => request<SourceDetail>(`/sources/${encodeURIComponent(id)}`),
  assertions: (status?: Assertion["status"]) =>
    request<Assertion[]>(`/assertions${status ? `?status=${status}` : ""}`),
  importSource: (payload: CreateSourceInput, expectedWorkspaceId?: string) =>
    request<ImportResult>("/sources", {
      method: "POST",
      body: JSON.stringify(payload),
    }, expectedWorkspaceId),
  fileSource: (id: string, knowledgeBaseId: string) =>
    request<FileSourceResult>(`/sources/${encodeURIComponent(id)}/file`, {
      method: "POST",
      body: JSON.stringify({ knowledgeBaseId }),
    }),
  storage: () => request<StorageStatus>("/storage"),
  retrievalStatus: () => request<RetrievalStatus>("/retrieval/status"),
  revealLibraryFolder: () => request<{ opened: boolean }>("/storage/reveal", { method: "POST" }),
  serviceSettings: () => request<ServiceSettingsList>("/settings/services"),
  /** Save a service's values: null goes back to the default; a secret left out stays. */
  saveServiceSettings: (id: string, values: Record<string, ServiceSettingValue>) =>
    request<ServiceSettings>(`/settings/services/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({ values }),
    }),
  /** The models Ask can use now, for the model menu. */
  modelMenu: () => request<ModelMenu>("/models"),
  modelsOverview: () => request<ModelsOverview>("/settings/models"),
  addProvider: (payload: ProviderInput) =>
    request<ModelsOverview>("/settings/providers", { method: "POST", body: JSON.stringify(payload) }),
  updateProvider: (id: string, payload: ProviderInput) =>
    request<ModelsOverview>(`/settings/providers/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(payload) }),
  removeProvider: (id: string) =>
    request<ModelsOverview>(`/settings/providers/${encodeURIComponent(id)}`, { method: "DELETE" }),
  /** Try a provider with the values on screen; without an id, one not added yet. */
  testProvider: (id: string | null, payload: ProviderInput) =>
    request<ProviderTestResult>(id ? `/settings/providers/${encodeURIComponent(id)}/test` : "/settings/providers/test", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  saveModelRoles: (roles: Partial<Record<ModelRole["id"], { model: string | null; effort: Effort }>>) =>
    request<ModelsOverview>("/settings/model-roles", { method: "PUT", body: JSON.stringify({ roles }) }),
  speechOverview: () => request<SpeechOverview>("/settings/speech"),
  addSpeechProvider: (payload: SpeechProviderInput) =>
    request<SpeechOverview>("/settings/speech/providers", { method: "POST", body: JSON.stringify(payload) }),
  updateSpeechProvider: (id: string, payload: SpeechProviderInput) =>
    request<SpeechOverview>(`/settings/speech/providers/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(payload) }),
  removeSpeechProvider: (id: string) =>
    request<SpeechOverview>(`/settings/speech/providers/${encodeURIComponent(id)}`, { method: "DELETE" }),
  testSpeechProvider: (id: string, payload: SpeechProviderInput) =>
    request<SpeechTestResult>(`/settings/speech/providers/${encodeURIComponent(id)}/test`, { method: "POST", body: JSON.stringify(payload) }),
  ttsOverview: () => request<TtsOverview>("/settings/tts"),
  addTtsProvider: (payload: TtsProviderInput) =>
    request<TtsOverview>("/settings/tts/providers", { method: "POST", body: JSON.stringify(payload) }),
  updateTtsProvider: (id: string, payload: TtsProviderInput) =>
    request<TtsOverview>(`/settings/tts/providers/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(payload) }),
  removeTtsProvider: (id: string) =>
    request<TtsOverview>(`/settings/tts/providers/${encodeURIComponent(id)}`, { method: "DELETE" }),
  saveTtsRoles: (roles: Partial<Record<TtsRole["id"], TtsRoleInput>>) =>
    request<TtsOverview>("/settings/tts/roles", { method: "PUT", body: JSON.stringify({ roles }) }),
  clearTtsCache: () => request<TtsOverview>("/settings/tts/cache", { method: "DELETE" }),
  developerSettings: () => request<DeveloperSettings>("/settings/developer"),
  saveDeveloperSettings: (settings: DeveloperSettings) =>
    request<DeveloperSettings>("/settings/developer", { method: "PUT", body: JSON.stringify(settings) }),
  /** How an answer was made; fails with a reason when it was not kept. */
  messageTrace: (sessionId: string, messageId: string) =>
    request<AnswerTrace>(`/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/trace`),
  developerLogs: (level: "info" | "warning" | "error" = "info") =>
    request<{ lines: LogLine[] }>(`/developer/logs?level=${level}`).then((body) => body.lines),
  developerJobs: () => request<{ jobs: BackgroundJob[] }>("/developer/jobs").then((body) => body.jobs),
  /** A short line spoken with the settings on screen, saved or not. */
  ttsSample: (payload: TtsSampleInput) => requestBlob("/settings/tts/sample", { method: "POST", body: JSON.stringify(payload) }),
  /** Make (or find) the audio of an answer. */
  speakMessage: (sessionId: string, messageId: string) =>
    request<SpeechClip>(`/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/speech`, { method: "POST" }),
  /** Find the audio of an answer, or start making it in parts that can play as they arrive. */
  beginSpeech: (sessionId: string, messageId: string, fresh = false) =>
    request<SpeechStart>(`/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/speech/begin${fresh ? "?fresh=true" : ""}`, { method: "POST" }),
  speechPart: (jobId: string, index: number) => requestBlob(`/speech/jobs/${encodeURIComponent(jobId)}/parts/${index}`),
  /** What was (or is being) spoken for an answer: tables and pictures as the narrator put them. */
  speechScript: (source: { clipId: string } | { jobId: string }) =>
    request<{ script: string }>("clipId" in source ? `/speech/clips/${encodeURIComponent(source.clipId)}/script` : `/speech/jobs/${encodeURIComponent(source.jobId)}/script`),
  speechAudio: (clipId: string) => requestBlob(`/speech/clips/${encodeURIComponent(clipId)}/audio`),
  saveSpeechRoles: (roles: Partial<Record<SpeechRole["id"], { model: string | null; stream: boolean; language: string }>>) =>
    request<SpeechOverview>("/settings/speech/roles", { method: "PUT", body: JSON.stringify({ roles }) }),
  testServiceSettings: (id: string, values: Record<string, ServiceSettingValue>) =>
    request<ServiceTestResult>(`/settings/services/${encodeURIComponent(id)}/test`, {
      method: "POST",
      body: JSON.stringify({ values }),
    }),
  trash: () => request<TrashItem[]>("/trash"),
  trashSource: (id: string) => request<TrashItem>(`/sources/${encodeURIComponent(id)}/trash`, { method: "POST" }),
  trashNote: (id: string) => request<TrashItem>(`/notes/${encodeURIComponent(id)}/trash`, { method: "POST" }),
  trashLibrary: (id: string) => request<TrashItem>(`/knowledge-bases/${encodeURIComponent(id)}/trash`, { method: "POST" }),
  restoreFromTrash: (kind: TrashItemKind, id: string) =>
    request<TrashItem>(`/trash/${kind}/${encodeURIComponent(id)}/restore`, { method: "POST" }),
  deleteForever: (kind: TrashItemKind, id: string) =>
    request<TrashItem>(`/trash/${kind}/${encodeURIComponent(id)}`, { method: "DELETE" }),
  emptyTrash: () => request<{ deleted: number }>("/trash", { method: "DELETE" }),
  updateAssertionStatus: (id: string, payload: UpdateAssertionStatusInput) =>
    request<Assertion>(`/assertions/${id}/status`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  updateSourceAssertionStatuses: (id: string, payload: UpdateAssertionStatusInput) =>
    request<Assertion[]>(`/sources/${encodeURIComponent(id)}/assertions/status`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  sessions: (knowledgeBaseId: string, includeArchived = false) =>
    request<KnowledgeSessionSummary[]>(
      `/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/sessions${includeArchived ? "?includeArchived=true" : ""}`,
    ),
  createSession: (knowledgeBaseId: string, payload: CreateKnowledgeSessionInput = { selectedSourceIds: [] }) =>
    request<KnowledgeSession>(`/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/sessions`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  session: (id: string) => request<KnowledgeSession>(`/sessions/${encodeURIComponent(id)}`),
  branchSession: (sessionId: string, messageId: string) =>
    request<KnowledgeSession>(`/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/branch`, {
      method: "POST",
    }),
  updateSession: (id: string, payload: UpdateKnowledgeSessionInput) =>
    request<KnowledgeSessionSummary>(`/sessions/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  sendMessage: (id: string, payload: CreateSessionMessageInput, signal?: AbortSignal) =>
    request<ConversationTurn>(`/sessions/${encodeURIComponent(id)}/messages`, {
      method: "POST",
      body: JSON.stringify(payload),
      ...(signal ? { signal } : {}),
    }),
  /** Send a message and hear the agent at work; resolves with the saved turn. */
  sendMessageStream: async (id: string, payload: CreateSessionMessageInput, onEvent: (event: AnswerEvent) => void, signal?: AbortSignal): Promise<ConversationTurn> => {
    await waitForDesktopBackend();
    let response: Response;
    try {
      response = await backendFetch(`${apiBase}/sessions/${encodeURIComponent(id)}/messages/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        ...(signal ? { signal } : {}),
      });
    } catch (reason) {
      if (reason instanceof TypeError) throw new ServiceUnavailableError();
      throw reason;
    }
    if (!response.ok || !response.body) {
      const body = (await response.json().catch(() => null)) as { detail?: string } | null;
      throw new Error(body?.detail ?? `Request failed with status ${response.status}`);
    }
    return readAnswer(response.body, onEvent);
  },
  /**
   * Follow an answer still being written in this conversation: what it did so far, then live.
   * Null when none is. Aborting only stops listening; the answer is still saved.
   */
  followAnswer: async (id: string, onEvent: (event: AnswerEvent) => void, signal?: AbortSignal): Promise<ConversationTurn | null> => {
    await waitForDesktopBackend();
    const response = await backendFetch(`${apiBase}/sessions/${encodeURIComponent(id)}/answer/stream`, signal ? { signal } : {});
    if (response.status === 204 || !response.body) return null;
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    return readAnswer(response.body, onEvent);
  },
  /** Stop the answer being written; its stream then ends with AnswerStoppedError. */
  stopAnswer: (id: string) => request<void>(`/sessions/${encodeURIComponent(id)}/answer/stop`, { method: "POST" }),
  fileSession: (id: string, knowledgeBaseId: string) =>
    request<KnowledgeSessionSummary>(`/sessions/${encodeURIComponent(id)}/file`, {
      method: "POST",
      body: JSON.stringify({ knowledgeBaseId }),
    }),
  createProposal: (sessionId: string, messageId: string, payload: CreateKnowledgeProposalInput = {}) =>
    request<KnowledgeProposal>(`/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/proposal`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  proposals: (knowledgeBaseId: string, status?: KnowledgeProposal["status"]) =>
    request<KnowledgeProposal[]>(`/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/proposals${status ? `?status=${status}` : ""}`),
  knowledgeUnits: (knowledgeBaseId: string) =>
    request<KnowledgeUnit[]>(`/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/units`),
  artifacts: (knowledgeBaseId: string, expectedWorkspaceId: string) =>
    request<ArtifactSummary[]>(
      `/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/artifacts`,
      undefined,
      expectedWorkspaceId,
    ),
  artifact: (knowledgeBaseId: string, artifactId: string, expectedWorkspaceId: string) =>
    request<Artifact>(
      `/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/artifacts/${encodeURIComponent(artifactId)}`,
      undefined,
      expectedWorkspaceId,
    ),
  createArtifact: (
    knowledgeBaseId: string,
    payload: CreateArtifactInput,
    expectedWorkspaceId: string,
  ) => request<Artifact>(
    `/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/artifacts`,
    {
      method: "POST",
      body: JSON.stringify(createArtifactSchema.parse(payload)),
    },
    expectedWorkspaceId,
  ),
  updateProposal: (id: string, payload: UpdateKnowledgeProposalInput) =>
    request<KnowledgeProposal>(`/proposals/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
};
