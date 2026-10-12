import { useEffect, useSyncExternalStore } from "react";
import {
  getCaptureStatus,
  isTauriRuntime,
  listenForCaptureStatus,
  sendCaptureControl,
} from "../capture/captureBridge";
import { CAPTURE_CONTROL_DOM_EVENT, type CaptureControl } from "../capture/captureTypes";

/** The phases of the recorder that show in the top bar. */
export type RecordingPhase = "requesting" | "importing" | "recording" | "paused" | "stopped";

export interface RecordingStatus {
  phase: RecordingPhase;
  seconds: number;
  title: string;
  transcriptWords: number;
}

/** Whatever carries a recorder's state: the shell's status, or the recorder's own snapshot. */
interface RecorderLike {
  phase: string;
  seconds?: number | null | undefined;
  elapsedSeconds?: number | null | undefined;
  title?: string | null | undefined;
  transcriptWords?: number | null | undefined;
}

const PHASES: readonly string[] = ["requesting", "importing", "recording", "paused", "stopped"];

/** Null for idle, and for any phase the top bar does not show. */
export function toRecordingStatus(source: RecorderLike): RecordingStatus | null {
  const phase = source.phase.trim().toLowerCase();
  if (!PHASES.includes(phase)) return null;
  return {
    phase: phase as RecordingPhase,
    seconds: Math.max(0, Math.floor(source.elapsedSeconds ?? source.seconds ?? 0)),
    title: (source.title ?? "").trim(),
    transcriptWords: Math.max(0, source.transcriptWords ?? 0),
  };
}

// One shared copy: the browser's Capture sheet publishes it, the Tauri shell's events fill it.
let current: RecordingStatus | null = null;
const listeners = new Set<() => void>();

const same = (a: RecordingStatus | null, b: RecordingStatus | null) =>
  a === b || (!!a && !!b && a.phase === b.phase && a.seconds === b.seconds && a.title === b.title && a.transcriptWords === b.transcriptWords);

export function publishRecordingStatus(source: RecorderLike | null) {
  const next = source ? toRecordingStatus(source) : null;
  if (same(current, next)) return;
  current = next;
  listeners.forEach((listener) => listener());
}

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};

// Native: one listener for every consumer, started with the first and ended with the last.
let consumers = 0;
let stopListening: (() => void) | null = null;

function startNative(): () => void {
  consumers += 1;
  if (consumers === 1) {
    let alive = true;
    let unlisten: (() => void) | null = null;
    // Events can arrive before the first read comes back; the newer one wins.
    let heard = false;
    void listenForCaptureStatus((status) => { heard = true; publishRecordingStatus(status); })
      .then((off) => { if (alive) unlisten = off; else off(); })
      .catch(() => undefined);
    void getCaptureStatus().then((status) => { if (alive && !heard) publishRecordingStatus(status); }).catch(() => undefined);
    stopListening = () => { alive = false; unlisten?.(); };
  }
  return () => {
    consumers -= 1;
    if (consumers === 0) {
      stopListening?.();
      stopListening = null;
    }
  };
}

/** The recording in progress (or waiting to be saved), or null. */
export function useRecordingStatus(): RecordingStatus | null {
  useEffect(() => (isTauriRuntime() ? startNative() : undefined), []);
  return useSyncExternalStore(subscribe, () => current, () => null);
}

/** Pause, resume, finish or show the recording, wherever it runs. */
export async function sendRecordingControl(control: Exclude<CaptureControl, "quit-blocked">): Promise<void> {
  if (isTauriRuntime()) {
    await sendCaptureControl(control);
    return;
  }
  window.dispatchEvent(new CustomEvent<CaptureControl>(CAPTURE_CONTROL_DOM_EVENT, { detail: control }));
}
