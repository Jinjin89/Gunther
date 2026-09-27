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
      message: "Editable note is safe in Inbox; its requested knowledge base was unavailable.",
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
