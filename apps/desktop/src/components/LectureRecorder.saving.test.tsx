import type { RecordingSession } from "@gunther/contracts";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi } from "../api";
import type { RecordingSpoolChunk } from "../services/recordingSpool";
import * as pcm from "../services/pcm";
import { CaptureSheet } from "./AtlasUtilities";
import { loadRecordingDrafts } from "./LectureRecorder";

const spool = vi.hoisted(() => new Map<number, RecordingSpoolChunk>());

vi.mock("../services/recordingSpool", () => ({
  assertRecordingSpoolAvailable: vi.fn().mockResolvedValue(undefined),
  bindRecordingSpoolWorkspace: vi.fn().mockResolvedValue(undefined),
  spoolRecordingChunk: vi.fn(async (sessionId: string, sequence: number, blob: Blob, workspaceId: string) => {
    spool.set(sequence, {
      key: `${sessionId}:${sequence}`, sessionId, sequence, blob, workspaceId,
      mimeType: blob.type, sizeBytes: blob.size, checksum: "a".repeat(64),
      source: "live", createdAt: new Date().toISOString(),
    });
  }),
  listRecordingSpool: vi.fn(async () => [...spool.values()]),
  deleteRecordingSpoolChunk: vi.fn(async (_id: string, sequence: number) => { spool.delete(sequence); }),
  deleteAcknowledgedRecordingSpool: vi.fn(async (_id: string, next: number) => {
    for (const sequence of spool.keys()) if (sequence < next) spool.delete(sequence);
  }),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

class RecordingSocket {
  static OPEN = 1;
  static latest: RecordingSocket;
  readyState = 1;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn(() => { this.readyState = 3; this.onclose?.(); });
  constructor() { RecordingSocket.latest = this; }
  receive(event: Record<string, unknown>) { this.onmessage?.({ data: JSON.stringify(event) }); }
}

class AudioRecorder {
  static isTypeSupported = () => true;
  state = "inactive";
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  start() { this.state = "recording"; }
  pause() { this.state = "paused"; }
  resume() { this.state = "recording"; }
  requestData() { /* No periodic chunk is due in these short recordings. */ }
  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["last audio bytes"], { type: "audio/webm" }) });
    this.onstop?.();
  }
}

class RecordingAudioContext {
  static latest: RecordingAudioContext;
  processor = { connect: vi.fn(), disconnect: vi.fn(), onaudioprocess: null as ((event: { inputBuffer: { getChannelData: () => Float32Array } }) => void) | null };
  constructor() { RecordingAudioContext.latest = this; }
  sampleRate = 48_000;
  destination = {};
  createMediaStreamSource() { return { connect: vi.fn() }; }
  createAnalyser() {
    return { fftSize: 512, frequencyBinCount: 256, getByteTimeDomainData: (samples: Uint8Array) => samples.fill(128) };
  }
  createScriptProcessor() { return this.processor; }
  capture(seconds: number) {
    this.processor.onaudioprocess?.({ inputBuffer: { getChannelData: () => new Float32Array(Math.round(this.sampleRate * seconds)) } });
  }
  close = vi.fn().mockResolvedValue(undefined);
  suspend = vi.fn().mockResolvedValue(undefined);
  resume = vi.fn().mockResolvedValue(undefined);
}

const originalMediaDevices = Object.getOwnPropertyDescriptor(navigator, "mediaDevices");
let server: RecordingSession;

function fixture(): RecordingSession {
  const now = new Date().toISOString();
  return {
    id: "rec_0123456789abcdef01234567", title: "Recording", fileName: "recording.webm",
    contentType: "audio/webm", status: "capturing", sizeBytes: 0, nextExpectedSequence: 0,
    transcript: "", transcriptSegments: null, durationSeconds: 0, moments: [],
    recordingContext: "lecture", knowledgeBaseId: null, checkpointRevision: 0, checkpointedAt: null,
    createdAt: now, updatedAt: now, completedAt: null, storedAt: now,
    recovery: { canResume: true, audioAvailable: false, nextExpectedSequence: 0, checkpointRevision: 0, checkpointedAt: null },
  };
}

async function startCapture(checks = true, grouping = true) {
  const onCaptured = vi.fn().mockResolvedValue(undefined);
  const view = render(<CaptureSheet
    open surface="window" bases={[]} workspaceId="wsp_primary" initialKind="recording"
    onClose={vi.fn()} onCaptured={onCaptured} onAssetCaptured={vi.fn()}
  />);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Start recording" })); });
  const socket = RecordingSocket.latest;
  await act(async () => socket.receive({ type: "service.ready", check_again: checks, group_paragraphs: grouping }));
  return { ...view, socket, onCaptured };
}

