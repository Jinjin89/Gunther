import { beforeEach, describe, expect, it } from "vitest";
import {
  importedAudioSpoolIsComplete,
  importStorageHasCapacity,
  loadRecordingDrafts,
  recordingDraftsForWorkspace,
  type ImportedAudioDraft,
  type RecordingDraft,
} from "./LectureRecorder";

const recording = {
  id: "rec_0123456789abcdef01234567",
  title: "Lecture",
  contentType: "audio/webm",
};

describe("recording draft workspace binding", () => {
  beforeEach(() => window.localStorage.clear());

  it("keeps legacy drafts recoverable as data but marks them unsafe to auto-resume", () => {
    window.localStorage.setItem("gunther:recording-drafts", JSON.stringify([{
      id: recording.id,
      title: "Older lecture",
      transcript: "",
      summary: null,
      seconds: 12,
      recording,
      moments: [],
      recordingContext: "lecture",
      knowledgeBaseId: "",
      updatedAt: new Date().toISOString(),
    }]));

    expect(loadRecordingDrafts()).toEqual([
      expect.objectContaining({
        formatVersion: 2,
        id: recording.id,
        workspaceId: null,
        importedAudio: null,
      }),
    ]);
  });

  it("migrates and retains only a structurally complete imported-audio manifest", () => {
    const importedAudio: ImportedAudioDraft = {
      fileName: "four-hour-lecture.wav",
      contentType: "audio/wav",
      sizeBytes: 17,
      chunkSizeBytes: 8,
      chunkCount: 3,
      stagedChunkCount: 3,
      checksumAlgorithm: "SHA-256",
      phase: "uploading",
    };
    window.localStorage.setItem("gunther:recording-drafts", JSON.stringify([{
      id: recording.id,
      title: "Imported lecture",
      transcript: "",
      summary: null,
      seconds: 12,
      recording,
      moments: [],
      recordingContext: "lecture",
      knowledgeBaseId: "biology",
      workspaceId: "wsp_primary",
      importedAudio,
      updatedAt: new Date().toISOString(),
    }]));

    expect(loadRecordingDrafts()).toEqual([
      expect.objectContaining({ formatVersion: 2, importedAudio }),
    ]);

    window.localStorage.setItem("gunther:recording-drafts", JSON.stringify([{
      ...loadRecordingDrafts()[0],
      importedAudio: { ...importedAudio, chunkCount: 2 },
    }]));
    expect(loadRecordingDrafts()).toEqual([
      expect.objectContaining({ importedAudio: null }),
    ]);
  });

  it("retains the exact workspace on current drafts", () => {
    window.localStorage.setItem("gunther:recording-drafts", JSON.stringify([{
      id: recording.id,
      title: "Bound lecture",
      transcript: "",
      summary: null,
      seconds: 12,
      recording,
      moments: [],
      recordingContext: "lecture",
      knowledgeBaseId: "biology",
      workspaceId: "wsp_primary",
      updatedAt: new Date().toISOString(),
    }]));

    expect(loadRecordingDrafts()).toEqual([
      expect.objectContaining({ id: recording.id, workspaceId: "wsp_primary" }),
    ]);
  });

  it("never lets foreign or legacy drafts hide the current workspace recovery list", () => {
    const drafts = [
      ...Array.from({ length: 3 }, (_, index) => ({
        id: `foreign-${index}`,
        title: "Foreign",
        transcript: "",
        summary: null,
        seconds: 1,
        recording: { ...recording, id: `${recording.id}-${index}` },
        moments: [],
        recordingContext: "lecture" as const,
        knowledgeBaseId: "",
        workspaceId: index === 0 ? null : "wsp_foreign",
        updatedAt: new Date().toISOString(),
      })),
      {
        id: "current",
        title: "Current workspace recording",
        transcript: "",
        summary: null,
        seconds: 1,
        recording,
        moments: [],
        recordingContext: "lecture" as const,
        knowledgeBaseId: "",
        workspaceId: "wsp_primary",
        updatedAt: new Date().toISOString(),
      },
    ];

    expect(recordingDraftsForWorkspace(drafts as unknown as RecordingDraft[], "wsp_primary")).toEqual([
      expect.objectContaining({ id: "current" }),
    ]);
  });
});

describe("durable imported-audio planning", () => {
  const manifest: ImportedAudioDraft = {
    fileName: "lecture.wav",
    contentType: "audio/wav",
    sizeBytes: 17,
    chunkSizeBytes: 8,
    chunkCount: 3,
    stagedChunkCount: 3,
    checksumAlgorithm: "SHA-256",
    phase: "ready",
  };

  it("requires a contiguous import-only chunk manifest with the exact byte total", () => {
    expect(importedAudioSpoolIsComplete(manifest, [
      { sequence: 0, sizeBytes: 8, source: "import" },
      { sequence: 1, sizeBytes: 8, source: "import" },
      { sequence: 2, sizeBytes: 1, source: "import" },
    ])).toBe(true);
    expect(importedAudioSpoolIsComplete(manifest, [
      { sequence: 0, sizeBytes: 8, source: "import" },
      { sequence: 2, sizeBytes: 8, source: "import" },
      { sequence: 3, sizeBytes: 1, source: "import" },
    ])).toBe(false);
    expect(importedAudioSpoolIsComplete(manifest, [
      { sequence: 0, sizeBytes: 8, source: "live" },
      { sequence: 1, sizeBytes: 8, source: "import" },
      { sequence: 2, sizeBytes: 1, source: "import" },
    ])).toBe(false);
  });

  it("reserves headroom when the browser reports a storage quota", () => {
    expect(importStorageHasCapacity({ quota: 300_000_000, usage: 10_000_000 }, 100_000_000)).toBe(true);
    expect(importStorageHasCapacity({ quota: 150_000_000, usage: 10_000_000 }, 100_000_000)).toBe(false);
    expect(importStorageHasCapacity({}, 100_000_000)).toBe(true);
  });
});
