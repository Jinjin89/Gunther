import type { LectureSummary, RecordingAsset, RecordingContext, RecordingMoment } from "@gunther/contracts";
import { persistentStorage } from "../persistentStorage";
import { decodeTranscriptState, type TranscriptState } from "./liveTranscript";

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
  /** 3 adds `segments`; a version 2 draft has only the text and still restores. */
  formatVersion?: 2 | 3;
  id: string;
  title: string;
  transcript: string;
  /** The live transcript as sentences and paragraphs (drafts are never kept). */
  segments?: TranscriptState | null;
  summary: LectureSummary | null;
  seconds: number;
  recording: RecordingAsset;
  moments: RecordingMoment[];
  recordingContext: RecordingContext;
  knowledgeBaseId: string;
  workspaceId: string | null;
  importedAudio?: ImportedAudioDraft | null;
  updatedAt: string;
}

const RECORDING_DRAFTS_KEY = "gunther:recording-drafts";
const RECORDING_DRAFT_LIMIT = 20;

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

export function loadRecordingDrafts(): RecordingDraft[] {
  try {
    const parsed = JSON.parse(persistentStorage.getItem(RECORDING_DRAFTS_KEY) ?? "[]") as RecordingDraft[];
    return parsed
      .filter((draft) => draft?.id && draft?.recording?.id)
      .map((draft) => ({
        ...draft,
        formatVersion: draft.formatVersion === 3 ? (3 as const) : (2 as const),
        segments: decodeTranscriptState(draft.segments),
        workspaceId: typeof draft.workspaceId === "string" ? draft.workspaceId : null,
        importedAudio: normalizeImportedAudioDraft(draft.importedAudio),
      }))
      .slice(0, RECORDING_DRAFT_LIMIT);
  } catch {
    return [];
  }
}

export function removeRecordingDraft(id: string) {
  const next = loadRecordingDrafts().filter((draft) => draft.id !== id);
  persistentStorage.setItem(RECORDING_DRAFTS_KEY, JSON.stringify(next));
}

export function recordingDraftsForWorkspace(
  drafts: RecordingDraft[],
  workspaceId: string | null,
): RecordingDraft[] {
  return workspaceId
    ? drafts.filter((draft) => draft.workspaceId === workspaceId)
    : [];
}

export function preserveRecordingDraft(draft: RecordingDraft) {
  const next = [draft, ...loadRecordingDrafts().filter((item) => item.id !== draft.id)].slice(0, RECORDING_DRAFT_LIMIT);
  persistentStorage.setItem(RECORDING_DRAFTS_KEY, JSON.stringify(next));
}
