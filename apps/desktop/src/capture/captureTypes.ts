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

export interface CaptureRuntimeStatus extends RecorderSnapshot {
  title: string;
  hasUnreviewedWork: boolean;
}

export interface CaptureSavedEvent {
  message: string;
}
