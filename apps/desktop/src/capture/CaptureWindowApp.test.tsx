import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CaptureWindowApp from "./CaptureWindowApp";
import type { RecorderSnapshot } from "../components/LectureRecorder";
import type { CaptureControl, CaptureLaunchRequest } from "./captureTypes";

const mocks = vi.hoisted(() => ({
  workspaceBootstrap: vi.fn(),
  knowledgeBases: vi.fn(),
  listenRequest: vi.fn(),
  listenControl: vi.fn(),
  takeRequest: vi.fn(),
  updateStatus: vi.fn(),
  hide: vi.fn(),
  showMain: vi.fn(),
  emitSaved: vi.fn(),
  emitSearch: vi.fn(),
  persistText: vi.fn(),
  persistAsset: vi.fn(),
  order: [] as string[],
  requestHandler: null as ((request: CaptureLaunchRequest) => void) | null,
  controlHandler: null as ((control: CaptureControl) => void) | null,
  snapshotHandler: null as ((snapshot: RecorderSnapshot, title: string, hasUnreviewedWork: boolean) => void) | null,
  resolveTake: null as ((request: CaptureLaunchRequest | null) => void) | null,
  sheetMounts: 0,
}));

vi.mock("../api", () => ({
  knowledgeApi: {
    workspaceBootstrap: mocks.workspaceBootstrap,
    knowledgeBases: mocks.knowledgeBases,
  },
}));
vi.mock("../atlasMetadata", () => ({ metadataToBase: (metadata: unknown) => metadata }));
vi.mock("./capturePersistence", () => ({
  persistTextCapture: mocks.persistText,
  persistAssetCapture: mocks.persistAsset,
}));
vi.mock("./captureBridge", () => ({
  listenForCaptureRequest: mocks.listenRequest,
  listenForCaptureControl: mocks.listenControl,
  takeCaptureLaunchRequest: mocks.takeRequest,
  updateCaptureStatus: mocks.updateStatus,
  hideCaptureWindow: mocks.hide,
  showMainWindow: mocks.showMain,
  emitCaptureSaved: mocks.emitSaved,
  emitOpenSearch: mocks.emitSearch,
}));
vi.mock("../components/AtlasUtilities", async () => {
  const React = await import("react");
  return {
    CaptureSheet: (props: {
      initialKind?: string | null;
      initialRecordingContext?: string;
      baseId?: string | null;
      surface?: string;
      onRecorderSnapshot?: (snapshot: RecorderSnapshot, title: string, hasUnreviewedWork: boolean) => void;
      onClose: (force?: boolean) => void;
      onSearch?: () => void;
    }) => {
      React.useEffect(() => {
        mocks.sheetMounts += 1;
        mocks.snapshotHandler = props.onRecorderSnapshot ?? null;
        props.onRecorderSnapshot?.({
          phase: "idle",
          seconds: 0,
          persistence: "idle",
          transcriptWords: 0,
          transcriptionLabel: "SenseVoice · local",
          markedMoments: 0,
        }, "Capture test", false);
      }, []);
      return React.createElement("main", { "data-testid": "capture-sheet", "data-surface": props.surface },
        React.createElement("span", null, `${props.initialKind ?? "all"}|${props.initialRecordingContext}|${props.baseId ?? "inbox"}`),
        React.createElement("button", { type: "button", onClick: () => props.onClose(true) }, "Keep draft"),
        React.createElement("button", { type: "button", onClick: () => props.onSearch?.() }, "Web search"),
      );
    },
  };
});

