import { describe, expect, it } from "vitest";

import {
  recordingBlobChecksum,
  recordingChunkMatches,
  type RecordingSpoolChunk,
} from "./recordingSpool";

async function stored(blob: Blob): Promise<RecordingSpoolChunk> {
  return {
    key: "session:000000000000",
    sessionId: "session",
    sequence: 0,
    blob,
    mimeType: blob.type,
    sizeBytes: blob.size,
    checksum: await recordingBlobChecksum(blob),
    workspaceId: "wsp_primary",
    source: "live",
    createdAt: "2026-08-30T00:00:00.000Z",
  };
}

function bytes(...values: number[]): Uint8Array<ArrayBuffer> {
  return Uint8Array.from(values) as Uint8Array<ArrayBuffer>;
}

describe("recording spool chunk integrity", () => {
  it("accepts only byte-identical retries", async () => {
    const existing = await stored(new Blob([bytes(1, 2, 3, 4)], { type: "audio/webm" }));

    await expect(
      recordingChunkMatches(
        existing,
        new Blob([bytes(1, 2, 3, 4)], { type: "audio/webm" }),
      ),
    ).resolves.toBe(true);
    await expect(
      recordingChunkMatches(
        existing,
        new Blob([bytes(4, 3, 2, 1)], { type: "audio/webm" }),
      ),
    ).resolves.toBe(false);
  });

  it("rejects a changed explicit MIME type and tolerates an omitted retry type", async () => {
    const existing = await stored(new Blob([bytes(1, 2)], { type: "audio/webm" }));

    await expect(
      recordingChunkMatches(existing, new Blob([bytes(1, 2)], { type: "audio/ogg" })),
    ).resolves.toBe(false);
    await expect(recordingChunkMatches(existing, new Blob([bytes(1, 2)]))).resolves.toBe(true);
  });

  it("pins a retry to the checksum recorded with the durable chunk", async () => {
    const existing = await stored(new Blob([bytes(1, 2, 3)], { type: "audio/webm" }));
    existing.checksum = "0".repeat(64);

    await expect(
      recordingChunkMatches(existing, new Blob([bytes(1, 2, 3)], { type: "audio/webm" })),
    ).resolves.toBe(false);
  });
});
