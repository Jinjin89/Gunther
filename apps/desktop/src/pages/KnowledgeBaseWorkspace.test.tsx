import type {
  Artifact, ArtifactConversationDetail, ArtifactTurnOut, BuildTurnInput, ContentsSource, KnowledgeSession,
  LibraryContents, ModelMenu, OutputScope, OutputType, SessionMessage,
} from "@gunther/contracts";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiRequestError, knowledgeApi } from "../api";
import { announceModelsChanged } from "../models/askModel";
import { libraryScope } from "../outputs/artifactConversations";
import { makeBase, makeSource } from "../test/fixtures";
import { KnowledgeBaseWorkspace } from "./KnowledgeBaseWorkspace";

vi.mock("../components/markdown/MarkdownEditor", () => ({
  MarkdownEditor: ({ value, onChange, ariaLabel }: { value: string; onChange: (value: string) => void; ariaLabel: string }) =>
    <textarea value={value} aria-label={ariaLabel} onChange={(event) => onChange(event.target.value)} />,
}));

const ws = "workspace";
const base = makeBase({ id: "study", title: "Reading group" });
const at = "2026-10-01T00:05:00.000Z";
const selected = (...sourceIds: string[]): OutputScope => ({ ...libraryScope(), mode: "selection", sourceIds });
const source = (id: string, title: string): ContentsSource => ({
  ...makeSource({ id, title, kind: "note" }), durationSeconds: null, authors: null, year: null,
  pages: null, mediaType: null, host: null, processing: "ready",
});
const contents: LibraryContents = {
  sources: [source("s1", "Lab notes"), source("s2", "Field notes"), source("s3", "Review notes")],
  sections: [
    { id: "methods", parentId: null, title: "Methods", description: "", position: 0, version: 1, passageCount: 0 },
    { id: "fieldwork", parentId: "methods", title: "Fieldwork", description: "", position: 1, version: 1, passageCount: 0 },
  ],
  placements: [{ sourceId: "s1", sectionId: "methods", position: 0 }, { sourceId: "s2", sectionId: "fieldwork", position: 0 }],
};
const report: OutputType = { id: "report", label: "Report", shape: "report", description: "A written report", icon: "FileText", options: ["focus", "length"], partLimit: 8 };
const slides: OutputType = { id: "slides", label: "Slides", shape: "slides", description: "A slide deck", icon: "Presentation", options: ["focus", "use"], partLimit: 14 };
const readyModels: ModelMenu = {
  models: [{ ref: "local/model", provider: "Local", label: "Model", display: "Local Model", vision: false, levels: [] }],
  default: { model: "local/model", effort: "high" }, efforts: [],
};
const originalSession: KnowledgeSession = {
  id: "chat-1", title: "Original discussion", knowledgeBaseId: base.id, summary: "", messageCount: 0, messages: [],
  focusChapterId: "methods", selectedSourceIds: ["s3"], pinned: false, archived: false, parentSessionId: null,
  branchedFromMessageId: null, createdAt: at, updatedAt: at,
};
const secondSession: KnowledgeSession = { ...originalSession, id: "chat-2", title: "History discussion", selectedSourceIds: ["s1"] };

function version(changes: Partial<Artifact> = {}): Artifact {
  return {
    id: "version-1", lineageId: "lineage", conversationId: "earlier", workspaceId: ws, knowledgeBaseId: base.id,
    versionNumber: 1, supersedesArtifactId: null, format: "report", style: "report", kind: "report", origin: "build",
    outputType: "Report", audience: "student", title: "Field findings", createdAt: at,
    content: "# Field findings\n\n## Observations\n\nConditions changed after midday.", contentHash: "hash", manifestHash: "seal",
    unitSnapshots: [], acceptedUnitIds: [], unitCount: 0, brief: "Observations", modelLabel: null,
    provenance: {
      acceptedOnly: false, acceptedUnitIds: [], schemaVersion: 2, generator: "gunther.output-agents.v1",
      workspaceId: ws, knowledgeBaseId: base.id, knowledgeBaseQuestion: base.question, revisionIds: [],
    },
    outline: [{ heading: "Observations", goal: "Review the records" }], sections: [], citations: [],
    scope: selected("s1"), inputs: { sources: 1, sessions: 0, units: 0 }, ...changes,
  };
}
function conversation(id: string, changes: Partial<ArtifactConversationDetail> = {}): ArtifactConversationDetail {
  return {
    id, title: "New conversation", scope: libraryScope(), updatedAt: at, createdAt: at, artifactCount: 0,
    waiting: false, outputType: null, shape: null, lineageId: null, lastTurn: null, messages: [],
    brief: { goal: null, audience: null, constraints: [], open: [] }, works: [], active: null, pending: null, archived: false, ...changes,
  };
}
const done = (artifact: Artifact | null = null): ArtifactTurnOut => ({
  outcome: artifact ? "saved" : "replied", artifact, reply: "Done.", changes: [], question: null, message: null,
});

