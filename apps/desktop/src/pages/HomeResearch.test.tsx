import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { forgetAskSkills } from "../components/search/skills";
import { HomePage } from "./HomePage";

const api = vi.hoisted(() => ({
  sources: vi.fn(),
  search: vi.fn(),
  askSkills: vi.fn(),
  sessions: vi.fn(),
  createSession: vi.fn(),
  sendMessageStream: vi.fn(),
}));
vi.mock("../api", async (importOriginal) => ({ ...(await importOriginal<typeof import("../api")>()), knowledgeApi: api }));
vi.mock("../speech/readAloud", async (importOriginal) => ({ ...(await importOriginal<typeof import("../speech/readAloud")>()), readAloud: { auto: vi.fn(), focusSession: vi.fn() } }));

const skills = [{ command: "research", title: "Deep research", description: "Research a question in depth.", budgets: ["standard", "deep"] }];
const context = { sourcesConsidered: 0, assertionsConsidered: 0, verifiedAssertions: 0, retrievalMode: "all", responderMode: "model", modelLabel: "DeepSeek", skill: { name: "research", version: 1, title: "Deep research", auto: false } };
const turn = (research: object | null, content: string, first = 1) => ({
  session: { id: "ses_home", knowledgeBaseId: "@home", messageCount: 2 },
  userMessage: { id: `m${first}`, sessionId: "ses_home", role: "user", content: "Compare the options.", citations: [], context: {}, createdAt: "2026-10-02T08:00:00.000Z" },
  assistantMessage: { id: `m${first + 1}`, sessionId: "ses_home", role: "assistant", content, citations: [], context: { ...context, research }, createdAt: "2026-10-02T08:00:01.000Z" },
});
const asking = { state: "asking", budget: "deep", used: { searches: 0, reads: 0 }, limits: { searches: 16, reads: 8 }, coreQuestion: "", doneWhen: "" };

const renderHome = () => render(<HomePage bases={[]} inboxCount={0} onCapture={vi.fn()} onOpenBase={vi.fn()} onOpenChapter={vi.fn()} onAskBase={vi.fn()} onOpenNote={vi.fn()} onOpenLibraries={vi.fn()} onCreateBase={vi.fn()} onOpenInbox={vi.fn()} onNotify={vi.fn()} resolveWorkspaceId={vi.fn().mockResolvedValue("wsp")} />);
const box = () => screen.getByRole("textbox", { name: "Search your knowledge or the web" });

describe("research from Home", () => {
  beforeEach(() => {
    window.localStorage.clear();
    forgetAskSkills();
    Object.values(api).forEach((mock) => mock.mockReset());
    api.sources.mockResolvedValue([]);
    api.search.mockResolvedValue([]);
    api.askSkills.mockResolvedValue(skills);
    api.sessions.mockResolvedValue([]);
    api.createSession.mockResolvedValue({ id: "ses_home", knowledgeBaseId: "@home", messages: [], messageCount: 0 });
  });

  it("asks with the depth chosen", async () => {
    const user = userEvent.setup();
    api.sendMessageStream.mockResolvedValue(turn(null, "An answer."));
    renderHome();
    await user.type(box(), "/res{Enter}");
    await user.click(await screen.findByRole("button", { name: "Deep" }));
    await user.type(box(), "Compare the options.{Enter}");
    await waitFor(() => expect(api.sendMessageStream).toHaveBeenCalled());
    expect(api.sendMessageStream.mock.calls[0]![1]).toMatchObject({ content: "Compare the options.", skill: "research", budget: "deep" });
  });

  it("puts the research chip back, at the same depth, when it asked something first", async () => {
    const user = userEvent.setup();
    api.sendMessageStream.mockResolvedValueOnce(turn(asking, "Which options?")).mockResolvedValue(turn({ ...asking, state: "done" }, "Done.", 3));
    renderHome();
    await user.type(box(), "/res{Enter}");
    await user.click(await screen.findByRole("button", { name: "Deep" }));
    await user.type(box(), "Compare the options.{Enter}");
    expect(await screen.findByText("Which options?")).toBeVisible();
    // The chip was cleared when the question went, and is back for the answer to it.
    expect(await screen.findByRole("button", { name: "Remove /research" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Deep" })).toHaveAttribute("aria-pressed", "true");
    await user.type(box(), "Cool roofs and green roofs{Enter}");
    await waitFor(() => expect(api.sendMessageStream).toHaveBeenCalledTimes(2));
    expect(api.sendMessageStream.mock.calls[1]![1]).toMatchObject({ skill: "research", budget: "deep" });
  });

  it("leaves the chip off after a research answer that did not ask", async () => {
    const user = userEvent.setup();
    api.sendMessageStream.mockResolvedValue(turn({ ...asking, state: "done" }, "An answer."));
    renderHome();
    await user.type(box(), "/res{Enter}");
    await user.type(box(), "Compare the options.{Enter}");
    expect(await screen.findByText("An answer.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Remove /research" })).not.toBeInTheDocument();
  });
});
