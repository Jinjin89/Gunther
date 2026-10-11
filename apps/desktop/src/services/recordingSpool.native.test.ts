import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("../persistentStorage", () => ({ flushPersistentStorage: vi.fn().mockResolvedValue(undefined) }));

describe("native recording persistence", () => {
  beforeEach(() => {
    invoke.mockReset();
    Object.defineProperty(window, "__TAURI_INTERNALS__", { configurable: true, value: {} });
  });
  afterEach(() => { Reflect.deleteProperty(window, "__TAURI_INTERNALS__"); });

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
});
