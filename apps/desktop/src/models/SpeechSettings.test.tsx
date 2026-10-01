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
    { id: "sensevoice", name: "SenseVoice", kind: "sensevoice", baseUrl: "http://127.0.0.1:8765", keySet: false, keyHint: null, keySource: "saved", keyShared: false, keyOptional: true, note: "", models: [{ id: "sensevoice-small", ref: "sensevoice/sensevoice-small", label: "sensevoice-small" }], status: { state: "configured", summary: "1 model", check: null } },
    { id: "qwen", name: "Qwen", kind: "qwen", baseUrl: "https://maas.qianwenaiapi.com", keySet: true, keyHint: "1234", keySource: "saved", keyShared: false, keyOptional: false, note: "", models: [{ id: "qwen-audio-3.1-asr-flash", ref: "qwen/qwen-audio-3.1-asr-flash", label: "qwen-audio-3.1-asr-flash" }], status: { state: "configured", summary: "1 model", check: null } },
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

  it("shows each job with its model and no live switch", async () => {
    render(<SpeechSettings onNotify={() => undefined} />);
    const jobs = await screen.findByRole("group", { name: "Used for" });
    expect(within(jobs).queryAllByRole("switch")).toEqual([]);
    expect(screen.getByText("Qwen")).toBeTruthy();
  });

  it("changes one job without touching the other", async () => {
    api.saveSpeechRoles.mockResolvedValue(overview());
    render(<SpeechSettings onNotify={() => undefined} />);
    const select = await screen.findByLabelText("Ask dictation", { selector: "select" });
    await userEvent.selectOptions(select, "qwen/qwen-audio-3.1-asr-flash");
    expect(api.saveSpeechRoles).toHaveBeenCalledWith({ dictation: { model: "qwen/qwen-audio-3.1-asr-flash", stream: false, language: "" } });
  });
});