function setup({ artifacts = [], conversations = [] }: { artifacts?: Artifact[]; conversations?: ArtifactConversationDetail[] } = {}) {
  const stored = new Map(conversations.map((item) => [item.id, item]));
  const create = vi.spyOn(knowledgeApi, "createArtifactConversation").mockImplementation(async (_base, payload) => {
    const detail = conversation(payload.id!, { scope: payload.scope ?? libraryScope() });
    stored.set(detail.id, detail);
    return detail;
  });
  const patch = vi.spyOn(knowledgeApi, "updateArtifactConversation").mockImplementation(async (_base, id, patch) => {
    const previous = stored.get(id)!;
    const detail = { ...previous, refinementScopes: { ...previous.refinementScopes, [patch.targetLineageId!]: patch.scope! } };
    stored.set(id, detail);
    return detail;
  });
  vi.spyOn(knowledgeApi, "artifactConversations").mockImplementation(async () => [...stored.values()]);
  vi.spyOn(knowledgeApi, "artifactConversation").mockImplementation(async (_base, id) => {
    const detail = stored.get(id);
    if (!detail) throw new ApiRequestError("No such conversation", 404);
    return detail;
  });
  vi.spyOn(knowledgeApi, "artifacts").mockImplementation(async () => [...artifacts]);
  vi.spyOn(knowledgeApi, "artifact").mockImplementation(async (_base, id) => artifacts.find((item) => item.id === id)!);
  vi.spyOn(knowledgeApi, "followArtifactTurn").mockResolvedValue(null);
  const turn = vi.spyOn(knowledgeApi, "artifactTurnStream").mockResolvedValue(done());
  const view = render(<KnowledgeBaseWorkspace base={base} workspaceId={ws} onOpenSource={vi.fn()} onNotify={vi.fn()} onAdd={vi.fn()} />);
  return { view, create, patch, turn, artifacts };
}

beforeEach(() => {
  window.localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async () => Response.json([])));
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  vi.spyOn(knowledgeApi, "contents").mockResolvedValue(contents);
  vi.spyOn(knowledgeApi, "sessions").mockResolvedValue([originalSession, secondSession]);
  vi.spyOn(knowledgeApi, "session").mockImplementation(async (id) => id === secondSession.id ? secondSession : originalSession);
  vi.spyOn(knowledgeApi, "followAnswer").mockResolvedValue(null);
  vi.spyOn(knowledgeApi, "modelMenu").mockResolvedValue(readyModels);
  vi.spyOn(knowledgeApi, "outputTypes").mockResolvedValue([report, slides]);
  vi.spyOn(knowledgeApi, "sourceDigest").mockResolvedValue({ sourceId: "s1", state: "off", stale: false, method: null, offReason: "no_key", error: null, digest: null });
});

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("renders each catalog icon on its Studio tile", async () => {
  // Icon names served by the backend's output_types.py catalog.
  const catalog: [OutputType, string][] = [
    [report, "lucide-file-text"],
    [{ ...report, id: "briefing", label: "Briefing doc", icon: "ClipboardList" }, "lucide-clipboard-list"],
    [{ ...report, id: "study_guide", label: "Study guide", icon: "BookOpen" }, "lucide-book-open"],
    [{ ...report, id: "faq", label: "FAQ", icon: "CircleHelp" }, "lucide-circle-question-mark"],
    [{ ...report, id: "timeline", label: "Timeline", icon: "CalendarDays" }, "lucide-calendar-days"],
    [slides, "lucide-presentation"],
  ];
  vi.mocked(knowledgeApi.outputTypes).mockResolvedValue(catalog.map(([type]) => type));
  setup();
  const studio = within(screen.getByRole("complementary", { name: "Studio" }));
  for (const [type, iconClass] of catalog) {
    const tile = await studio.findByRole("button", { name: type.label });
    expect(tile.querySelector("svg")).toHaveClass(iconClass);
  }
});

