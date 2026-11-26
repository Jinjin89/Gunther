import type { Artifact, ArtifactAudio, ConversationCitation } from "@gunther/contracts";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { knowledgeApi } from "../api";
import { AudioDurationScope, AudioOverview, audioFile } from "./AudioOverview";
import type { ReaderProps } from "./OutputViews";
import { PrintRoot } from "./PrintRoot";

const citation: ConversationCitation = {
  id: "source-passage", sourceId: "source", sourceTitle: "Local notes", locator: "p. 2",
  quote: "The field log identifies a difference.", assertionId: null, status: "verified", confidence: 1,
};
const marker = "<!-- audio: audio_overview; design: v1 -->";
const timings: ArtifactAudio["timings"] = [
  { line: 1, speaker: "A", start: 0, end: 2 },
  { line: 2, speaker: "B", start: 2, end: 4 },
  { line: 3, speaker: "A", start: 5, end: 8 },
];
const recording: ArtifactAudio = {
  status: "ready", contentHash: "script-hash", audioAssetId: "asset-1", voices: ["one", "two"], timings, error: null,
};
function script(changes: Partial<Artifact> = {}): Artifact {
  return {
    id: "version-1", workspaceId: "workspace", knowledgeBaseId: "library",
    kind: "audio", title: "Field discussion", contentHash: recording.contentHash, audio: recording,
    content: `${marker}\nA: What changed?\nB: The field log [1] explains the change. See [methods](#methods).\nA: What follows?`,
    versionNumber: 1, createdAt: "2026-01-01T00:00:00Z", citations: [citation], ...changes,
  } as Artifact;
}

const media = () => screen.getByLabelText<HTMLAudioElement>("Audio overview recording");
const position = () => screen.getByRole("slider", { name: "Position" });
const lines = () => within(screen.getByRole("list", { name: "Script" })).getAllByRole("listitem");
function timeAt(seconds: number) {
  media().currentTime = seconds;
  fireEvent.timeUpdate(media());
}

afterEach(() => vi.unstubAllGlobals());

it("uses half-open timing ranges, leaves gaps clear, and prefers actual WAV duration", () => {
  render(<AudioOverview artifact={script()} onCite={vi.fn()} />);
  expect(position()).toHaveAttribute("max", "8");
  expect(lines()[0]).not.toHaveAttribute("aria-current");
  timeAt(1.999);
  expect(lines()[0]).toHaveAttribute("aria-current", "true");
  timeAt(2);
  expect(lines()[0]).not.toHaveAttribute("aria-current");
  expect(lines()[1]).toHaveAttribute("aria-current", "true");
  timeAt(4);
  expect(lines().some((line) => line.hasAttribute("aria-current"))).toBe(false);
  timeAt(5);
  expect(lines()[2]).toHaveAttribute("aria-current", "true");
  timeAt(8);
  expect(lines()[2]).not.toHaveAttribute("aria-current");
  Object.defineProperty(media(), "duration", { value: 10 });
  fireEvent.loadedMetadata(media());
  expect(position()).toHaveAttribute("aria-valuetext", "0:08 of 0:10");
});

it("leaves total duration unknown and seeks beyond a timed first turn when the script has an untimed tail", () => {
  const saved = script({ audio: { ...recording, timings: [timings[0]!] } });
  const { container } = render(<AudioOverview artifact={saved} onCite={vi.fn()} />);
  expect(container.querySelector(".gx-player-total")).toHaveTextContent("—");
  expect(position()).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Forward 15 seconds" }));
  expect(media().currentTime).toBe(15);
  expect(position()).toHaveAttribute("aria-valuetext", "0:15 elapsed");
  Object.defineProperty(media(), "duration", { value: 22 });
  fireEvent.loadedMetadata(media());
  expect(position()).toHaveAttribute("aria-valuetext", "0:15 of 0:22");
});

