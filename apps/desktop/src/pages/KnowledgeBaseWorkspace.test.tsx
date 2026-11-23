import type {
  Artifact, ArtifactConversationDetail, ArtifactTurnOut, BuildTurnInput, ContentsSource, KnowledgeSession,
  KeyResult, KeyResultJob, LibraryContents, LibraryOverview, ModelMenu, OutputScope, OutputType,
  SessionMessage, SourceDigest,
} from "@gunther/contracts";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiRequestError, knowledgeApi } from "../api";
import { keyResultsChanged } from "../keyResults/events";
import { announceModelsChanged, OPEN_SETTINGS_EVENT } from "../models/askModel";
import { libraryScope } from "../outputs/artifactConversations";
import { makeBase, makeSource, makeTrashItem } from "../test/fixtures";
import { KnowledgeBaseWorkspace } from "./KnowledgeBaseWorkspace";

vi.mock("../components/markdown/MarkdownEditor", () => ({
  MarkdownEditor: ({ value, onChange, ariaLabel }: { value: string; onChange: (value: string) => void; ariaLabel: string }) =>
    <textarea value={value} aria-label={ariaLabel} onChange={(event) => onChange(event.target.value)} />,
}));
vi.mock("../outputs/LazySlides", () => ({ LazySlidesView: () => <p>Slide reader</p> }));

const ws = "workspace";
const base = makeBase({ id: "study", title: "Reading group" });
const at = "2026-10-01T00:05:00.000Z";
const finding: KeyResult = {
  id: "result-1", text: "Field conditions changed after midday.",
  origin: { kind: "output", id: "version-1", title: "Field findings", sessionId: null, createdAt: at },
  evidence: [
    { id: "passage-1", sourceId: "s1", revisionId: "revision-1", blockId: "block-1", regionIds: [],
      sourceTitle: "Lab notes", locator: "Page 3", quote: "The air warmed after noon." },
    { id: "passage-2", sourceId: "s2", revisionId: "revision-2", blockId: "block-2", regionIds: [],
      sourceTitle: "Field notes", locator: "Page 5", quote: "Humidity fell during the afternoon." },
  ],
};
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
const report: OutputType = {
  id: "report", label: "Report", shape: "report", description: "A written report", icon: "FileText", options: ["focus", "length"], partLimit: 8, legacyStyles: ["auto", "overview", "field_guide"],
  capabilities: { unitLabel: "section", outline: true, design: false, exports: ["markdown", "pdf", "docx"] },
};
const briefing: OutputType = { ...report, id: "briefing", label: "Briefing doc", legacyStyles: ["decision_brief"] };
const guide: OutputType = { ...report, id: "study_guide", label: "Study guide", partLimit: 12, legacyStyles: ["teaching_path"] };
const slides: OutputType = {
  id: "slides", label: "Slides", shape: "slides", description: "A slide deck", icon: "Presentation", options: ["focus", "use"], partLimit: 14,
  capabilities: { unitLabel: "slide", outline: true, design: true, exports: ["markdown", "pdf", "pptx"] },
};
const flashcards: OutputType = {
  id: "flashcards", label: "Flashcards", shape: "cards", description: "Study the source material", icon: "Layers", options: ["focus", "audience", "length", "language"], partLimit: 40,
  capabilities: { unitLabel: "card", outline: false, design: false, exports: ["markdown", "pdf"] },
};
const quiz: OutputType = { ...flashcards, id: "quiz", label: "Quiz", icon: "ListChecks" };
const mindMap: OutputType = {
  ...flashcards, id: "mind_map", label: "Mind map", icon: "Network", shape: "map", partLimit: 60,
  capabilities: { ...flashcards.capabilities, unitLabel: "node" },
};
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

const emptyOverview: LibraryOverview = { state: "empty", summary: "", suggestedQuestions: [], error: null };
const suggestedQuestion = "Which changes are measured?";
const readyOverview: LibraryOverview = {
  ...emptyOverview, state: "ready", summary: "The sources compare three ways to record changes.",
  suggestedQuestions: [suggestedQuestion],
};

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
  const onAdd = vi.fn();
  const onNotify = vi.fn();
  const onTrashed = vi.fn();
  const view = render(<KnowledgeBaseWorkspace base={base} workspaceId={ws} onOpenSource={vi.fn()} onNotify={onNotify} onAdd={onAdd} onTrashed={onTrashed} />);
  return { view, create, patch, turn, artifacts, onAdd, onNotify, onTrashed };
}