it("disables Studio builds until Models settings provide a model", async () => {
  const user = userEvent.setup();
  vi.mocked(knowledgeApi.modelMenu).mockResolvedValue({ models: [], default: null, efforts: [] });
  const { create, turn } = setup();
  const studio = within(screen.getByRole("complementary", { name: "Studio" }));
  expect(await studio.findByText("Studio needs a model.")).toBeVisible();
  expect(studio.getByRole("button", { name: "Open Models settings" })).toBeEnabled();
  for (const name of ["Report", "Report options", "Slides", "Slides options"]) {
    expect(studio.getByRole("button", { name })).toBeDisabled();
  }
  await user.click(studio.getByRole("button", { name: "Report" }));
  expect(create).not.toHaveBeenCalled();
  expect(turn).not.toHaveBeenCalled();

  vi.mocked(knowledgeApi.modelMenu).mockResolvedValue(readyModels);
  act(() => announceModelsChanged());
  await waitFor(() => expect(studio.getByRole("button", { name: "Report" })).toBeEnabled());
  expect(studio.queryByText("Studio needs a model.")).toBeNull();
});

it("shares all sources, blocks an empty selection, and uses section ticks for each Ask turn", async () => {
  const user = userEvent.setup();
  const send = vi.spyOn(knowledgeApi, "sendMessageStream").mockImplementation(async (id, request) => {
    const session = id === secondSession.id ? secondSession : originalSession;
    const question: SessionMessage = { id: "question", sessionId: id, role: "user", content: request.content, citations: [], context: { sourcesConsidered: 0, assertionsConsidered: 0, verifiedAssertions: 0, retrievalMode: "all", responderMode: "model" }, createdAt: at };
    return { session, userMessage: question, assistantMessage: { ...question, id: "answer", role: "assistant", content: "Here are the findings." } };
  });
  vi.spyOn(knowledgeApi, "refreshBrief").mockResolvedValue({ goal: "", constraints: [], settled: [], open: [], edited: [], error: null });
  const { turn } = setup();
  const wholeLibrary = await screen.findByRole("checkbox", { name: "All sources" });
  const field = await screen.findByRole("checkbox", { name: "Select Field notes" });
  const button = screen.getByRole("button", { name: "Send message" });
  const draft = screen.getByRole("textbox", { name: "Message Gunther" });
  expect(wholeLibrary).toBeChecked();
  expect(field).toBeChecked();
  const newSource = source("new", "New notes");
  vi.mocked(knowledgeApi.contents).mockResolvedValue({ ...contents, sources: [...contents.sources, newSource] });
  act(() => { window.dispatchEvent(new Event("gunther:sources-updated")); });
  expect(await screen.findByRole("checkbox", { name: "Select New notes" })).toBeChecked();

  await user.type(draft, "Check all the records");
  await user.click(button);
  expect(send).toHaveBeenLastCalledWith(originalSession.id, expect.objectContaining({ selectedSourceIds: [], focusChapterId: null }), expect.any(Function), expect.any(AbortSignal));
  await screen.findByText("Here are the findings.");
  await user.click(wholeLibrary);
  expect(screen.getByText("Select at least one source")).toBeVisible();
  await user.type(draft, "Check the selected records{Enter}");
  expect(button).toBeDisabled();
  expect(screen.getByRole("button", { name: "Report" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Report options" })).toBeDisabled();
  expect(send).toHaveBeenCalledTimes(1);
  expect(turn).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "Collapse Fieldwork" }));
  await user.click(screen.getByRole("checkbox", { name: "Select sources in Methods" }));
  await user.click(screen.getByRole("button", { name: "Expand Fieldwork" }));
  expect(field).not.toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "Select Field notes" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Select Review notes" })).not.toBeChecked();
  await user.click(screen.getByRole("button", { name: "Show conversations" }));
  await user.click(within(screen.getByRole("dialog", { name: "Session history" })).getByRole("button", { name: /^History discussion/ }));
  await waitFor(() => expect(button).toBeEnabled());
  await user.click(button);
  expect(send).toHaveBeenLastCalledWith(secondSession.id, expect.objectContaining({ selectedSourceIds: ["s1", "s2"], focusChapterId: null }), expect.any(Function), expect.any(AbortSignal));
  expect(JSON.parse(window.localStorage.getItem(`gunther:library-sources:${base.id}`)!)).toEqual({ mode: "selected", sourceIds: ["s1", "s2"] });
});