it("uses the measured WAV bounds on paper and in the reader for only that version and asset", () => {
  const saved = script({ audio: { ...recording, timings: [timings[0]!, { ...timings[2]!, start: 9, end: 11 }] } });
  const view = (artifact: Artifact) => <AudioDurationScope artifact={artifact}>
    <AudioOverview artifact={artifact} onCite={vi.fn()} />
    <PrintRoot artifact={artifact} />
  </AudioDurationScope>;
  const { rerender } = render(view(saved));
  const printedTail = () => document.querySelector("#print-root .audio-script > li:last-child");
  const tailStamp = () => printedTail()?.querySelector(".audio-script-time");
  expect(tailStamp()).toHaveTextContent("0:09");
  Object.defineProperty(media(), "duration", { value: 6 });
  fireEvent.loadedMetadata(media());
  expect(screen.queryByRole("button", { name: /^Play line 3,/ })).toBeNull();
  expect(document.querySelectorAll("#print-root .audio-script-time")).toHaveLength(1);
  expect(tailStamp()).toBeNull();
  expect(printedTail()).toHaveTextContent("What follows?");

  const differentAsset = { ...saved, audio: { ...saved.audio!, audioAssetId: "asset-2" } };
  rerender(view(differentAsset));
  expect(tailStamp()).toHaveTextContent("0:09");
  Object.defineProperty(media(), "duration", { value: 6 });
  fireEvent.loadedMetadata(media());
  expect(tailStamp()).toBeNull();
  rerender(view({ ...differentAsset, id: "version-2" }));
  expect(tailStamp()).toHaveTextContent("0:09");
});

it("keeps independently valid lines timed when other ranges, positions or speakers are invalid", () => {
  const saved = script({ audio: { ...recording, timings: [
    timings[0]!,
    { ...timings[1]!, speaker: "A" },
    { ...timings[1]!, end: 2 },
    { ...timings[1]!, start: -1 },
    { ...timings[1]!, start: NaN },
    { ...timings[1]!, line: 0 },
    { ...timings[1]!, line: 2.5 },
    { ...timings[1]!, line: 4 },
    timings[2]!,
  ] } });
  const { rerender } = render(<AudioOverview artifact={saved} onCite={vi.fn()} />);
  expect(screen.getAllByRole("button", { name: /^Play line/ })).toHaveLength(2);
  Object.defineProperty(media(), "duration", { value: 6 });
  fireEvent.loadedMetadata(media());
  expect(screen.getAllByRole("button", { name: /^Play line/ })).toHaveLength(1);
  expect(position()).toHaveAttribute("max", "6");
  expect(within(lines()[2]!).getByText("What follows?")).toBeVisible();
  rerender(<AudioOverview artifact={script({ audio: { ...recording, audioAssetId: "asset-2", timings: [
    timings[0]!, { ...timings[1]!, start: 1 }, timings[2]!, timings[2]!,
  ] } })} onCite={vi.fn()} />);
  expect(screen.queryByRole("button", { name: /^Play line/ })).toBeNull();
});

it.each([
  { contentHash: "old-script" }, { audioAssetId: null }, { audioAssetId: " " }, { status: "pending" },
] satisfies Array<Partial<ArtifactAudio>>)("keeps scripts readable without a matching ready asset (%j)", (audio) => {
  const saved = script({ audio: { ...recording, ...audio } });
  render(<AudioOverview artifact={saved} onCite={vi.fn()} />);
  expect(screen.getByText("What changed?")).toBeVisible();
  expect(screen.queryByLabelText("Audio overview recording")).toBeNull();
  expect(screen.queryByRole("button", { name: /^Play line/ })).toBeNull();
  expect(audioFile.available(saved, false)).toBe(false);
});