beforeEach(() => {
  window.localStorage.clear();
  vi.stubGlobal("innerWidth", 1280);
  vi.stubGlobal("fetch", vi.fn(async () => Response.json([])));
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  vi.spyOn(knowledgeApi, "contents").mockResolvedValue(contents);
  vi.spyOn(knowledgeApi, "sessions").mockResolvedValue([originalSession, secondSession]);
  vi.spyOn(knowledgeApi, "session").mockImplementation(async (id) => id === secondSession.id ? secondSession : originalSession);
  vi.spyOn(knowledgeApi, "followAnswer").mockResolvedValue(null);
  vi.spyOn(knowledgeApi, "modelMenu").mockResolvedValue(readyModels);
  vi.spyOn(knowledgeApi, "libraryOverview").mockResolvedValue(emptyOverview);
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
    [flashcards, "lucide-layers"],
    [quiz, "lucide-list-checks"],
    [mindMap, "lucide-network"],
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

function mockAnswers() {
  vi.spyOn(knowledgeApi, "refreshBrief").mockResolvedValue({ goal: "", constraints: [], settled: [], open: [], edited: [], error: null });
  return vi.spyOn(knowledgeApi, "sendMessageStream").mockImplementation(async (id, request) => {
    const session = id === secondSession.id ? secondSession : originalSession;
    const question: SessionMessage = { id: "question", sessionId: id, role: "user", content: request.content, citations: [], context: { sourcesConsidered: 0, assertionsConsidered: 0, verifiedAssertions: 0, retrievalMode: "all", responderMode: "model" }, createdAt: at };
    return { session, userMessage: question, assistantMessage: { ...question, id: "answer", role: "assistant", content: "Here are the findings." } };
  });
}

it("asks about a map path in Chat using the current source selection", async () => {
  const user = userEvent.setup();
  const send = mockAnswers();
  vi.mocked(knowledgeApi.outputTypes).mockResolvedValue([report, mindMap]);
  const saved = version({
    kind: "map", format: "map", outputType: "Mind map", style: "mind_map", title: "Field map",
    content: "<!-- map: mind_map; design: v1 -->\n- Trial\n  - Weather\n    - Midday observations [1]",
    scope: selected("s1"),
    citations: [{
      id: "map-passage", sourceId: "s1", sourceTitle: "Lab notes", assertionId: null,
      quote: "Observations were recorded at noon.", locator: "Page 3", status: "verified", confidence: 1,
    }],
  });
  setup({ artifacts: [saved] });
  resizeWindow(1199);
  await user.click(await screen.findByRole("checkbox", { name: "All sources" }));
  await user.click(screen.getByRole("checkbox", { name: "Select Field notes" }));
  await user.click(await screen.findByRole("button", { name: /Field map Mind map Version 1/ }));
  const reader = within(await screen.findByRole("region", { name: "Mind map" }));
  expect(screen.getByRole("button", { name: "Expand Sources" })).toBeVisible();
  await user.click(reader.getByRole("button", { name: "Inspect citation 1" }));
  const evidence = within(screen.getByRole("complementary", { name: "Evidence" }));
  expect(evidence.getByText("Observations were recorded at noon.")).toBeVisible();
  expect(send).not.toHaveBeenCalled();
  await user.click(evidence.getByRole("button", { name: "Close evidence" }));
  await user.click(reader.getByRole("button", { name: "Ask about Midday observations" }));
  expect(screen.queryByRole("region", { name: "Mind map" })).not.toBeInTheDocument();
  expect(await screen.findByRole("textbox", { name: "Message Gunther" })).toBeVisible();
  expect(send).toHaveBeenCalledExactlyOnceWith(originalSession.id, expect.objectContaining({
    content: "Tell me about Trial › Weather › Midday observations", selectedSourceIds: ["s2"], focusChapterId: null,
  }), expect.any(Function), expect.any(AbortSignal));
});

it("shows the overview and sends a suggested question with the current scope", async () => {
  const user = userEvent.setup();
  const send = mockAnswers();
  vi.mocked(knowledgeApi.libraryOverview).mockResolvedValue(readyOverview);
  setup();
  expect(await screen.findByText(readyOverview.summary)).toBeVisible();
  expect(screen.queryByText(base.description)).not.toBeInTheDocument();
  await user.click(await screen.findByRole("checkbox", { name: "All sources" }));
  const suggestion = screen.getByRole("button", { name: suggestedQuestion });
  expect(suggestion).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: "Select Field notes" }));
  await user.click(suggestion);
  expect(send).toHaveBeenCalledExactlyOnceWith(originalSession.id, expect.objectContaining({
    content: suggestedQuestion, selectedSourceIds: ["s2"], focusChapterId: null,
  }), expect.any(Function), expect.any(AbortSignal));
});

