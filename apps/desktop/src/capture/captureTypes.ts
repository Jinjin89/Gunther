import type { RecorderSnapshot } from "../components/LectureRecorder";

export type CaptureKind = "note" | "link" | "file" | "image" | "table" | "recording";
export type RecordingContext = "lecture" | "meeting" | "memo";

export interface CaptureLaunchRequest {
  kind?: CaptureKind | null;
  recordingContext?: RecordingContext;
  targetBaseId?: string | null;
  source?: string;
}

export type CaptureControl = "pause" | "resume" | "mark" | "finish" | "show" | "quit-blocked";
export const CAPTURE_CONTROL_DOM_EVENT = "gunther:capture-control";
/**
 * A launch request for a Capture that is already busy (a recording, or unsaved
 * work). The open sheet switches to the requested type in place, so a
 * recording keeps running while a note, file or link is captured beside it.
 */
export const CAPTURE_SWITCH_DOM_EVENT = "gunther:capture-switch";

export interface CaptureRuntimeStatus extends RecorderSnapshot {
  title: string;
  hasUnreviewedWork: boolean;
}

export interface CaptureSavedEvent {
  message: string;
  /** Something else (a recording, or another unsaved capture) is still open. */
  captureContinues?: boolean;
}

/** The recorder holds the microphone, an import, or a recording to review. */
export function recorderOwnsCapture(status: Pick<RecorderSnapshot, "phase">): boolean {
  return status.phase === "requesting"
    || status.phase === "importing"
    || status.phase === "recording"
    || status.phase === "paused"
    || status.phase === "stopped";
}