it("seeks only from timestamps and player controls, with independent citations, text and links", () => {
  const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  const onCite = vi.fn();
  const onAsk = vi.fn();
  render(<AudioOverview artifact={script()} onCite={onCite} onAsk={onAsk} />);
  timeAt(2.5);
  fireEvent.click(screen.getByText("What changed?"));
  fireEvent.click(screen.getByRole("link", { name: "methods" }));
  fireEvent.click(screen.getByRole("button", { name: "Inspect citation 1" }));
  expect(onCite).toHaveBeenCalledWith(citation, 0);
  expect(media().currentTime).toBe(2.5);
  expect(play).not.toHaveBeenCalled();
  fireEvent.click(within(lines()[1]!).getByRole("button", { name: "Refine line" }));
  expect(onAsk).toHaveBeenCalledWith(1);
  fireEvent.click(screen.getByRole("button", { name: /^Play line 1,/ }));
  expect(media().currentTime).toBe(0);
  expect(lines()[0]).toHaveAttribute("aria-current", "true");
  expect(play).toHaveBeenCalledOnce();

  fireEvent.keyDown(document.body, { key: "ArrowRight" });
  expect(media().currentTime).toBe(0);
  const region = screen.getByRole("group", { name: "Audio player" });
  fireEvent.keyDown(region, { key: "ArrowRight" });
  expect(media().currentTime).toBe(5);
  fireEvent.keyDown(screen.getByRole("slider", { name: "Volume" }), { key: " " });
  expect(play).toHaveBeenCalledOnce();
  fireEvent.keyDown(region, { key: " " });
  expect(play).toHaveBeenCalledTimes(2);
});

it("reveals the offscreen playing line only on request and accounts for the sticky transport", () => {
  const { container } = render(<div className="library-middle-page">
    <div className="library-middle-bar" />
    <AudioOverview artifact={script()} onCite={vi.fn()} />
  </div>);
  const pane = container.firstElementChild!;
  const toolbar = container.querySelector(".audio-transport")!;
  vi.spyOn(pane, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 500, 500));
  vi.spyOn(toolbar, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 44, 500, 100));
  const row = lines()[1]!;
  const bounds = vi.spyOn(row, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 55, 500, 70));
  const scrollIntoView = vi.fn(() => {
    bounds.mockReturnValue(new DOMRect(0, 152, 500, 90));
    fireEvent.scroll(pane);
  });
  Object.defineProperty(row, "scrollIntoView", { value: scrollIntoView });
  const play = screen.getByRole("button", { name: "Play" });
  play.focus();
  timeAt(2);
  expect(play).toHaveFocus();
  expect(scrollIntoView).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Go to playing line" }));
  expect(row).toHaveFocus();
  expect(scrollIntoView).toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Go to playing line" })).toBeNull();
});

it("updates playing-line visibility when Change voices opens and closes at 320 px without scrolling", () => {
  let resize: (target: Element) => void;
  vi.stubGlobal("ResizeObserver", class {
    private targets = new Set<Element>();

    constructor(callback: () => void) {
      resize = (target) => { if (this.targets.has(target)) callback(); };
    }
    observe(target: Element) { this.targets.add(target); }
    disconnect() { this.targets.clear(); }
  });
  const work: NonNullable<ReaderProps["work"]> = {
    working: false, disabled: false, run: vi.fn(),
    voices: { choices: [], defaults: ["one", "two"], allowCustom: true },
  };
  const { container } = render(<div className="library-middle-page" style={{ width: 320 }}>
    <AudioOverview artifact={script()} onCite={vi.fn()} work={work} />
  </div>);
  const pane = container.querySelector<HTMLElement>(".library-middle-page")!;
  const actions = pane.querySelector<HTMLDivElement>(".audio-actions")!;
  const summary = within(actions).getByText("Change voices");
  const voices = summary.closest("details")!;
  const row = lines()[2]!;
  vi.spyOn(pane, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 320, 500));
  vi.spyOn(row, "getBoundingClientRect").mockImplementation(() => new DOMRect(0, voices.open ? 540 : 430, 320, 60));
  timeAt(5);
  fireEvent.pause(media());
  const jump = () => screen.queryByRole("button", { name: "Go to playing line" });
  expect(row).toHaveAttribute("aria-current", "true");
  expect(jump()).toBeNull();

  fireEvent.click(summary);
  expect(voices.open).toBe(true);
  act(() => resize(actions));
  expect(jump()).toBeVisible();

  fireEvent.click(summary);
  expect(voices.open).toBe(false);
  act(() => resize(actions));
  expect(jump()).toBeNull();
  expect(pane.scrollTop).toBe(0);
});

