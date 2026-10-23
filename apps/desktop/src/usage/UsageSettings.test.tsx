import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsPageV2 } from "../components/AtlasUtilities";
import { UsageSettings } from "./UsageSettings";
import type { UsageRequest, UsageSummary } from "./types";

const api = vi.hoisted(() => ({
  usageSummary: vi.fn(), usageRecent: vi.fn(), clearUsage: vi.fn(),
  modelsOverview: vi.fn(), speechOverview: vi.fn(), ttsOverview: vi.fn(),
  health: vi.fn(), pairedDevices: vi.fn(), mobileGatewayStatus: vi.fn(),
}));
vi.mock("../api", () => ({ knowledgeApi: api }));

const totals = { input: 1_200_000, cached: 812_000, output: 40_000, reasoning: 12_000, requests: 25 };
const empty: UsageSummary = { totals: { input: null, cached: null, output: null, reasoning: null, requests: 0 }, byDay: [], byModel: [], byJob: [] };
const summary = (): UsageSummary => ({
  totals,
  byDay: [{ ...totals, day: new Date().toLocaleDateString("en-CA") }],
  byModel: [
    { input: 100, cached: 0, output: 50, reasoning: 0, requests: 1, providerId: "local", modelId: "small" },
    { ...totals, providerId: "provider", modelId: "large" },
  ],
  byJob: ["other", "photos", "analysis", "outputs", "ask"].map((job) => ({ ...totals, job })),
});
const recent = (): UsageRequest[] => [
  { id: "one", at: "2026-10-23T10:20:30+00:00", providerId: "provider", modelId: "large", job: "outputs", detail: "slides", ref: null, input: 2000, cached: 1000, output: 400, reasoning: 100, status: "ok", ms: 1250 },
  { id: "two", at: "2026-10-23T10:19:30+00:00", providerId: "local", modelId: "small", job: "other", detail: null, ref: null, input: null, cached: null, output: null, reasoning: null, status: "unreported", ms: 20 },
  { id: "three", at: "2026-10-23T10:18:30+00:00", providerId: "local", modelId: "small", job: "ask", detail: "answer", ref: null, input: null, cached: null, output: null, reasoning: null, status: "error", ms: 0 },
];

