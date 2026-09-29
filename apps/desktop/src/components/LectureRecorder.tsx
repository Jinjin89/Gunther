import type {
  LectureSummary,
  RecordingAsset,
  RecordingCheckpointInput,
  RecordingMoment,
  RecordingSession,
} from "@gunther/contracts";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState, type ChangeEvent } from "react";
import {
  AudioLines,
  Bookmark,
  CircleAlert,
  Download,
  HardDrive,
  LoaderCircle,
  Pause,
  Play,
  RotateCcw,
  Sparkles,
  Square,
  Upload,
  X,
} from "lucide-react";
import { knowledgeApi, recordingAssetUrl, recordingSocketUrl } from "../api";
import {
  assertRecordingSpoolAvailable,
  bindRecordingSpoolWorkspace,
  deleteAcknowledgedRecordingSpool,
  deleteRecordingSpoolChunk,
  listRecordingSpool,
  spoolRecordingChunk,
  type RecordingSpoolChunk,
} from "../services/recordingSpool";

export type RecorderPhase = "idle" | "requesting" | "importing" | "recording" | "paused" | "stopped" | "error";

type ImportedAudioPhase = "staging" | "ready" | "uploading" | "complete";

export interface ImportedAudioDraft {
  fileName: string;
  contentType: string;
  sizeBytes: number;
  chunkSizeBytes: number;
  chunkCount: number;
  stagedChunkCount: number;
  checksumAlgorithm: "SHA-256";
  phase: ImportedAudioPhase;
}

export interface RecordingDraft {
  formatVersion?: 2;
  id: string;
  title: string;
  transcript: string;
  summary: LectureSummary | null;
  seconds: number;
  recording: RecordingAsset;
  moments: RecordingMoment[];
  recordingContext: "lecture" | "meeting" | "memo";
  knowledgeBaseId: string;
  workspaceId: string | null;
  importedAudio?: ImportedAudioDraft | null;
  updatedAt: string;
}

export interface RecorderSnapshot {
  phase: RecorderPhase;
  seconds: number;
  persistence: "idle" | "saving" | "saved" | "recovering";
  transcriptWords: number;
  transcriptionLabel: string;
  markedMoments: number;
}

export interface LectureRecorderHandle {
  start: () => void;
  pause: () => void;
  resume: () => void;
  stop: () => void;
  markMoment: () => void;
  restore: (draft: RecordingDraft) => void;
}

const RECORDING_DRAFTS_KEY = "gunther:recording-drafts";
const IMPORT_CHUNK_SIZE_BYTES = 8 * 1024 * 1024;
const IMPORT_MAX_SIZE_BYTES = 2 * 1024 * 1024 * 1024;
export const MICROPHONE_PERMISSION_TIMEOUT_MS = 15_000;

export class MicrophonePermissionTimeoutError extends Error {
  constructor() {
    super("macOS did not answer the microphone permission request in time.");
    this.name = "MicrophonePermissionTimeoutError";
  }
}

function stopMediaStream(stream: MediaStream) {
  stream.getTracks().forEach((track) => track.stop());
}

/**
 * `getUserMedia` has no portable cancellation primitive. Once Gunther stops
 * waiting, a late permission grant still resolves the browser promise, so the
 * returned tracks must be stopped explicitly instead of leaking a live mic.
 */
export function requestMicrophoneStream(
  mediaDevices: Pick<MediaDevices, "getUserMedia">,
  constraints: MediaStreamConstraints,
  options: { signal?: AbortSignal; timeoutMs?: number } = {},
): Promise<MediaStream> {
  const { signal, timeoutMs = MICROPHONE_PERMISSION_TIMEOUT_MS } = options;
  return new Promise<MediaStream>((resolve, reject) => {
    let finished = false;
    let timeoutId: number | null = null;

    const cleanup = () => {
      if (timeoutId !== null) window.clearTimeout(timeoutId);
      timeoutId = null;
      signal?.removeEventListener("abort", onAbort);
    };
    const rejectOnce = (reason: unknown) => {
      if (finished) return;
      finished = true;
      cleanup();
      reject(reason);
    };
    const onAbort = () => {
      rejectOnce(new DOMException("The microphone request was cancelled.", "AbortError"));
    };

    if (signal?.aborted) {
      onAbort();
      return;
    }
    signal?.addEventListener("abort", onAbort, { once: true });
    timeoutId = window.setTimeout(() => {
      rejectOnce(new MicrophonePermissionTimeoutError());
    }, timeoutMs);

    try {
      void mediaDevices.getUserMedia(constraints).then(
        (stream) => {
          if (finished || signal?.aborted) {
            stopMediaStream(stream);
            return;
          }
          finished = true;
          cleanup();
          resolve(stream);
        },
        (reason: unknown) => rejectOnce(reason),
      );
    } catch (reason) {
      rejectOnce(reason);
    }
  });
}

type MicrophoneIssue = "timeout" | "denied" | "unavailable" | "failed";

function microphoneFailure(reason: unknown): { issue: MicrophoneIssue; notice: string } {
  const name = reason && typeof reason === "object" && "name" in reason
    ? String(reason.name)
    : "";
  if (name === "MicrophonePermissionTimeoutError") {
    return {
      issue: "timeout",
      notice: "macOS has not answered the microphone request. Check for a permission dialog behind this window. If none appears, open System Settings → Privacy & Security → Microphone, enable Gunther, then choose Retry microphone.",
    };
  }
  if (name === "NotAllowedError" || name === "SecurityError") {
    return {
      issue: "denied",
      notice: "Microphone access was denied. Open macOS System Settings → Privacy & Security → Microphone, enable Gunther, then return here and choose Retry microphone.",
    };
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") {
    return {
      issue: "unavailable",
      notice: "macOS could not find an available microphone. Connect or enable an input in System Settings → Sound → Input, then choose Retry microphone.",
    };
  }
  const detail = reason instanceof Error ? reason.message : "Unknown microphone error";
  return {
    issue: "failed",
    notice: `macOS could not open the microphone. Check System Settings → Privacy & Security → Microphone and Sound → Input, then choose Retry microphone. Details: ${detail}`,
  };
}

function normalizeImportedAudioDraft(value: unknown): ImportedAudioDraft | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<ImportedAudioDraft>;
  const validPhase = candidate.phase === "staging"
    || candidate.phase === "ready"
    || candidate.phase === "uploading"
    || candidate.phase === "complete";
  if (
    typeof candidate.fileName !== "string"
    || !candidate.fileName
    || typeof candidate.contentType !== "string"
    || !candidate.contentType
    || typeof candidate.sizeBytes !== "number"
    || !Number.isSafeInteger(candidate.sizeBytes)
    || candidate.sizeBytes <= 0
    || typeof candidate.chunkSizeBytes !== "number"
    || !Number.isSafeInteger(candidate.chunkSizeBytes)
    || candidate.chunkSizeBytes <= 0
    || typeof candidate.chunkCount !== "number"
    || !Number.isSafeInteger(candidate.chunkCount)
    || candidate.chunkCount <= 0
    || typeof candidate.stagedChunkCount !== "number"
    || !Number.isSafeInteger(candidate.stagedChunkCount)
    || candidate.stagedChunkCount < 0
    || candidate.stagedChunkCount > candidate.chunkCount
    || candidate.checksumAlgorithm !== "SHA-256"
    || !validPhase
  ) return null;
  if (candidate.chunkCount !== Math.ceil(candidate.sizeBytes / candidate.chunkSizeBytes)) return null;
  return {
    fileName: candidate.fileName,
    contentType: candidate.contentType,
    sizeBytes: candidate.sizeBytes,
    chunkSizeBytes: candidate.chunkSizeBytes,
    chunkCount: candidate.chunkCount,
    stagedChunkCount: candidate.stagedChunkCount,
    checksumAlgorithm: "SHA-256",
    phase: candidate.phase as ImportedAudioPhase,
  };
}

