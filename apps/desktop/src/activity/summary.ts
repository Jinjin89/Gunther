import type { ActivityItem } from "@gunther/contracts";
import type { RecordingStatus } from "./recordingStatus";

/** How long a finished job is flashed in the top bar. */
export const FLASH_MS = 6_000;

export const IDLE_TEXT = "Saved locally";
export const OFFLINE_TEXT = "Reconnecting…";

/** What the pill's small picture is. */
export type PillIcon = "dot" | "recording" | "pause" | "grey-dot" | "spinner" | "ring" | "check" | "alert";
/** The colour family: red = microphone on, green = done, warning = failed or offline. */
export type PillTone = "idle" | "offline" | "recording" | "paused" | "waiting" | "busy" | "done" | "failed";

export interface PillView {
  tone: PillTone;
  icon: PillIcon;
  /** The words. */
  text: string;
  /** The recording clock, shown before the words. */
  clock?: string;
  /** "42%", shown after the words; also what stays when the window is narrow. */
  percent?: string;
  /** 0..1 for a ring that fills; null for one that turns; absent for other icons. */
  progress?: number | null;
  /** The small "+N" chip: running work the words do not show. */
  extra: number;
  ariaLabel: string;
}

export const isActive = (item: ActivityItem) => item.state === "waiting" || item.state === "running";

/** `m:ss` under an hour, `h:mm:ss` from an hour. */
export function clock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor(total / 60) % 60;
  const rest = String(total % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

/** `just now`, `2 min ago`, `1 h ago` (and days, for anything older). */
export function ago(iso: string | null, now: number): string {
  const then = iso ? Date.parse(iso) : Number.NaN;
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, Math.floor((now - then) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? `${hours} h ago` : `${Math.floor(hours / 24)} d ago`;
}

/** The mean of the progresses that are known; null when none is. */
export function meanProgress(items: ActivityItem[]): number | null {
  const known = items.flatMap((item) => (typeof item.progress === "number" ? [item.progress] : []));
  return known.length ? known.reduce((sum, value) => sum + value, 0) / known.length : null;
}

const percentOf = (value: number) => `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%`;

/** The key that tells one failure from another, so opening the panel can mark it seen. */
export const failureKey = (item: ActivityItem) => `${item.id}@${item.endedAt ?? ""}`;

export interface PillInput {
  items: ActivityItem[];
  recording: RecordingStatus | null;
  /** The service answers (the app's own check and the activity list both). */
  reachable: boolean;
  /** `failureKey`s of failures the person has already seen. */
  seenFailures: ReadonlySet<string>;
  /** The id of a job that finished a moment ago and is not on the current page. */
  flash: string | null;
}

/** Newest first by when it started. */
const byStartedDesc = (a: ActivityItem, b: ActivityItem) => Date.parse(b.startedAt) - Date.parse(a.startedAt) || 0;
const byEndedDesc = (a: ActivityItem, b: ActivityItem) => (Date.parse(b.endedAt ?? "") || 0) - (Date.parse(a.endedAt ?? "") || 0);

function recordingView(recording: RecordingStatus, extra: number): PillView {
  const time = clock(recording.seconds);
  switch (recording.phase) {
    case "requesting":
      return { tone: "busy", icon: "spinner", text: "Starting recording…", extra, ariaLabel: "Starting recording" };
    case "importing":
      return { tone: "busy", icon: "spinner", text: "Importing audio…", extra, ariaLabel: "Importing audio" };
    case "paused":
      return { tone: "paused", icon: "pause", text: "Paused", clock: time, extra, ariaLabel: `Recording paused at ${time}` };
    case "stopped":
      return { tone: "waiting", icon: "grey-dot", text: "Recording to save", extra, ariaLabel: "Recording to save" };
    default:
      return { tone: "recording", icon: "recording", text: "Recording", clock: time, extra, ariaLabel: `Recording, ${time}` };
  }
}

/**
 * What the top bar's pill shows. Priority: recording, a failure not yet seen, a job that just
 * finished, running jobs, offline, then "Saved locally".
 */
export function pillView({ items, recording, reachable, seenFailures, flash }: PillInput): PillView {
  const active = items.filter(isActive);
  if (recording) return recordingView(recording, active.length);

  const failed = items.filter((item) => item.state === "failed" && !seenFailures.has(failureKey(item))).sort(byEndedDesc)[0];
  if (failed) {
    return { tone: "failed", icon: "alert", text: failed.title, extra: active.length, ariaLabel: failed.error ? `${failed.title}. ${failed.error}` : failed.title };
  }

  const finished = flash ? items.find((item) => item.id === flash && item.state === "done") : undefined;
  if (finished) {
    return { tone: "done", icon: "check", text: finished.title, extra: active.length, ariaLabel: finished.title };
  }

  if (active.length === 1) {
    const [only] = active as [ActivityItem];
    const known = typeof only.progress === "number";
    const view: PillView = { tone: "busy", icon: "ring", text: only.title, progress: known ? only.progress : null, extra: 0, ariaLabel: `${only.title}${only.about ? `: ${only.about}` : ""}` };
    if (known) view.percent = percentOf(only.progress as number);
    return view;
  }
  if (active.length > 1) {
    // The ring shows the mean of what is known; a percent of unrelated jobs would mean little.
    return { tone: "busy", icon: "ring", text: `${active.length} running`, progress: meanProgress(active), extra: 0, ariaLabel: `${active.length} things running` };
  }

  if (!reachable) return { tone: "offline", icon: "dot", text: OFFLINE_TEXT, extra: 0, ariaLabel: OFFLINE_TEXT };
  return { tone: "idle", icon: "dot", text: IDLE_TEXT, extra: 0, ariaLabel: IDLE_TEXT };
}

export type NowRow =
  | { type: "recording"; recording: RecordingStatus }
  | { type: "job"; item: ActivityItem };

/** The recording first, then running jobs, then those waiting their turn; newest started first in each. */
export function nowRows(items: ActivityItem[], recording: RecordingStatus | null): NowRow[] {
  const waits = (item: ActivityItem) => (item.state === "waiting" ? 1 : 0);
  const jobs = items.filter(isActive).sort((a, b) => waits(a) - waits(b) || byStartedDesc(a, b)).map((item): NowRow => ({ type: "job", item }));
  return recording ? [{ type: "recording", recording }, ...jobs] : jobs;
}

/** Done and failed, newest ended first. */
export function earlierRows(items: ActivityItem[]): ActivityItem[] {
  return items.filter((item) => !isActive(item)).sort(byEndedDesc);
}
