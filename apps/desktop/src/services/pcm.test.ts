import { describe, expect, it } from "vitest";
import { encodePcm16, pcm16Length } from "./pcm";

const samplesOf = (base64: string) => window.atob(base64).length / 2;

describe("pcm16", () => {
  it.each([48_000, 44_100])("blocks of 4096 at %i Hz add up to exactly the 16 kHz total", (rate) => {
    let seen = 0;
    let total = 0;
    for (let block = 0; block < 1_000; block += 1) {
      total += pcm16Length(4_096, rate, 16_000, seen);
      seen += 4_096;
    }
    expect(total).toBe(Math.round((4_096_000 * 16_000) / rate));
  });

  it("makes the same number of samples as it counts, block after block", () => {
    const block = new Float32Array(4_096).fill(0.25);
    let seen = 0;
    let total = 0;
    for (let index = 0; index < 20; index += 1) {
      const audio = encodePcm16(block, 48_000, 16_000, seen);
      expect(samplesOf(audio)).toBe(pcm16Length(4_096, 48_000, 16_000, seen));
      total += samplesOf(audio);
      seen += 4_096;
    }
    expect(total).toBe(Math.round((20 * 4_096) / 3));
  });

  it("keeps the old rule without a start sample (dictation)", () => {
    expect(pcm16Length(4_096, 48_000, 24_000)).toBe(2_048);
    expect(samplesOf(encodePcm16(new Float32Array(4_096), 48_000))).toBe(2_048);
  });

  it("keeps a steady level through the block edges", () => {
    const block = new Float32Array(4_096).fill(0.5);
    const decoded = Array.from(window.atob(encodePcm16(block, 48_000, 16_000, 4_096)), (c) => c.charCodeAt(0));
    const values = new Int16Array(new Uint8Array(decoded).buffer);
    expect(Math.min(...values)).toBeGreaterThan(16_000);
  });
});
