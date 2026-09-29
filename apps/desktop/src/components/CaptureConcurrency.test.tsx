import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi } from "../api";
import { CAPTURE_SWITCH_DOM_EVENT } from "../capture/captureTypes";
import { CaptureSheet } from "./AtlasUtilities";

const recorder = vi.hoisted(() => ({ mounts: 0, unmounts: 0, title: "" }));

vi.mock("../api", () => ({
  knowledgeApi: { recordings: vi.fn() },
}));

// A stand-in recorder: Start goes live, Finish leaves a transcript to review.
vi.mock("./LectureRecorder", async () => {
  const React = await import("react");
  const idle = { phase: "idle", seconds: 0, persistence: "idle", transcriptWords: 0, transcriptionLabel: "Local audio", markedMoments: 0 };
  return {
    loadRecordingDrafts: () => [],
    recordingDraftsForWorkspace: () => [],
    removeRecordingDraft: vi.fn(),
    LectureRecorder: React.forwardRef(function FakeRecorder(props: {
      title: string;
      onKnowledgeContent: (content: string) => void;
      onStatusChange?: (snapshot: typeof idle) => void;
      onActiveChange?: (active: boolean) => void;
    }, ref) {
      const [phase, setPhase] = React.useState("idle");
      recorder.title = props.title;
      React.useEffect(() => {
        recorder.mounts += 1;
        return () => { recorder.unmounts += 1; };
      }, []);
      const stop = () => {
        setPhase("stopped");
        props.onStatusChange?.({ ...idle, phase: "stopped", seconds: 65, persistence: "saved" });
        props.onKnowledgeContent(`# ${props.title}\n\n## Transcript\n\nCells divide.`);
      };
      React.useImperativeHandle(ref, () => ({ start: vi.fn(), pause: vi.fn(), resume: vi.fn(), stop, markMoment: vi.fn(), restore: vi.fn() }));
      return React.createElement("div", { "data-testid": "recorder" },
        phase === "idle" && React.createElement("button", {
          type: "button",
          onClick: () => {
            setPhase("recording");
            props.onActiveChange?.(true);
            props.onStatusChange?.({ ...idle, phase: "recording", seconds: 65, persistence: "saving" });
          },
        }, "Start recording"),
        phase === "recording" && React.createElement("button", { type: "button", onClick: stop }, "Finish"),
      );
    }),
  };
});

describe("Capture while recording", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(knowledgeApi.recordings).mockResolvedValue([]);
    window.localStorage.clear();
    recorder.mounts = 0;
    recorder.unmounts = 0;
  });

  const renderSheet = () => {
    const onCaptured = vi.fn().mockResolvedValue(undefined);
    const onAssetCaptured = vi.fn().mockResolvedValue(undefined);
    const onRecorderSnapshot = vi.fn();
    render(
      <CaptureSheet
        open
        surface="window"
        bases={[]}
        workspaceId="wsp_primary"
        initialKind="recording"
        initialRecordingContext="lecture"
        onHide={vi.fn()}
        onClose={vi.fn()}
        onRecorderSnapshot={onRecorderSnapshot}
        onCaptured={onCaptured}
        onAssetCaptured={onAssetCaptured}
      />,
    );
    return { onCaptured, onAssetCaptured, onRecorderSnapshot };
  };

  it("saves a note beside a running recording without stopping it", async () => {
    const user = userEvent.setup();
    const { onCaptured, onRecorderSnapshot } = renderSheet();
    await user.click(await screen.findByRole("button", { name: "Start recording" }));

    const noteTab = screen.getByRole("tab", { name: /Note/ });
    expect(noteTab).toBeEnabled();
    expect(screen.getByRole("tab", { name: /Recording/ })).not.toHaveTextContent("01:05");
    await user.click(noteTab);
    // The recording's timer rides on its tab while another type is open.
    expect(screen.getByRole("tab", { name: /Recording/ })).toHaveTextContent("01:05");
    expect(screen.getByRole("status", { name: "" })).toHaveTextContent("Recording in the background · 01:05");
    await user.type(screen.getByRole("textbox", { name: "Title" }), "Question for later");
    await user.type(screen.getByRole("textbox", { name: "Source content" }), "Ask about mitosis timing.");
    await user.click(screen.getByRole("button", { name: /Save note/ }));

    await waitFor(() => expect(onCaptured).toHaveBeenCalledOnce());
    expect(onCaptured.mock.calls[0]?.slice(0, 4)).toEqual(["Question for later", null, "Ask about mitosis timing.", "note"]);
    // The recorder was never unmounted, and Capture returns to it.
    expect(recorder.mounts).toBe(1);
    expect(recorder.unmounts).toBe(0);
    expect(await screen.findByText(/The recording kept going/)).toBeVisible();
    expect(screen.getByRole("tab", { name: /Recording/ })).toHaveAttribute("aria-selected", "true");
    expect(onRecorderSnapshot).toHaveBeenLastCalledWith(expect.objectContaining({ phase: "recording" }), expect.stringMatching(/^Lecture ·/), false);
  });

  it("switches to a type asked for elsewhere, and keeps the recording's own title and text", async () => {
    const user = userEvent.setup();
    const { onCaptured } = renderSheet();
    await user.click(await screen.findByRole("button", { name: "Start recording" }));
    await user.clear(screen.getByRole("textbox", { name: "Title" }));
    await user.type(screen.getByRole("textbox", { name: "Title" }), "Cell biology 3");

    act(() => {
      window.dispatchEvent(new CustomEvent(CAPTURE_SWITCH_DOM_EVENT, { detail: { kind: "link", source: "tray" } }));
    });
    expect(screen.getByRole("tab", { name: /Web page/ })).toHaveAttribute("aria-selected", "true");
    await user.type(screen.getByRole("textbox", { name: "Title" }), "Unsaved page title");

    // Asking for the recording shows it again; the page's text is not lost.
    act(() => {
      window.dispatchEvent(new CustomEvent(CAPTURE_SWITCH_DOM_EVENT, { detail: { kind: "recording", source: "tray" } }));
    });
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Cell biology 3");
    await user.click(screen.getByRole("button", { name: "Finish" }));
    await user.click(screen.getByRole("button", { name: /Save recording/ }));

    await waitFor(() => expect(onCaptured).toHaveBeenCalledOnce());
    expect(onCaptured.mock.calls[0]?.[0]).toBe("Cell biology 3");
    expect(onCaptured.mock.calls[0]?.[2]).toContain("Cells divide.");
    expect(onCaptured.mock.calls[0]?.[3]).toBe("recording");
    // The unsaved page is shown again, as it was.
    expect(await screen.findByRole("tab", { name: /Web page/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Unsaved page title");
  });

  it("accepts a dropped file while recording", async () => {
    const user = userEvent.setup();
    const { onAssetCaptured } = renderSheet();
    await user.click(await screen.findByRole("button", { name: "Start recording" }));
    await user.click(screen.getByRole("tab", { name: /Document/ }));
    const file = new File(["%PDF-1.7"], "handout.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("Choose a file"), file);
    await user.click(screen.getByRole("button", { name: /Save document/ }));

    await waitFor(() => expect(onAssetCaptured).toHaveBeenCalledOnce());
    expect(onAssetCaptured.mock.calls[0]?.[0]).toBe(file);
    expect(recorder.unmounts).toBe(0);
  });
});