describe("CaptureWindowApp", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.order.length = 0;
    mocks.requestHandler = null;
    mocks.controlHandler = null;
    mocks.snapshotHandler = null;
    mocks.sheetMounts = 0;
    mocks.resolveTake = null;
    mocks.workspaceBootstrap.mockResolvedValue({ workspaceId: "wsp-primary" });
    mocks.knowledgeBases.mockResolvedValue([]);
    mocks.updateStatus.mockResolvedValue(undefined);
    mocks.hide.mockResolvedValue(undefined);
    mocks.showMain.mockResolvedValue(undefined);
    mocks.emitSaved.mockResolvedValue(undefined);
    mocks.emitSearch.mockResolvedValue(undefined);
    mocks.listenRequest.mockImplementation(async (handler: (request: CaptureLaunchRequest) => void) => {
      mocks.order.push("listen-request");
      mocks.requestHandler = handler;
      return vi.fn();
    });
    mocks.listenControl.mockImplementation(async (handler: (control: CaptureControl) => void) => {
      mocks.order.push("listen-control");
      mocks.controlHandler = handler;
      return vi.fn();
    });
    mocks.takeRequest.mockImplementation(() => {
      mocks.order.push("take-request");
      return new Promise<CaptureLaunchRequest | null>((resolve) => { mocks.resolveTake = resolve; });
    });
  });

  it("registers both event listeners before taking the pending launch request", async () => {
    render(<CaptureWindowApp />);

    await waitFor(() => expect(mocks.order).toEqual([
      "listen-request",
      "listen-control",
      "take-request",
    ]));

    act(() => {
      mocks.requestHandler?.({
        kind: "note",
        recordingContext: "memo",
        targetBaseId: "base-1",
        source: "titlebar",
      });
    });

    expect(await screen.findByText("note|memo|base-1")).toBeVisible();
    await act(async () => { mocks.resolveTake?.(null); });
    expect(screen.getByText("note|memo|base-1")).toBeVisible();
  });

  it("clears a pending fallback without applying a live launch twice", async () => {
    render(<CaptureWindowApp />);
    await waitFor(() => expect(mocks.requestHandler).toBeTypeOf("function"));
    const request = {
      kind: "recording" as const,
      recordingContext: "meeting" as const,
      targetBaseId: "base-2",
      source: "knowledge-base",
    };

    act(() => { mocks.requestHandler?.(request); });
    expect(await screen.findByText("recording|meeting|base-2")).toBeVisible();
    const mountsAfterLiveEvent = mocks.sheetMounts;
    await act(async () => { mocks.resolveTake?.(request); });

    expect(mocks.sheetMounts).toBe(mountsAfterLiveEvent);

    act(() => {
      mocks.requestHandler?.({ kind: "note", recordingContext: "memo", source: "tray" });
    });
    expect(await screen.findByText("note|memo|inbox")).toBeVisible();
    expect(mocks.sheetMounts).toBe(mountsAfterLiveEvent + 1);
  });

  it("releases stopped draft ownership and routes Web search through the main window", async () => {
    mocks.takeRequest.mockImplementationOnce(async () => {
      mocks.order.push("take-request");
      return { kind: "recording", recordingContext: "lecture" } satisfies CaptureLaunchRequest;
    });
    const user = userEvent.setup();
    render(<CaptureWindowApp />);

    expect(await screen.findByText("recording|lecture|inbox")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Keep draft" }));

    await waitFor(() => expect(mocks.hide).toHaveBeenCalledOnce());
    expect(mocks.updateStatus).toHaveBeenCalledWith(expect.objectContaining({ phase: "idle" }));

    await user.click(screen.getByRole("button", { name: "Web search" }));
    await waitFor(() => expect(mocks.showMain).toHaveBeenCalledOnce());
    expect(mocks.emitSearch).toHaveBeenCalledOnce();
    expect(mocks.hide).toHaveBeenCalledTimes(2);
  });

  it("guards a recorder synchronously when a second launch arrives in the same frame", async () => {
    const { container } = render(<CaptureWindowApp />);
    expect(await screen.findByText("all|lecture|inbox")).toBeVisible();
    await waitFor(() => {
      expect(mocks.requestHandler).toBeTypeOf("function");
      expect(mocks.snapshotHandler).toBeTypeOf("function");
    });
    await act(async () => { mocks.resolveTake?.(null); });

    act(() => {
      mocks.snapshotHandler?.({
        phase: "requesting",
        seconds: 0,
        persistence: "saving",
        transcriptWords: 0,
        transcriptionLabel: "Opening microphone",
        markedMoments: 0,
      }, "Lecture", false);
      mocks.requestHandler?.({ kind: "note", source: "titlebar" });
    });

    expect(container).toHaveTextContent("all|lecture|inbox");
    expect(container).not.toHaveTextContent("note|lecture|inbox");
  });

  it("guards unsaved non-recording work synchronously and reports it to the shell", async () => {
    const { container } = render(<CaptureWindowApp />);
    expect(await screen.findByText("all|lecture|inbox")).toBeVisible();
    await waitFor(() => {
      expect(mocks.requestHandler).toBeTypeOf("function");
      expect(mocks.snapshotHandler).toBeTypeOf("function");
    });
    await act(async () => { mocks.resolveTake?.(null); });

    act(() => {
      mocks.snapshotHandler?.({
        phase: "idle",
        seconds: 0,
        persistence: "idle",
        transcriptWords: 0,
        transcriptionLabel: "Live transcript",
        markedMoments: 0,
      }, "Draft note", true);
      mocks.requestHandler?.({ kind: "recording", source: "tray" });
    });

    expect(container).toHaveTextContent("all|lecture|inbox");
    expect(container).not.toHaveTextContent("recording|lecture|inbox");
    await waitFor(() => expect(mocks.updateStatus).toHaveBeenCalledWith(expect.objectContaining({
      title: "Draft note",
      hasUnreviewedWork: true,
    })));
  });

  it("keeps Capture visible when the Web Search handoff cannot show the main window", async () => {
    mocks.showMain.mockRejectedValueOnce(new Error("main unavailable"));
    const user = userEvent.setup();
    render(<CaptureWindowApp />);
    expect(await screen.findByText("all|lecture|inbox")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Web search" }));

    await waitFor(() => expect(mocks.showMain).toHaveBeenCalledOnce());
    expect(mocks.emitSearch).not.toHaveBeenCalled();
    expect(mocks.hide).not.toHaveBeenCalled();
  });
});
