import { describe, expect, it } from "vitest";
import { activityItem as item, recordingStatus as rec } from "./fixtures";
import { ago, clock, earlierRows, failureKey, meanProgress, nowRows, pillView, type PillInput } from "./summary";

const view = (input: Partial<PillInput>) =>
  pillView({ items: [], recording: null, reachable: true, seenFailures: new Set(), flash: null, ...input });

describe("clock and ago", () => {
  it("counts m:ss under an hour and h:mm:ss from an hour", () => {
    expect(clock(0)).toBe("0:00");
    expect(clock(302)).toBe("5:02");
    expect(clock(754)).toBe("12:34");
    expect(clock(3599)).toBe("59:59");
    expect(clock(3600)).toBe("1:00:00");
    expect(clock(3725)).toBe("1:02:05");
  });

  it("says how long ago", () => {
    const now = Date.parse("2026-10-12T12:00:00Z");
    expect(ago("2026-10-12T11:59:40Z", now)).toBe("just now");
    expect(ago("2026-10-12T11:58:00Z", now)).toBe("2 min ago");
    expect(ago("2026-10-12T11:00:00Z", now)).toBe("1 h ago");
    expect(ago(null, now)).toBe("");
  });

  it("averages only the progresses that are known", () => {
    expect(meanProgress([item({ progress: 0.2 }), item({ progress: 0.6 }), item({ progress: null })])).toBeCloseTo(0.4);
    expect(meanProgress([item(), item()])).toBeNull();
  });
});

describe("the pill", () => {
  it("is calm when nothing runs", () => {
    expect(view({})).toMatchObject({ tone: "idle", icon: "dot", text: "Saved locally", extra: 0 });
  });

  it("says Reconnecting… when the service is offline", () => {
    expect(view({ reachable: false })).toMatchObject({ tone: "offline", text: "Reconnecting…" });
  });

  it("shows the recording clock and its phases", () => {
    expect(view({ recording: rec() })).toMatchObject({ tone: "recording", icon: "recording", clock: "12:34", text: "Recording" });
    expect(view({ recording: rec({ phase: "paused" }) })).toMatchObject({ tone: "paused", icon: "pause", clock: "12:34", text: "Paused" });
    expect(view({ recording: rec({ phase: "stopped" }) })).toMatchObject({ icon: "grey-dot", text: "Recording to save" });
    expect(view({ recording: rec({ phase: "requesting" }) })).toMatchObject({ icon: "spinner", text: "Starting recording…" });
    expect(view({ recording: rec({ phase: "importing" }) })).toMatchObject({ icon: "spinner", text: "Importing audio…" });
  });

  it("shows one job with its percent, or without when the length is unknown", () => {
    expect(view({ items: [item({ progress: 0.42 })] })).toMatchObject({ icon: "ring", text: "Transcribing again", percent: "42%", progress: 0.42, extra: 0 });
    const unknown = view({ items: [item()] });
    expect(unknown).toMatchObject({ icon: "ring", text: "Transcribing again", progress: null });
    expect(unknown.percent).toBeUndefined();
  });

  it("counts several jobs; the ring averages what is known, with no percent in words", () => {
    const items = [item({ id: "a", progress: 0.2 }), item({ id: "b", progress: 0.4 }), item({ id: "c", state: "waiting" })];
    const several = view({ items });
    expect(several).toMatchObject({ text: "3 running", extra: 0 });
    expect(several.percent).toBeUndefined();
    expect(several.progress).toBeCloseTo(0.3);
    expect(view({ items: [item({ id: "a" }), item({ id: "b" })] }).progress).toBeNull();
  });

  it("flashes a job that just finished", () => {
    const done = item({ id: "output:lib", state: "done", title: "Slides ready", endedAt: "2026-10-12T10:05:00Z" });
    expect(view({ items: [done], flash: "output:lib" })).toMatchObject({ tone: "done", icon: "check", text: "Slides ready" });
    expect(view({ items: [done], flash: null }).tone).toBe("idle");
    expect(view({ items: [done], flash: "output:other" }).tone).toBe("idle");
  });

  it("keeps a failure until it has been seen", () => {
    const failed = item({ id: "output:lib", state: "failed", title: "Couldn't build slides", error: "The model timed out.", endedAt: "2026-10-12T10:05:00Z" });
    expect(view({ items: [failed] })).toMatchObject({ tone: "failed", icon: "alert", text: "Couldn't build slides" });
    expect(view({ items: [failed], seenFailures: new Set([failureKey(failed)]) }).tone).toBe("idle");
  });

  it("orders what to show: recording, failure, flash, running jobs, offline, saved", () => {
    const failed = item({ id: "f", state: "failed", title: "Couldn't write the report", endedAt: "2026-10-12T10:05:00Z" });
    const done = item({ id: "d", state: "done", title: "Report ready", endedAt: "2026-10-12T10:06:00Z" });
    const running = item({ id: "r", progress: 0.5 });
    const all = { items: [failed, done, running], flash: "d", reachable: false };
    expect(view({ ...all, recording: rec() }).tone).toBe("recording");
    expect(view(all)).toMatchObject({ tone: "failed", extra: 1 });
    expect(view({ ...all, seenFailures: new Set([failureKey(failed)]) })).toMatchObject({ tone: "done", extra: 1 });
    expect(view({ ...all, items: [done, running], flash: null }).tone).toBe("busy");
    expect(view({ ...all, items: [done], flash: null }).tone).toBe("offline");
  });

  it("holds a recording plus five jobs: the recording, then +5; waiting jobs after running ones", () => {
    const jobs = [
      item({ id: "transcribe:a", state: "running", progress: 0.4, startedAt: "2026-10-12T10:00:00Z" }),
      item({ id: "transcribe:b", state: "waiting", title: "Waiting to transcribe again", startedAt: "2026-10-12T10:01:00Z" }),
      item({ id: "output:one", kind: "output", title: "Building slides", about: "Library one", startedAt: "2026-10-12T10:02:00Z" }),
      item({ id: "output:two", kind: "output", title: "Writing a report", about: "Library two", startedAt: "2026-10-12T10:03:00Z" }),
      item({ id: "answer:s", kind: "answer", title: "Writing an answer", about: "Why?", startedAt: "2026-10-12T10:04:00Z" }),
    ];
    expect(view({ items: jobs, recording: rec() })).toMatchObject({ clock: "12:34", text: "Recording", extra: 5 });
    const rows = nowRows(jobs, rec());
    expect(rows).toHaveLength(6);
    expect(rows[0]!.type).toBe("recording");
    expect(rows.slice(1).map((row) => (row.type === "job" ? row.item.id : ""))).toEqual(["answer:s", "output:two", "output:one", "transcribe:a", "transcribe:b"]);
  });

  it("lists finished work newest first under Earlier", () => {
    const rows = earlierRows([
      item({ id: "old", state: "done", endedAt: "2026-10-12T10:01:00Z" }),
      item({ id: "now", state: "running" }),
      item({ id: "new", state: "failed", endedAt: "2026-10-12T10:09:00Z" }),
    ]);
    expect(rows.map((row) => row.id)).toEqual(["new", "old"]);
  });
});