function piece(socket: RecordingSocket, id: string, start: number, end: number, text: string) {
  act(() => socket.receive({ type: "transcript.piece", id, start_ms: start, end_ms: end, text, final: true }));
}

describe("saving a recording independently of transcript processing", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.localStorage.clear();
    spool.clear();
    server = fixture();
    vi.stubGlobal("WebSocket", RecordingSocket);
    vi.stubGlobal("MediaRecorder", AudioRecorder);
    vi.stubGlobal("AudioContext", RecordingAudioContext);
    vi.stubGlobal("requestAnimationFrame", vi.fn());
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] }) },
    });
    vi.spyOn(knowledgeApi, "health").mockResolvedValue({
      status: "ok", extractionMode: "local", webSearchMode: "not_configured",
      transcriptionMode: "not_configured", transcriptionProvider: "none", transcriptionModel: "", summaryMode: "off",
    });
    vi.spyOn(knowledgeApi, "recordings").mockResolvedValue([]);
    vi.spyOn(knowledgeApi, "startRecordingSession").mockImplementation(async () => ({ ...server }));
    vi.spyOn(knowledgeApi, "recordingMetadata").mockImplementation(async () => ({ ...server }));
    vi.spyOn(knowledgeApi, "checkpointRecordingSession").mockImplementation(async (_id, payload) => {
      server = { ...server, ...payload, transcriptSegments: payload.transcriptSegments ?? null, checkpointRevision: server.checkpointRevision + 1 };
      return { ...server };
    });
    vi.spyOn(knowledgeApi, "appendRecordingChunk").mockImplementation(async (_id, blob, sequence) => {
      server = { ...server, sizeBytes: server.sizeBytes + blob.size, nextExpectedSequence: sequence + 1 };
      return { ...server };
    });
    vi.spyOn(knowledgeApi, "completeRecordingSession").mockImplementation(async () => {
      server = { ...server, status: "completed", recovery: { ...server.recovery, canResume: false, audioAvailable: true } };
      return { ...server };
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    if (originalMediaDevices) Object.defineProperty(navigator, "mediaDevices", originalMediaDevices);
    else Reflect.deleteProperty(navigator, "mediaDevices");
  });

  it("waits for the last audio chunk, then saves without waiting for the live socket to flush", async () => {
    const completing = deferred<void>();
    vi.mocked(knowledgeApi.completeRecordingSession).mockImplementation(async () => {
      await completing.promise;
      return { ...server, status: "completed", recovery: { ...server.recovery, canResume: false, audioAvailable: true } };
    });
    const { socket, onCaptured } = await startCapture();
    piece(socket, "first", 0, 5_000, "Already heard text.");

    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Finish" })); });
    expect(screen.getByRole("button", { name: /Save recording/ })).toBeDisabled();
    expect(screen.getByText("Saving the last audio chunk…")).toBeVisible();
    expect(knowledgeApi.appendRecordingChunk).toHaveBeenCalledOnce();
    expect(spool.size).toBe(0);
    // Save is withheld until the audio service, rather than a model, acknowledges completion.
    expect(knowledgeApi.completeRecordingSession).toHaveBeenCalledOnce();

    await act(async () => { completing.resolve(); });
    expect(screen.getByRole("button", { name: /Save recording/ })).toBeEnabled();
    expect(screen.getByText(/Audio saved\. You can save now/)).toBeVisible();
    await act(async () => {
      socket.receive({ type: "transcript.piece", id: "tail", start_ms: 5_000, end_ms: 6_000, text: "Last sentence.", final: true });
      fireEvent.click(screen.getByRole("button", { name: /Save recording/ }));
    });
    expect(onCaptured).toHaveBeenCalledOnce();
    expect(onCaptured.mock.calls[0]?.[2]).toContain("Already heard text.");
    expect(onCaptured.mock.calls[0]?.[2]).toContain("Last sentence.");
    expect(socket.close).toHaveBeenCalled();
    expect(loadRecordingDrafts()).toEqual([]);

    // A queued socket callback cannot recreate the draft after Capture resets.
    act(() => socket.receive({ type: "transcript.piece", id: "late", start_ms: 5_000, end_ms: 6_000, text: "Late text.", final: true }));
    expect(loadRecordingDrafts()).toEqual([]);
  });

  it("keeps the recovery draft and withholds Save when audio completion fails", async () => {
    vi.mocked(knowledgeApi.completeRecordingSession).mockRejectedValue(new Error("storage unavailable"));
    const { onCaptured } = await startCapture();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Finish" })); });
    expect(screen.getByRole("button", { name: /Save recording/ })).toBeDisabled();
    expect(screen.getByText(/could not finish yet: storage unavailable/)).toBeVisible();
    expect(loadRecordingDrafts()).toHaveLength(1);
    expect(onCaptured).not.toHaveBeenCalled();
  });

  it("freezes the latest live text and ignores late checks and grouping answers after saving", async () => {
    const checking = deferred<Awaited<ReturnType<typeof knowledgeApi.relistenRecording>>>();
    const grouping = deferred<Awaited<ReturnType<typeof knowledgeApi.groupParagraphs>>>();
    vi.spyOn(knowledgeApi, "relistenRecording").mockReturnValue(checking.promise);
    vi.spyOn(knowledgeApi, "groupParagraphs").mockReturnValue(grouping.promise);
    const { socket, onCaptured } = await startCapture(false);
    piece(socket, "confirmed", 0, 61_000, "Confirmed text.");
    expect(knowledgeApi.groupParagraphs).toHaveBeenCalledOnce();
    await act(async () => socket.receive({ type: "service.ready", check_again: true, group_paragraphs: true }));
    piece(socket, "live", 61_000, 122_000, "Latest live text.");
    expect(knowledgeApi.relistenRecording).toHaveBeenCalledOnce();

    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Finish" })); });
    act(() => socket.receive({ type: "transcript.flushed", until_ms: 122_000 }));
    expect(screen.getByRole("button", { name: /Save recording/ })).toBeEnabled();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Save recording/ })); });
    expect(onCaptured).toHaveBeenCalledOnce();
    const saved = onCaptured.mock.calls[0]?.[2] as string;
    expect(saved).toContain("Confirmed text.");
    expect(saved).toContain("Latest live text.");
    expect(vi.mocked(knowledgeApi.relistenRecording).mock.calls[0]?.[2]?.aborted).toBe(true);
    expect(vi.mocked(knowledgeApi.groupParagraphs).mock.calls[0]?.[2]?.aborted).toBe(true);

    await act(async () => {
      checking.resolve({ text: "Late replacement.", sentences: [], model: "test", provider: "test", missing: [] });
      grouping.resolve({ starts: [0, 1], model: "test" });
    });
    expect(loadRecordingDrafts()).toEqual([]);
    expect(server.transcript).toContain("Latest live text.");
    expect(server.transcript).not.toContain("Late replacement.");
    expect(knowledgeApi.groupParagraphs).toHaveBeenCalledOnce();
  });

  it("preserves typed transcripts when saving before the live transcript finishes", async () => {
    const { onCaptured } = await startCapture();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Finish" })); });
    fireEvent.change(screen.getByRole("textbox", { name: "" }), { target: { value: "My pasted transcript." } });
    expect(screen.getByRole("button", { name: /Save recording/ })).toBeEnabled();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Save recording/ })); });
    expect(onCaptured).toHaveBeenCalledOnce();
    expect(onCaptured.mock.calls[0]?.[2]).toContain("My pasted transcript.");
  });

  it("uses audio time after delayed timer ticks and snapshots the last samples on Pause, Mark and Finish", async () => {
    vi.spyOn(pcm, "encodePcm16").mockReturnValue("audio");
    const { onCaptured } = await startCapture(false, false);
    const audio = RecordingAudioContext.latest;
    // Six seconds of real audio passed, while the UI timer got only one callback.
    act(() => { audio.capture(6.4); vi.advanceTimersByTime(1_000); });
    expect(document.querySelector(".lecture-clock strong")).toHaveTextContent("00:06");
    act(() => audio.capture(9.25));
    fireEvent.click(screen.getByRole("button", { name: "Mark moment" }));
    expect(document.querySelector(".recording-moments strong")).toHaveTextContent("00:15");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Pause" })); });
    expect(server.durationSeconds).toBe(15);
    act(() => { vi.advanceTimersByTime(10_000); audio.capture(20); });
    expect(document.querySelector(".lecture-clock strong")).toHaveTextContent("00:15");
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    act(() => audio.capture(2.4));
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Finish" })); });
    expect(server.durationSeconds).toBe(18);
    expect(server.moments[0]?.seconds).toBe(15);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Save recording/ })); });
    expect(onCaptured.mock.calls[0]?.[2]).toContain("Duration: 00:18");
  });
});
