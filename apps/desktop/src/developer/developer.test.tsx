import type { AnswerTrace as Trace } from "@gunther/contracts";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AnswerTrace } from "./AnswerTrace";
import { DeveloperSettings } from "./DeveloperSettings";

const api = vi.hoisted(() => ({
  messageTrace: vi.fn(),
  developerSettings: vi.fn(),
  saveDeveloperSettings: vi.fn(),
  developerLogs: vi.fn(),
  developerJobs: vi.fn(),
}));
vi.mock("../api", () => ({ knowledgeApi: api }));

const trace: Trace = {
  version: 1,
  totalMs: 2400,
  steps: [
    {
      kind: "plan", label: "Deciding what to look up", atMs: 0, ms: 600, action: "search_library", query: "CD3D",
      children: [{ kind: "model", label: "DeepSeek · Flash", atMs: 1, ms: 590, model: "deepseek/deepseek-flash", effort: "off", system: "You plan searches. Reply with one JSON object.", messages: [{ role: "user", content: "Question: What marks T cells?" }], reply: "{\"action\":\"search_library\"}", usage: { prompt_tokens: 812, completion_tokens: 21 } }],
    },
    { kind: "search", label: "Searched your library for “CD3D”", atMs: 610, results: [{ title: "Markers", kind: "library", where: "p. 2", text: "CD3D is a marker of T cells." }] },
    { kind: "write", label: "Writing the answer", atMs: 700, ms: 1600, style: "balanced", sources: ["[1] Markers"] },
    { kind: "cite", label: "Numbering the sources in reading order", atMs: 2390, sources: ["[1] Markers (pool 1)"], uncited: [] },
  ],
};

describe("the Trace tab", () => {
  beforeEach(() => Object.values(api).forEach((mock) => mock.mockReset()));

  it("shows each step and, opened, the exact prompt and reply", async () => {
    const user = userEvent.setup();
    const onCopy = vi.fn();
    api.messageTrace.mockResolvedValue(trace);
    render(<AnswerTrace sessionId="ses_1" messageId="msg_1" onCopy={onCopy} />);
    expect(await screen.findByText("4 steps")).toBeTruthy();
    expect(api.messageTrace).toHaveBeenCalledWith("ses_1", "msg_1");
    expect(screen.getByText("search_library “CD3D”")).toBeTruthy();
    expect(screen.getByText("1 found")).toBeTruthy();
    expect(screen.getByText("deepseek/deepseek-flash · off · 812 → 21 tokens")).toBeTruthy();
    await user.click(screen.getByText("DeepSeek · Flash"));
    expect(screen.getByText("You plan searches. Reply with one JSON object.")).toBeTruthy();
    expect(screen.getByText("Question: What marks T cells?")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /Copy/ }));
    expect(onCopy).toHaveBeenCalledWith("Trace", JSON.stringify(trace, null, 2));
  });

  it("says why there is none", async () => {
    api.messageTrace.mockRejectedValue(new Error("No trace was kept for this answer. Turn on “Keep how answers are made” in Settings → Developer, then ask again."));
    render(<AnswerTrace sessionId="ses_1" messageId="msg_1" onCopy={vi.fn()} />);
    expect(await screen.findByText(/No trace was kept/)).toBeTruthy();
  });
});

describe("Settings → Developer", () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset());
    api.developerSettings.mockResolvedValue({ traces: false });
    api.saveDeveloperSettings.mockResolvedValue({ traces: true });
    api.developerLogs.mockResolvedValue([{ at: "2026-09-30T08:00:00Z", level: "warning", logger: "gunther.processing", message: "Knowledge job failed (RuntimeError)" }]);
    api.developerJobs.mockResolvedValue([{ id: "job_1", kind: "digest", state: "failed", attempts: 3, error: "The model could not write the summary.", sourceId: "src_1", sourceTitle: "Markers", createdAt: "2026-09-30T08:00:00Z", updatedAt: "2026-09-30T08:01:00Z" }]);
  });

  it("turns traces on and shows recent lines and jobs", async () => {
    const user = userEvent.setup();
    const onNotify = vi.fn();
    render(<DeveloperSettings onNotify={onNotify} />);
    expect(await screen.findByText("Knowledge job failed (RuntimeError)")).toBeTruthy();
    expect(api.developerLogs).toHaveBeenCalledWith("warning");
    expect(screen.getByText("Summary")).toBeTruthy();
    expect(screen.getByText("failed · 3 tries")).toBeTruthy();
    await user.click(screen.getByRole("switch", { name: "Keep how answers are made" }));
    expect(api.saveDeveloperSettings).toHaveBeenCalledWith({ traces: true });
    await waitFor(() => expect(onNotify).toHaveBeenCalledWith(expect.stringMatching(/choose Trace/)));
    await user.selectOptions(screen.getByLabelText("Which lines"), "info");
    await waitFor(() => expect(api.developerLogs).toHaveBeenLastCalledWith("info"));
  });

  it("turns making outputs with skills off, keeping the traces switch as it is", async () => {
    const user = userEvent.setup();
    const onNotify = vi.fn();
    api.developerSettings.mockResolvedValue({ traces: false, outputSkills: true });
    api.saveDeveloperSettings.mockResolvedValue({ traces: false, outputSkills: false });
    render(<DeveloperSettings onNotify={onNotify} />);
    const skills = await screen.findByRole("switch", { name: "Make outputs with skills" });
    await waitFor(() => expect((skills as HTMLInputElement).checked).toBe(true));
    await user.click(skills);
    expect(api.saveDeveloperSettings).toHaveBeenCalledWith({ traces: false, outputSkills: false });
    await waitFor(() => expect((skills as HTMLInputElement).checked).toBe(false));
    expect(onNotify).toHaveBeenCalledWith(expect.stringMatching(/without a skill/));
  });
});
