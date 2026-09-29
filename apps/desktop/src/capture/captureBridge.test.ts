import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  CAPTURE_CONTROL_EVENT,
  CAPTURE_REQUEST_EVENT,
  CAPTURE_SAVED_EVENT,
  OPEN_SEARCH_EVENT,
  emitCaptureSaved,
  emitOpenSearch,
  hideCaptureWindow,
  listenForCaptureControl,
  listenForCaptureRequest,
  listenForCaptureSaved,
  listenForOpenSearch,
  openCaptureWindow,
  showMainWindow,
  takeCaptureLaunchRequest,
  updateCaptureStatus,
} from "./captureBridge";

const bridge = vi.hoisted(() => ({
  invoke: vi.fn(),
  emit: vi.fn().mockResolvedValue(undefined),
  listen: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => ({ invoke: bridge.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ emit: bridge.emit, listen: bridge.listen }));

describe("native Capture bridge", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    bridge.invoke.mockResolvedValue(undefined);
    bridge.listen.mockResolvedValue(vi.fn());
  });

  it("keeps launch requests and complete recorder status aligned with the Rust commands", async () => {
    const request = {
      kind: "recording" as const,
      recordingContext: "meeting" as const,
      targetBaseId: "base-1",
      source: "knowledge-base",
    };
    const status = {
      title: "Weekly review",
      phase: "recording" as const,
      seconds: 42,
      persistence: "saving" as const,
      transcriptWords: 118,
      transcriptionLabel: "SenseVoice · local",
      markedMoments: 2,
      hasUnreviewedWork: false,
    };

    await openCaptureWindow(request);
    await updateCaptureStatus(status);
    await hideCaptureWindow();
    await showMainWindow();

    expect(bridge.invoke).toHaveBeenNthCalledWith(1, "open_capture_window", { request });
    expect(bridge.invoke).toHaveBeenNthCalledWith(2, "update_capture_status", { status });
    expect(bridge.invoke).toHaveBeenNthCalledWith(3, "hide_capture_window");
    expect(bridge.invoke).toHaveBeenNthCalledWith(4, "show_main_window");
  });

  it("takes the pending launch request and forwards native event payloads", async () => {
    bridge.invoke.mockResolvedValueOnce({ kind: "note", source: "tray" });
    const requestHandler = vi.fn();
    const controlHandler = vi.fn();
    const savedHandler = vi.fn();
    const searchHandler = vi.fn();

    expect(await takeCaptureLaunchRequest()).toEqual({ kind: "note", source: "tray" });
    await listenForCaptureRequest(requestHandler);
    await listenForCaptureControl(controlHandler);
    await listenForCaptureSaved(savedHandler);
    await listenForOpenSearch(searchHandler);

    const handlers = new Map(bridge.listen.mock.calls.map(([name, handler]) => [name, handler]));
    handlers.get(CAPTURE_REQUEST_EVENT)?.({ payload: { kind: "recording" } });
    handlers.get(CAPTURE_CONTROL_EVENT)?.({ payload: "pause" });
    handlers.get(CAPTURE_SAVED_EVENT)?.({ payload: { message: "Saved" } });
    handlers.get(OPEN_SEARCH_EVENT)?.({ payload: null });

    expect(requestHandler).toHaveBeenCalledWith({ kind: "recording" });
    expect(controlHandler).toHaveBeenCalledWith("pause");
    expect(savedHandler).toHaveBeenCalledWith({ message: "Saved" });
    expect(searchHandler).toHaveBeenCalledOnce();
  });

  it("emits saved and open-search events without coupling them to main-window routing", async () => {
    await emitCaptureSaved("Preserved in Inbox");
    await emitOpenSearch();

    expect(bridge.emit).toHaveBeenNthCalledWith(1, CAPTURE_SAVED_EVENT, { message: "Preserved in Inbox", captureContinues: false });
    expect(bridge.emit).toHaveBeenNthCalledWith(2, OPEN_SEARCH_EVENT);
  });
});
