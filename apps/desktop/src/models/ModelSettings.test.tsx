import type { ModelProvider, ModelsOverview } from "@gunther/contracts";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ModelSettings } from "./ModelSettings";

const api = vi.hoisted(() => ({
  modelsOverview: vi.fn(),
  saveModelRoles: vi.fn(),
  addProvider: vi.fn(),
  updateProvider: vi.fn(),
  removeProvider: vi.fn(),
  testProvider: vi.fn(),
}));
vi.mock("../api", () => ({ knowledgeApi: api }));

const deepseekLevels = [{ id: "off", label: "Off" }, { id: "low", label: "Low" }, { id: "high", label: "High" }, { id: "max", label: "Max" }] as const;

const deepseek: ModelProvider = {
  id: "deepseek",
  name: "DeepSeek",
  kind: "deepseek",
  baseUrl: "https://api.deepseek.com",
  keySet: true,
  keyHint: "1234",
  keySource: "environment",
  keyOptional: false,
  note: "",
  models: [
    { id: "deepseek-flash", ref: "deepseek/deepseek-flash", label: "Flash", vision: true, visionBuiltIn: true, dialect: "deepseek", dialectBuiltIn: "deepseek", levels: [...deepseekLevels] },
    { id: "deepseek-v4-pro", ref: "deepseek/deepseek-v4-pro", label: "V4 Pro", vision: false, visionBuiltIn: false, dialect: "deepseek", dialectBuiltIn: "deepseek", levels: [...deepseekLevels] },
  ],
  status: { state: "configured", summary: "2 models", check: null },
};

const overview = (patch: Partial<ModelsOverview> = {}): ModelsOverview => ({
  persisted: true,
  fromEnvironment: true,
  providers: [deepseek],
  roles: [
    { id: "analysis", label: "Analysis", description: "Reads what you capture.", needsVision: false, model: "deepseek/deepseek-flash", effort: "low", problem: null },
    { id: "ask", label: "Ask", description: "Answers questions.", needsVision: false, model: "deepseek/deepseek-flash", effort: "high", problem: null },
    { id: "photos", label: "Photos", description: "Looks at photos.", needsVision: true, model: "deepseek/deepseek-flash", effort: "low", problem: null },
  ],
  presets: [
    { kind: "deepseek", name: "DeepSeek", baseUrl: "https://api.deepseek.com", models: ["deepseek-flash"], keyOptional: false, note: "" },
    { kind: "kimi", name: "Kimi", baseUrl: "https://api.moonshot.ai/v1", models: ["kimi-k3"], keyOptional: false, note: "" },
    { kind: "compatible", name: "Self-hosted", baseUrl: "http://127.0.0.1:8000/v1", models: [], keyOptional: true, note: "" },
  ],
  dialects: [
    { id: "deepseek", label: "Thinking on/off, effort low/high/max (DeepSeek)", levels: [...deepseekLevels] },
    { id: "none", label: "No thinking setting", levels: [] },
  ],
  efforts: [],
  ...patch,
});

describe("ModelSettings", () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset());
    api.modelsOverview.mockResolvedValue(overview());
  });

  it("shows each job's model and effort, and saves a change at once", async () => {
    const onNotify = vi.fn();
    api.saveModelRoles.mockResolvedValue(overview());
    render(<ModelSettings onNotify={onNotify} />);
    const ask = await screen.findByRole("combobox", { name: "Ask" });
    expect(ask).toHaveValue("deepseek/deepseek-flash");
    expect(screen.getByText(/These come from the backend’s .env/)).toBeInTheDocument();
    await userEvent.selectOptions(ask, "deepseek/deepseek-v4-pro");
    expect(api.saveModelRoles).toHaveBeenCalledWith({ ask: { model: "deepseek/deepseek-v4-pro", effort: "high" } });
    const effort = screen.getByRole("combobox", { name: "Ask thinking effort" });
    expect(within(effort).getByRole("option", { name: "Medium (uses High)" })).toBeInTheDocument();
    await userEvent.selectOptions(effort, "max");
    expect(api.saveModelRoles).toHaveBeenLastCalledWith({ ask: { model: "deepseek/deepseek-flash", effort: "max" } });
  });

  it("fetches the models a provider offers and adds one", async () => {
    api.testProvider.mockResolvedValue({ ok: true, message: "Connected, and the key works.", warning: null, available: ["deepseek-flash", "deepseek-v4-pro", "deepseek-v5-preview"], overview: overview() });
    api.updateProvider.mockResolvedValue(overview());
    render(<ModelSettings onNotify={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: /^DeepSeek/ }));
    expect(screen.getByText("•••• 1234")).toBeInTheDocument();
    expect(screen.getByText("from .env")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Fetch models" }));
    expect(api.testProvider).toHaveBeenCalledWith("deepseek", { baseUrl: "https://api.deepseek.com" });
    expect(await screen.findByText(/3 models offered/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "deepseek-v5-preview" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(api.updateProvider).toHaveBeenCalledWith("deepseek", {
      name: "DeepSeek",
      baseUrl: "https://api.deepseek.com",
      models: [
        { id: "deepseek-flash", label: "Flash", vision: null, dialect: null },
        { id: "deepseek-v4-pro", label: "V4 Pro", vision: null, dialect: null },
        { id: "deepseek-v5-preview", label: "", vision: null, dialect: null },
      ],
    });
  });

  it("replaces a key without ever showing the saved one", async () => {
    api.updateProvider.mockResolvedValue(overview());
    render(<ModelSettings onNotify={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: /^DeepSeek/ }));
    await userEvent.click(screen.getByRole("button", { name: "Replace" }));
    const key = screen.getByPlaceholderText("Paste your key");
    expect(key).toHaveAttribute("type", "password");
    await userEvent.type(key, "sk-new-0000000000009999");
    await userEvent.click(screen.getByRole("button", { name: "Show API key" }));
    expect(key).toHaveAttribute("type", "text");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(api.updateProvider).toHaveBeenCalledWith("deepseek", expect.objectContaining({ apiKey: "sk-new-0000000000009999" }));
  });

  it("refuses an address that cannot work", async () => {
    render(<ModelSettings onNotify={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: /^DeepSeek/ }));
    const url = screen.getByLabelText("Base URL");
    await userEvent.clear(url);
    await userEvent.type(url, "api.deepseek.com");
    expect(screen.getByText("Use a full address starting with http:// or https://.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("adds a provider from its preset and opens it", async () => {
    const kimi: ModelProvider = { ...deepseek, id: "kimi", name: "Kimi", kind: "kimi", keySet: false, keyHint: null, keySource: "none", models: [], status: { state: "not_configured", summary: "Needs an API key.", check: null } };
    api.addProvider.mockResolvedValue(overview({ providers: [deepseek, kimi], fromEnvironment: false }));
    render(<ModelSettings onNotify={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: /Add provider/ }));
    await userEvent.click(screen.getByRole("button", { name: "Kimi" }));
    expect(api.addProvider).toHaveBeenCalledWith({ kind: "kimi" });
    expect(await screen.findByRole("button", { name: /^Kimi/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByPlaceholderText("Paste your key")).toBeInTheDocument();
  });
});