it("opens the Chat brief, answer sources, and trace in place", async () => {
  const user = userEvent.setup();
  const answer: SessionMessage = {
    id: "answer-with-evidence", role: "assistant", sessionId: originalSession.id, content: "The field records support that finding.", createdAt: at,
    context: { sourcesConsidered: 1, assertionsConsidered: 1, verifiedAssertions: 1, retrievalMode: "all", responderMode: "model" },
    citations: [{
      id: "citation", kind: "web", url: "https://example.org/field", sourceId: "", sourceTitle: "Field log", assertionId: null,
      quote: "A recorded observation.", locator: "example.org", status: "provisional", confidence: 0, ref: 1,
    }],
  };
  vi.mocked(knowledgeApi.session).mockResolvedValue({
    ...originalSession, messages: [answer], messageCount: 1,
    brief: { goal: "Summarize the field records", constraints: [], settled: [], open: [], edited: [], error: null },
  });
  vi.spyOn(knowledgeApi, "developerSettings").mockResolvedValue({ traces: true });
  const readTrace = vi.spyOn(knowledgeApi, "messageTrace").mockResolvedValue({ version: 1, totalMs: 12, steps: [] });
  setup();

  const brief = await screen.findByText("Brief", { selector: "summary" });
  await user.click(brief);
  expect(screen.getByText("Summarize the field records")).toBeVisible();
  await user.click(brief);
  await user.click(screen.getByText("1 source", { selector: "summary" }));
  await user.click(screen.getByRole("button", { name: "Field log" }));
  const evidence = screen.getByRole("complementary", { name: "Evidence" });
  expect(evidence).toHaveTextContent("A recorded observation.");
  await user.click(within(evidence).getByRole("button", { name: "Close evidence" }));
  await user.click(screen.getByRole("button", { name: "Trace" }));
  expect(screen.getByRole("dialog", { name: "Trace" })).toBeVisible();
  expect(readTrace).toHaveBeenCalledWith(originalSession.id, answer.id);
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog", { name: "Trace" })).toBeNull();
});

