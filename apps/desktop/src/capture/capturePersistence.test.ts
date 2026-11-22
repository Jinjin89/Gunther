import { beforeEach, describe, expect, it, vi } from "vitest";
import { persistAssetCapture, persistTextCapture } from "./capturePersistence";

const mocks = vi.hoisted(() => ({
  createNote: vi.fn(),
  fileNote: vi.fn(),
  captureWeb: vi.fn(),
  importSource: vi.fn(),
  captureAsset: vi.fn(),
  removeStoredCapture: vi.fn(),
}));

vi.mock("../api", () => ({
  knowledgeApi: {
    createNote: mocks.createNote,
    fileNote: mocks.fileNote,
    captureWeb: mocks.captureWeb,
    importSource: mocks.importSource,
    captureAsset: mocks.captureAsset,
  },
}));
vi.mock("../localCaptureQueue", () => ({ removeStoredCapture: mocks.removeStoredCapture }));

describe("shared Capture persistence", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.createNote.mockResolvedValue({ id: "note-1" });
    mocks.fileNote.mockResolvedValue(undefined);
    mocks.captureWeb.mockResolvedValue({});
    mocks.importSource.mockResolvedValue({});
    mocks.captureAsset.mockResolvedValue({});
  });

  it("preserves an editable note even when its requested filing destination is temporarily unavailable", async () => {
    mocks.fileNote.mockRejectedValueOnce(new Error("base unavailable"));

    const result = await persistTextCapture({
      title: "Observation",
      baseId: "biology",
      content: "Quality control comes first.",
      kind: "note",
      captureId: "capture-1",
      workspaceId: "wsp-primary",
    });

    expect(mocks.createNote).toHaveBeenCalledWith({
      title: "Observation",
      content: "Quality control comes first.",
      clientCaptureId: "capture-1",
    }, "wsp-primary");
    expect(mocks.removeStoredCapture).toHaveBeenCalledWith("capture-1");
    expect(result).toEqual({
      savedToService: true,
      message: "Editable note is safe in Inbox; its requested library was unavailable.",
    });
  });

  it("leaves the local retry item intact when the knowledge service cannot accept a source", async () => {
    mocks.importSource.mockRejectedValueOnce(new Error("offline"));

    const result = await persistTextCapture({
      title: "Lecture transcript",
      baseId: null,
      content: "Preserved text",
      kind: "recording",
      captureId: "capture-2",
      workspaceId: "wsp-primary",
    });

    expect(mocks.removeStoredCapture).not.toHaveBeenCalled();
    expect(result.savedToService).toBe(false);
    expect(result.message).toContain("local retry queue");
  });

  it("saves a recording with model analysis queued after durable acceptance", async () => {
    const result = await persistTextCapture({
      title: "Lecture transcript", baseId: null, content: "Preserved text",
      kind: "recording", captureId: "capture-recording", workspaceId: "wsp-primary",
    });
    expect(mocks.importSource).toHaveBeenCalledWith({
      title: "Lecture transcript", content: "Preserved text", kind: "recording",
    }, "wsp-primary", { deferAnalysis: true });
    expect(result.savedToService).toBe(true);
    expect(mocks.removeStoredCapture).toHaveBeenCalledWith("capture-recording");
  });

  it("retains a link on workspace rejection and retries the same snapshot request", async () => {
    const input = {
      title: " Report ", baseId: null, content: " Keep this context ", kind: "link" as const,
      captureId: "cap-report", url: "https://example.org/report", workspaceId: "wsp-primary",
    };
    mocks.captureWeb.mockRejectedValueOnce(new Error("Workspace identity changed"));

    expect((await persistTextCapture(input)).savedToService).toBe(false);
    expect(mocks.removeStoredCapture).not.toHaveBeenCalled();
    expect((await persistTextCapture(input)).savedToService).toBe(true);
    expect(mocks.captureWeb.mock.calls).toEqual(Array.from({ length: 2 }, () => [
      { url: input.url, title: "Report", notes: "Keep this context", clientCaptureId: input.captureId },
      input.workspaceId,
    ]));
    expect(mocks.removeStoredCapture).toHaveBeenCalledExactlyOnceWith(input.captureId);
  });

  it("keeps original asset capture and its destination message in the shared path", async () => {
    const file = new File(["paper"], "paper.pdf", { type: "application/pdf" });

    const result = await persistAssetCapture({
      file,
      title: "Paper",
      baseId: "biology",
      kind: "file",
      notes: "Read methods",
      workspaceId: "wsp-primary",
    });

    expect(mocks.captureAsset).toHaveBeenCalledWith(file, "Paper", "file", "biology", "Read methods", "wsp-primary");
    expect(result.message).toContain("Original file preserved");
  });
});
