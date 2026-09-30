import type { TtsOverview } from "@gunther/contracts";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { readAloud } from "./readAloud";
import { SpeakerButton } from "./SpeakerButton";
import { TtsSettings } from "./TtsSettings";

const api = vi.hoisted(() => ({
  beginSpeech: vi.fn(),
  speechPart: vi.fn(),
  speechAudio: vi.fn(),
  ttsOverview: vi.fn(),
  saveTtsRoles: vi.fn(),
  addTtsProvider: vi.fn(),
  updateTtsProvider: vi.fn(),
  removeTtsProvider: vi.fn(),
  ttsSample: vi.fn(),
  clearTtsCache: vi.fn(),
  speechScript: vi.fn(),
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
  addEventListener = vi.fn();
}

const message = { id: "m1", sessionId: "s1" };
const QWEN_OPTIONS = [
  { key: "voice", label: "Voice", default: "Cherry", allowCustom: true, help: "", choices: [{ value: "Cherry", label: "Cherry" }, { value: "Ethan", label: "Ethan" }] },
  { key: "language", label: "Language", default: "Auto", allowCustom: false, help: "", choices: [{ value: "Auto", label: "Detect it" }, { value: "English", label: "English" }] },
];
const overview = (patch: Partial<TtsOverview> = {}, job: Partial<TtsOverview["roles"][number]> = {}): TtsOverview => ({
  persisted: true,
  autoRead: false,
  problem: null,
  cache: { clips: 2, bytes: 3 * 1024 * 1024 },
  providers: [{
    id: "qwen", name: "Qwen", kind: "qwen", baseUrl: "https://dashscope.aliyuncs.com", keySet: true, keyHint: "1234", keyShared: false, keyOptional: false, readsStructure: false, note: "",
    models: [{ id: "qwen3-tts-flash", ref: "qwen/qwen3-tts-flash", label: "qwen3-tts-flash" }],
    status: { state: "configured", summary: "1 model", check: null },
  }],
  roles: [{ id: "answers", label: "Answers", description: "Speaks an answer.", model: "qwen/qwen3-tts-flash", kind: "qwen", options: { voice: "Cherry", language: "Auto" }, autoRead: false, problem: null, ...job }],
  presets: [{ kind: "qwen", name: "Qwen", baseUrl: "https://dashscope.aliyuncs.com", models: ["qwen3-tts-flash"], keyOptional: false, readsStructure: false, note: "", options: QWEN_OPTIONS }],
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
    api.beginSpeech.mockResolvedValue({ clip: { id: "c1" }, jobId: null, parts: 1 });
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
    expect(api.beginSpeech).toHaveBeenCalledTimes(2);
    expect(api.speechAudio).toHaveBeenCalledTimes(1);
  });

  it("opens into a player whose controls are about the audio: record again, what is spoken, stop", async () => {
    api.speechScript.mockResolvedValue({ script: "Two drugs were compared." });
    const user = userEvent.setup();
    render(<SpeakerButton message={message} />);
    expect(screen.queryByRole("button", { name: "Record again" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Read aloud" }));
    await screen.findByRole("button", { name: "Pause" });
    await user.click(screen.getByRole("button", { name: "What is spoken" }));
    expect(await screen.findByText("Two drugs were compared.")).toBeTruthy();
    expect(api.speechScript).toHaveBeenCalledWith({ clipId: "c1" });
    await user.click(screen.getByRole("button", { name: "Record again" }));
    await waitFor(() => expect(api.beginSpeech).toHaveBeenLastCalledWith("s1", "m1", true));
    await screen.findByRole("button", { name: "Pause" });
    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(await screen.findByRole("button", { name: "Read aloud" })).toBeTruthy();
  });

  it("plays a long answer part by part, fetching the next while one plays", async () => {
    api.beginSpeech.mockResolvedValue({ clip: null, jobId: "j1", parts: 2 });
    api.speechPart.mockResolvedValue(new Blob(["x"]));
    const user = userEvent.setup();
    render(<SpeakerButton message={message} />);
    await user.click(screen.getByRole("button", { name: "Read aloud" }));
    expect(await screen.findByRole("button", { name: "Pause" })).toBeTruthy();
    expect(players).toHaveLength(1);
    await waitFor(() => expect(api.speechPart).toHaveBeenCalledTimes(2));
    act(() => players[0]?.onended?.());
    await waitFor(() => expect(players).toHaveLength(2));
    act(() => players[1]?.onended?.());
    expect(await screen.findByRole("button", { name: "Read aloud" })).toBeTruthy();
    expect(api.speechPart).toHaveBeenNthCalledWith(1, "j1", 0);
    expect(api.speechPart).toHaveBeenNthCalledWith(2, "j1", 1);
  });

  it("shows why it failed and lets you retry", async () => {
    const user = userEvent.setup();
    api.beginSpeech.mockRejectedValueOnce(new Error("Qwen did not accept this API key."));
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
    expect(api.beginSpeech).not.toHaveBeenCalled();
    api.ttsOverview.mockResolvedValueOnce(overview({ autoRead: true, problem: "Qwen needs an API key." }));
    await readAloud.auto({ id: "auto-1", sessionId: "s1" });
    expect(api.beginSpeech).not.toHaveBeenCalled();
    api.ttsOverview.mockResolvedValueOnce(overview({ autoRead: true }));
    await readAloud.auto({ id: "auto-1", sessionId: "s1" });
    await waitFor(() => expect(api.beginSpeech).toHaveBeenCalledTimes(1));
  });
});

describe("TtsSettings", () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset());
    api.ttsOverview.mockResolvedValue(overview());
    vi.stubGlobal("Audio", FakeAudio);
    URL.createObjectURL = vi.fn(() => "blob:sample");
  });

  it("chooses the voice on the Answers job and hears it", async () => {
    const user = userEvent.setup();
    api.saveTtsRoles.mockResolvedValue(overview({}, { options: { voice: "Ethan", language: "Auto" } }));
    api.ttsSample.mockResolvedValue(new Blob(["x"]));
    render(<TtsSettings onNotify={() => undefined} />);
    await user.selectOptions(await screen.findByLabelText("Voice"), "Ethan");
    expect(api.saveTtsRoles).toHaveBeenCalledWith({ answers: { options: { voice: "Ethan", language: "Auto" } } });
    await user.click(screen.getByRole("button", { name: "Hear" }));
    expect(api.ttsSample).toHaveBeenCalledWith({ options: { voice: "Ethan", language: "Auto" } });
  });

  it("turns reading off by choosing Off, and automatic reading on", async () => {
    const user = userEvent.setup();
    api.saveTtsRoles.mockResolvedValue(overview({ autoRead: true }, { autoRead: true }));
    render(<TtsSettings onNotify={() => undefined} />);
    await user.click(await screen.findByRole("switch", { name: "Read new answers automatically" }));
    expect(api.saveTtsRoles).toHaveBeenCalledWith({ answers: { autoRead: true } });
    await user.selectOptions(screen.getByLabelText("Answers"), "");
    expect(api.saveTtsRoles).toHaveBeenLastCalledWith({ answers: { model: null } });
  });

  it("lists providers like Transcription: edit one, hear it with its unsaved key, add another", async () => {
    const user = userEvent.setup();
    api.ttsSample.mockResolvedValue(new Blob(["x"]));
    api.updateTtsProvider.mockResolvedValue(overview());
    const two = overview();
    two.providers.push({ ...two.providers[0]!, id: "qwen-1a2b", name: "Qwen 2", keySet: false, keyHint: null, status: { state: "not_configured", summary: "Needs an API key.", check: null } });
    api.addTtsProvider.mockResolvedValue(two);
    render(<TtsSettings onNotify={() => undefined} />);
    await user.click(await screen.findByRole("button", { name: /Qwen\s+1 model/ }));
    await user.click(screen.getByRole("button", { name: "Replace" }));
    await user.type(screen.getByPlaceholderText("Paste your key"), "sk-new-5678");
    await user.click(screen.getByRole("button", { name: /hear a sample/i }));
    expect(api.ttsSample).toHaveBeenCalledWith({ providerId: "qwen", baseUrl: "https://dashscope.aliyuncs.com", apiKey: "sk-new-5678", model: "qwen3-tts-flash" });
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(api.updateTtsProvider).toHaveBeenCalledWith("qwen", expect.objectContaining({ apiKey: "sk-new-5678" }));
    await user.click(screen.getByRole("button", { name: /Add provider/ }));
    await user.click(screen.getByRole("button", { name: "Qwen" }));
    expect(api.addTtsProvider).toHaveBeenCalledWith({ kind: "qwen" });
    expect(await screen.findByText("Qwen 2")).toBeTruthy();
  });

  it("clears the saved recordings and says tables are described first", async () => {
    const user = userEvent.setup();
    api.clearTtsCache.mockResolvedValue(overview({ cache: { clips: 0, bytes: 0 } }));
    render(<TtsSettings onNotify={() => undefined} />);
    expect(await screen.findByText(/described in words by your language model/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(api.clearTtsCache).toHaveBeenCalled();
    expect(await screen.findByText("None yet.")).toBeTruthy();
  });
});
