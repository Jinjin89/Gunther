import type { ActivityItem } from "@gunther/contracts";
import type { RecordingStatus } from "./recordingStatus";

export const activityItem = (overrides: Partial<ActivityItem> = {}): ActivityItem => ({
  id: "transcribe:rec_1",
  kind: "transcribe",
  title: "Transcribing again",
  about: "Week 3 lecture",
  state: "running",
  progress: null,
  detail: "",
  error: null,
  startedAt: "2026-10-12T10:00:00Z",
  endedAt: null,
  open: null,
  canStop: true,
  ...overrides,
});

export const recordingStatus = (overrides: Partial<RecordingStatus> = {}): RecordingStatus => ({
  phase: "recording",
  seconds: 754,
  title: "Cell biology lecture",
  transcriptWords: 1240,
  ...overrides,
});