it("shows the library details and a Models link when the overview needs a model", async () => {
  const user = userEvent.setup();
  const settings = vi.fn();
  window.addEventListener(OPEN_SETTINGS_EVENT, settings, { once: true });
  vi.mocked(knowledgeApi.libraryOverview).mockResolvedValue({ ...emptyOverview, state: "needs_model" });
  setup();
  expect(await screen.findByText("The library overview needs a model.")).toBeVisible();
  expect(screen.getByText(base.description)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Open Models settings" }));
  expect(settings).toHaveBeenCalledWith(expect.objectContaining({ detail: { pane: "models" } }));
});

it("shows the overview failure with Retry and replaces the fallback after success", async () => {
  const user = userEvent.setup();
  const error = "The Analysis model could not write the library overview.";
  const overview = vi.mocked(knowledgeApi.libraryOverview).mockImplementation(async (_id, retry) =>
    retry ? readyOverview : { ...emptyOverview, state: "failed", error });
  setup();
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(error);
  expect(screen.getByText(base.description)).toBeVisible();
  await user.click(within(alert).getByRole("button", { name: "Retry" }));
  expect(await screen.findByText(readyOverview.summary)).toBeVisible();
  expect(overview).toHaveBeenLastCalledWith(base.id, true, expect.any(AbortSignal));
});

it("shares all sources, blocks an empty selection, and uses section ticks for each Ask turn", async () => {
  const user = userEvent.setup();
  const send = mockAnswers();
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
  await user.click(screen.getByRole("button", { name: "Chat actions" }));
  await user.click(screen.getByRole("menuitem", { name: "History" }));
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

it("shows a digest beside paper metadata, opens its cited revision, and brings its questions to Chat", async () => {
  const user = userEvent.setup();
  const send = mockAnswers();
  const lab = { ...contents.sources[0]!, mediaType: "application/pdf" };
  vi.mocked(knowledgeApi.contents).mockResolvedValue({ ...contents, sources: [lab, ...contents.sources.slice(1)] });
  const quote = "The gauge is emptied each morning.";
  const digest: SourceDigest = {
    sourceId: lab.id, revisionId: "revision-1", profile: "paper", method: "analysis",
    engine: "Local", model: "Model", suggestedTitle: null, overview: "A daily record of rain.",
    keyPoints: [{ text: "Daily checks", citations: [{ number: 1, blockId: "block-1", quote, locator: "Page 2" }] }],
    actionItems: [], openQuestions: ["How are readings recorded?"], terms: ["Rain gauge"],
    markdown: "", updatedAt: at,
  };
  vi.mocked(knowledgeApi.sourceDigest).mockResolvedValue({
    sourceId: lab.id, state: "ready", stale: false, method: "analysis", offReason: null, error: null, digest,
  });
  vi.spyOn(knowledgeApi, "sourcePaper").mockResolvedValue({
    sourceId: lab.id, revisionId: digest.revisionId, title: lab.title, authors: "A. Reader", year: 2024,
    doi: null, arxivId: null, abstract: "A paper about observation.", abstractBlockIds: [], outline: [], summaryMarkdown: "", copies: [],
  });
  vi.spyOn(knowledgeApi, "source").mockResolvedValue({
    ...makeSource({ id: lab.id, title: lab.title, kind: lab.kind }), content: quote, assertions: [], webSnapshot: null,
  });
  const structure = vi.spyOn(knowledgeApi, "sourceStructure").mockResolvedValue({
    sourceId: lab.id, revisionId: digest.revisionId, processing: { state: "ready", jobId: null, revisionId: digest.revisionId, warning: null },
    parser: null, nextOffset: null, blocks: [],
  });
  setup();
  await user.click(await screen.findByRole("checkbox", { name: "Select Field notes" }));
  const openSummary = () => user.click(screen.getByRole("button", { name: /^Lab notes/ }));
  await openSummary();
  const summary = within(screen.getByRole("article", { name: "Source summary" }));
  expect(await summary.findByText(digest.overview)).toBeVisible();
  expect(summary.getByText("Daily checks")).toBeVisible();
  expect(await summary.findByText("A paper about observation.")).toBeVisible();
  expect(summary.queryByRole("heading", { name: "To do" })).not.toBeInTheDocument();
  await user.click(summary.getByRole("button", { name: "Inspect citation 1" }));
  const evidence = within(screen.getByRole("complementary", { name: "Evidence" }));
  expect(evidence.getByText(quote)).toBeVisible();
  expect(structure).toHaveBeenCalledWith(lab.id, 0, digest.revisionId, null, 500);
  await user.click(evidence.getByRole("button", { name: "Close evidence" }));
  await user.click(summary.getByRole("button", { name: "Rain gauge" }));
  expect(screen.getByRole("textbox", { name: "Message Gunther" })).toHaveValue("Rain gauge");
  expect(send).not.toHaveBeenCalled();

  await openSummary();
  await user.click(await screen.findByRole("button", { name: "Send How are readings recorded? to Chat" }));
  expect(send).toHaveBeenCalledExactlyOnceWith(originalSession.id, expect.objectContaining({
    content: "How are readings recorded?", selectedSourceIds: ["s1", "s3"],
  }), expect.any(Function), expect.any(AbortSignal));
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

it("moves the whole output to Trash from the menu on an older open version", async () => {
  const user = userEvent.setup();
  const older = version();
  const saved = [older, version({ id: "version-2", versionNumber: 2, supersedesArtifactId: older.id })];
  const entry = makeTrashItem({ id: "trash-batch", title: older.title, kind: "output", versionCount: 2 });
  const { onTrashed } = setup({ artifacts: saved });
  const move = vi.spyOn(knowledgeApi, "trashOutput").mockImplementation(async () => {
    saved.splice(0);
    return entry;
  });
  const openMenu = () => user.click(screen.getByRole("button", { name: "Output actions" }));
  await user.click(await screen.findByRole("button", { name: /Field findings Report Version 2/ }));
  await screen.findByRole("button", { name: "Output actions" });
  await openMenu();
  await user.click(screen.getByRole("menuitem", { name: "Versions" }));
  await user.click(screen.getByRole("button", { name: "Version 1" }));
  await screen.findByRole("button", { name: "Output actions" });
  await openMenu();
  await user.click(screen.getByRole("menuitem", { name: "Move to Trash" }));
  expect(move).toHaveBeenCalledExactlyOnceWith(base.id, older.id, ws);
  expect(onTrashed).toHaveBeenCalledExactlyOnceWith(entry);
  expect(screen.queryByRole("article", { name: "Open output" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Field findings Report Version/ })).not.toBeInTheDocument();
});

it("keeps an unsaved edit when a running turn blocks Move to Trash", async () => {
  const user = userEvent.setup();
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(true).mockReturnValueOnce(false);
  const { onNotify } = setup({ artifacts: [version()] });
  const detail = "Stop the output run before moving it to Trash.";
  vi.spyOn(knowledgeApi, "trashOutput").mockRejectedValue(new ApiRequestError(detail, 409));
  await user.click(await screen.findByRole("button", { name: /Field findings Report Version 1/ }));
  await user.click(await screen.findByRole("button", { name: "Output actions" }));
  await user.click(screen.getByRole("menuitem", { name: "Edit" }));
  const editor = screen.getByRole("textbox", { name: "Report, as Markdown" });
  await user.type(editor, " Continued observations.");
  await user.click(screen.getByRole("button", { name: "Output actions" }));
  await user.click(screen.getByRole("menuitem", { name: "Move to Trash" }));
  expect(onNotify).toHaveBeenCalledWith(detail);
  await user.click(screen.getByRole("button", { name: "Back to chat" }));
  expect(confirm).toHaveBeenCalledTimes(2);
  expect(editor).toBeInTheDocument();
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

it("opens card evidence and shows a refined card in the new version", async () => {
  const user = userEvent.setup();
  vi.mocked(knowledgeApi.outputTypes).mockResolvedValue([report, slides, flashcards, quiz]);
  const saved = version({
    kind: "cards", format: "cards", outputType: "Flashcards", style: "flashcards", title: "Trial cards",
    content: "<!-- cards: flashcards; design: v1 -->\n\n## What was the sample size?\nTwelve samples.\n\n## When was the sample read?\nThe sample was read at noon [1].",
    outline: [{ id: "card-1", heading: "What was the sample size?", goal: "" }, { id: "card-2", heading: "When was the sample read?", goal: "" }],
    citations: [{
      id: "card-citation", sourceId: "s1", sourceTitle: "Lab notes", assertionId: null,
      quote: "Readings were recorded at noon.", locator: "Page 3", status: "verified", confidence: 1,
    }],
  });
  const revised = version({
    ...saved, id: "version-2", versionNumber: 2, supersedesArtifactId: saved.id, origin: "revise",
    content: saved.content.replace("When was the sample read?", "At what time were readings recorded?"),
    outline: [saved.outline[0]!, { ...saved.outline[1]!, heading: "At what time were readings recorded?" }],
  });
  const { turn, artifacts } = setup({ artifacts: [saved], conversations: [conversation("earlier")] });
  turn.mockImplementation(async () => { artifacts.push(revised); return done(revised); });

  await user.click(await screen.findByRole("button", { name: /Trial cards Flashcards Version 1/ }));
  const reader = () => within(screen.getByRole("region", { name: "Card reader" }));
  await user.click(await screen.findByRole("button", { name: "Next" }));
  await user.click(reader().getByRole("button", { name: "Show answer" }));
  const citation = reader().getByRole("button", { name: "Inspect citation 1" });
  expect(citation).toHaveAttribute("data-opens-evidence");
  await user.click(citation);
  const evidence = within(screen.getByRole("complementary", { name: "Evidence" }));
  expect(evidence.getByText("Readings were recorded at noon.")).toBeVisible();
  await user.click(evidence.getByRole("button", { name: "Close evidence" }));
  await user.click(reader().getByRole("button", { name: "Refine this card" }));
  expect(screen.getByText("About card 2", { exact: false })).toBeVisible();
  await user.type(screen.getByRole("textbox", { name: "Refine" }), "Ask about the time recorded in the log");
  await user.click(screen.getByRole("button", { name: "Refine" }));
  expect(turn).toHaveBeenLastCalledWith(base.id, "earlier", expect.objectContaining({
    focus: { section: 2, selected: true }, targetLineageId: saved.lineageId,
  }), ws, expect.any(Function), expect.any(AbortSignal));
  expect(await screen.findByRole("heading", { name: "At what time were readings recorded?" })).toBeVisible();
  expect(reader().getByText("2 / 2")).toBeVisible();

  await user.click(screen.getByRole("button", { name: "Output actions" }));
  await user.click(screen.getByRole("menuitem", { name: "Versions" }));
  await user.click(screen.getByRole("button", { name: "Version 1" }));
  await user.click(await screen.findByRole("button", { name: "Next" }));
  expect(reader().getByRole("heading", { name: "When was the sample read?" })).toBeVisible();
});

it("offers the card export formats from the catalog and prints the answer sheet", async () => {
  const user = userEvent.setup();
  vi.mocked(knowledgeApi.outputTypes).mockResolvedValue([quiz]);
  const content = "<!-- cards: quiz; design: v1 -->\n\n## (quiz) When did the reading end?\n- [ ] Midnight\n- [x] Noon\nWhy: The end time was 12:00.";
  const saved = version({ kind: "cards", format: "cards", outputType: "Quiz", style: "quiz", title: "Trial quiz", content });
  setup({ artifacts: [saved] });
  let paper: string | null | undefined;
  const print = vi.spyOn(window, "print").mockImplementation(() => { paper = document.getElementById("print-root")?.textContent; });
  await user.click(await screen.findByRole("button", { name: /Trial quiz Quiz Version 1/ }));
  const reader = within(await screen.findByRole("region", { name: "Card reader" }));
  expect(reader.getByRole("button", { name: "Noon" })).toBeVisible();
  expect(reader.queryByText("Why:")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Output actions" }));
  for (const name of ["Outline", "Style", "Download Word", "Download PowerPoint"]) {
    expect(screen.queryByRole("menuitem", { name })).not.toBeInTheDocument();
  }
  expect(screen.getByRole("menuitem", { name: "Download Markdown" })).toBeVisible();
  await user.click(screen.getByRole("menuitem", { name: "Export PDF" }));
  await waitFor(() => expect(print).toHaveBeenCalledOnce());
  expect(paper).toContain("NoonCorrect answer");
  expect(paper).toContain("Why: The end time was 12:00.");
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

it("shows key results on the open output version and undoes that version's contribution", async () => {
  const job: KeyResultJob = {
    id: "output-job", originKind: "output", originId: "version-1", state: "done",
    keptCount: 2, keptIds: ["result-1", "result-2"], error: null,
  };
  const undo = vi.spyOn(knowledgeApi, "undoKeyResults")
    .mockResolvedValue({ ...job, keptCount: 0, keptIds: [] });
  setup({ artifacts: [version({ keyResults: job })] });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /Field findings Report Version 1/ }));
  const reader = within(screen.getByRole("article", { name: "Open output" }));
  expect(await reader.findByText("✓ 2 key results kept")).toBeVisible();
  await user.click(reader.getByRole("button", { name: "Undo" }));
  expect(await reader.findByText("Key results undone")).toBeVisible();
  expect(undo).toHaveBeenCalledExactlyOnceWith(base.id, job);
});

it("opens every original passage in Evidence and offers Undo after removing a result", async () => {
  let kept = [finding];
  vi.spyOn(knowledgeApi, "keyResults").mockImplementation(async () => kept);
  const remove = vi.spyOn(knowledgeApi, "removeKeyResult").mockImplementation(async () => {
    kept = [];
    return { removedAt: "removal-stamp" };
  });
  const restore = vi.spyOn(knowledgeApi, "restoreKeyResult").mockImplementation(async () => { kept = [finding]; });
  vi.spyOn(knowledgeApi, "source").mockImplementation(async (id) => ({
    ...makeSource({ id }), content: "", assertions: [], webSnapshot: null,
  }));
  const structure = vi.spyOn(knowledgeApi, "sourceStructure").mockResolvedValue({
    sourceId: "s1", revisionId: "revision-1",
    processing: { state: "ready", jobId: null, revisionId: "revision-1", warning: null },
    parser: null, nextOffset: null, blocks: [],
  });
  const { onNotify } = setup({ artifacts: [version()] });
  const user = userEvent.setup();
  await screen.findByRole("button", { name: /Field findings Report Version 1/ });
  const group = within(screen.getByRole("region", { name: "Key results" }));
  expect(await group.findByRole("button", { name: "Key results (1)" })).toBeVisible();
  const row = group.getByRole("button", { name: /Field conditions changed/ });
  expect(row).toHaveAttribute("data-opens-evidence");
  expect(row).toHaveTextContent("from Report: Field findings");
  await user.click(row);
  const evidence = within(screen.getByRole("complementary", { name: "Evidence" }));
  expect(evidence.getByText("The air warmed after noon.")).toBeVisible();
  expect(evidence.getByRole("heading", { name: "Page 3" })).toBeVisible();
  await user.selectOptions(evidence.getByRole("combobox", { name: "Passage" }), "passage-2");
  expect(evidence.getByText("Humidity fell during the afternoon.")).toBeVisible();
  expect(evidence.getByRole("heading", { name: "Page 5" })).toBeVisible();
  expect(evidence.getByRole("option", { name: /Field notes · Page 5/ })).toBeInTheDocument();
  expect(structure).toHaveBeenLastCalledWith("s2", 0, "revision-2", null, 500);
  await user.click(evidence.getByRole("button", { name: "Close evidence" }));
  await user.click(group.getByRole("button", { name: "Remove" }));
  expect(await group.findByRole("button", { name: "Key results (0)" })).toBeVisible();
  expect(remove).toHaveBeenCalledExactlyOnceWith(base.id, finding.id);
  expect(onNotify).toHaveBeenCalledWith("Key result removed.", expect.objectContaining({ label: "Undo" }));
  await act(async () => onNotify.mock.calls.find(([message]) => message === "Key result removed.")?.[1].run());
  expect(await group.findByRole("button", { name: "Key results (1)" })).toBeVisible();
  expect(restore).toHaveBeenCalledExactlyOnceWith(base.id, finding.id, "removal-stamp");
});

it("collapses long lists, filters them, and shows a read-only link while saved answers are pending", async () => {
  const rows = Array.from({ length: 6 }, (_, index): KeyResult => ({
    ...finding, id: `result-${index}`, text: `Finding ${index}`,
    origin: { kind: "answer", id: `answer-${index}`, title: null, sessionId: originalSession.id, createdAt: at },
  }));
  vi.spyOn(knowledgeApi, "keyResults").mockResolvedValue(rows);
  const pending = vi.spyOn(knowledgeApi, "pendingSavedAnswers").mockResolvedValue([
    { id: "saved-1", title: "Prior answer", content: "Stored wording." },
  ]);
  setup();
  const user = userEvent.setup();
  const group = within(await screen.findByRole("region", { name: "Key results" }));
  const header = group.getByRole("button", { name: "Key results (6)" });
  expect(header).toHaveAttribute("aria-expanded", "false");
  await user.type(screen.getByRole("textbox", { name: "Filter contents" }), "Finding 3");
  expect(group.getByRole("button", { name: /Finding 3 from your question/ })).toBeVisible();
  expect(group.queryByRole("button", { name: /Finding 1/ })).not.toBeInTheDocument();
  await user.click(group.getByText("Saved answers awaiting key results"));
  expect(group.getByText("Stored wording.")).toBeVisible();
  pending.mockResolvedValue([]);
  act(() => keyResultsChanged(base.id));
  await waitFor(() => expect(group.queryByText("Saved answers awaiting key results")).not.toBeInTheDocument());
});

it("updates Key results when the selected sources change", async () => {
  const keyResults = vi.spyOn(knowledgeApi, "keyResults").mockImplementation(async (_base, _signal, ids) =>
    ids?.includes("s2") === false ? [] : [finding],
  );
  setup();
  const user = userEvent.setup();
  expect(await screen.findByRole("button", { name: "Key results (1)" })).toBeVisible();
  await user.click(screen.getByRole("checkbox", { name: "Select Field notes" }));
  expect(await screen.findByRole("button", { name: "Key results (0)" })).toBeVisible();
  expect(keyResults).toHaveBeenLastCalledWith(base.id, expect.any(AbortSignal), ["s1", "s3"]);
  await user.click(screen.getByRole("checkbox", { name: "Select Field notes" }));
  expect(await screen.findByRole("button", { name: "Key results (1)" })).toBeVisible();
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

it("opens Capture in Note mode from the Add source menu", async () => {
  const user = userEvent.setup();
  const { onAdd } = setup();
  await screen.findByRole("checkbox", { name: "Select Lab notes" });
  await user.click(screen.getByRole("button", { name: "Add source" }));
  await user.click(screen.getByRole("menuitem", { name: "Note or pasted text" }));
  expect(onAdd).toHaveBeenCalledExactlyOnceWith("note");
});

const resizeWindow = (width: number) => act(() => {
  vi.stubGlobal("innerWidth", width);
  window.dispatchEvent(new Event("resize"));
});

it("collapses the columns at their window thresholds and reopens either rail", async () => {
  const user = userEvent.setup();
  setup();
  await screen.findByRole("checkbox", { name: "Select Lab notes" });
  const column = (action: string) => screen.getByRole("button", { name: action });
  resizeWindow(1080);
  expect(column("Collapse Studio")).toHaveAttribute("aria-expanded", "true");
  resizeWindow(1079);
  expect(column("Expand Studio")).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("button", { name: "Slides" })).not.toBeInTheDocument();
  await user.click(column("Expand Studio"));
  expect(screen.getByRole("button", { name: "Slides" })).toBeVisible();
  await user.click(column("Collapse Studio"));
  resizeWindow(840);
  expect(column("Collapse Sources")).toBeVisible();
  resizeWindow(839);
  expect(column("Expand Sources")).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("checkbox", { name: "Select Lab notes" })).not.toBeInTheDocument();
  await user.click(column("Expand Sources"));
  expect(screen.getByRole("checkbox", { name: "Select Lab notes" })).toBeChecked();
});

it("resizes the columns from their inner edges and remembers the widths", async () => {
  const user = userEvent.setup();
  setup();
  await screen.findByRole("checkbox", { name: "Select Lab notes" });
  const sourcesWidth = screen.getByRole("separator", { name: "Resize Sources" });
  const studioWidth = screen.getByRole("separator", { name: "Resize Studio" });
  const widen = (separator: HTMLElement, key: string) => {
    const before = Number(separator.getAttribute("aria-valuenow"));
    fireEvent.keyDown(separator, { key });
    const after = Number(separator.getAttribute("aria-valuenow"));
    expect(after).toBeGreaterThan(before);
    return after;
  };
  const widerSources = widen(sourcesWidth, "ArrowRight");
  const widerStudio = widen(studioWidth, "ArrowLeft");
  await user.click(screen.getByRole("button", { name: "Collapse Sources" }));
  await user.click(screen.getByRole("button", { name: "Expand Sources" }));
  expect(screen.getByRole("separator", { name: "Resize Sources" })).toHaveAttribute("aria-valuenow", String(widerSources));
  expect(window.localStorage.getItem("gunther:library-sources-width")).toBe(String(widerSources));
  expect(window.localStorage.getItem("gunther:library-studio-width")).toBe(String(widerStudio));
});

it("gives an open slide reader more room below 1200px", async () => {
  const user = userEvent.setup();
  resizeWindow(1200);
  const deck = version({ kind: "slides", format: "slides", style: "slides", outputType: "Slides" });
  setup({ artifacts: [deck] });
  await user.click(await screen.findByRole("button", { name: /Field findings Slides Version 1/ }));
  expect(await screen.findByText("Slide reader")).toBeVisible();
  expect(screen.getByRole("button", { name: "Collapse Sources" })).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Back to chat" }));
  resizeWindow(1199);
  expect(screen.getByRole("button", { name: "Collapse Sources" })).toBeVisible();
  await user.click(screen.getByRole("button", { name: /Field findings Slides Version 1/ }));
  await user.click(await screen.findByRole("button", { name: "Expand Sources" }));
  expect(screen.getByRole("checkbox", { name: "Select Lab notes" })).toBeChecked();
  await user.click(screen.getByRole("button", { name: "Collapse Sources" }));
  await user.click(screen.getByRole("button", { name: "Back to chat" }));
  expect(screen.getByRole("button", { name: "Collapse Sources" })).toBeVisible();
});

it.each([
  ["auto", report],
  ["overview", report],
  ["field_guide", report],
  ["teaching_path", guide],
  ["decision_brief", briefing],
] as const)("enforces the catalog outline limit for the legacy %s style", async (style, type) => {
  const user = userEvent.setup();
  vi.mocked(knowledgeApi.outputTypes).mockResolvedValue([report, briefing, guide, slides]);
  const outline = Array.from({ length: type.partLimit! }, (_, index) => ({ heading: `Section ${index + 1}`, goal: "" }));
  setup({ artifacts: [version({ style, outputType: type.label, outline })] });
  await user.click(await screen.findByRole("button", { name: /Field findings .* Version 1/ }));
  await user.click(await screen.findByRole("button", { name: "Output actions" }));
  await user.click(screen.getByRole("menuitem", { name: "Outline" }));
  await user.click(screen.getByRole("button", { name: "Edit outline" }));
  expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Remove 1" }));
  expect(screen.getByRole("button", { name: "Add" })).toBeEnabled();
});