describe("Usage settings", () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset());
    window.localStorage.clear();
    api.usageSummary.mockResolvedValue(summary());
    api.usageRecent.mockResolvedValue(recent());
    api.clearUsage.mockResolvedValue(undefined);
    api.modelsOverview.mockResolvedValue({ roles: [], providers: [] });
    api.speechOverview.mockResolvedValue({ roles: [] });
    api.ttsOverview.mockResolvedValue({ roles: [] });
    api.health.mockResolvedValue({ status: "ok" });
    api.pairedDevices.mockResolvedValue([]);
    api.mobileGatewayStatus.mockResolvedValue(null);
  });

  it("renders totals, sorted model and job tables, daily stacks and recent requests", async () => {
    const { container } = render(<UsageSettings />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading…");
    const modelTable = await screen.findByRole("table", { name: "By model" });
    const numbers = within(screen.getByLabelText("Usage totals"));
    for (const value of ["1.2M", "812k", "40k", "25", "68% of input", "incl. 12k reasoning"]) expect(numbers.getByText(value)).toBeInTheDocument();
    expect(within(modelTable).getAllByRole("rowheader").map((cell) => cell.textContent)).toEqual(["provider · large", "local · small"]);
    expect(within(modelTable).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Model", "Input", "Cached", "Output", "Reasoning", "Requests"]);
    expect(within(screen.getByRole("table", { name: "By job" })).getAllByRole("rowheader").map((cell) => cell.textContent)).toEqual(["Ask", "Outputs", "Analysis", "Photos", "Other"]);
    expect(screen.getByRole("img", { name: /Daily tokens/ })).toBeInTheDocument();
    expect(container.querySelectorAll("svg rect")).toHaveLength(3);
    expect(screen.getByRole("img", { name: /Daily tokens/ })).toHaveTextContent("Cached input 812k, Uncached input 388k, Output 40k");
    const requests = within(screen.getByRole("table", { name: "Recent requests" }));
    for (const value of ["Outputs · slides", "1.3 s", "20 ms", "0 ms", "Not reported", "Error"]) expect(requests.getByText(value)).toBeInTheDocument();
    expect(requests.getAllByRole("row")[1]).toHaveTextContent("2k (1k)");
    expect(api.usageRecent).toHaveBeenCalledWith(50);
  });

  it("keeps null counts unknown in totals, tables and the chart", async () => {
    const missing = { input: null, cached: null, output: null, reasoning: null, requests: 1 };
    const seeded = summary();
    api.usageSummary.mockResolvedValue({ totals: missing, byModel: [{ ...missing, providerId: "local", modelId: "small" }], byJob: [{ ...missing, job: "ask" }], byDay: [{ ...missing, day: seeded.byDay[0]!.day }] });
    const { container } = render(<UsageSettings />);
    const table = await screen.findByRole("table", { name: "By model" });
    expect(within(table).getAllByText("—")).toHaveLength(4);
    expect(within(screen.getByRole("table", { name: "By job" })).getAllByText("—")).toHaveLength(4);
    const numbers = within(screen.getByLabelText("Usage totals"));
    expect(numbers.getAllByText("—")).toHaveLength(3);
    expect(numbers.getByText("— of input")).toBeInTheDocument();
    expect(numbers.getByText("incl. — reasoning")).toBeInTheDocument();
    const request = within(screen.getByRole("table", { name: "Recent requests" })).getAllByRole("row")[2]!;
    expect(within(request).getAllByRole("cell")[3]).toHaveTextContent("— (—)");
    expect(within(request).getAllByRole("cell")[4]).toHaveTextContent("—");
    expect(container.querySelectorAll("svg rect")).toHaveLength(0);
    expect(container.querySelector(".usage-chart-missing")).toHaveTextContent("—");
  });

  it("uses known provider and model labels and falls back to ids for removed models", async () => {
    api.modelsOverview.mockResolvedValue({ providers: [
      { id: "provider", name: "My provider", models: [{ id: "large", label: "Fast model" }] },
      { id: "local", name: "Local server", models: [] },
    ] });
    render(<UsageSettings />);
    const models = await screen.findByRole("table", { name: "By model" });
    expect(within(models).getAllByRole("rowheader").map((cell) => cell.textContent)).toEqual(["My provider · Fast model", "local · small"]);
    const requests = within(screen.getByRole("table", { name: "Recent requests" }));
    expect(requests.getByText("My provider · Fast model")).toHaveAttribute("title", "My provider · Fast model");
    expect(requests.getAllByText("local · small")).toHaveLength(2);
  });

  it("still shows usage with model ids when the overview is unavailable", async () => {
    api.modelsOverview.mockRejectedValue(new Error("Models unavailable"));
    render(<UsageSettings />);
    const models = await screen.findByRole("table", { name: "By model" });
    expect(within(models).getByRole("rowheader", { name: "provider · large" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows short local times in 24-hour format, including midnight", async () => {
    api.usageRecent.mockResolvedValue([
      { ...recent()[0], at: new Date(2026, 9, 23, 0, 5, 45).toISOString() },
      { ...recent()[1], at: new Date(2026, 9, 23, 15, 30, 20).toISOString() },
    ]);
    render(<UsageSettings />);
    const requests = within(await screen.findByRole("table", { name: "Recent requests" }));
    expect(requests.getByText("Oct 23 · 00:05")).toBeInTheDocument();
    expect(requests.getByText("Oct 23 · 15:30")).toBeInTheDocument();
  });

  it.each([-330, 420])("refetches ranges with the local offset (%s) and hides today's chart", async (offset) => {
    vi.spyOn(Date.prototype, "getTimezoneOffset").mockReturnValue(offset);
    const user = userEvent.setup();
    render(<UsageSettings />);
    await screen.findByRole("table", { name: "By model" });
    expect(api.usageSummary).toHaveBeenLastCalledWith("7d", -offset);
    expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute("aria-pressed", "true");
    for (const [label, range] of [["Today", "today"], ["30 days", "30d"], ["All", "all"]] as const) {
      await user.click(screen.getByRole("button", { name: label }));
      await screen.findByRole("table", { name: "By model" });
      expect(api.usageSummary).toHaveBeenLastCalledWith(range, -offset);
      expect(screen.queryByRole("img", { name: /Daily tokens/ }) !== null).toBe(range !== "today");
    }
  });

  it("confirms before clearing, preserves cancelled history, and refreshes after deletion", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    render(<UsageSettings />);
    const clear = await screen.findByRole("button", { name: "Clear history" });
    await user.click(clear);
    expect(confirm).toHaveBeenCalledWith("Clear all usage history? This cannot be undone.");
    expect(api.clearUsage).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    api.clearUsage.mockImplementation(async () => {
      expect(confirm).toHaveBeenCalledTimes(2);
      api.usageSummary.mockResolvedValue(empty);
      api.usageRecent.mockResolvedValue([]);
    });
    await user.click(clear);
    expect(await screen.findByText("No model requests yet.")).toBeInTheDocument();
    expect(api.clearUsage).toHaveBeenCalledOnce();
    expect(api.usageSummary).toHaveBeenCalledTimes(2);
    expect(api.usageRecent).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows a load error and allows retrying", async () => {
    api.usageSummary.mockRejectedValueOnce(new Error("Service unavailable"));
    const user = userEvent.setup();
    render(<UsageSettings />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Service unavailable");
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("table", { name: "By model" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps history visible when clearing fails", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    api.clearUsage.mockRejectedValue(new Error("Could not clear history"));
    const user = userEvent.setup();
    render(<UsageSettings />);
    await user.click(await screen.findByRole("button", { name: "Clear history" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not clear history");
    expect(screen.getByRole("table", { name: "By model" })).toBeInTheDocument();
    expect(api.usageSummary).toHaveBeenCalledOnce();
  });

  it("ignores an old range response that arrives after a new one", async () => {
    let resolveOld!: (value: UsageSummary) => void;
    api.usageSummary.mockReturnValueOnce(new Promise<UsageSummary>((resolve) => { resolveOld = resolve; }));
    api.usageSummary.mockResolvedValue({ ...summary(), totals: { ...totals, input: 123 } });
    const user = userEvent.setup();
    render(<UsageSettings />);
    await user.click(screen.getByRole("button", { name: "Today" }));
    await screen.findByRole("table", { name: "By model" });
    await act(async () => resolveOld(summary()));
    expect(within(screen.getByLabelText("Usage totals")).getByText("123")).toBeInTheDocument();
  });

  it("keeps recent history available when the selected range has no requests", async () => {
    api.usageSummary.mockResolvedValue(empty);
    render(<UsageSettings />);
    expect(await screen.findByRole("table", { name: "Recent requests" })).toBeInTheDocument();
    expect(screen.queryByText("No model requests yet.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear history" })).toBeEnabled();
  });

  it("appears after Models in Settings and opens the real usage pane", async () => {
    window.localStorage.setItem("gunther:settings-pane", "usage");
    render(<SettingsPageV2 theme="light" onTheme={vi.fn()} onNotify={vi.fn()} />);
    const nav = within(screen.getByRole("navigation", { name: "Settings topics" }));
    const labels = nav.getAllByRole("button").map((button) => button.textContent);
    expect(labels.slice(1, 3)).toEqual(["Models", "Usage"]);
    expect(nav.getByRole("button", { name: "Usage" })).toHaveAttribute("aria-current", "page");
    const pane = screen.getByRole("region", { name: "Usage" });
    await waitFor(() => expect(within(pane).getByRole("table", { name: "By model" })).toBeInTheDocument());
  });
});
