import type { ServiceField, ServiceSettings as Service } from "@gunther/contracts";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fieldProblem, ServiceSettings } from "./ServiceSettings";

const api = vi.hoisted(() => ({ serviceSettings: vi.fn(), saveServiceSettings: vi.fn(), testServiceSettings: vi.fn() }));
vi.mock("../api", () => ({ knowledgeApi: api }));

const field = (overrides: Partial<ServiceField> & Pick<ServiceField, "key" | "label" | "kind">): ServiceField => ({
  help: "", placeholder: "", options: [], min: null, max: null, step: null, required: false, pattern: null,
  patternMessage: "", shownWhen: null, value: null, default: null, isSet: false, hint: null, source: "default",
  envVar: overrides.key.toUpperCase(), ...overrides,
});

const languageModel = (overrides: Partial<Service> = {}, key: Partial<ServiceField> = {}): Service => ({
  id: "language_model",
  title: "Language model",
  description: "Reads what you capture.",
  note: "",
  canTest: true,
  status: { state: "not_configured", summary: "No key yet.", checkedAt: null, check: null },
  fields: [
    field({ key: "llm_provider", label: "Provider", kind: "select", value: "deepseek", options: [
      { value: "deepseek", label: "DeepSeek", description: "", presets: { llm_base_url: "https://api.deepseek.com", llm_model: "deepseek-v4-flash" } },
      { value: "openai", label: "OpenAI", description: "", presets: { llm_base_url: "https://api.openai.com/v1", llm_model: "gpt-5.6" } },
      { value: "compatible", label: "Other", description: "Any OpenAI-compatible endpoint", presets: {} },
    ] }),
    field({ key: "llm_api_key", label: "API key", kind: "secret", ...key }),
    field({ key: "llm_base_url", label: "Base URL", kind: "url", required: true, value: "https://api.deepseek.com" }),
    field({ key: "llm_model", label: "Model", kind: "text", required: true, value: "deepseek-v4-flash", pattern: "[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}", patternMessage: "Use the model's id, without spaces." }),
  ],
  ...overrides,
});

const transcription: Service = {
  id: "transcription",
  title: "Transcription",
  description: "Turns speech into words while you record.",
  note: "",
  canTest: true,
  status: { state: "error", summary: "OpenAI is chosen, but OpenAI has no API key.", checkedAt: null, check: null },
  fields: [
    field({ key: "stt_provider", label: "Engine", kind: "select", value: "openai", options: [
      { value: "auto", label: "Automatic", description: "", presets: {} },
      { value: "sensevoice", label: "SenseVoice", description: "Private, on this device", presets: {} },
      { value: "openai", label: "OpenAI", description: "", presets: {} },
    ] }),
    field({ key: "sensevoice_url", label: "SenseVoice address", kind: "url", required: true, value: "http://127.0.0.1:8765", shownWhen: { key: "stt_provider", values: ["auto", "sensevoice"] } }),
  ],
};

const open = async (title: string) => {
  await userEvent.click(await screen.findByRole("button", { name: new RegExp(title) }));
};