it("opens a source independently of scope and keeps the Chat draft and scroll position", async () => {
  const user = userEvent.setup();
  setup();
  await screen.findByText(originalSession.title);
  const draft = screen.getByRole("textbox", { name: "Message Gunther" });
  await user.type(draft, "A thought to keep");
  const chatScroll = draft.closest(".session-workspace")!.querySelector<HTMLElement>(".conversation-scroll")!;
  Object.defineProperty(chatScroll, "scrollHeight", { value: 950 });
  chatScroll.scrollTop = 128;
  fireEvent.scroll(chatScroll);
  const check = await screen.findByRole("checkbox", { name: "Select Lab notes" });

  await user.click(check);
  expect(screen.queryByRole("article", { name: "Source summary" })).toBeNull();
  await user.click(screen.getByRole("button", { name: /^Lab notes/ }));
  expect(await screen.findByRole("article", { name: "Source summary" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Open full page" })).toBeVisible();
  expect(check).not.toBeChecked();
  expect(draft).not.toBeVisible();
  chatScroll.scrollTop = 0;
  await user.click(screen.getByRole("button", { name: "Back to chat" }));
  expect(screen.getByRole("textbox", { name: "Message Gunther" })).toBe(draft);
  expect(draft).toHaveValue("A thought to keep");
  expect(chatScroll.scrollTop).toBe(128);
});

it("keeps an unsaved document edit until leaving is confirmed", async () => {
  const user = userEvent.setup();
  const ask = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  setup({ artifacts: [version()] });
  await user.click(await screen.findByRole("button", { name: /Field findings Report Version 1/ }));
  await user.click(await screen.findByRole("button", { name: "Output actions" }));
  await user.click(screen.getByRole("menuitem", { name: "Edit" }));
  const editor = screen.getByRole("textbox", { name: "Report, as Markdown" });
  await user.type(editor, " A new detail.");
  await user.click(screen.getByRole("button", { name: "Back to chat" }));
  expect((editor as HTMLTextAreaElement).value).toContain("A new detail.");
  await user.click(screen.getByRole("button", { name: /^Lab notes/ }));
  expect(ask).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("article", { name: "Source summary" })).toBeVisible();
  expect(editor).not.toBeInTheDocument();
});

it("refines the open lineage and updates its scope only on request", async () => {
  const user = userEvent.setup();
  const saved = version();
  const { create, patch, turn } = setup({ artifacts: [saved], conversations: [conversation("earlier", { scope: selected("s3") })] });
  await user.click(await screen.findByRole("checkbox", { name: "Select Review notes" }));
  await user.click(await screen.findByRole("button", { name: /Field findings Report Version 1/ }));
  const input = await screen.findByRole("textbox", { name: "Refine" });
  const refine = screen.getByRole("button", { name: "Refine" });
  expect(screen.getByText("Using this output’s 1 source")).toBeVisible();
  await user.type(input, "Make the conclusion clearer");
  await user.click(refine);
  expect(turn).toHaveBeenLastCalledWith(base.id, "earlier", expect.objectContaining({ kind: "message", text: "Make the conclusion clearer", targetLineageId: saved.lineageId }), ws, expect.any(Function), expect.any(AbortSignal));
  expect(turn.mock.calls[0]![2]).not.toHaveProperty("build");
  expect(patch).not.toHaveBeenCalled();

  await user.click(await screen.findByRole("button", { name: "Update to current 2" }));
  expect(patch).toHaveBeenCalledExactlyOnceWith(base.id, "earlier", { targetLineageId: saved.lineageId, scope: selected("s1", "s2") }, ws);
  expect(create).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.queryByRole("button", { name: "Update to current 2" })).toBeNull());
});

it.each([
  { scope: { ...selected(), sessionIds: ["chat-1"] }, label: "1 discussion" },
  { scope: { ...selected(), unitIds: ["answer-1"] }, label: "1 saved answer" },
  { scope: { ...selected("s1", "s2"), unitIds: ["answer-1", "answer-2"], sessionIds: ["chat-1", "chat-2"] }, label: "2 sources · 2 saved answers · 2 discussions" },
  { scope: selected(), label: "0 sources" },
])("describes the saved scope as $label", async ({ scope, label }) => {
  setup({ artifacts: [version({ scope })] });
  await userEvent.click(await screen.findByRole("button", { name: /Field findings Report Version 1/ }));
  expect(await screen.findByText(`Using this output’s ${label}`)).toBeVisible();
});

