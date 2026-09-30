import type { SpeechOverview } from "@gunther/contracts";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SpeechSettings } from "./SpeechSettings";

const api = vi.hoisted(() => ({
  speechOverview: vi.fn(),
  saveSpeechRoles: vi.fn(),
  addSpeechProvider: vi.fn(),
  updateSpeechProvider: vi.fn(),
  removeSpeechProvider: vi.fn(),
  testSpeechProvider: vi.fn(),
}));
vi.mock("../api", () => ({ knowledgeApi: api }));

const overview = (patch: Partial<SpeechOverview> = {}): SpeechOverview => ({
  persisted: true,
  fromEnvironment: false,
  providers: [
    { id: "sensevoice", name: "SenseVoice", kind: "sensevoice", baseUrl: "http://127.0.0.1:8765", keySet: false, keyHint: null, keySource: "saved", keyOptional: true, note: "", models: [{ id: "sensevoice-small", ref: "sensevoice/sensevoice-small", label: "sensevoice-small" }], status: { state: "configured", summary: "1 model", check: null } },
    { id: "qwen", name: "Qwen", kind: "qwen", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", keySet: true, keyHint: "1234", keySource: "saved", keyOptional: false, note: "", models: [{ id: "qwen3-asr-flash", ref: "qwen/qwen3-asr-flash", label: "qwen3-asr-flash" }], status: { state: "configured", summary: "1 model", check: null } },
  ],
  roles: [
    { id: "recording", label: "Recording", description: "Writes the words while you record.", model: "sensevoice/sensevoice-small", stream: true, language: "", problem: null },
    { id: "dictation", label: "Ask dictation", description: "Writes the words when you speak into Ask.", model: "sensevoice/sensevoice-small", stream: false, language: "", problem: null },
  ],
  presets: [{ kind: "openai", name: "OpenAI", baseUrl: "https://api.openai.com/v1", models: ["whisper-1"], keyOptional: false, note: "" }],
  ...patch,
});

describe("SpeechSettings", () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset());
    api.speechOverview.mockResolvedValue(overview());
  });

  it("shows each job with its model and whether it works live", async () => {
    render(<SpeechSettings onNotify={() => undefined} />);
    const jobs = await screen.findByRole("group", { name: "Used for" });
    expect(within(jobs).getAllByRole("switch", { name: "Live" })).toBeTruthy();
    const switches = within(jobs).getAllByRole("switch");
    expect(switches.map((item) => (item as HTMLInputElement).checked)).toEqual([true, false]);
    expect(screen.getByText("Qwen")).toBeTruthy();
  });

  it("changes one job without touching the other", async () => {
    api.saveSpeechRoles.mockResolvedValue(overview());
    render(<SpeechSettings onNotify={() => undefined} />);
    const select = await screen.findByLabelText("Ask dictation", { selector: "select" });
    await userEvent.selectOptions(select, "qwen/qwen3-asr-flash");
    expect(api.saveSpeechRoles).toHaveBeenCalledWith({ dictation: { model: "qwen/qwen3-asr-flash", stream: false, language: "" } });
  });

  it("turns live on for Ask dictation", async () => {
    api.saveSpeechRoles.mockResolvedValue(overview());
    render(<SpeechSettings onNotify={() => undefined} />);
    const jobs = await screen.findByRole("group", { name: "Used for" });
    await userEvent.click(within(jobs).getAllByRole("switch")[1]!);
    expect(api.saveSpeechRoles).toHaveBeenCalledWith({ dictation: { model: "sensevoice/sensevoice-small", stream: true, language: "" } });
  });
});
