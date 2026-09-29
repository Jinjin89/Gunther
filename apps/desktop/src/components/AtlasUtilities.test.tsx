import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi } from "../api";
import { CaptureSheet } from "./AtlasUtilities";

vi.mock("../api", () => ({
  knowledgeApi: {
    recordings: vi.fn().mockResolvedValue([]),
    health: vi.fn().mockResolvedValue({
      status: "ok",
      extractionMode: "local",
      webSearchMode: "not_configured",
      transcriptionMode: "sensevoice_local",
      transcriptionProvider: "sensevoice",
      transcriptionModel: "sensevoice-small",
      transcriptionDelay: "medium",
      transcriptionLanguages: ["en", "zh-cn"],
      summaryMode: "off",
    }),
    search: vi.fn().mockResolvedValue([]),
    webSearch: vi.fn().mockResolvedValue({ query: "", answer: "", sources: [], mode: "not_configured" }),
  },
}));

describe("CaptureSheet web snapshots", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  const renderSheet = (workspaceId: string | null = null) => {
    const onCaptured = vi.fn().mockResolvedValue(undefined);
    render(
      <CaptureSheet
        open
        bases={[]}
        workspaceId={workspaceId}
        onClose={vi.fn()}
        onCaptured={onCaptured}
        onAssetCaptured={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    return onCaptured;
  };

  it("separates the page URL from notes and sends a normalized snapshot request", async () => {
    const user = userEvent.setup();
    const onCaptured = renderSheet("wsp_primary");

    await user.click(screen.getByRole("tab", { name: /Web page/i }));
    await user.type(screen.getByRole("textbox", { name: "Web page URL" }), "example.org/article#section");
    await user.type(screen.getByRole("textbox", { name: "Title" }), "Primary article");
    await user.type(screen.getByRole("textbox", { name: "Source content" }), "Preserve the methods section.");
    await user.click(screen.getByRole("button", { name: /Save web page/i }));

    await waitFor(() => expect(onCaptured).toHaveBeenCalledOnce());
    expect(onCaptured).toHaveBeenCalledWith(
      "Primary article",
      null,
      "Preserve the methods section.",
      "link",
      expect.stringMatching(/^cap-\d+-[a-z0-9]{6}$/),
      "https://example.org/article",
      "wsp_primary",
    );
  });

  it("does not enable capture for embedded credentials or non-http URLs", async () => {
    const user = userEvent.setup();
    renderSheet();

    await user.click(screen.getByRole("tab", { name: /Web page/i }));
    const url = screen.getByRole("textbox", { name: "Web page URL" });
    const submit = screen.getByRole("button", { name: /Save web page/i });
    await user.type(url, "https://user:secret@example.org/private");
    expect(submit).toBeDisabled();
    await user.clear(url);
    await user.type(url, "file:///etc/passwd");
    expect(submit).toBeDisabled();
  });

  it("quarantines a damaged local queue without losing the new capture", async () => {
    window.localStorage.setItem("gunther:local-captures", "{damaged-json");
    const user = userEvent.setup();
    const onCaptured = renderSheet("wsp_primary");

    await user.type(screen.getByRole("textbox", { name: "Title" }), "Recovered thought");
    await user.type(screen.getByRole("textbox", { name: "Source content" }), "Do not lose this note.");
    await user.click(screen.getByRole("button", { name: /Save note/i }));

    await waitFor(() => expect(onCaptured).toHaveBeenCalledOnce());
    expect(window.localStorage.getItem("gunther:local-captures-quarantine")).toContain("{damaged-json");
    expect(JSON.parse(window.localStorage.getItem("gunther:local-captures") ?? "[]")).toEqual([
      expect.objectContaining({ title: "Recovered thought", content: "Do not lose this note.", kind: "note", workspaceId: "wsp_primary" }),
    ]);
  });
});

describe("CaptureSheet native window surface", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("renders as a non-modal movable surface and hides without unmounting its content", async () => {
    const user = userEvent.setup();
    const onHide = vi.fn();
    const onClose = vi.fn();
    const onRecorderSnapshot = vi.fn();
    const view = render(
      <CaptureSheet
        open
        surface="window"
        bases={[]}
        workspaceId="wsp_primary"
        initialKind="note"
        onHide={onHide}
        onClose={onClose}
        onRecorderSnapshot={onRecorderSnapshot}
        onCaptured={vi.fn()}
        onAssetCaptured={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(view.container.querySelector(".capture-window-surface")).toBeInTheDocument();
    expect(view.container.querySelector(".atlas-overlay")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Source content" })).toBeInTheDocument();
    expect(onRecorderSnapshot).toHaveBeenCalledWith(expect.objectContaining({ phase: "idle" }), "", false);

    await user.click(screen.getByRole("button", { name: "Hide to menu bar" }));

    expect(onHide).toHaveBeenCalledOnce();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox", { name: "Source content" })).toBeInTheDocument();
  });

  it("reports unsaved text immediately and clears ownership when the draft is cleared", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const onRecorderSnapshot = vi.fn();
    render(
      <CaptureSheet
        open
        surface="window"
        bases={[]}
        workspaceId="wsp_primary"
        initialKind="note"
        onClose={vi.fn()}
        onRecorderSnapshot={onRecorderSnapshot}
        onCaptured={vi.fn()}
        onAssetCaptured={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    await user.type(screen.getByRole("textbox", { name: "Title" }), "Unfiled thought");
    await user.type(screen.getByRole("textbox", { name: "Source content" }), "Still needs review.");
    await waitFor(() => expect(onRecorderSnapshot).toHaveBeenLastCalledWith(
      expect.objectContaining({ phase: "idle" }),
      "Unfiled thought",
      true,
    ));

    await user.click(screen.getByRole("button", { name: "Discard this capture" }));
    await waitFor(() => expect(onRecorderSnapshot).toHaveBeenLastCalledWith(
      expect.objectContaining({ phase: "idle" }),
      "",
      false,
    ));
    expect(confirm).toHaveBeenCalledOnce();
    confirm.mockRestore();
  });

  it("does not discard an in-progress Capture when Back is cancelled", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(
      <CaptureSheet
        open
        surface="window"
        bases={[]}
        workspaceId="wsp_primary"
        initialKind="link"
        onClose={vi.fn()}
        onCaptured={vi.fn()}
        onAssetCaptured={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    const url = screen.getByRole("textbox", { name: "Web page URL" });
    await user.type(url, "example.org/keep-me");
    await user.click(screen.getByRole("button", { name: "Discard this capture" }));

    expect(confirm).toHaveBeenCalledOnce();
    expect(screen.getByRole("textbox", { name: "Web page URL" })).toHaveValue("example.org/keep-me");
    confirm.mockRestore();
  });
});