it("starts a new tile build with type options and reopens its running card from the tile", async () => {
  const user = userEvent.setup();
  const { artifacts, create, turn } = setup();
  let complete!: (result: ArtifactTurnOut) => void;
  const finished = new Promise<ArtifactTurnOut>((resolve) => { complete = resolve; });
  turn.mockImplementation(async (_base, _id, _input, _ws, hear) => {
    hear({ type: "stage", id: "write", label: "Writing the report", state: "running", detail: "3 of 8" });
    return finished;
  });
  await user.click(await screen.findByRole("checkbox", { name: "Select Review notes" }));
  await user.click(await screen.findByRole("button", { name: "Report options" }));
  const options = screen.getByRole("form", { name: "Report options" });
  const focus = within(options).getByRole("textbox", { name: "Focus" });
  expect(focus).toHaveAttribute("maxlength", "2000");
  await user.type(focus, "The fieldwork");
  await user.selectOptions(within(options).getByRole("combobox", { name: "Length" }), "short");
  expect(within(options).queryByRole("combobox", { name: "Use" })).toBeNull();
  await user.click(within(options).getByRole("button", { name: "Build Report" }));
  await screen.findByRole("button", { name: /Writing the report · 3 of 8/ });
  const id = create.mock.calls[0]![1].id!;
  const scope = selected("s1", "s2");
  expect(create).toHaveBeenCalledExactlyOnceWith(base.id, { id, scope }, ws);
  expect(turn).toHaveBeenCalledExactlyOnceWith(base.id, id, {
    clientRequestId: expect.any(String), kind: "build", build: { type: "report", scope, focus: "The fieldwork", length: "short" },
  }, ws, expect.any(Function), expect.any(AbortSignal));

  await user.click(screen.getByRole("button", { name: "Report" }));
  expect(within(screen.getByRole("article", { name: "Open output" })).getByRole("status")).toHaveTextContent("Writing the report · 3 of 8");
  expect(create).toHaveBeenCalledTimes(1);
  const saved = version({ conversationId: id, scope });
  artifacts.push(saved);
  await act(async () => complete(done(saved)));
  expect(await screen.findByRole("heading", { name: saved.title })).toBeVisible();
});

it("retries a failed card with its stored build options and original conversation", async () => {
  const user = userEvent.setup();
  const build: BuildTurnInput = { type: "slides", scope: selected("s3"), focus: "Last week's findings", use: "read" };
  const failed = conversation("failed", {
    lastTurn: { kind: "build", build, error: "The model timed out.", createdAt: at },
    messages: [{ id: "old", role: "user", kind: "build", text: "", artifactId: null, payload: { build }, clientRequestId: "first-request", createdAt: at }],
  });
  const { create, turn } = setup({ conversations: [failed] });
  expect(await screen.findByRole("button", { name: /Slides The model timed out/ })).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Retry" }));
  expect(turn).toHaveBeenCalledExactlyOnceWith(base.id, failed.id, { clientRequestId: expect.any(String), kind: "build", build }, ws, expect.any(Function), expect.any(AbortSignal));
  expect(turn.mock.calls[0]![2].clientRequestId).not.toBe("first-request");
  expect(create).not.toHaveBeenCalled();
});

it("uses the library ticks for tiles and the discussion for an explicit report from Chat", async () => {
  const user = userEvent.setup();
  const answer: SessionMessage = {
    id: "research-answer", role: "assistant", sessionId: originalSession.id, createdAt: at, content: "The research is complete.", citations: [],
    context: {
      sourcesConsidered: 1, assertionsConsidered: 1, verifiedAssertions: 1, retrievalMode: "all", responderMode: "model",
      research: { state: "done", budget: "standard", used: { searches: 1, reads: 0 }, limits: { searches: 3, reads: 3 }, coreQuestion: "What changed?", doneWhen: "" },
    },
  };
  vi.mocked(knowledgeApi.session).mockResolvedValue({ ...originalSession, messages: [answer], messageCount: 1 });
  const { turn } = setup();
  await user.click(await screen.findByRole("checkbox", { name: "Select Review notes" }));
  const makeReport = await screen.findByRole("button", { name: "Make a report from this" });
  await user.click(makeReport);
  expect(screen.getByText("Using this chat as material.")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Slides" }));
  expect(turn.mock.calls[0]![2].build).toEqual({ type: "slides", scope: selected("s1", "s2") });

  await user.click(makeReport);
  await user.click(screen.getByRole("button", { name: "Build Report" }));
  expect(turn.mock.calls[1]![2].build).toEqual({
    type: "report", scope: { ...selected(), sessionIds: [originalSession.id] },
  });
});
