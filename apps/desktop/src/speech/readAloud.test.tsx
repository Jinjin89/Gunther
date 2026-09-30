import type { TtsOverview } from "@gunther/contracts";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { readAloud } from "./readAloud";
import { SpeakerButton } from "./SpeakerButton";
import { TtsSettings } from "./TtsSettings";

const api = vi.hoisted(() => ({
  speakMessage: vi.fn(),
  speechAudio: vi.fn(),
  ttsOverview: vi.fn(),
  saveTts: vi.fn(),
  ttsSample: vi.fn(),
  clearTtsCache: vi.fn(),
}));
vi.mock("../api", () => ({ knowledgeApi: api }));

const players: FakeAudio[] = [];
class FakeAudio {
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  paused = true;
  constructor(public src: string) { players.push(this); }
  play = vi.fn(async () => { this.paused = false; });
  pause = vi.fn(() => { this.paused = true; });
}

const message = { id: "m1", sessionId: "s1" };
const overview = (patch: Partial<TtsOverview> = {}): TtsOverview => ({
  persisted: true,
  active: "qwen",
  autoRead: false,
  problem: null,
  cache: { clips: 2, bytes: 3 * 1024 * 1024 },
  providers: [{
    kind: "qwen", name: "Qwen", baseUrl: "https://dashscope.aliyuncs.com", model: "qwen3-tts-flash", models: ["qwen3-tts-flash"],
    keyOptional: false, readsStructure: false, note: "", values: { voice: "Cherry", language: "Auto" }, keySet: true, keyHint: "1234", keyShared: false, problem: null,
    options: [
      { key: "voice", label: "Voice", default: "Cherry", allowCustom: true, help: "", choices: [{ value: "Cherry", label: "Cherry" }, { value: "Ethan", label: "Ethan" }] },
      { key: "language", label: "Language", default: "Auto", allowCustom: false, help: "", choices: [{ value: "Auto", label: "Detect it" }, { value: "English", label: "English" }] },
    ],
  }],
  ...patch,
});

describe("reading an answer aloud", () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset());
    players.length = 0;
    readAloud.stop();
    vi.stubGlobal("Audio", FakeAudio);
    URL.createObjectURL = vi.fn(() => "blob:clip");
    URL.revokeObjectURL = vi.fn();
    api.speakMessage.mockResolvedValue({ id: "c1" });
    api.speechAudio.mockResolvedValue(new Blob(["x"]));
  });

  it("makes the audio once, then pauses, resumes and replays without asking again", async () => {
    const user = userEvent.setup();
    render(<SpeakerButton message={message} />);
    await user.click(screen.getByRole("button", { name: "Read aloud" }));
    expect(await screen.findByRole("button", { name: "Pause" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Pause" }));
    expect(players[0]?.pause).toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Resume" }));
    expect(players[0]?.paused).toBe(false);
    act(() => players[0]?.onended?.());
    await user.click(await screen.findByRole("button", { name: "Read aloud" }));
    await screen.findByRole("button", { name: "Pause" });
    expect(api.speakMessage).toHaveBeenCalledTimes(1);
    expect(api.speechAudio).toHaveBeenCalledTimes(1);
  });

  it("shows why it failed and lets you retry", async () => {
    const user = userEvent.setup();
    api.speakMessage.mockRejectedValueOnce(new Error("Qwen did not accept this API key."));
    render(<SpeakerButton message={{ id: "m-fail", sessionId: "s1" }} />);
    await user.click(screen.getByRole("button", { name: "Read aloud" }));
    expect((await screen.findByRole("alert")).textContent).toContain("did not accept");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("button", { name: "Pause" })).toBeTruthy();
  });

  it("reads one answer at a time", async () => {
    await act(async () => { await readAloud.toggle(message); });
    await act(async () => { await readAloud.toggle({ id: "m2", sessionId: "s1" }); });
    expect(players[0]?.pause).toHaveBeenCalled();
    expect(readAloud.state.messageId).toBe("m2");
  });

  it("reads a fresh answer by itself only when asked to and ready", async () => {
    api.ttsOverview.mockResolvedValueOnce(overview({ autoRead: false }));
    await readAloud.auto({ id: "auto-1", sessionId: "s1" });
    expect(api.speakMessage).not.toHaveBeenCalled();
    api.ttsOverview.mockResolvedValueOnce(overview({ autoRead: true, problem: "Qwen needs an API key." }));
    await readAloud.auto({ id: "auto-1", sessionId: "s1" });
    expect(api.speakMessage).not.toHaveBeenCalled();
    api.ttsOverview.mockResolvedValueOnce(overview({ autoRead: true }));
    await readAloud.auto({ id: "auto-1", sessionId: "s1" });
    await waitFor(() => expect(api.speakMessage).toHaveBeenCalledTimes(1));
  });
});

describe("TtsSettings", () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset());
    api.ttsOverview.mockResolvedValue(overview());
    vi.stubGlobal("Audio", FakeAudio);
    URL.createObjectURL = vi.fn(() => "blob:sample");
  });

  it("saves a supplier-specific option and can play a sample before saving", async () => {
    const user = userEvent.setup();
    api.saveTts.mockResolvedValue(overview({ providers: [{ ...overview().providers[0]!, values: { voice: "Ethan", language: "Auto" } }] }));
    api.ttsSample.mockResolvedValue(new Blob(["x"]));
    render(<TtsSettings onNotify={() => undefined} />);
    await user.selectOptions(await screen.findByLabelText("Voice"), "Ethan");
    await user.click(screen.getByRole("button", { name: /hear a sample/i }));
    expect(api.ttsSample).toHaveBeenCalledWith(expect.objectContaining({ kind: "qwen", provider: expect.objectContaining({ options: { voice: "Ethan", language: "Auto" } }) }));
    expect(api.ttsSample.mock.calls[0]?.[0].provider).not.toHaveProperty("apiKey");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(api.saveTts).toHaveBeenCalledTimes(1);
  });

  it("turns automatic reading on and clears the saved recordings", async () => {
    const user = userEvent.setup();
    api.saveTts.mockResolvedValue(overview({ autoRead: true }));
    api.clearTtsCache.mockResolvedValue(overview({ cache: { clips: 0, bytes: 0 } }));
    render(<TtsSettings onNotify={() => undefined} />);
    await user.click(await screen.findByRole("switch", { name: "Read new answers automatically" }));
    expect(api.saveTts).toHaveBeenCalledWith({ autoRead: true });
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(api.clearTtsCache).toHaveBeenCalled();
    expect(await screen.findByText("None yet.")).toBeTruthy();
    expect(screen.getByText(/described in words by your language model/)).toBeTruthy();
  });
});
