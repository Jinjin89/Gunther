import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RecordingSpoolChunk } from "./recordingSpool";

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

function seedBrowserSpool(chunk: RecordingSpoolChunk) {
  const rows = new Map([[chunk.key, chunk]]);
  const database = {
    transaction: () => {
      const transaction = {
        oncomplete: () => undefined,
        objectStore: () => ({
          index: () => ({
            getAll: (sessionId: string) => {
              const request = {
                result: [...rows.values()].filter((row) => row.sessionId === sessionId),
                onsuccess: () => undefined,
              };
              queueMicrotask(() => {
                request.onsuccess();
                queueMicrotask(() => transaction.oncomplete());
              });
              return request;
            },
          }),
          delete: (key: string) => {
            rows.delete(key);
            queueMicrotask(() => transaction.oncomplete());
          },
        }),
      };
      return transaction;
    },
  };
  vi.stubGlobal("indexedDB", { open: () => {
    const request = { result: database, onsuccess: () => undefined };
    queueMicrotask(() => request.onsuccess());
    return request;
  } });
  vi.stubGlobal("IDBKeyRange", { only: (sessionId: string) => sessionId });
  return rows;
}

describe("native recording persistence", () => {
  beforeEach(() => {
    vi.resetModules();
    invoke.mockReset();
    window.localStorage.clear();
    Object.defineProperty(window, "__TAURI_INTERNALS__", { configurable: true, value: {} });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    Reflect.deleteProperty(window, "__TAURI_INTERNALS__");
  });

  it("stores native bytes and reopens them without browser storage", async () => {
    const stored: Record<string, unknown>[] = [];
    invoke.mockImplementation(async (command, input) => {
      if (command === "recording_spool_put") stored.push(input.chunk);
      if (command === "recording_spool_list") return stored;
      return undefined;
    });
    const spool = await import("./recordingSpool");
    const blob = new Blob([new Uint8Array([1, 2, 3])], { type: "audio/webm" });
    await spool.spoolRecordingChunk("rec_kept", 0, blob, "wsp_kept");
    const chunks = await spool.listRecordingSpool("rec_kept", "wsp_kept");
    expect(chunks).toHaveLength(1);
    expect(await spool.recordingChunkMatches(chunks[0]!, blob)).toBe(true);
    await expect(spool.listRecordingSpool("rec_kept", "wsp_foreign")).rejects.toThrow("another workspace");
    stored[0]!.bytes = [9, 2, 3];
    await expect(spool.listRecordingSpool("rec_kept", "wsp_kept")).rejects.toThrow("SHA-256");
  });

  it("reopens a first-release v2 draft and moves its browser audio into native storage", async () => {
    const { initializePersistentStorage } = await import("../persistentStorage");
    const { loadRecordingDrafts, recordingDraftsForWorkspace } = await import("./recordingDrafts");
    const { listRecordingSpool, recordingChunkMatches } = await import("./recordingSpool");
    // The first app stored the draft in localStorage and its bound chunks in IndexedDB.
    const id = "rec_0123456789abcdef01234567";
    const workspaceId = "wsp_kept";
    const createdAt = "2025-07-08T10:11:12Z";
    const bytes = [1, 2, 3];
    const blob = new Blob([new Uint8Array(bytes)], { type: "audio/webm" });
    const chunk: RecordingSpoolChunk = {
      key: `${id}:000000000000`, sessionId: id, sequence: 0, blob, mimeType: blob.type,
      sizeBytes: blob.size,
      checksum: "039058c6f2c0cb492c533b0a4d14ef77cc0f78abccced5287d84a1a2011cfb81",
      workspaceId, source: "live", createdAt,
    };
    const browserRows = seedBrowserSpool(chunk);
    const draft = {
      formatVersion: 2, id, title: "Saved lecture", transcript: "[00:00] Preserved words.",
      summary: null, seconds: 12, moments: [], recordingContext: "lecture",
      recording: { id, fileName: "recording.webm", contentType: blob.type, sizeBytes: 0, storedAt: createdAt },
      knowledgeBaseId: "", workspaceId, updatedAt: createdAt,
    };
    const draftJson = JSON.stringify([draft]);
    window.localStorage.setItem("gunther:recording-drafts", draftJson);
    const nativeRows: unknown[] = [];
    invoke.mockImplementation(async (command, input) => {
      if (command === "desktop_storage_load") return input.legacy;
      if (command === "recording_spool_put") nativeRows.push(input.chunk);
      if (command === "recording_spool_list") return nativeRows;
      return undefined;
    });

    await initializePersistentStorage();
    expect(invoke).toHaveBeenCalledWith("desktop_storage_load", { legacy: { "gunther:recording-drafts": draftJson } });
    expect(recordingDraftsForWorkspace(loadRecordingDrafts(), workspaceId)).toEqual([
      { ...draft, segments: null, importedAudio: null },
    ]);
    const [restored] = await listRecordingSpool(id, workspaceId);
    expect(restored).toMatchObject({ sessionId: id, sequence: 0, workspaceId, checksum: chunk.checksum });
    expect(await recordingChunkMatches(restored!, blob)).toBe(true);
    expect(browserRows.size).toBe(0);
    expect(nativeRows).toEqual([expect.objectContaining({ sessionId: id, workspaceId, bytes })]);
  });
});