/** Keep enough headroom for IndexedDB bookkeeping and normal app writes. */
export function importStorageHasCapacity(
  estimate: { quota?: number; usage?: number },
  fileSizeBytes: number,
): boolean {
  if (!Number.isFinite(estimate.quota) || !Number.isFinite(estimate.usage)) return true;
  const quota = estimate.quota ?? 0;
  const usage = estimate.usage ?? 0;
  const reserve = Math.max(64 * 1024 * 1024, Math.ceil(fileSizeBytes * 0.05));
  return Math.max(0, quota - usage) >= fileSizeBytes + reserve;
}

export function importedAudioSpoolIsComplete(
  imported: ImportedAudioDraft,
  chunks: Array<Pick<RecordingSpoolChunk, "sequence" | "sizeBytes" | "source">>,
): boolean {
  if (chunks.length !== imported.chunkCount) return false;
  let totalSize = 0;
  for (let index = 0; index < chunks.length; index += 1) {
    const chunk = chunks[index]!;
    if (chunk.sequence !== index || chunk.source !== "import") return false;
    totalSize += chunk.sizeBytes;
  }
  return totalSize === imported.sizeBytes;
}

async function assertImportedAudioStorageAvailable(fileSizeBytes: number): Promise<void> {
  const storage = navigator.storage;
  if (!storage) return;
  try {
    await storage.persist?.();
  } catch {
    // Persistence is an optimization. IndexedDB errors remain authoritative.
  }
  const estimate = await storage.estimate?.();
  if (estimate && !importStorageHasCapacity(estimate, fileSizeBytes)) {
    throw new Error("This device does not have enough durable app storage for a recoverable copy of this audio file.");
  }
}

export function loadRecordingDrafts(): RecordingDraft[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(RECORDING_DRAFTS_KEY) ?? "[]") as RecordingDraft[];
    return parsed
      .filter((draft) => draft?.id && draft?.recording?.id)
      .map((draft) => ({
        ...draft,
        formatVersion: 2 as const,
        workspaceId: typeof draft.workspaceId === "string" ? draft.workspaceId : null,
        importedAudio: normalizeImportedAudioDraft(draft.importedAudio),
      }))
      .slice(0, 20);
  } catch {
    return [];
  }
}

export function removeRecordingDraft(id: string) {
  const next = loadRecordingDrafts().filter((draft) => draft.id !== id);
  window.localStorage.setItem(RECORDING_DRAFTS_KEY, JSON.stringify(next));
}

export function recordingDraftsForWorkspace(
  drafts: RecordingDraft[],
  workspaceId: string | null,
): RecordingDraft[] {
  return workspaceId
    ? drafts.filter((draft) => draft.workspaceId === workspaceId)
    : [];
}

function preserveRecordingDraft(draft: RecordingDraft) {
  const next = [draft, ...loadRecordingDrafts().filter((item) => item.id !== draft.id)].slice(0, 20);
  window.localStorage.setItem(RECORDING_DRAFTS_KEY, JSON.stringify(next));
}

function isRecordingSession(recording: RecordingAsset): recording is RecordingSession {
  return "checkpointRevision" in recording && "recovery" in recording;
}

function mergeTranscript(
  localTranscript: string,
  remoteTranscript: string,
  localUpdatedAt: string,
  remoteUpdatedAt: string | null,
) {
  const local = localTranscript.trim();
  const remote = remoteTranscript.trim();
  if (!local) return remote;
  if (!remote) return local;
  if (local.includes(remote)) return local;
  if (remote.includes(local)) return remote;
  if (remoteUpdatedAt && remoteUpdatedAt > localUpdatedAt) return remote;
  if (localUpdatedAt) return local;
  return remote.length > local.length ? remote : local;
}

function mergeMoments(local: RecordingMoment[], remote: RecordingMoment[]) {
  const merged = new Map<string, RecordingMoment>();
  [...remote, ...local].forEach((moment) => {
    merged.set(`${moment.seconds}:${moment.label}`, moment);
  });
  return [...merged.values()].sort((left, right) => left.seconds - right.seconds);
}

interface TranscriptPart {
  draft: string;
  final: string | null;
  startSeconds: number | undefined;
}

interface LectureRecorderProps {
  title: string;
  knowledgeBaseId: string;
  workspaceId: string | null;
  recordingContext?: "lecture" | "meeting" | "memo";
  onKnowledgeContent: (content: string) => void;
  onActiveChange?: (active: boolean) => void;
  onTitleChange?: (title: string) => void;
  onStatusChange?: (snapshot: RecorderSnapshot) => void;
  onDraftChange?: (draft: RecordingDraft | null) => void;
}

function formatTime(totalSeconds: number) {
  const hours = Math.floor(totalSeconds / 3_600);
  const minutes = Math.floor(totalSeconds / 60).toString().padStart(2, "0");
  const seconds = (totalSeconds % 60).toString().padStart(2, "0");
  return hours > 0
    ? `${hours.toString().padStart(2, "0")}:${(Math.floor(totalSeconds / 60) % 60).toString().padStart(2, "0")}:${seconds}`
    : `${minutes}:${seconds}`;
}

function encodePcm16(samples: Float32Array, sourceRate: number) {
  const ratio = sourceRate / 24_000;
  const length = Math.max(1, Math.floor(samples.length / ratio));
  const pcm = new Int16Array(length);
  for (let index = 0; index < length; index += 1) {
    const start = Math.floor(index * ratio);
    const end = Math.min(samples.length, Math.floor((index + 1) * ratio));
    let total = 0;
    for (let cursor = start; cursor < end; cursor += 1) total += samples[cursor] ?? 0;
    const normalized = Math.max(-1, Math.min(1, total / Math.max(1, end - start)));
    pcm[index] = normalized < 0 ? normalized * 0x8000 : normalized * 0x7fff;
  }
  const bytes = new Uint8Array(pcm.buffer);
  let binary = "";
  for (let index = 0; index < bytes.length; index += 1) binary += String.fromCharCode(bytes[index]!);
  return window.btoa(binary);
}

function knowledgeDocument(
  title: string,
  transcript: string,
  summary: LectureSummary | null,
  seconds: number,
  recording: RecordingAsset | null,
  moments: RecordingMoment[],
) {
  const heading = `# ${title.trim() || "Lecture recording"}`;
  const metadata = `Duration: ${formatTime(seconds)} · Captured: ${new Date().toLocaleString()}${recording ? ` · Local recording: ${recording.id}` : ""}`;
  const momentSection = moments.length ? `\n\n## Marked moments\n\n${moments.map((moment) => `- ${formatTime(moment.seconds)} · ${moment.label}`).join("\n")}` : "";
  if (!summary) return `${heading}\n\n${metadata}${momentSection}\n\n## Transcript\n\n${transcript.trim()}`;
  const section = (label: string, items: string[]) => items.length
    ? `\n\n## ${label}\n\n${items.map((item) => `- ${item}`).join("\n")}`
    : "";
  return `${heading}\n\n${metadata}${momentSection}\n\n## Summary\n\n${summary.overview}${section("Key points", summary.keyPoints)}${section("Actions", summary.actionItems)}${section("Open questions", summary.openQuestions)}${summary.terms.length ? `\n\nTerms: ${summary.terms.join(" · ")}` : ""}\n\n## Full transcript\n\n${transcript.trim()}`;
}

