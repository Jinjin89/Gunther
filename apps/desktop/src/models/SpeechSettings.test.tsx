import type { SpeechOverview } from "@gunther/contracts";
import { act, render, screen, within } from "@testing-library/react";
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
    // The service answers a save with the provider as saved (an empty label becomes the id).
    api.updateSpeechProvider.mockImplementation(async (_id: string, body: { baseUrl: string; models: { id: string; label: string }[] }) => {
      const next = overview();
      next.providers[1] = { ...next.providers[1]!, baseUrl: body.baseUrl, models: body.models.map((model) => ({ id: model.id, ref: `qwen/${model.id}`, label: model.label || model.id })) };
      return next;
    });
  });

  it("shows each job with its model and no live switch", async () => {
    render(<SpeechSettings onNotify={() => undefined} />);
    const jobs = await screen.findByRole("group", { name: "Used for" });
    expect(within(jobs).queryAllByRole("switch")).toEqual([]);
    expect(screen.getByText("Qwen")).toBeTruthy();
  });

  it("turns checking each minute again off from the Recording job, keeping its model", async () => {
    const withSwitch = overview();
    withSwitch.roles[0]!.checkAgain = true;
    withSwitch.roles[0]!.model = "qwen/qwen-audio-3.1-asr-flash";
    api.speechOverview.mockResolvedValue(withSwitch);
    api.saveSpeechRoles.mockResolvedValue(withSwitch);
    render(<SpeechSettings onNotify={() => undefined} />);
    expect(await screen.findByText("Sends the audio a second time for better text.")).toBeTruthy();
    const toggle = screen.getByRole("switch", { name: "Check each minute again" });
    expect(toggle).toBeChecked();
    await userEvent.click(toggle);
    expect(api.saveSpeechRoles).toHaveBeenCalledWith({ recording: { model: "qwen/qwen-audio-3.1-asr-flash", stream: true, language: "", check_again: false } });
  });

  it("shows the minute check as off and disabled for SenseVoice, with the reason", async () => {
    const withSwitch = overview();
    withSwitch.roles[0]!.checkAgain = true;
    api.speechOverview.mockResolvedValue(withSwitch);
    render(<SpeechSettings onNotify={() => undefined} />);
    expect(await screen.findByText(/SenseVoice can't use what was said before/)).toBeTruthy();
    const toggle = screen.getByRole("switch", { name: "Check each minute again" });
    expect(toggle).not.toBeChecked();
    expect(toggle).toBeDisabled();
  });

  it("changes one job without touching the other", async () => {
    api.saveSpeechRoles.mockResolvedValue(overview());
    render(<SpeechSettings onNotify={() => undefined} />);
    const select = await screen.findByLabelText("Ask dictation", { selector: "select" });
    await userEvent.selectOptions(select, "qwen/qwen-audio-3.1-asr-flash");
    expect(api.saveSpeechRoles).toHaveBeenCalledWith({ dictation: { model: "qwen/qwen-audio-3.1-asr-flash", stream: false, language: "" } });
  });

  it("saves each model change at once, so Test checks what is saved", async () => {
    api.testSpeechProvider.mockResolvedValue({ ok: true, message: "Current model checked.", warning: null });
    render(<SpeechSettings onNotify={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: /^Qwen/ }));
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Model id to add"), "provider-supplied-asr");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(api.updateSpeechProvider).toHaveBeenLastCalledWith("qwen", {
      name: "Qwen", baseUrl: "https://maas.qianwenaiapi.com",
      models: [{ id: "qwen-audio-3.1-asr-flash", label: "qwen-audio-3.1-asr-flash" }, { id: "provider-supplied-asr", label: "" }],
    });
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove qwen-audio-3.1-asr-flash" }));
    expect(api.updateSpeechProvider).toHaveBeenLastCalledWith("qwen", expect.objectContaining({ models: [{ id: "provider-supplied-asr", label: "provider-supplied-asr" }] }));
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(api.testSpeechProvider).toHaveBeenCalledWith("qwen", {
      name: "Qwen", baseUrl: "https://maas.qianwenaiapi.com",
      models: [{ id: "provider-supplied-asr", label: "provider-supplied-asr" }],
    });
    expect(await screen.findByText("Current model checked.")).toBeInTheDocument();
    // Closing and opening the provider again shows what was saved, not an older model.
    await userEvent.click(screen.getByRole("button", { name: /^Qwen/ }));
    await userEvent.click(screen.getByRole("button", { name: /^Qwen/ }));
    expect(screen.getByText("provider-supplied-asr", { selector: "code" })).toBeInTheDocument();
    expect(screen.queryByText("qwen-audio-3.1-asr-flash", { selector: "code" })).not.toBeInTheDocument();
  });

  it("saves the address when it is left, and keeps one that isn't an address on screen with its problem", async () => {
    render(<SpeechSettings onNotify={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: /^Qwen/ }));
    const address = screen.getByLabelText("Address");
    await userEvent.clear(address);
    await userEvent.type(address, "not an address");
    await userEvent.tab();
    expect(api.updateSpeechProvider).not.toHaveBeenCalled();
    expect(screen.getByText(/Use a full address/)).toBeInTheDocument();
    await userEvent.clear(address);
    await userEvent.type(address, "https://dashscope.aliyuncs.com{Enter}");
    expect(api.updateSpeechProvider).toHaveBeenCalledWith("qwen", expect.objectContaining({ baseUrl: "https://dashscope.aliyuncs.com" }));
  });

  it("discards a late test result when its model has been changed", async () => {
    let finish!: (value: unknown) => void;
    api.testSpeechProvider.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    render(<SpeechSettings onNotify={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: /^Qwen/ }));
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove qwen-audio-3.1-asr-flash" }));
    await act(async () => { finish({ ok: true, message: "Old model checked.", warning: null }); });
    expect(screen.queryByText("Old model checked.")).not.toBeInTheDocument();
  });
});
