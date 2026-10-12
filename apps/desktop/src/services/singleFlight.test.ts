import { describe, expect, it } from "vitest";
import { runOnce } from "./singleFlight";

describe("runOnce", () => {
  it("runs the body again after one that finished without waiting", async () => {
    const ref: { current: Promise<void> | null } = { current: null };
    let runs = 0;
    await runOnce(ref, async () => { runs += 1; });
    await runOnce(ref, async () => { runs += 1; });
    expect(runs).toBe(2);
    expect(ref.current).toBeNull();
  });

  it("returns the run in flight instead of starting a second", async () => {
    const ref: { current: Promise<void> | null } = { current: null };
    let release = () => undefined;
    let runs = 0;
    const first = runOnce(ref, () => new Promise<void>((resolve) => { runs += 1; release = resolve; }));
    expect(runOnce(ref, async () => { runs += 1; })).toBe(first);
    release();
    await first;
    expect(runs).toBe(1);
    expect(ref.current).toBeNull();
  });

  it("clears the ref when the body fails", async () => {
    const ref: { current: Promise<void> | null } = { current: null };
    await runOnce(ref, async () => { throw new Error("x"); }).catch(() => undefined);
    await Promise.resolve();
    expect(ref.current).toBeNull();
  });
});

describe("the minute check loop", () => {
  it("starts a check on a final piece after a first pump that found nothing", async () => {
    const { applyPiece, emptyTranscript, nextWindow } = await import("./liveTranscript");
    const ref: { current: Promise<void> | null } = { current: null };
    let state = emptyTranscript();
    const taken: ({ startMs: number; endMs: number } | null)[] = [];
    const pump = () => runOnce(ref, async () => {
      taken.push(nextWindow(state));
    });
    await pump(); // at service.ready: nothing to check yet
    state = applyPiece(state, {
      id: "a", start_ms: 0, end_ms: 70_000, text: "Long.", final: true,
      sentences: [{ start_ms: 0, end_ms: 70_000, text: "Long." }],
    });
    await pump();
    expect(taken).toEqual([null, { startMs: 0, endMs: 70_000 }]);
  });
});
