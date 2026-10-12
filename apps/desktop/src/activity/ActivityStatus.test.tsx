import type { ActivityItem, ActivityList } from "@gunther/contracts";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi } from "../api";
import { ActivityStatus } from "./ActivityStatus";
import { activityItem as item } from "./fixtures";
import { publishRecordingStatus } from "./recordingStatus";
import { ActivityProvider } from "./useActivity";

let lists: ActivityList[] = [];
let waiting: Array<(value: ActivityList) => void> = [];

/** The service: answers the first ask with the first list; each later ask waits for `push`. */
function serve(first: ActivityItem[]) {
  lists = [{ version: 1, items: first }];
  waiting = [];
  vi.spyOn(knowledgeApi, "activity").mockImplementation(() => {
    const next = lists.shift();
    return next ? Promise.resolve(next) : new Promise<ActivityList>((resolve) => { waiting.push(resolve); });
  });
}
const push = async (items: ActivityItem[], version = 2) => {
  await act(async () => { waiting.shift()?.({ version, items }); });
};

const onOpen = vi.fn();
function mount(options: { online?: boolean; here?: boolean } = {}) {
  return render(
    <ActivityProvider>
      <ActivityStatus serviceOnline={options.online ?? true} onOpen={onOpen} isHere={() => options.here ?? false} />
    </ActivityProvider>,
  );
}
const settle = () => act(async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); });
const pill = () => screen.getByRole("button", { name: /./, expanded: false });