describe("ServiceSettings", () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset());
    api.serviceSettings.mockResolvedValue({ persisted: true, services: [languageModel(), transcription] });
  });

  it("lists each service with its status", async () => {
    render(<ServiceSettings onNotify={vi.fn()} />);
    const model = await screen.findByRole("button", { name: /Language model/ });
    expect(within(model).getByText("Not set up")).toBeInTheDocument();
    expect(within(screen.getByRole("button", { name: /Transcription/ })).getByText("Needs attention")).toBeInTheDocument();
    expect(model).toHaveAttribute("aria-expanded", "false");
  });

  it("fills a provider's defaults, masks the key, and saves only what changed", async () => {
    const onNotify = vi.fn();
    const saved = languageModel({ status: { state: "configured", summary: "OpenAI · gpt-5.6", checkedAt: null, check: null } }, { isSet: true, hint: "1234" });
    api.saveServiceSettings.mockResolvedValue(saved);
    api.testServiceSettings.mockResolvedValue({ ok: true, message: "Connected. gpt-5.6 is available.", warning: null, checkedAt: "2026-09-29T10:00:00Z", service: { ...saved, status: { ...saved.status, checkedAt: "2026-09-29T10:00:00Z", check: { ok: true, message: "Connected.", warning: null } } } });
    render(<ServiceSettings onNotify={onNotify} />);
    await open("Language model");

    await userEvent.click(screen.getByRole("radio", { name: "OpenAI" }));
    expect(screen.getByLabelText("Base URL")).toHaveValue("https://api.openai.com/v1");
    expect(screen.getByLabelText("Model")).toHaveValue("gpt-5.6");

    const key = screen.getByLabelText("API key");
    expect(key).toHaveAttribute("type", "password");
    await userEvent.type(key, "sk-secret-000000001234");
    await userEvent.click(screen.getByRole("button", { name: "Show API key" }));
    expect(key).toHaveAttribute("type", "text");

    const checked = { ...saved, status: { ...saved.status, checkedAt: "2026-09-29T10:00:00Z", check: { ok: true, message: "Connected.", warning: null } } };
    api.serviceSettings.mockResolvedValue({ persisted: true, services: [checked, transcription] });
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(api.saveServiceSettings).toHaveBeenCalledWith("language_model", {
      llm_provider: "openai",
      llm_base_url: "https://api.openai.com/v1",
      llm_model: "gpt-5.6",
      llm_api_key: "sk-secret-000000001234",
    });
    // What was saved is tested right away, and its status follows.
    await waitFor(() => expect(api.testServiceSettings).toHaveBeenCalledWith("language_model", {}));
    expect(await screen.findByText("Connected. gpt-5.6 is available.")).toBeInTheDocument();
    expect(within(screen.getByRole("button", { name: /Language model/ })).getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("•••• 1234")).toBeInTheDocument();
    expect(onNotify).toHaveBeenCalledWith("Language model saved.");
  });

  it("keeps a typed model the user chose when the provider changes", async () => {
    render(<ServiceSettings onNotify={vi.fn()} />);
    await open("Language model");
    const model = screen.getByLabelText("Model");
    await userEvent.clear(model);
    await userEvent.type(model, "my-own-model");
    await userEvent.click(screen.getByRole("radio", { name: "Other" }));
    await userEvent.click(screen.getByRole("radio", { name: "OpenAI" }));
    expect(model).toHaveValue("my-own-model");
  });

  it("refuses values that cannot work before anything is sent", async () => {
    render(<ServiceSettings onNotify={vi.fn()} />);
    await open("Language model");
    const url = screen.getByLabelText("Base URL");
    await userEvent.clear(url);
    await userEvent.type(url, "api.deepseek.com");
    expect(screen.getByText("Use a full address starting with http:// or https://.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(api.saveServiceSettings).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(url).toHaveValue("https://api.deepseek.com");
  });

  it("tests unsaved values and shows why a connection failed", async () => {
    api.testServiceSettings.mockResolvedValue({ ok: false, message: "The service did not accept this API key.", warning: null, checkedAt: "2026-09-29T10:00:00Z", service: languageModel() });
    render(<ServiceSettings onNotify={vi.fn()} />);
    await open("Language model");
    await userEvent.type(screen.getByLabelText("API key"), "sk-wrong-00000000");
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(api.testServiceSettings).toHaveBeenCalledWith("language_model", { llm_api_key: "sk-wrong-00000000" });
    expect(await screen.findByRole("alert")).toHaveTextContent("The service did not accept this API key.");
    expect(api.saveServiceSettings).not.toHaveBeenCalled();
  });

  it("replaces or removes a saved key without ever showing it", async () => {
    api.serviceSettings.mockResolvedValue({ persisted: true, services: [languageModel({}, { isSet: true, hint: "1234", source: "environment" })] });
    api.saveServiceSettings.mockResolvedValue(languageModel());
    render(<ServiceSettings onNotify={vi.fn()} />);
    await open("Language model");
    expect(screen.getByText("•••• 1234")).toBeInTheDocument();
    expect(screen.getByText("from .env")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(screen.getByText("Removed when you save")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(api.saveServiceSettings).toHaveBeenCalledWith("language_model", { llm_api_key: "" });
  });

  it("shows fields only for the engine that uses them", async () => {
    render(<ServiceSettings onNotify={vi.fn()} />);
    await open("Transcription");
    expect(screen.queryByLabelText("SenseVoice address")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: "SenseVoice" }));
    expect(screen.getByLabelText("SenseVoice address")).toHaveValue("http://127.0.0.1:8765");
    expect(screen.getByText("Private, on this device")).toBeInTheDocument();
  });

  it("says when the service cannot be reached", async () => {
    api.serviceSettings.mockRejectedValue(new Error("Gunther’s local service isn’t responding."));
    render(<ServiceSettings onNotify={vi.fn()} />);
    expect(await screen.findByText("Gunther’s local service isn’t responding.")).toBeInTheDocument();
  });
});

describe("fieldProblem", () => {
  it("checks numbers against their range", () => {
    const seconds = field({ key: "s", label: "Segment", kind: "number", min: 1, max: 10 });
    expect(fieldProblem(seconds, 40)).toBe("Use a number from 1 to 10.");
    expect(fieldProblem(seconds, 3.2)).toBeNull();
  });
});