export const LectureRecorder = forwardRef<LectureRecorderHandle, LectureRecorderProps>(function LectureRecorder({ title, knowledgeBaseId, workspaceId, recordingContext = "lecture", onKnowledgeContent, onActiveChange, onTitleChange, onStatusChange, onDraftChange }, ref) {
  const recordingLabel = recordingContext === "lecture" ? "course recording" : recordingContext === "meeting" ? "meeting recording" : "voice memo";
  const requireWorkspaceId = useCallback(() => {
    if (!workspaceId) throw new Error("The local workspace identity is not available yet.");
    return workspaceId;
  }, [workspaceId]);
  const [phase, setPhase] = useState<RecorderPhase>("idle");
  const [seconds, setSeconds] = useState(0);
  const [level, setLevel] = useState(0);
  const [transcript, setTranscript] = useState("");
  const [summary, setSummary] = useState<LectureSummary | null>(null);
  const [summarizing, setSummarizing] = useState(false);
  const [provider, setProvider] = useState<"checking" | "available" | "connecting" | "live" | "local-only">("checking");
  const [transcriptionLabel, setTranscriptionLabel] = useState("Live transcript");
  const [notice, setNotice] = useState<string | null>(null);
  const [microphoneIssue, setMicrophoneIssue] = useState<MicrophoneIssue | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [audioType, setAudioType] = useState("audio/webm");
  const [savedRecording, setSavedRecording] = useState<RecordingAsset | null>(null);
  const [moments, setMoments] = useState<RecordingMoment[]>([]);
  const [importedAudio, setImportedAudio] = useState<ImportedAudioDraft | null>(null);
  const [persistence, setPersistence] = useState<"idle" | "saving" | "saved" | "recovering">("idle");
  const phaseRef = useRef<RecorderPhase>("idle");
  const streamRef = useRef<MediaStream | null>(null);
  const microphoneRequestRef = useRef<AbortController | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const recordingSessionRef = useRef<RecordingSession | null>(null);
  const uploadChainRef = useRef<Promise<void>>(Promise.resolve());
  const checkpointChainRef = useRef<Promise<void>>(Promise.resolve());
  const checkpointRevisionRef = useRef(0);
  const checkpointSnapshotRef = useRef<Omit<RecordingCheckpointInput, "expectedRevision">>({
    transcript: "",
    durationSeconds: 0,
    moments: [],
    recordingContext,
    knowledgeBaseId: knowledgeBaseId || null,
  });
  const nextChunkSequenceRef = useRef(0);
  const nextSpoolSequenceRef = useRef(0);
  const persistenceInterruptedRef = useRef(false);
  const spoolFailureRef = useRef<Error | null>(null);
  const pendingPcmRef = useRef<string[]>([]);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const transcriptionEnabledRef = useRef(true);
  const partsRef = useRef(new Map<string, TranscriptPart>());
  const timerRef = useRef<number | null>(null);
  const transcriptRef = useRef<HTMLTextAreaElement | null>(null);
  const importedAudioRef = useRef<ImportedAudioDraft | null>(null);

  const updatePhase = (next: RecorderPhase) => {
    phaseRef.current = next;
    setPhase(next);
  };

  const queueCheckpoint = useCallback((sessionOverride?: RecordingSession | null) => {
    const persist = async () => {
      const session = sessionOverride ?? recordingSessionRef.current;
      if (!session) return;
      const payload: RecordingCheckpointInput = {
        ...checkpointSnapshotRef.current,
        expectedRevision: checkpointRevisionRef.current,
      };
      try {
        const checkpointed = await knowledgeApi.checkpointRecordingSession(
          session.id,
          payload,
          requireWorkspaceId(),
        );
        if (recordingSessionRef.current?.id !== session.id) return;
        checkpointRevisionRef.current = checkpointed.checkpointRevision;
        recordingSessionRef.current = checkpointed;
        setSavedRecording((current) => (
          current?.id === checkpointed.id && current.sizeBytes > checkpointed.sizeBytes
            ? { ...checkpointed, sizeBytes: current.sizeBytes }
            : checkpointed
        ));
      } catch {
        if (recordingSessionRef.current?.id !== session.id) return;
        try {
          const recovered = await knowledgeApi.recordingMetadata(session.id, requireWorkspaceId());
          if (recordingSessionRef.current?.id !== session.id) return;
          checkpointRevisionRef.current = recovered.checkpointRevision;
          recordingSessionRef.current = recovered;
          nextChunkSequenceRef.current = Math.max(
            nextChunkSequenceRef.current,
            recovered.nextExpectedSequence,
          );
        } catch {
          // The local draft remains the fallback; the next interval retries.
        }
        if (phaseRef.current === "recording" || phaseRef.current === "paused") {
          setNotice("Transcript checkpoint was interrupted. Audio and the local draft remain safe; Gunther will retry.");
        }
      }
    };
    const queued = checkpointChainRef.current.then(persist, persist);
    checkpointChainRef.current = queued;
    return queued;
  }, [requireWorkspaceId]);

  const flushRecordingSpool = useCallback(async (
    sessionOverride: RecordingSession,
  ): Promise<RecordingSession> => {
    await assertRecordingSpoolAvailable();
    const boundWorkspaceId = requireWorkspaceId();
    // The server lookup is the authority for the session/workspace relationship.
    // Only after it succeeds may v1 (previously unbound) spool rows be migrated.
    let latest = await knowledgeApi.recordingMetadata(sessionOverride.id, boundWorkspaceId);
    await bindRecordingSpoolWorkspace(latest.id, boundWorkspaceId);
    await deleteAcknowledgedRecordingSpool(
      latest.id,
      latest.nextExpectedSequence,
      boundWorkspaceId,
    );
    let chunks = await listRecordingSpool(latest.id, boundWorkspaceId);

    if (latest.status === "failed") {
      throw new Error("The saved recording session is marked as failed.");
    }
    if (latest.status === "completed") {
      if (chunks.length) {
        throw new Error("The recording was completed before all durable chunks were accepted.");
      }
      recordingSessionRef.current = latest;
      checkpointRevisionRef.current = latest.checkpointRevision;
      nextChunkSequenceRef.current = latest.nextExpectedSequence;
      nextSpoolSequenceRef.current = Math.max(
        nextSpoolSequenceRef.current,
        latest.nextExpectedSequence,
      );
      setSavedRecording(latest);
      return latest;
    }

    let expectedSequence = latest.nextExpectedSequence;
    for (const chunk of chunks) {
      if (chunk.sequence < expectedSequence) {
        await deleteRecordingSpoolChunk(latest.id, chunk.sequence);
        continue;
      }
      if (chunk.sequence > expectedSequence) {
        throw new Error(
          `Durable recording chunk ${expectedSequence} is missing; later chunks were preserved.`,
        );
      }
      latest = await knowledgeApi.appendRecordingChunk(
        latest.id,
        chunk.blob,
        chunk.sequence,
        boundWorkspaceId,
        chunk.checksum,
      );
      if (latest.nextExpectedSequence <= chunk.sequence) {
        throw new Error(`The server did not acknowledge recording chunk ${chunk.sequence}.`);
      }
      await deleteRecordingSpoolChunk(latest.id, chunk.sequence);
      expectedSequence = latest.nextExpectedSequence;
    }

    // A response can be lost after the server commits. Reconcile once more so
    // only server-acknowledged entries are removed from IndexedDB.
    await deleteAcknowledgedRecordingSpool(
      latest.id,
      latest.nextExpectedSequence,
      boundWorkspaceId,
    );
    chunks = await listRecordingSpool(latest.id, boundWorkspaceId);
    if (chunks.length && chunks[0]!.sequence > latest.nextExpectedSequence) {
      throw new Error(
        `Durable recording chunk ${latest.nextExpectedSequence} is missing; later chunks were preserved.`,
      );
    }
    recordingSessionRef.current = latest;
    checkpointRevisionRef.current = latest.checkpointRevision;
    nextChunkSequenceRef.current = latest.nextExpectedSequence;
    nextSpoolSequenceRef.current = Math.max(
      nextSpoolSequenceRef.current,
      latest.nextExpectedSequence,
      ...chunks.map((chunk) => chunk.sequence + 1),
    );
    setSavedRecording(latest);
    return latest;
  }, [requireWorkspaceId]);

  useEffect(() => {
    checkpointSnapshotRef.current = {
      transcript,
      durationSeconds: seconds,
      moments,
      recordingContext,
      knowledgeBaseId: knowledgeBaseId || null,
    };
  }, [knowledgeBaseId, moments, recordingContext, seconds, transcript]);

  const publishTranscript = useCallback(() => {
    const next = [...partsRef.current.values()]
      .map((part) => `${part.startSeconds === undefined ? "" : `[${formatTime(Math.round(part.startSeconds))}] `}${part.final ?? part.draft}`)
      .join("\n")
      .replace(/[ \t]+/g, " ")
      .trim();
    checkpointSnapshotRef.current = {
      ...checkpointSnapshotRef.current,
      transcript: next,
    };
    setTranscript(next);
  }, []);

  const closeAudioPipeline = useCallback(() => {
    microphoneRequestRef.current?.abort();
    microphoneRequestRef.current = null;
    if (timerRef.current) window.clearInterval(timerRef.current);
    timerRef.current = null;
    processorRef.current?.disconnect();
    processorRef.current = null;
    void audioContextRef.current?.close().catch(() => undefined);
    audioContextRef.current = null;
    if (streamRef.current) stopMediaStream(streamRef.current);
    streamRef.current = null;
  }, []);

  const stopTranscriptionReconnect = useCallback(() => {
    transcriptionEnabledRef.current = false;
    pendingPcmRef.current = [];
    if (reconnectTimerRef.current) window.clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = null;
  }, []);

  const openTranscriptionSocket = (context: string) => {
    if (!transcriptionEnabledRef.current) return;
    const socket = new WebSocket(recordingSocketUrl(context));
    socketRef.current = socket;
    setProvider("connecting");
    socket.onmessage = (message) => {
      const event = JSON.parse(String(message.data)) as Record<string, unknown>;
      if (event.type === "service.ready") {
        reconnectAttemptsRef.current = 0;
        setProvider("live");
        setTranscriptionLabel(event.provider === "sensevoice" ? "SenseVoice · local" : "OpenAI live");
        const buffered = pendingPcmRef.current.splice(0);
        buffered.forEach((audio) => socket.send(JSON.stringify({ type: "input_audio_buffer.append", audio })));
        if (buffered.length) setNotice("Live transcription reconnected and caught up with buffered audio.");
        return;
      }
      if (event.type === "service.error") {
        if (event.code === "sensevoice_segment_failed") {
          setNotice(String(event.message ?? "SenseVoice missed one short segment; the original audio is still safe."));
          return;
        }
        if (event.code === "not_configured") {
          transcriptionEnabledRef.current = false;
          pendingPcmRef.current = [];
        }
        setProvider("local-only");
        setNotice(String(event.message ?? "Live transcription is unavailable; audio remains local."));
        return;
      }
      const itemId = typeof event.item_id === "string" ? event.item_id : `current-${reconnectAttemptsRef.current}`;
      if (event.type === "conversation.item.input_audio_transcription.delta") {
        const current = partsRef.current.get(itemId) ?? { draft: "", final: null, startSeconds: typeof event.start_seconds === "number" ? event.start_seconds : undefined };
        current.draft += String(event.delta ?? "");
        partsRef.current.set(itemId, current);
        publishTranscript();
      }
      if (event.type === "conversation.item.input_audio_transcription.completed") {
        const current = partsRef.current.get(itemId) ?? { draft: "", final: null, startSeconds: typeof event.start_seconds === "number" ? event.start_seconds : undefined };
        current.final = String(event.transcript ?? current.draft);
        partsRef.current.set(itemId, current);
        publishTranscript();
      }
    };
    socket.onerror = () => {
      if (transcriptionEnabledRef.current) {
        setProvider("connecting");
        setNotice("Live transcript connection was interrupted. Audio is still saving locally while Gunther reconnects.");
      }
    };
    socket.onclose = () => {
      if (!transcriptionEnabledRef.current || !streamRef.current || phaseRef.current === "stopped" || phaseRef.current === "error") return;
      reconnectAttemptsRef.current += 1;
      if (reconnectAttemptsRef.current > 8) {
        setProvider("local-only");
        setNotice("Live transcription could not reconnect. The complete audio continues saving locally.");
        return;
      }
      const delay = Math.min(8_000, 750 * 2 ** (reconnectAttemptsRef.current - 1));
      reconnectTimerRef.current = window.setTimeout(() => openTranscriptionSocket(context), delay);
    };
  };

  const importAudio = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!file.size) {
      setNotice("Choose a non-empty audio file.");
      return;
    }
    if (file.size > IMPORT_MAX_SIZE_BYTES) {
      setNotice("Choose an audio file smaller than 2 GB.");
      return;
    }
    updatePhase("importing");
    setPersistence("saving");
    setMicrophoneIssue(null);
    setNotice("Saving the existing audio locally in reliable chunks…");
    setSummary(null);
    setTranscript("");
    setMoments([]);
    setSeconds(0);
    setSavedRecording(null);
    setImportedAudio(null);
    importedAudioRef.current = null;
    partsRef.current.clear();
    recordingSessionRef.current = null;
    checkpointChainRef.current = Promise.resolve();
    checkpointRevisionRef.current = 0;
    checkpointSnapshotRef.current = {
      transcript: "",
      durationSeconds: 0,
      moments: [],
      recordingContext,
      knowledgeBaseId: knowledgeBaseId || null,
    };
    const importedTitle = file.name.replace(/\.[^.]+$/, "") || title.trim() || "Imported recording";
    onTitleChange?.(importedTitle);
    const fileExtension = file.name.split(".").pop()?.toLowerCase() ?? "";
    const typeByExtension: Record<string, string> = { mp3: "audio/mpeg", m4a: "audio/mp4", mp4: "audio/mp4", wav: "audio/wav", flac: "audio/flac", aac: "audio/aac", webm: "audio/webm" };
    const contentType = file.type || typeByExtension[fileExtension] || "audio/webm";
    setAudioType(contentType);
    let recoverableImport: ImportedAudioDraft | null = null;
    try {
      const boundWorkspaceId = requireWorkspaceId();
      await assertRecordingSpoolAvailable();
      await assertImportedAudioStorageAvailable(file.size);

      let importedDuration = 0;
      const previewUrl = URL.createObjectURL(file);
      const probe = document.createElement("audio");
      probe.preload = "metadata";
      probe.src = previewUrl;
      await new Promise<void>((resolve) => {
        let settled = false;
        const finish = () => {
          if (settled) return;
          settled = true;
          if (Number.isFinite(probe.duration)) {
            importedDuration = Math.max(0, Math.round(probe.duration));
            setSeconds(importedDuration);
          }
          resolve();
        };
        probe.onloadedmetadata = finish;
        probe.onerror = finish;
        window.setTimeout(finish, 1_500);
      });
      URL.revokeObjectURL(previewUrl);

      const session = await knowledgeApi.startRecordingSession(
        importedTitle,
        contentType,
        boundWorkspaceId,
      );
      if (session.nextExpectedSequence !== 0 || session.sizeBytes !== 0) {
        throw new Error("A new import session did not start with an empty server ledger.");
      }
      recordingSessionRef.current = session;
      checkpointRevisionRef.current = session.checkpointRevision;
      setSavedRecording(session);
      const chunkCount = Math.ceil(file.size / IMPORT_CHUNK_SIZE_BYTES);
      let importState: ImportedAudioDraft = {
        fileName: file.name,
        contentType,
        sizeBytes: file.size,
        chunkSizeBytes: IMPORT_CHUNK_SIZE_BYTES,
        chunkCount,
        stagedChunkCount: 0,
        checksumAlgorithm: "SHA-256",
        phase: "staging",
      };
      const preserveImportState = (
        recording: RecordingAsset,
        nextState: ImportedAudioDraft,
      ) => {
        importState = nextState;
        recoverableImport = nextState;
        importedAudioRef.current = nextState;
        setImportedAudio(nextState);
        const draft: RecordingDraft = {
          formatVersion: 2,
          id: session.id,
          title: importedTitle,
          transcript: "",
          summary: null,
          seconds: importedDuration,
          recording,
          moments: [],
          recordingContext,
          knowledgeBaseId,
          workspaceId: boundWorkspaceId,
          importedAudio: nextState,
          updatedAt: new Date().toISOString(),
        };
        preserveRecordingDraft(draft);
        onDraftChange?.(draft);
      };
      // Commit discoverability before the first upload. If the process exits or
      // a completion response is lost, reopening the exact workspace can find
      // the server ledger and the app-owned IndexedDB copy.
      preserveImportState(session, importState);
      for (let sequence = 0; sequence < chunkCount; sequence += 1) {
        const offset = sequence * IMPORT_CHUNK_SIZE_BYTES;
        const chunk = file.slice(
          offset,
          Math.min(file.size, offset + IMPORT_CHUNK_SIZE_BYTES),
          contentType,
        );
        await spoolRecordingChunk(
          session.id,
          sequence,
          chunk,
          boundWorkspaceId,
          "import",
        );
        preserveImportState(session, {
          ...importState,
          stagedChunkCount: sequence + 1,
        });
        setNotice(`Making a durable local copy… ${sequence + 1} of ${chunkCount} chunks`);
      }

      const stagedChunks = await listRecordingSpool(session.id, boundWorkspaceId);
      if (!importedAudioSpoolIsComplete(importState, stagedChunks)) {
        throw new Error("The durable audio copy did not match its import manifest.");
      }
      preserveImportState(session, { ...importState, phase: "ready" });
      setNotice("Durable local copy ready. Uploading from the confirmed server offset…");
      preserveImportState(session, { ...importState, phase: "uploading" });

      let latest = await flushRecordingSpool(session);
      if (
        latest.nextExpectedSequence !== importState.chunkCount
        || latest.sizeBytes !== importState.sizeBytes
      ) {
        throw new Error("The server ledger does not match the complete durable audio copy.");
      }
      await queueCheckpoint(latest);
      latest = recordingSessionRef.current?.id === latest.id
        ? recordingSessionRef.current
        : latest;
      const asset = latest.status === "completed"
        ? latest
        : await knowledgeApi.completeRecordingSession(session.id, boundWorkspaceId);
      recordingSessionRef.current = asset;
      checkpointRevisionRef.current = asset.checkpointRevision;
      nextChunkSequenceRef.current = asset.nextExpectedSequence;
      nextSpoolSequenceRef.current = asset.nextExpectedSequence;
      preserveImportState(asset, { ...importState, phase: "complete" });
      checkpointSnapshotRef.current = {
        transcript: "",
        durationSeconds: importedDuration,
        moments: [],
        recordingContext,
        knowledgeBaseId: knowledgeBaseId || null,
      };
      await queueCheckpoint(asset);
      setSavedRecording(asset);
      setAudioUrl(recordingAssetUrl(asset.id));
      setPersistence("saved");
      setProvider("local-only");
      setNotice("Existing audio imported. Add or paste a transcript here, then summarize it or save the recording as a source.");
      updatePhase("stopped");
    } catch (reason) {
      setPersistence("recovering");
      const failedImport = recoverableImport as ImportedAudioDraft | null;
      const canResumeWithoutFile = Boolean(failedImport && failedImport.phase !== "staging");
      updatePhase(canResumeWithoutFile ? "stopped" : "error");
      const detail = reason instanceof Error ? reason.message : "Unknown error";
      setNotice(failedImport?.phase === "staging"
        ? `The initial durable copy was interrupted: ${detail} Choose the original file again to restart the copy; already copied chunks were left untouched for diagnosis.`
        : canResumeWithoutFile
          ? `The audio import could not finish: ${detail} Its complete durable copy remains bound to this workspace and can resume without selecting the file again.`
          : `The audio import could not start: ${detail} No upload began; choose the file again after resolving the problem.`);
    }
  };

  const start = async () => {
    const resumableSession = recordingSessionRef.current?.recovery.canResume
      ? recordingSessionRef.current
      : null;
    if (resumableSession && importedAudioRef.current) {
      setPersistence("recovering");
      setNotice("This session is a durable file import. Resume its upload instead of appending microphone audio.");
      updatePhase("stopped");
      return;
    }
    microphoneRequestRef.current?.abort();
    microphoneRequestRef.current = null;
    let failureStage: "spool" | "session" | "microphone" | "draft" | "recorder" = "spool";
    updatePhase("requesting");
    setMicrophoneIssue(null);
    setNotice(null);
    setSummary(null);
    if (!resumableSession) {
      setSavedRecording(null);
      setImportedAudio(null);
      importedAudioRef.current = null;
      setMoments([]);
      partsRef.current.clear();
      setTranscript("");
      setSeconds(0);
      checkpointSnapshotRef.current = {
        transcript: "",
        durationSeconds: 0,
        moments: [],
        recordingContext,
        knowledgeBaseId: knowledgeBaseId || null,
      };
    }
    setPersistence("idle");
    recordingSessionRef.current = resumableSession;
    uploadChainRef.current = Promise.resolve();
    checkpointChainRef.current = Promise.resolve();
    checkpointRevisionRef.current = resumableSession?.checkpointRevision ?? 0;
    nextChunkSequenceRef.current = resumableSession?.nextExpectedSequence ?? 0;
    nextSpoolSequenceRef.current = resumableSession?.nextExpectedSequence ?? 0;
    persistenceInterruptedRef.current = false;
    spoolFailureRef.current = null;
    pendingPcmRef.current = [];
    reconnectAttemptsRef.current = 0;
    transcriptionEnabledRef.current = true;
    if (audioUrl?.startsWith("blob:")) URL.revokeObjectURL(audioUrl);
    setAudioUrl(null);
    try {
      await assertRecordingSpoolAvailable();

      let session = resumableSession;
      if (session) {
        failureStage = "session";
        session = await flushRecordingSpool(session);
        if (!session.recovery.canResume) {
          throw new Error("This recording is already complete and cannot accept more audio.");
        }
      }

      failureStage = "microphone";
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new DOMException("Microphone capture is unavailable in this environment.", "NotSupportedError");
      }
      const microphoneRequest = new AbortController();
      microphoneRequestRef.current = microphoneRequest;
      let stream: MediaStream;
      try {
        stream = await requestMicrophoneStream(
          navigator.mediaDevices,
          { audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } },
          { signal: microphoneRequest.signal },
        );
      } finally {
        if (microphoneRequestRef.current === microphoneRequest) {
          microphoneRequestRef.current = null;
        }
      }
      streamRef.current = stream;
      failureStage = "recorder";
      const preferredTypes = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"];
      const selectedType = session?.contentType
        ?? preferredTypes.find((value) => MediaRecorder.isTypeSupported(value));
      if (session && selectedType && !MediaRecorder.isTypeSupported(selectedType)) {
        throw new Error(
          `This device cannot continue the saved ${selectedType} recording. Finish its saved portion instead.`,
        );
      }
      const activeAudioType = selectedType || "audio/webm";

      if (!session) {
        failureStage = "session";
        session = await knowledgeApi.startRecordingSession(
          title.trim() || `${recordingLabel[0]?.toUpperCase()}${recordingLabel.slice(1)}`,
          activeAudioType,
          requireWorkspaceId(),
        );
      }
      recordingSessionRef.current = session;
      checkpointRevisionRef.current = session.checkpointRevision;
      nextChunkSequenceRef.current = session.nextExpectedSequence;
      nextSpoolSequenceRef.current = session.nextExpectedSequence;
      setSavedRecording(session);
      setAudioType(session.contentType);

      // The smallest recovery record is committed before MediaRecorder starts.
      // It lets a restart discover both the server session and any IndexedDB chunks.
      failureStage = "draft";
      const minimalDraft: RecordingDraft = {
        formatVersion: 2,
        id: session.id,
        title: title.trim() || recordingLabel,
        transcript: checkpointSnapshotRef.current.transcript,
        summary: null,
        seconds: checkpointSnapshotRef.current.durationSeconds,
        recording: session,
        moments: checkpointSnapshotRef.current.moments,
        recordingContext,
        knowledgeBaseId,
        workspaceId: requireWorkspaceId(),
        importedAudio: null,
        updatedAt: new Date().toISOString(),
      };
      preserveRecordingDraft(minimalDraft);
      onDraftChange?.(minimalDraft);

      failureStage = "recorder";
      const recorder = new MediaRecorder(
        stream,
        selectedType ? { mimeType: selectedType } : undefined,
      );
      recorder.ondataavailable = (event) => {
        if (!event.data.size) return;
        const chunk = event.data;
        const activeSession = recordingSessionRef.current;
        if (!activeSession) {
          spoolFailureRef.current = new Error("The recording session disappeared.");
          return;
        }
        const sequence = nextSpoolSequenceRef.current;
        nextSpoolSequenceRef.current += 1;
        uploadChainRef.current = uploadChainRef.current
          .catch(() => undefined)
          .then(async () => {
            await spoolRecordingChunk(
              activeSession.id,
              sequence,
              chunk,
              requireWorkspaceId(),
              "live",
            );
            try {
              const flushed = await flushRecordingSpool(activeSession);
              persistenceInterruptedRef.current = false;
              recordingSessionRef.current = flushed;
              setPersistence("saving");
            } catch {
              // Network/server failures do not stop capture: the chunk and all
              // following chunks remain ordered in IndexedDB for a later retry.
              persistenceInterruptedRef.current = true;
              setPersistence("recovering");
              setNotice(
                "The service is offline. Audio continues into Gunther's durable device spool and will retry in order.",
              );
            }
          })
          .catch((reason: unknown) => {
            const error = reason instanceof Error
              ? reason
              : new Error("The durable recording spool failed.");
            spoolFailureRef.current = error;
            setPersistence("recovering");
            setNotice(
              `Recording stopped because durable device storage failed: ${error.message}`,
            );
            if (recorder.state !== "inactive") recorder.stop();
            closeAudioPipeline();
            updatePhase("error");
          });
      };
      recorder.onstop = async () => {
        setPersistence("saving");
        await uploadChainRef.current;
        const stoppedSession = recordingSessionRef.current;
        if (!stoppedSession) return;
        if (spoolFailureRef.current) {
          setPersistence("recovering");
          return;
        }
        try {
          let flushed = await flushRecordingSpool(stoppedSession);
          await queueCheckpoint(flushed);
          flushed = await flushRecordingSpool(flushed);
          const asset = await knowledgeApi.completeRecordingSession(flushed.id, requireWorkspaceId());
          recordingSessionRef.current = asset;
          checkpointRevisionRef.current = asset.checkpointRevision;
          setSavedRecording(asset);
          setAudioUrl(recordingAssetUrl(asset.id));
          setPersistence("saved");
          setNotice((current) => current?.includes("offline")
            ? "The connection recovered and every durable chunk is now saved."
            : current);
        } catch (reason) {
          setPersistence("recovering");
          setNotice(reason instanceof Error
            ? `Audio remains in the durable device spool and could not finish yet: ${reason.message}`
            : "Audio remains in the durable device spool and could not finish yet.");
        }
      };
      recorderRef.current = recorder;

      openTranscriptionSocket(title || recordingLabel);

      const audioContext = new AudioContext();
      audioContextRef.current = audioContext;
      const source = audioContext.createMediaStreamSource(stream);
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 512;
      const processor = audioContext.createScriptProcessor(4_096, 1, 1);
      processorRef.current = processor;
      source.connect(analyser);
      source.connect(processor);
      processor.connect(audioContext.destination);
      const samples = new Uint8Array(analyser.frequencyBinCount);
      const meter = () => {
        if (!streamRef.current) return;
        analyser.getByteTimeDomainData(samples);
        let energy = 0;
        samples.forEach((sample) => { energy += ((sample - 128) / 128) ** 2; });
        setLevel(Math.min(1, Math.sqrt(energy / samples.length) * 3.4));
        window.requestAnimationFrame(meter);
      };
      window.requestAnimationFrame(meter);
      processor.onaudioprocess = (event) => {
        if (phaseRef.current !== "recording" || !transcriptionEnabledRef.current) return;
        const audio = encodePcm16(event.inputBuffer.getChannelData(0), audioContext.sampleRate);
        const socket = socketRef.current;
        if (socket?.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: "input_audio_buffer.append", audio }));
          return;
        }
        pendingPcmRef.current.push(audio);
        if (pendingPcmRef.current.length > 120) pendingPcmRef.current.shift();
      };
      recorder.start(10_000);
      setPersistence("saving");
      if (resumableSession) {
        setNotice("Recovered and flushed the saved recording before continuing.");
      }
      updatePhase("recording");
      timerRef.current = window.setInterval(() => {
        if (phaseRef.current === "recording") setSeconds((value) => value + 1);
      }, 1_000);
    } catch (reason) {
      const reasonName = reason && typeof reason === "object" && "name" in reason
        ? String(reason.name)
        : "";
      if (reasonName === "AbortError") return;
      closeAudioPipeline();
      updatePhase("error");
      const detail = reason instanceof Error ? reason.message : "Unknown error";
      if (failureStage === "microphone") {
        const failure = microphoneFailure(reason);
        setMicrophoneIssue(failure.issue);
        setNotice(failure.notice);
        return;
      }
      setMicrophoneIssue(null);
      setNotice(
        failureStage === "spool"
            ? `Recording did not start because durable device storage is unavailable: ${detail}`
            : failureStage === "session"
              ? `Recording did not start because Gunther's knowledge service is unavailable: ${detail}`
              : failureStage === "draft"
                ? `Recording did not start because its recovery draft could not be saved: ${detail}`
                : `Gunther could not start durable recording: ${detail}`,
      );
    }
  };

  const pause = () => {
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ type: "input_audio_buffer.commit" }));
    }
    if (recorderRef.current?.state === "recording") recorderRef.current.requestData();
    recorderRef.current?.pause();
    void audioContextRef.current?.suspend();
    updatePhase("paused");
    void queueCheckpoint();
  };

  const resume = () => {
    recorderRef.current?.resume();
    void audioContextRef.current?.resume();
    updatePhase("recording");
  };

  const stop = () => {
    const transcriptionSocket = socketRef.current;
    if (transcriptionSocket?.readyState === WebSocket.OPEN) {
      pendingPcmRef.current.splice(0).forEach((audio) => {
        transcriptionSocket.send(JSON.stringify({ type: "input_audio_buffer.append", audio }));
      });
      transcriptionSocket.send(JSON.stringify({ type: "input_audio_buffer.commit" }));
      window.setTimeout(() => transcriptionSocket.close(), 1_800);
    } else transcriptionSocket?.close();
    stopTranscriptionReconnect();
    if (recorderRef.current?.state !== "inactive") recorderRef.current?.stop();
    closeAudioPipeline();
    setLevel(0);
    updatePhase("stopped");
  };

  const finishRecovered = async () => {
    const session = recordingSessionRef.current;
    if (!session?.recovery.canResume) return;
    setPersistence("saving");
    try {
      let flushed = await flushRecordingSpool(session);
      const importState = importedAudioRef.current;
      if (importState && (
        flushed.nextExpectedSequence !== importState.chunkCount
        || flushed.sizeBytes !== importState.sizeBytes
      )) {
        throw new Error("The server ledger does not match the durable import manifest.");
      }
      await queueCheckpoint(flushed);
      flushed = await flushRecordingSpool(flushed);
      const completed = await knowledgeApi.completeRecordingSession(flushed.id, requireWorkspaceId());
      recordingSessionRef.current = completed;
      checkpointRevisionRef.current = completed.checkpointRevision;
      nextChunkSequenceRef.current = completed.nextExpectedSequence;
      nextSpoolSequenceRef.current = completed.nextExpectedSequence;
      setSavedRecording(completed);
      if (importState) {
        const completedImport: ImportedAudioDraft = {
          ...importState,
          stagedChunkCount: importState.chunkCount,
          phase: "complete",
        };
        importedAudioRef.current = completedImport;
        setImportedAudio(completedImport);
      }
      setAudioUrl(recordingAssetUrl(completed.id));
      setPersistence("saved");
      setNotice("Flushed every durable chunk, then finished the recovered recording.");
    } catch (reason) {
      setPersistence("recovering");
      setNotice(reason instanceof Error
        ? `The recovered recording is still safe but could not be finished: ${reason.message}`
        : "The recovered recording is still safe but could not be finished.");
    }
  };

  const markMoment = () => {
    if (phaseRef.current !== "recording" && phaseRef.current !== "paused") return;
    const nextMoments = [...checkpointSnapshotRef.current.moments, {
      seconds,
      label: "Important moment",
    }];
    checkpointSnapshotRef.current = {
      ...checkpointSnapshotRef.current,
      moments: nextMoments,
    };
    setMoments(nextMoments);
    void queueCheckpoint();
    setNotice(`Marked ${formatTime(seconds)} for review.`);
  };

  const removeMoment = (index: number) => {
    const nextMoments = moments.filter((_, cursor) => cursor !== index);
    checkpointSnapshotRef.current = {
      ...checkpointSnapshotRef.current,
      moments: nextMoments,
    };
    setMoments(nextMoments);
    void queueCheckpoint();
  };

  const restore = (draft: RecordingDraft) => {
    const boundWorkspaceId = requireWorkspaceId();
    if (!draft.workspaceId || draft.workspaceId !== boundWorkspaceId) {
      setPersistence("recovering");
      setNotice("This recovery draft is not bound to the currently verified workspace, so Gunther left it untouched.");
      updatePhase("error");
      return;
    }
    stopTranscriptionReconnect();
    closeAudioPipeline();
    onTitleChange?.(draft.title);
    setTranscript(draft.transcript);
    setSummary(draft.summary);
    setMoments(draft.moments ?? []);
    setSeconds(draft.seconds);
    setSavedRecording(draft.recording);
    const draftImport = draft.importedAudio ?? null;
    importedAudioRef.current = draftImport;
    setImportedAudio(draftImport);
    recordingSessionRef.current = isRecordingSession(draft.recording)
      ? draft.recording
      : null;
    checkpointRevisionRef.current = isRecordingSession(draft.recording)
      ? draft.recording.checkpointRevision
      : 0;
    nextChunkSequenceRef.current = isRecordingSession(draft.recording)
      ? draft.recording.nextExpectedSequence
      : 0;
    nextSpoolSequenceRef.current = isRecordingSession(draft.recording)
      ? draft.recording.nextExpectedSequence
      : 0;
    checkpointSnapshotRef.current = {
      transcript: draft.transcript,
      durationSeconds: draft.seconds,
      moments: draft.moments ?? [],
      recordingContext: draft.recordingContext,
      knowledgeBaseId: draft.knowledgeBaseId || null,
    };
    setAudioType(draft.recording.contentType);
    setAudioUrl(recordingAssetUrl(draft.recording.id));
    setPersistence("recovering");
    setLevel(0);
    setNotice("Recovering the server checkpoint and saved audio…");
    updatePhase("stopped");
    void (async () => {
      await assertRecordingSpoolAvailable();
      const metadata = await knowledgeApi.recordingMetadata(
        draft.recording.id,
        boundWorkspaceId,
      );
      let restoredImport = draftImport;
      if (restoredImport?.phase === "staging") {
        // A staging marker can lag one synchronous localStorage write behind the
        // final IndexedDB transaction. Promote only an exact, verified manifest.
        await bindRecordingSpoolWorkspace(metadata.id, boundWorkspaceId);
        const stagedChunks = await listRecordingSpool(metadata.id, boundWorkspaceId);
        if (
          metadata.nextExpectedSequence !== 0
          || metadata.sizeBytes !== 0
          || !importedAudioSpoolIsComplete(restoredImport, stagedChunks)
        ) {
          throw new Error(
            "The initial app-owned copy was interrupted before every chunk was durable. Select the original file again to restart this import.",
          );
        }
        restoredImport = {
          ...restoredImport,
          stagedChunkCount: restoredImport.chunkCount,
          phase: "ready",
        };
      }
      let server = await flushRecordingSpool(metadata);
      const mergedTranscript = mergeTranscript(
        draft.transcript,
        server.transcript,
        draft.updatedAt,
        server.checkpointedAt,
      );
      const mergedMoments = mergeMoments(draft.moments ?? [], server.moments);
      const mergedSeconds = Math.max(draft.seconds, server.durationSeconds);
      const mergedContext = server.checkpointRevision > 0
        ? server.recordingContext
        : draft.recordingContext;
      const mergedKnowledgeBaseId = server.checkpointRevision > 0
        ? server.knowledgeBaseId ?? draft.knowledgeBaseId
        : draft.knowledgeBaseId;
      checkpointSnapshotRef.current = {
        transcript: mergedTranscript,
        durationSeconds: mergedSeconds,
        moments: mergedMoments,
        recordingContext: mergedContext,
        knowledgeBaseId: mergedKnowledgeBaseId || null,
      };
      recordingSessionRef.current = server;
      checkpointRevisionRef.current = server.checkpointRevision;

      if (restoredImport) {
        if (
          server.nextExpectedSequence !== restoredImport.chunkCount
          || server.sizeBytes !== restoredImport.sizeBytes
        ) {
          throw new Error(
            "The verified server offset does not match this workspace's durable import manifest.",
          );
        }
        if (server.status === "failed") {
          throw new Error("The imported recording session is marked as failed.");
        }
        if (server.status !== "completed") {
          await queueCheckpoint(server);
          if (recordingSessionRef.current?.id === server.id) {
            server = recordingSessionRef.current;
          }
          server = await knowledgeApi.completeRecordingSession(server.id, boundWorkspaceId);
        }
        restoredImport = {
          ...restoredImport,
          stagedChunkCount: restoredImport.chunkCount,
          phase: "complete",
        };
        importedAudioRef.current = restoredImport;
        setImportedAudio(restoredImport);
      }
      const mergedDraft: RecordingDraft = {
        ...draft,
        formatVersion: 2,
        transcript: mergedTranscript,
        seconds: mergedSeconds,
        moments: mergedMoments,
        recordingContext: mergedContext,
        knowledgeBaseId: mergedKnowledgeBaseId,
        workspaceId: boundWorkspaceId,
        importedAudio: restoredImport,
        recording: server,
        updatedAt: new Date().toISOString(),
      };
      setTranscript(mergedTranscript);
      setMoments(mergedMoments);
      setSeconds(mergedSeconds);
      setSavedRecording(server);
      recordingSessionRef.current = server;
      checkpointRevisionRef.current = server.checkpointRevision;
      nextChunkSequenceRef.current = server.nextExpectedSequence;
      nextSpoolSequenceRef.current = server.nextExpectedSequence;
      setAudioType(server.contentType);
      setPersistence(server.status === "completed" ? "saved" : "recovering");
      setNotice(restoredImport
        ? "Recovered the durable imported audio from the verified server offset and finished it without selecting the file again."
        : server.recovery.canResume
          ? "Recovered the latest server checkpoint. Continue recording or finish the saved session."
          : "Recovered the complete recording and merged its latest transcript checkpoint.");
      preserveRecordingDraft(mergedDraft);
      onDraftChange?.(mergedDraft);
      if (!restoredImport && server.status !== "failed") void queueCheckpoint(server);
    })().catch((reason: unknown) => {
      setPersistence("recovering");
      setNotice(reason instanceof Error
        ? `The draft and durable audio chunks remain safe, but recovery is waiting: ${reason.message}`
        : "The draft and durable audio chunks remain safe, but recovery is waiting for the service.");
    });
  };

  useImperativeHandle(ref, () => ({
    start: () => { void start(); },
    pause,
    resume,
    stop,
    markMoment,
    restore,
  }));

  const summarize = async () => {
    if (transcript.trim().length < 3) return;
    setSummarizing(true);
    setNotice(null);
    try {
      setSummary(await knowledgeApi.summarizeLecture({
        title: title.trim() || "Lecture recording",
        transcript: transcript.trim(),
        durationSeconds: seconds,
      }));
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "The lecture could not be summarized.");
    } finally {
      setSummarizing(false);
    }
  };

  const active = phase === "recording" || phase === "paused";
  const capturing = phase === "requesting" || phase === "importing" || active || phase === "stopped";
  const canRetryMicrophone = phase === "error" && microphoneIssue !== null;
  const downloadExtension = savedRecording?.fileName.split(".").pop() || (audioType.includes("mp4") ? "m4a" : "webm");
  const transcriptWords = transcript.trim() ? transcript.trim().split(/\s+/).length : 0;
  useEffect(() => {
    onActiveChange?.(capturing);
  }, [capturing, onActiveChange]);
  useEffect(() => {
    onStatusChange?.({ phase, seconds, persistence, transcriptWords, transcriptionLabel, markedMoments: moments.length });
  }, [moments.length, onStatusChange, persistence, phase, seconds, transcriptWords, transcriptionLabel]);
  useEffect(() => {
    if (!active) return undefined;
    void queueCheckpoint();
    const checkpointTimer = window.setInterval(() => {
      if (recorderRef.current?.state === "recording") recorderRef.current.requestData();
      void queueCheckpoint();
    }, 5_000);
    return () => window.clearInterval(checkpointTimer);
  }, [active, queueCheckpoint]);
  useEffect(() => {
    if (phase !== "stopped" || !recordingSessionRef.current) return undefined;
    const checkpointTimer = window.setTimeout(() => {
      void queueCheckpoint();
    }, 800);
    return () => window.clearTimeout(checkpointTimer);
  }, [knowledgeBaseId, moments, phase, queueCheckpoint, recordingContext, seconds, transcript]);
  useEffect(() => {
    if (!savedRecording || phase === "idle" || phase === "error" || phase === "requesting" || phase === "importing") {
      onDraftChange?.(null);
      return;
    }
    if (phase === "recording" && seconds % 5 !== 0) return;
    const draft: RecordingDraft = {
      formatVersion: 2,
      id: savedRecording.id,
      title: title.trim() || recordingLabel,
      transcript,
      summary,
      seconds,
      recording: savedRecording,
      moments,
      recordingContext,
      knowledgeBaseId,
      workspaceId: requireWorkspaceId(),
      importedAudio,
      updatedAt: new Date().toISOString(),
    };
    preserveRecordingDraft(draft);
    onDraftChange?.(draft);
  }, [importedAudio, knowledgeBaseId, moments, onDraftChange, phase, recordingContext, recordingLabel, requireWorkspaceId, savedRecording, seconds, summary, title, transcript]);
  useEffect(() => {
    onKnowledgeContent(phase === "stopped" && persistence === "saved"
      ? knowledgeDocument(title, transcript, summary, seconds, savedRecording, moments)
      : "");
  }, [moments, onKnowledgeContent, persistence, phase, savedRecording, seconds, summary, title, transcript]);
  useEffect(() => {
    if (!active || !transcriptRef.current || document.activeElement === transcriptRef.current) return;
    transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
  }, [active, transcript]);
  useEffect(() => {
    void knowledgeApi.health().then((health) => {
      setProvider(health.transcriptionMode === "not_configured" ? "local-only" : "available");
      setTranscriptionLabel(health.transcriptionProvider === "sensevoice" ? "SenseVoice · local" : health.transcriptionProvider === "compatible" ? `${health.transcriptionModel} · server` : "Local audio");
    }).catch(() => setProvider("local-only"));
  }, []);
  useEffect(() => () => {
    stopTranscriptionReconnect();
    if (recorderRef.current?.state !== "inactive") recorderRef.current?.stop();
    socketRef.current?.close();
    closeAudioPipeline();
  }, [closeAudioPipeline, stopTranscriptionReconnect]);
  useEffect(() => () => { if (audioUrl?.startsWith("blob:")) URL.revokeObjectURL(audioUrl); }, [audioUrl]);

  return <section className={`lecture-recorder is-${phase}`}>
    <div className="lecture-stage">
      <div className="lecture-meter" aria-label={`Microphone level ${Math.round(level * 100)} percent`}>
        {Array.from({ length: 36 }, (_, index) => <i key={index} style={{ transform: `scaleY(${Math.max(.08, Math.min(1, level * 1.8 - Math.abs(index - 18) / 42))})` }} />)}
      </div>
      <div className="lecture-clock"><span className={active ? "is-live" : ""} /><strong>{formatTime(seconds)}</strong><small>{phase === "recording" ? "Listening" : phase === "paused" ? "Paused" : phase === "requesting" ? "Opening microphone…" : phase === "importing" ? "Importing audio…" : recordingLabel}</small></div>
      <div className="lecture-controls">
        {(phase === "idle" || phase === "error") && <><button type="button" className="record-start" onClick={() => void start()}>{canRetryMicrophone ? <RotateCcw size={16} /> : <AudioLines size={17} />}{canRetryMicrophone ? "Retry microphone" : "Start recording"}</button><label className="record-import"><input type="file" accept="audio/*,.mp3,.m4a,.wav,.webm,.mp4,.aac,.flac" onChange={(event) => void importAudio(event)} /><Upload size={15} />Import audio</label></>}
        {phase === "requesting" && <button type="button" disabled><LoaderCircle className="spin" size={16} />Requesting access</button>}
        {phase === "importing" && <button type="button" disabled><LoaderCircle className="spin" size={16} />Importing audio</button>}
        {active && <button type="button" className="record-mark" onClick={markMoment}><Bookmark size={14} />Mark moment</button>}
        {phase === "recording" && <button type="button" onClick={pause}><Pause size={15} />Pause</button>}
        {phase === "paused" && <button type="button" onClick={resume}><Play size={15} />Resume</button>}
        {active && <button type="button" className="record-stop" onClick={stop}><Square size={13} />Finish</button>}
        {phase === "stopped" && savedRecording && isRecordingSession(savedRecording) && savedRecording.recovery.canResume
          ? <>{!importedAudio && <button type="button" onClick={() => void start()}><RotateCcw size={13} />Continue recording</button>}<button type="button" className="record-stop" onClick={() => void finishRecovered()}><Square size={13} />{importedAudio ? "Resume import" : "Flush and finish"}</button></>
          : phase === "stopped" && <span className="record-review-hint"><RotateCcw size={13} />Save this session before starting another</span>}
      </div>
      <div className={`transcription-state is-${provider}`}><i />{provider === "live" ? `${transcriptionLabel} · live` : provider === "available" ? `${transcriptionLabel} ready` : provider === "connecting" || provider === "checking" ? "Checking transcript…" : "Local audio · editable text"}</div>
      <div className={`recording-persistence is-${persistence}`}><HardDrive size={13} />{persistence === "saving" ? phase === "importing" ? "Importing audio in safe chunks" : "Saving locally as you record" : persistence === "saved" ? "Complete recording saved" : persistence === "recovering" ? "Recovering local save" : "Long-session local save ready"}</div>
    </div>
    {notice && <p className="lecture-notice" role={microphoneIssue ? "alert" : "status"}><CircleAlert size={13} />{notice}</p>}
    <div className="lecture-transcript">
      <header><span><strong>Live transcript</strong><small>Editable before it becomes knowledge</small></span><em>{transcript.trim() ? `${transcript.trim().split(/\s+/).length} words` : "Waiting for speech"}</em></header>
      <textarea ref={transcriptRef} value={transcript} onChange={(event) => {
        checkpointSnapshotRef.current = {
          ...checkpointSnapshotRef.current,
          transcript: event.target.value,
        };
        setTranscript(event.target.value);
        setSummary(null);
      }} placeholder={provider === "local-only" ? "Audio is recording locally. Paste or type a transcript here to summarize it." : "Your words will appear here as the lecture continues…"} rows={7} />
    </div>
    {moments.length > 0 && <div className="recording-moments"><span><Bookmark size={13} />Marked for review</span>{moments.map((moment, index) => <button type="button" key={`${moment.seconds}-${index}`} onClick={() => removeMoment(index)} title="Remove this marker"><strong>{formatTime(moment.seconds)}</strong><small>{moment.label}</small><X size={11} /></button>)}</div>}
    {audioUrl && <div className="lecture-playback"><audio controls src={audioUrl} /><span className={persistence === "saved" ? "is-saved" : ""}>{persistence === "saved" && savedRecording ? `Saved locally · ${savedRecording.sizeBytes >= 1_048_576 ? `${(savedRecording.sizeBytes / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(savedRecording.sizeBytes / 1024))} KB`}` : "Finishing local save…"}</span><a href={audioUrl} download={`${(title || "lecture").replace(/[^a-z0-9-_]+/gi, "-")}.${downloadExtension}`}><Download size={13} />Download</a></div>}
    <div className="lecture-summary-action">
      <span><Sparkles size={14} /><span><strong>{recordingContext === "meeting" ? "Turn the meeting into usable notes" : recordingContext === "memo" ? "Shape this thought into a clear note" : "Turn the recording into study notes"}</strong><small>Preview it here, or just save: Gunther writes the notes afterwards, citing the transcript.</small></span></span>
      <button type="button" onClick={() => void summarize()} disabled={summarizing || transcript.trim().length < 3 || active}>{summarizing ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />}{summarizing ? "Summarizing…" : summary ? "Refresh summary" : "Summarize recording"}</button>
    </div>
    {summary && <div className="lecture-summary-preview"><span><small>{summary.engine} summary</small><strong>{summary.overview}</strong></span>{summary.keyPoints.length > 0 && <ul>{summary.keyPoints.slice(0, 5).map((point) => <li key={point}>{point}</li>)}</ul>}</div>}
  </section>;
});