describe("ActivityStatus", () => {
  beforeEach(() => {
    onOpen.mockReset();
    publishRecordingStatus(null);
    vi.spyOn(knowledgeApi, "stopActivity").mockResolvedValue(undefined);
    vi.spyOn(knowledgeApi, "dismissActivity").mockResolvedValue(undefined);
    vi.spyOn(knowledgeApi, "clearActivity").mockResolvedValue(undefined);
  });
  afterEach(() => { vi.restoreAllMocks(); publishRecordingStatus(null); });

  it("says Saved locally when nothing runs", async () => {
    serve([]);
    mount();
    await settle();
    expect(screen.getByRole("button", { name: /Saved locally/ })).toHaveAttribute("aria-haspopup", "dialog");
    expect(screen.getByRole("button", { name: /Saved locally/ })).toHaveAttribute("data-no-drag");
  });

  it("says Reconnecting… when the service is offline", async () => {
    serve([]);
    mount({ online: false });
    await settle();
    expect(screen.getByRole("button", { name: /Reconnecting…/ })).toHaveClass("is-offline");
  });

  it("shows the recording clock first, with a +N chip for the jobs", async () => {
    serve([item({ id: "a" }), item({ id: "b", state: "waiting" })]);
    publishRecordingStatus({ phase: "recording", seconds: 754, title: "Cell biology" });
    mount();
    await settle();
    expect(screen.getByText("12:34")).toBeInTheDocument();
    expect(within(pill()).getByText("Recording")).toBeInTheDocument();
    expect(screen.getByText("+2")).toBeInTheDocument();
  });

  it("lists the rows in the panel and opens a row's page", async () => {
    const user = userEvent.setup();
    const open = { view: "library", libraryId: "lib", tab: "outputs" } as const;
    serve([
      item({ id: "output:lib", kind: "output", title: "Building slides", about: "Single-cell", progress: 0.5, detail: "Writing section 3 of 7", open }),
      item({ id: "answer:s", state: "done", title: "Answer ready", about: "Why?", endedAt: new Date().toISOString() }),
    ]);
    mount();
    await settle();
    await user.click(pill());
    const panel = screen.getByRole("dialog", { name: "Activity" });
    expect(panel.parentElement).toBe(document.body);
    expect(within(panel).getByText("Now")).toBeInTheDocument();
    expect(within(panel).getByText("Building slides: Single-cell")).toBeInTheDocument();
    expect(within(panel).getByText("Writing section 3 of 7")).toBeInTheDocument();
    expect(within(panel).getByText("50%")).toBeInTheDocument();
    expect(within(panel).getByText("Earlier")).toBeInTheDocument();
    expect(within(panel).getByText("Answer ready: Why?")).toBeInTheDocument();
    await user.click(within(panel).getByText("Building slides: Single-cell"));
    expect(onOpen).toHaveBeenCalledWith(open);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("stops, dismisses and clears through the service", async () => {
    const user = userEvent.setup();
    serve([item({ id: "transcribe:a" }), item({ id: "output:x", state: "done", title: "Slides ready", endedAt: new Date().toISOString() })]);
    mount();
    await settle();
    await user.click(pill());
    await user.click(screen.getByRole("button", { name: "Stop Transcribing again" }));
    expect(knowledgeApi.stopActivity).toHaveBeenCalledWith("transcribe:a");
    await user.click(screen.getByRole("button", { name: "Dismiss Slides ready" }));
    expect(knowledgeApi.dismissActivity).toHaveBeenCalledWith("output:x");
    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(knowledgeApi.clearActivity).toHaveBeenCalled();
  });

  it("finishes the recording from the panel, and shows it when its row is clicked", async () => {
    const user = userEvent.setup();
    const seenControls: string[] = [];
    const listen = (event: Event) => seenControls.push((event as CustomEvent<string>).detail);
    window.addEventListener("gunther:capture-control", listen);
    serve([]);
    publishRecordingStatus({ phase: "recording", seconds: 5, title: "Lecture", transcriptWords: 1240 });
    mount();
    await settle();
    await user.click(pill());
    expect(screen.getByText("1,240 words")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Pause recording" }));
    await user.click(screen.getByRole("button", { name: "Finish recording" }));
    await user.click(screen.getByText("Recording: Lecture"));
    window.removeEventListener("gunther:capture-control", listen);
    expect(seenControls).toEqual(["pause", "finish", "show"]);
  });

  it("closes with Esc and with a click outside, and returns focus to the pill", async () => {
    const user = userEvent.setup();
    serve([]);
    mount();
    await settle();
    await user.click(pill());
    expect(screen.getByText(/Nothing running/)).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: /Saved locally/ })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: /Saved locally/ }));
    await user.click(document.body);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps a failure in the pill until the panel has been opened", async () => {
    const user = userEvent.setup();
    serve([item({ id: "output:lib", state: "failed", title: "Couldn't build slides", error: "The model timed out.", endedAt: new Date().toISOString() })]);
    mount();
    await settle();
    expect(screen.getByRole("button", { name: /Couldn't build slides/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Couldn't build slides/ }));
    expect(screen.getByText("The model timed out.")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: /Saved locally/ })).toBeInTheDocument();
  });

  it("flashes a job that finishes elsewhere, and clears it after a while", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      serve([item({ id: "output:lib", title: "Building slides" })]);
      mount();
      await settle();
      await push([item({ id: "output:lib", state: "done", title: "Slides ready", endedAt: new Date().toISOString() })]);
      expect(screen.getByRole("button", { name: /Slides ready/ })).toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent("Slides ready");
      await act(async () => { vi.advanceTimersByTime(6_100); });
      expect(screen.getByRole("button", { name: /Saved locally/ })).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not flash a job that finishes on the page being looked at, and dismisses it", async () => {
    serve([item({ id: "output:lib", title: "Building slides", open: { view: "library", libraryId: "lib", tab: "outputs" } })]);
    mount({ here: true });
    await settle();
    await push([item({ id: "output:lib", state: "done", title: "Slides ready", endedAt: new Date().toISOString(), open: { view: "library", libraryId: "lib", tab: "outputs" } })]);
    expect(screen.queryByRole("button", { name: /Slides ready/ })).toBeNull();
    expect(knowledgeApi.dismissActivity).toHaveBeenCalledWith("output:lib");
  });
});