it("reports the real playback error and retries the exact script version", async () => {
  const run = vi.fn<NonNullable<ReaderProps["work"]>["run"]>();
  const saved = script();
  const work = { working: false, disabled: false, run };
  const retry = vi.spyOn(knowledgeApi, "retryAudioStream").mockResolvedValue(saved);
  vi.spyOn(HTMLMediaElement.prototype, "play").mockRejectedValueOnce(new Error("This WAV could not be decoded."));
  const { rerender } = render(<AudioOverview artifact={saved} onCite={vi.fn()} work={work} />);
  fireEvent.click(screen.getByRole("button", { name: /^Play line 1,/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("This WAV could not be decoded.");
  expect(screen.queryByRole("button", { name: /^Play line/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry audio" }));
  const hear = vi.fn();
  const signal = new AbortController().signal;
  expect(run).toHaveBeenCalledWith("Recording audio", expect.any(Function));
  const result = await run.mock.calls[0]![1](hear, signal);
  expect(retry).toHaveBeenCalledWith(saved.knowledgeBaseId, saved.id, {}, saved.workspaceId, hear, signal);
  expect(result).toMatchObject({ id: saved.id, contentHash: saved.contentHash });
  rerender(<AudioOverview artifact={saved} onCite={vi.fn()} work={{ ...work, working: true }} />);
  expect(screen.getByText("What follows?")).toBeVisible();
  expect(screen.queryByLabelText("Audio overview recording")).toBeNull();
  rerender(<AudioOverview artifact={saved} onCite={vi.fn()} work={work} />);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByRole("button", { name: "Play" })).toBeEnabled();
});

it("allows a second Play on the same file after the browser refuses the first request", async () => {
  const run = vi.fn();
  const play = vi.spyOn(HTMLMediaElement.prototype, "play")
    .mockRejectedValueOnce(new DOMException("Accept browser playback first.", "NotAllowedError"))
    .mockResolvedValue();
  render(<AudioOverview artifact={script()} onCite={vi.fn()} work={{ working: false, disabled: false, run }} />);
  const element = media();
  const control = screen.getByRole("button", { name: "Play" });
  fireEvent.click(control);
  expect(await screen.findByRole("alert")).toHaveTextContent("Accept browser playback first.");
  expect(control).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Retry audio" })).toBeNull();
  fireEvent.click(control);
  expect(play).toHaveBeenCalledTimes(2);
  expect(media()).toBe(element);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(run).not.toHaveBeenCalled();
});

it("keeps the native media failure reason and retry in the sticky player after scrolling", () => {
  const work = { working: false, disabled: false, run: vi.fn() };
  const { container } = render(<div className="library-middle-page">
    <AudioOverview artifact={script()} onCite={vi.fn()} work={work} />
  </div>);
  const pane = container.querySelector<HTMLElement>(".library-middle-page")!;
  const transport = within(pane.querySelector<HTMLElement>(".audio-transport")!);
  timeAt(5);
  pane.scrollTop = 360;
  fireEvent.scroll(pane);
  Object.defineProperty(media(), "error", { value: { code: 3, message: "" } });
  fireEvent.error(media());
  expect(transport.getByRole("alert")).toHaveTextContent("The audio file could not be decoded.");
  expect(transport.getByRole("button", { name: "Retry audio" })).toBeEnabled();
  expect(transport.getByRole("button", { name: "Play" })).toBeDisabled();
  expect(pane.scrollTop).toBe(360);
});

it("resets playback, speed, mute and errors when the asset or version changes", async () => {
  let reject!: (reason: Error) => void;
  vi.spyOn(HTMLMediaElement.prototype, "play").mockReturnValueOnce(new Promise<void>((_resolve, rejectPlay) => {
    reject = rejectPlay;
  }));
  const first = script();
  const { rerender } = render(<AudioOverview artifact={first} onCite={vi.fn()} />);
  const source = media();
  timeAt(5);
  fireEvent.click(screen.getByRole("button", { name: "Playback speed 1×" }));
  fireEvent.change(screen.getByRole("slider", { name: "Volume" }), { target: { value: "0.4" } });
  expect(source.volume).toBe(0.4);
  fireEvent.click(screen.getByRole("button", { name: "Mute" }));
  expect(source.muted).toBe(true);
  expect(source.playbackRate).toBe(1.25);
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  fireEvent.play(source);
  Object.defineProperty(source, "paused", { value: false });
  const pause = vi.spyOn(source, "pause").mockImplementation(() => {});

  const rerecorded = script({ audio: { ...recording, audioAssetId: "asset-2" } });
  rerender(<AudioOverview artifact={rerecorded} onCite={vi.fn()} />);
  expect(pause).toHaveBeenCalledOnce();
  expect(media()).not.toBe(source);
  await act(async () => reject(new Error("The older file failed.")));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(media()).toMatchObject({ currentTime: 0, muted: false, volume: 1, playbackRate: 1 });
  expect(screen.getByRole("button", { name: "Play" })).toBeEnabled();

  const newAsset = media();
  timeAt(5);
  rerender(<AudioOverview artifact={{ ...rerecorded, id: "version-2" }} onCite={vi.fn()} />);
  expect(media()).not.toBe(newAsset);
  expect(position()).toHaveAttribute("aria-valuetext", "0:00 of 0:08");
  expect(lines()[2]).not.toHaveAttribute("aria-current");
});

it.each(["missing", "partial", "complete"] as const)("prints all 120 lines with %s timing data", (coverage) => {
  const all = Array.from({ length: 120 }, (_, index): ArtifactAudio["timings"][number] =>
    ({ line: index + 1, speaker: index % 2 ? "B" : "A", start: index * 2, end: (index + 1) * 2 }));
  const content = [marker, ...all.map(({ line, speaker }) => `${speaker}: Words from turn ${line} [1].`)].join("\n");
  const audio = coverage === "missing" ? null : { ...recording, timings: coverage === "partial" ? [all[0]!, all.at(-1)!] : all };
  render(<PrintRoot artifact={script({ content, audio })} />);
  const paper = document.getElementById("print-root")!;
  const entries = [...paper.querySelectorAll<HTMLLIElement>(".audio-script > li")];
  expect(entries).toHaveLength(120);
  expect(within(entries[0]!).getByText("Speaker A")).toBeInTheDocument();
  expect(entries[0]).toHaveTextContent("Words from turn 1 [1].");
  expect(within(entries.at(-1)!).getByText("Speaker B")).toBeInTheDocument();
  expect(entries.at(-1)).toHaveTextContent("Words from turn 120 [1].");
  expect(paper.querySelectorAll(".audio-script-time")).toHaveLength(audio?.timings.length ?? 0);
  expect(entries.at(-1)?.querySelector(".audio-script-time")?.textContent).toBe(audio ? "3:58" : undefined);
  expect(entries[0]!.querySelector("sup")).toHaveTextContent("[1]");
  expect(paper.querySelector("audio, button, select")).toBeNull();
});

it("treats marker-only drafts as empty and preserves unmarked speech in the preview", () => {
  const { rerender } = render(<AudioOverview artifact={script({ content: marker })} onCite={vi.fn()} presentation="preview" />);
  expect(screen.getByText("This version has no audio script.")).toBeVisible();
  rerender(<AudioOverview artifact={script({ content: "An earlier script without speaker marks." })} onCite={vi.fn()} presentation="preview" />);
  expect(screen.getByText("An earlier script without speaker marks.")).toBeVisible();
  expect(screen.queryByRole("slider")).toBeNull();
});
