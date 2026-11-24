import type {
  Artifact, ArtifactAudio, ArtifactConversationDetail, ArtifactTurnOut, BuildTurnInput, ContentsSource, KnowledgeSession,
  KeyResult, KeyResultJob, LibraryContents, LibraryOverview, ModelMenu, OutputScope, OutputType,
  SessionMessage, SourceDigest,
} from "@gunther/contracts";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError, ServiceUnavailableError, knowledgeApi } from "../api";
import { forgetAskSkills } from "../components/search/skills";
import { keyResultsChanged } from "../keyResults/events";
import { announceModelsChanged, OPEN_SETTINGS_EVENT } from "../models/askModel";
import { libraryScope } from "../outputs/artifactConversations";
import { makeBase, makeSource, makeTrashItem } from "../test/fixtures";
import { KnowledgeBaseWorkspace } from "./KnowledgeBaseWorkspace";

vi.mock("../components/markdown/MarkdownEditor", () => ({
  MarkdownEditor: ({ value, onChange, ariaLabel }: { value: string; onChange: (value: string) => void; ariaLabel: string }) =>
    <textarea value={value} aria-label={ariaLabel} onChange={(event) => onChange(event.target.value)} />,
}));
vi.mock("../outputs/LazySlides", () => ({ LazySlidesView: ({ onSlide }: { onSlide?: (index: number) => void }) => <>
  <p>Slide reader</p><button type="button" onClick={() => onSlide?.(1)}>Show second slide</button>
</> }));

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
const audioOverview: OutputType = {
  id: "audio_overview", label: "Audio overview", icon: "AudioLines", description: "A recorded dialogue", shape: "audio",
  options: ["focus", "audience", "length", "language", "voices"], partLimit: 120,
  capabilities: { ...flashcards.capabilities, unitLabel: "line", exports: ["markdown", "wav"] },
  voices: {
    choices: [{ value: "cedar", label: "Cedar" }, { value: "ash", label: "Ash" }, { value: "elm", label: "Elm" }],
    defaults: ["cedar", "ash"], allowCustom: false,
  },
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
function audioVersion(status: ArtifactAudio["status"] = "ready"): Artifact {
  return version({
    kind: "audio", style: "audio_overview", format: "audio", outputType: "Audio overview", title: "Field interview",
    content: "<!-- audio: audio_overview; design: v1 -->\nA: When was the trial?\nB: The trial started at noon [1].",
    outline: [],
    audio: {
      status, audioAssetId: status === "ready" ? "wav-1" : null, contentHash: "hash", voices: ["cedar", "ash"],
      timings: status === "ready" ? [
        { line: 1, speaker: "A", start: 0, end: 1.5 }, { line: 2, speaker: "B", start: 1.5, end: 3 },
      ] : [],
      error: status === "error" ? "The configured voice is busy." : null,
    },
    citations: [{
      id: "trial-passage", sourceId: "s1", sourceTitle: "Lab notes", assertionId: null,
      quote: "Observations were recorded at noon.", locator: "Page 3", status: "verified", confidence: 1,
    }],
  });
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

afterEach(() => { forgetAskSkills(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Library chats column", () => {
  it("starts a new chat from the left column", async () => {
    const user = userEvent.setup();
    const created = { ...originalSession, id: "chat-new", title: "Fresh discussion" };
    const create = vi.spyOn(knowledgeApi, "createSession").mockResolvedValue(created);
    setup();

    await screen.findByRole("button", { name: /^Original discussion/ });
    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(await screen.findByRole("button", { name: /^Fresh discussion/ })).toBeVisible();
    expect(create).toHaveBeenCalledExactlyOnceWith(base.id, { focusChapterId: null, selectedSourceIds: [] });
    expect(screen.getByRole("textbox", { name: "Message Gunther" })).toBeEnabled();
  });

  it.each(["left column", "shortcut", "chat menu"])("clears the draft and skill for a new chat from the %s", async (entry) => {
    const user = userEvent.setup();
    forgetAskSkills();
    const skillsRead = vi.spyOn(knowledgeApi, "askSkills").mockResolvedValue([
      { command: "research", title: "Deep research", description: "Research a question.", budgets: ["standard", "deep"] },
    ]);
    const created = { ...originalSession, id: "chat-new", title: "Fresh discussion" };
    const create = vi.spyOn(knowledgeApi, "createSession").mockResolvedValue(created);
    const addListener = vi.spyOn(window, "addEventListener");
    setup();

    const composer = screen.getByRole("textbox", { name: "Message Gunther" });
    await waitFor(() => expect(composer).toBeEnabled());
    await waitFor(() => expect(skillsRead).toHaveBeenCalled());
    const registrations = addListener.mock.calls.filter(([event]) => event === "gunther:new-session").length;
    await user.type(composer, "/res{Enter}");
    await user.click(await screen.findByRole("button", { name: "Deep" }));
    await user.type(composer, "Unsent draft");
    expect(addListener.mock.calls.filter(([event]) => event === "gunther:new-session")).toHaveLength(registrations);

    if (entry === "left column") await user.click(screen.getByRole("button", { name: "New chat" }));
    else if (entry === "shortcut") act(() => window.dispatchEvent(new CustomEvent("gunther:new-session")));
    else {
      await user.click(screen.getByRole("button", { name: "Chat actions" }));
      await user.click(screen.getByRole("menuitem", { name: "New chat" }));
    }
    await screen.findByRole("button", { name: /^Fresh discussion/ });
    expect(create).toHaveBeenCalledOnce();
    await waitFor(() => expect(composer).toHaveValue(""));
    expect(screen.queryByRole("button", { name: "Remove /research" })).not.toBeInTheDocument();
    await user.type(composer, "/res{Enter}");
    expect(await screen.findByRole("button", { name: "Standard" })).toHaveAttribute("aria-pressed", "true");
  });

  it("closes the trace and title editor when another chat becomes active", async () => {
    const user = userEvent.setup();
    const answer: SessionMessage = {
      id: "answer", role: "assistant", sessionId: originalSession.id, content: "A saved answer.", createdAt: at,
      context: { sourcesConsidered: 0, assertionsConsidered: 0, verifiedAssertions: 0, retrievalMode: "all", responderMode: "model" },
      citations: [],
    };
    vi.mocked(knowledgeApi.session).mockImplementation(async (id) => id === secondSession.id
      ? secondSession : { ...originalSession, messages: [answer], messageCount: 1 });
    vi.spyOn(knowledgeApi, "developerSettings").mockResolvedValue({ traces: true });
    vi.spyOn(knowledgeApi, "messageTrace").mockResolvedValue({ version: 1, totalMs: 12, steps: [] });
    setup();

    await user.click(await screen.findByRole("button", { name: "Trace" }));
    expect(within(screen.getByRole("tabpanel", { name: "Evidence" })).getByRole("button", { name: "← Back to evidence" })).toBeVisible();
    act(() => window.dispatchEvent(new CustomEvent("gunther:open-session", {
      detail: { libraryId: base.id, sessionId: secondSession.id },
    })));
    await waitFor(() => expect(screen.queryByRole("button", { name: "← Back to evidence" })).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Chat actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Rename" }));
    expect(screen.getByRole("textbox", { name: "Session title" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: /^Original discussion/ }));
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "Session title" })).not.toBeInTheDocument());
  });

  it("pins and unpins a chat from its row menu", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(knowledgeApi, "updateSession").mockImplementation(async (id, patch) => {
      const chat = id === originalSession.id ? originalSession : secondSession;
      return { ...chat, pinned: patch.pinned ?? chat.pinned };
    });
    setup();

    await user.click(await screen.findByRole("button", { name: "Actions for Original discussion" }));
    await user.click(screen.getByRole("menuitem", { name: "Pin" }));
    await waitFor(() => expect(update).toHaveBeenCalledExactlyOnceWith(originalSession.id, { pinned: true }));
    await user.click(screen.getByRole("button", { name: "Actions for Original discussion" }));
    await user.click(screen.getByRole("menuitem", { name: "Unpin" }));
    expect(update).toHaveBeenLastCalledWith(originalSession.id, { pinned: false });
  });

  it("renames a chat from its row menu and discards a canceled edit", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(knowledgeApi, "updateSession").mockImplementation(async (_id, patch) =>
      ({ ...originalSession, title: patch.title ?? originalSession.title }));
    setup();

    await user.click(await screen.findByRole("button", { name: "Actions for Original discussion" }));
    await user.click(screen.getByRole("menuitem", { name: "Rename" }));
    let title = screen.getByRole("textbox", { name: "Chat title" });
    await user.clear(title);
    await user.type(title, "Discard this title{Escape}");
    expect(update).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Actions for Original discussion" }));
    await user.click(screen.getByRole("menuitem", { name: "Rename" }));
    title = screen.getByRole("textbox", { name: "Chat title" });
    expect(title).toHaveValue("Original discussion");
    await user.clear(title);
    await user.type(title, "Renamed discussion{Enter}");
    expect(await screen.findByRole("button", { name: /^Renamed discussion/ })).toBeVisible();
    expect(update).toHaveBeenCalledExactlyOnceWith(originalSession.id, { title: "Renamed discussion" });
  });

  it("archives a chat, restores it from the archived list, and keeps the next chat active", async () => {
    const user = userEvent.setup();
    const saved = new Map([originalSession, secondSession].map((chat) => [chat.id, chat]));
    const update = vi.spyOn(knowledgeApi, "updateSession").mockImplementation(async (id, patch) => {
      const previous = saved.get(id)!;
      const chat = { ...previous, archived: patch.archived ?? previous.archived };
      saved.set(id, chat);
      return chat;
    });
    vi.mocked(knowledgeApi.sessions).mockImplementation(async (_id, includeArchived) =>
      [...saved.values()].filter((chat) => includeArchived || !chat.archived));
    vi.mocked(knowledgeApi.session).mockImplementation(async (id) => saved.get(id)!);
    setup();

    await user.click(await screen.findByRole("button", { name: "Actions for Original discussion" }));
    await user.click(screen.getByRole("menuitem", { name: "Archive" }));
    expect(await screen.findByRole("button", { name: /^History discussion/ })).toBeVisible();
    expect(screen.queryByRole("button", { name: /^Original discussion/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Search chats" }));
    await user.click(screen.getByRole("button", { name: "Archived" }));
    await user.click(await screen.findByRole("button", { name: "Actions for Original discussion" }));
    await user.click(screen.getByRole("menuitem", { name: "Restore" }));
    expect(await screen.findByRole("button", { name: /^Original discussion/ })).toBeVisible();
    expect(screen.getByRole("button", { name: "Archived (0)" })).toBeVisible();
    expect(screen.getByRole("textbox", { name: "Message Gunther" })).toBeEnabled();
    expect(update).toHaveBeenLastCalledWith(originalSession.id, { archived: false });
  });

  it("searches chats by title and summary", async () => {
    const user = userEvent.setup();
    vi.mocked(knowledgeApi.sessions).mockResolvedValue([
      originalSession,
      { ...secondSession, summary: "Cell marker notes" },
    ]);
    setup();

    await screen.findByRole("button", { name: /^Original discussion/ });
    await user.click(screen.getByRole("button", { name: "Search chats" }));
    await user.type(screen.getByRole("textbox", { name: "Search chats" }), "cell marker");
    expect(screen.getByRole("button", { name: /^History discussion/ })).toBeVisible();
    expect(screen.queryByRole("button", { name: /^Original discussion/ })).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("textbox", { name: "Search chats" })).toHaveValue("");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("textbox", { name: "Search chats" })).not.toBeInTheDocument();
  });

  it("loads archived chats on each disclosure and hides the count until loaded", async () => {
    const user = userEvent.setup();
    const archived = [{ ...originalSession, id: "archived-1", title: "First archived", archived: true }];
    const list = vi.mocked(knowledgeApi.sessions).mockImplementation(async (_id, includeArchived) =>
      [originalSession, secondSession, ...(includeArchived ? archived : [])]);
    setup();

    await screen.findByRole("button", { name: /^Original discussion/ });
    await user.click(screen.getByRole("button", { name: "Search chats" }));
    const disclosure = screen.getByRole("button", { name: "Archived" });
    expect(disclosure).toHaveAttribute("aria-expanded", "false");
    await user.click(disclosure);
    expect(await screen.findByRole("button", { name: /^First archived/ })).toBeVisible();
    expect(screen.getByRole("button", { name: "Archived (1)" })).toHaveAttribute("aria-expanded", "true");
    await user.click(screen.getByRole("button", { name: "Archived (1)" }));
    expect(screen.queryByRole("button", { name: /^First archived/ })).not.toBeInTheDocument();
    archived.push({ ...originalSession, id: "archived-2", title: "Second archived", archived: true });
    await user.click(screen.getByRole("button", { name: "Archived (1)" }));
    expect(await screen.findByRole("button", { name: /^Second archived/ })).toBeVisible();
    expect(screen.getByRole("button", { name: "Archived (2)" })).toBeVisible();
    expect(list.mock.calls.filter(([, includeArchived]) => includeArchived)).toHaveLength(2);
    await user.click(screen.getByRole("textbox", { name: "Search chats" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("button", { name: /^Second archived/ })).not.toBeInTheDocument();
  });

  it("caps the compact list to pinned chats plus four recent chats and persists collapse", async () => {
    const user = userEvent.setup();
    const recent = Array.from({ length: 7 }, (_, index) => ({
      ...originalSession, id: `recent-${index}`, title: `Recent ${index + 1}`,
    }));
    const chats = [
      { ...originalSession, id: "pinned", title: "Pinned research", pinned: true },
      { ...secondSession, title: "Branch discussion", parentSessionId: originalSession.id },
      ...recent,
      originalSession,
    ];
    vi.mocked(knowledgeApi.sessions).mockResolvedValue(chats);
    vi.mocked(knowledgeApi.session).mockImplementation(async (id) => chats.find((chat) => chat.id === id)!);
    setup();

    await screen.findByRole("button", { name: /^Pinned research/ });
    expect(screen.getByTitle("Branched from Original discussion")).toBeVisible();
    expect(screen.getByRole("button", { name: /^Recent 3/ })).toBeVisible();
    expect(screen.queryByRole("button", { name: /^Recent 5/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "All chats (10)" }));
    expect(screen.getByRole("button", { name: /^Recent 5/ })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Search chats" }));
    await user.click(screen.getByRole("textbox", { name: "Search chats" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("button", { name: /^Recent 5/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All chats (10)" })).toHaveAttribute("aria-expanded", "false");
    await user.click(screen.getByRole("button", { name: "All chats (10)" }));
    await user.click(screen.getByRole("button", { name: /^Chats/ }));
    expect(screen.queryByRole("button", { name: /^Pinned research/ })).not.toBeInTheDocument();
    expect(window.localStorage.getItem(`gunther:library-chats-collapsed:${base.id}`)).toBe("1");
    await user.click(screen.getByRole("button", { name: /^Chats/ }));
    expect(screen.getByRole("button", { name: /^Pinned research/ })).toBeVisible();
    expect(window.localStorage.getItem(`gunther:library-chats-collapsed:${base.id}`)).toBe("0");
  });

  it("keeps an unsaved output edit when opening a chat is canceled", async () => {
    const user = userEvent.setup();
    const confirmLeave = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    setup({ artifacts: [version()] });

    await user.click(await screen.findByRole("button", { name: /Field findings Report Version 1/ }));
    await user.click(await screen.findByRole("button", { name: "Output actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Edit" }));
    const editor = screen.getByRole("textbox", { name: "Report, as Markdown" });
    await user.type(editor, " A kept detail.");
    await user.click(screen.getByRole("button", { name: /^History discussion/ }));
    expect(editor).toBeInTheDocument();
    expect(confirmLeave).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: /^History discussion/ }));
    expect(confirmLeave).toHaveBeenCalledTimes(2);
    expect(editor).not.toBeInTheDocument();
    expect(await screen.findByRole("textbox", { name: "Message Gunther" })).toBeEnabled();
  });

  it("shows the chats read error and retries in the Chats section", async () => {
    const user = userEvent.setup();
    vi.mocked(knowledgeApi.sessions)
      .mockRejectedValueOnce(new ServiceUnavailableError())
      .mockResolvedValue([originalSession, secondSession]);
    setup();

    expect(await screen.findByText("Could not load chats", { exact: false })).toBeVisible();
    expect(screen.getByRole("button", { name: "New chat" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("button", { name: /^Original discussion/ })).toBeVisible();
    expect(screen.getByRole("button", { name: "New chat" })).toBeEnabled();
  });
});

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
    [audioOverview, "lucide-audio-lines"],
  ];
  vi.mocked(knowledgeApi.outputTypes).mockResolvedValue(catalog.map(([type]) => type));
  setup();
  const studio = within(screen.getByRole("complementary", { name: "Studio" }));
  for (const [type, iconClass] of catalog) {
    const tile = await studio.findByRole("button", { name: type.label });
    expect(tile.querySelector("svg")).toHaveClass(iconClass);
  }
});

it("builds an audio overview with the two voices from Studio options", async () => {
  const user = userEvent.setup();
  vi.mocked(knowledgeApi.outputTypes).mockResolvedValue([audioOverview]);
  const { turn } = setup();
  await user.click(await screen.findByRole("button", { name: "Audio overview options" }));
  const options = within(screen.getByRole("form", { name: "Audio overview options" }));
  expect(options.getByLabelText("Speaker A voice")).toHaveValue("cedar");
  expect(options.getByLabelText("Speaker B voice")).toHaveValue("ash");
  await user.selectOptions(options.getByLabelText("Speaker B voice"), "elm");
  await user.click(options.getByRole("button", { name: "Build Audio overview" }));
  await waitFor(() => expect(turn).toHaveBeenCalledExactlyOnceWith(
    base.id, expect.any(String), expect.objectContaining({ build: expect.objectContaining({ type: "audio_overview", voices: ["cedar", "elm"] }) }),
    ws, expect.any(Function), expect.any(AbortSignal),
  ));
});

async function openAudio(user: ReturnType<typeof userEvent.setup>, status: ArtifactAudio["status"] = "ready") {
  vi.mocked(knowledgeApi.outputTypes).mockResolvedValue([audioOverview]);
  const saved = audioVersion(status);
  const artifacts = [saved];
  setup({ artifacts });
  await user.click(await screen.findByRole("button", { name: /Field interview Audio overview Version 1/ }));
  const reader = within(await screen.findByRole("region", { name: "Audio overview" }));
  return { artifacts, saved, reader };
}

it("follows the recording timings, seeks on line clicks, and opens citations without seeking", async () => {
  const user = userEvent.setup();
  const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  const { reader } = await openAudio(user);
  const audio = reader.getByLabelText<HTMLAudioElement>("Audio overview recording");
  const lines = reader.getAllByRole("listitem");
  expect(audio).toHaveAttribute("src", expect.stringContaining("/api/assets/wav-1"));
  audio.currentTime = 1.5;
  fireEvent.timeUpdate(audio);
  expect(lines[1]).toHaveAttribute("aria-current", "true");
  await user.click(reader.getByText("When was the trial?"));
  expect(audio.currentTime).toBe(0);
  expect(lines[0]).toHaveAttribute("aria-current", "true");
  expect(play).toHaveBeenCalledTimes(1);

  await user.click(reader.getByRole("button", { name: "Inspect citation 1" }));
  const evidence = within(screen.getByRole("tabpanel", { name: "Evidence" }));
  expect(evidence.getByText("Observations were recorded at noon.")).toBeVisible();
  expect(audio.currentTime).toBe(0);
  expect(play).toHaveBeenCalledTimes(1);
  await user.click(evidence.getByRole("button", { name: "All 1 source" }));
  await user.click(screen.getByRole("button", { name: "Output actions" }));
  for (const name of ["Download Markdown", "Download WAV", "Print script"]) {
    expect(screen.getByRole("menuitem", { name })).toBeVisible();
  }
});

it("keeps the script readable and opens Read aloud settings when audio needs setup", async () => {
  const user = userEvent.setup();
  const settings = vi.fn();
  window.addEventListener(OPEN_SETTINGS_EVENT, settings, { once: true });
  const { reader } = await openAudio(user, "needs_tts");
  expect(reader.getByText("Set up Read aloud in Settings to generate audio")).toBeVisible();
  expect(reader.getByText("When was the trial?")).toBeVisible();
  expect(reader.queryByLabelText("Audio overview recording")).toBeNull();
  await user.click(reader.getByRole("button", { name: "Open Read aloud settings" }));
  expect(settings).toHaveBeenCalledWith(expect.objectContaining({ detail: { pane: "voice" } }));
});

it("retries and changes voices on the same version while the recording stage stays visible", async () => {
  const user = userEvent.setup();
  const { reader, saved, artifacts } = await openAudio(user, "error");
  let finish!: () => void;
  const recording = vi.spyOn(knowledgeApi, "retryAudioStream").mockImplementation((_base, _id, payload, _ws, hear) => {
    hear({ type: "stage", id: "audio", label: "Recording audio", state: "running", detail: "line 2 of 2" });
    return new Promise((resolve) => {
      finish = () => {
        const ready = audioVersion();
        if (payload.voices) ready.audio = { ...ready.audio!, voices: payload.voices, audioAssetId: "wav-2" };
        artifacts[0] = ready;
        resolve(ready);
      };
    });
  });
  expect(reader.getByRole("alert")).toHaveTextContent("The configured voice is busy.");
  await user.click(reader.getByRole("button", { name: "Retry audio" }));
  expect(recording).toHaveBeenLastCalledWith(base.id, saved.id, {}, ws, expect.any(Function), expect.any(AbortSignal));
  expect(within(screen.getByRole("article", { name: "Open output" })).getByRole("status")).toHaveTextContent("Recording audio · line 2 of 2");
  expect(reader.getByText("When was the trial?")).toBeVisible();
  expect(reader.queryByLabelText("Audio overview recording")).toBeNull();
  act(finish);
  await reader.findByLabelText("Audio overview recording");
  await user.click(reader.getByText("Change voices"));
  await user.selectOptions(reader.getByLabelText("Speaker A voice"), "elm");
  await user.click(reader.getByRole("button", { name: "Record with these voices" }));
  expect(recording).toHaveBeenLastCalledWith(base.id, saved.id, { voices: ["elm", "ash"] }, ws, expect.any(Function), expect.any(AbortSignal));
  expect(reader.queryByLabelText("Audio overview recording")).toBeNull();
  act(finish);
  expect(await reader.findByLabelText("Audio overview recording")).toHaveAttribute("src", expect.stringContaining("/api/assets/wav-2"));
});

it("opens the saved script during a first recording and loads the WAV once it finishes", async () => {
  const user = userEvent.setup();
  vi.mocked(knowledgeApi.outputTypes).mockResolvedValue([audioOverview]);
  const { artifacts, turn } = setup();
  let start!: () => void;
  let finish!: () => void;
  turn.mockImplementation((_base, id, _payload, _ws, hear) => new Promise((resolve) => {
    const script = { ...audioVersion("pending"), conversationId: id };
    start = () => {
      artifacts.push(script);
      hear({ type: "stage", id: "audio", label: "Recording audio", state: "running", detail: "line 1 of 2" });
    };
    finish = () => {
      const ready = { ...script, audio: audioVersion().audio! };
      artifacts[0] = ready;
      hear({ type: "stage", id: "audio", label: "Recording audio", state: "done" });
      resolve(done(ready));
    };
  }));
  const tile = await screen.findByRole("button", { name: "Audio overview" });
  await user.click(tile);
  await waitFor(() => expect(turn).toHaveBeenCalledTimes(1));
  await user.click(tile);
  act(start);
  const reader = within(await screen.findByRole("region", { name: "Audio overview" }));
  expect(within(screen.getByRole("article", { name: "Open output" })).getByRole("status")).toHaveTextContent("Recording audio · line 1 of 2");
  expect(reader.getByText("When was the trial?")).toBeVisible();
  expect(reader.queryByLabelText("Audio overview recording")).toBeNull();
  act(finish);
  expect(await reader.findByLabelText("Audio overview recording")).toHaveAttribute("src", expect.stringContaining("/api/assets/wav-1"));
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
  expect(screen.getByRole("button", { name: "Expand Library" })).toBeVisible();
  await user.click(reader.getByRole("button", { name: "Inspect citation 1" }));
  const evidence = within(screen.getByRole("tabpanel", { name: "Evidence" }));
  expect(evidence.getByText("Observations were recorded at noon.")).toBeVisible();
  expect(send).not.toHaveBeenCalled();
  await user.click(evidence.getByRole("button", { name: "All 1 source" }));
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
  await user.click(screen.getByRole("button", { name: /^History discussion/ }));
  await waitFor(() => expect(button).toBeEnabled());
  await user.click(button);
  expect(send).toHaveBeenLastCalledWith(secondSession.id, expect.objectContaining({ selectedSourceIds: ["s1", "s2"], focusChapterId: null }), expect.any(Function), expect.any(AbortSignal));
  expect(JSON.parse(window.localStorage.getItem(`gunther:library-sources:${base.id}`)!)).toEqual({ mode: "selected", sourceIds: ["s1", "s2"] });
});

it("edits and retries the Brief and opens Trace inside Evidence", async () => {
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
    brief: { goal: "Summarize the field records", constraints: [], settled: [], open: [], edited: [], error: "Update failed" },
  });
  let finishRefresh!: (value: NonNullable<KnowledgeSession["brief"]>) => void;
  const refresh = vi.spyOn(knowledgeApi, "refreshBrief").mockImplementation(() => new Promise((resolve) => { finishRefresh = resolve; }));
  const edit = vi.spyOn(knowledgeApi, "editBrief").mockImplementation(async (_id, input) => ({ ...input, edited: [], error: null }));
  vi.spyOn(knowledgeApi, "developerSettings").mockResolvedValue({ traces: true });
  const readTrace = vi.spyOn(knowledgeApi, "messageTrace").mockResolvedValue({ version: 1, totalMs: 12, steps: [] });
  setup();

  await screen.findByRole("button", { name: "Trace" });
  expect(document.querySelector(".conversation-header details")).toBeNull();
  await user.click(screen.getByRole("tab", { name: "Evidence (1)" }));
  const evidence = screen.getByRole("tabpanel", { name: "Evidence" });
  const brief = within(evidence).getByText("Brief", { selector: "summary" });
  await user.click(brief);
  expect(screen.getByText("Summarize the field records")).toBeVisible();
  await user.click(within(evidence).getByRole("button", { name: "Retry" }));
  expect(refresh).toHaveBeenCalledWith(originalSession.id);
  expect(within(evidence).getByText("Updating…")).toBeVisible();
  await act(async () => finishRefresh({ goal: "Summarize the field records", constraints: [], settled: [], open: [], edited: [], error: null }));
  await user.click(within(evidence).getByRole("button", { name: "Summarize the field records" }));
  const goal = within(evidence).getByRole("textbox", { name: "Edit goal" });
  await user.clear(goal);
  await user.type(goal, "Compare records{Enter}");
  await waitFor(() => expect(edit).toHaveBeenCalledWith(originalSession.id, expect.objectContaining({ goal: "Compare records" })));
  expect(await within(evidence).findByRole("button", { name: "Compare records" })).toBeVisible();
  await user.click(screen.getByRole("button", { name: "1 source · 1 web" }));
  await user.click(within(evidence).getByRole("button", { name: /Field log/ }));
  expect(evidence).toHaveTextContent("A recorded observation.");
  await user.click(within(evidence).getByRole("button", { name: "All 1 source" }));
  await user.click(screen.getByRole("button", { name: "Trace" }));
  expect(within(evidence).getByRole("button", { name: "← Back to evidence" })).toBeVisible();
  expect(document.querySelector(".ask-trace-overlay")).toBeNull();
  expect(readTrace).toHaveBeenCalledWith(originalSession.id, answer.id);
  await user.keyboard("{Escape}");
  expect(within(evidence).queryByRole("button", { name: "← Back to evidence" })).toBeNull();
});

it("keeps the empty chat Evidence scope and Web status in sync with the composer", async () => {
  const user = userEvent.setup();
  vi.spyOn(knowledgeApi, "health").mockResolvedValue({
    status: "ok", extractionMode: "local", webSearchMode: "tavily", transcriptionMode: "not_configured",
    transcriptionProvider: "none", transcriptionModel: "", summaryMode: "off",
  });
  setup();
  await user.click(await screen.findByRole("tab", { name: "Evidence (0)" }));
  expect(await screen.findByText("All 3 sources")).toBeVisible();
  expect(screen.getByText("Web off")).toBeVisible();
  await user.click(screen.getByRole("checkbox", { name: "Select Field notes" }));
  expect(screen.getByText("2 of 3 selected")).toBeVisible();
  const web = screen.getByRole("button", { name: "Web" });
  await waitFor(() => expect(web).toBeEnabled());
  await user.click(web);
  expect(screen.getByText("Web on")).toBeVisible();
});

it("keeps Studio selected when an answer finishes, then opens and steps through its citations", async () => {
  const user = userEvent.setup();
  const citations = [1, 2].map((number) => ({ id: `web-${number}`, kind: "web" as const, url: `https://example.org/${number}`,
    sourceId: "", sourceTitle: `Web source ${number}`, assertionId: null, quote: `Passage ${number}`,
    locator: "example.org", status: "provisional" as const, confidence: 0 }));
  const question: SessionMessage = { id: "new-question", sessionId: originalSession.id, role: "user", content: "What changed?",
    citations: [], createdAt: at, context: { sourcesConsidered: 0, assertionsConsidered: 0, verifiedAssertions: 0, retrievalMode: "all", responderMode: "model" } };
  const answer: SessionMessage = { ...question, id: "new-answer", role: "assistant", content: "One [1] and two [2].", citations };
  vi.spyOn(knowledgeApi, "sendMessageStream").mockResolvedValue({ session: { ...originalSession, messageCount: 2 }, userMessage: question, assistantMessage: answer });
  vi.spyOn(knowledgeApi, "refreshBrief").mockResolvedValue({ goal: "", constraints: [], settled: [], open: [], edited: [], error: null });
  setup();
  const composer = screen.getByRole("textbox", { name: "Message Gunther" });
  await waitFor(() => expect(composer).toBeEnabled());
  expect(screen.getByRole("tab", { name: "Studio" })).toHaveAttribute("aria-selected", "true");
  await user.type(composer, "What changed?{Enter}");
  await screen.findByRole("button", { name: "2 sources · 2 web" });
  expect(screen.getByRole("tab", { name: "Studio" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: "Evidence (2)" })).toHaveAttribute("aria-selected", "false");

  await user.click(screen.getByRole("button", { name: "2 sources · 2 web" }));
  const evidence = within(screen.getByRole("tabpanel", { name: "Evidence" }));
  expect(evidence.getByRole("heading", { name: "Cited (2)" })).toBeVisible();
  await user.click(evidence.getByRole("button", { name: /Web source 1/ }));
  expect(evidence.getByText("Passage 1")).toBeVisible();
  expect(evidence.getByText("[1 / 2]")).toBeVisible();
  await user.click(evidence.getByRole("button", { name: "Next source" }));
  expect(evidence.getByText("Passage 2")).toBeVisible();
  await user.click(evidence.getByRole("button", { name: "All 2 sources" }));
  expect(evidence.getByRole("heading", { name: "Cited (2)" })).toBeVisible();
  await user.click(screen.getByRole("tab", { name: "Studio" }));
  await user.click(screen.getByRole("button", { name: "Inspect citation 1" }));
  expect(screen.getByRole("tab", { name: "Evidence (2)" })).toHaveAttribute("aria-selected", "true");
  expect(within(screen.getByRole("tabpanel", { name: "Evidence" })).getByText("Passage 1")).toBeVisible();
});

it("changes the selected answer without switching tabs and clears its old detail", async () => {
  const user = userEvent.setup();
  const citation = (id: string) => ({ id, kind: "web" as const, url: `https://example.org/${id}`, sourceId: "",
    sourceTitle: id, assertionId: null, quote: `${id} passage`, locator: "example.org", status: "provisional" as const, confidence: 0 });
  const context = { sourcesConsidered: 1, assertionsConsidered: 1, verifiedAssertions: 1, retrievalMode: "all" as const, responderMode: "model" as const };
  const first: SessionMessage = { id: "first-answer", sessionId: originalSession.id, role: "assistant", content: "Earlier answer [1].", createdAt: at, context, citations: [citation("earlier")] };
  const last: SessionMessage = { ...first, id: "last-answer", content: "Latest answer [1] [2].", citations: [citation("first"), citation("second")] };
  vi.mocked(knowledgeApi.session).mockResolvedValue({ ...originalSession, messages: [first, last], messageCount: 2 });
  setup();
  await screen.findByText("Latest answer", { exact: false });
  expect(screen.getByRole("tab", { name: "Evidence (2)" })).toBeVisible();
  await user.click(screen.getAllByRole("button", { name: "Inspect citation 1" })[1]!);
  expect(within(screen.getByRole("tabpanel", { name: "Evidence" })).getByText("first passage")).toBeVisible();
  await user.click(screen.getByText("Earlier answer", { exact: false }));
  expect(screen.getByRole("tab", { name: "Evidence (1)" })).toHaveAttribute("aria-selected", "true");
  expect(screen.queryByRole("button", { name: "All 2 sources" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "Studio" }));
  await user.click(screen.getByText("Latest answer", { exact: false }));
  expect(screen.getByRole("tab", { name: "Studio" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: "Evidence (2)" })).toHaveAttribute("aria-selected", "false");
});

it("keeps Evidence selected when an output opens from Activity", async () => {
  const user = userEvent.setup();
  const { view, onAdd, onNotify, onTrashed } = setup({ artifacts: [version()] });
  await user.click(await screen.findByRole("tab", { name: "Evidence (0)" }));
  view.rerender(<KnowledgeBaseWorkspace base={base} workspaceId={ws} onOpenSource={vi.fn()} onNotify={onNotify}
    onAdd={onAdd} onTrashed={onTrashed} outputActivity={{ view: "library", libraryId: base.id, tab: "outputs", artifactId: "version-1" }} />);
  expect(await screen.findByRole("article", { name: "Open output" })).toBeVisible();
  expect(screen.getByRole("tab", { name: "Evidence (0)" })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByText(/Loading evidence|Nothing cited/)).toBeVisible();
});

it("groups output citations, follows the visible section and steps through the saved version", async () => {
  const user = userEvent.setup();
  const citations = Array.from({ length: 14 }, (_, index) => ({
    id: `cite-${index + 1}`, kind: index === 5 || index === 6 ? "web" as const : "library" as const,
    url: index === 5 || index === 6 ? "https://example.org/record" : null,
    sourceId: index < 5 ? "s1" : index === 5 || index === 6 ? "" : `source-${index}`,
    sourceTitle: index < 5 ? "Lab notes" : index === 5 || index === 6 ? "Web record" : `Source ${index + 1}`,
    assertionId: null, quote: `Passage ${index + 1}`, locator: `Page ${index + 1}`,
    status: "verified" as const, confidence: 1,
  }));
  const newer = version({ id: "version-2", versionNumber: 2, supersedesArtifactId: "version-1", citations,
    content: "# Findings\n\n## First\nFirst [1] [2] [3] [4] [5].\n\n## Second\nSecond [6] [7] [8] [9] [10] [11] [12] [13] [14]." });
  const older = version({ citations: [citations[0]!], content: "# Findings\n\n## Earlier\nEarlier [1]." });
  setup({ artifacts: [older, newer] });
  await user.click(await screen.findByRole("button", { name: /Field findings Report Version 2/ }));
  const reader = screen.getByRole("article", { name: "Open output" });
  await waitFor(() => expect(screen.getByRole("tab", { name: "Evidence (14)" })).toBeVisible());
  expect(screen.getByRole("tab", { name: "Studio" })).toHaveAttribute("aria-selected", "true");
  await user.click(screen.getByRole("tab", { name: "Evidence (14)" }));
  const pane = within(screen.getByRole("tabpanel", { name: "Evidence" }));
  expect(pane.getByText(/Scope: 1 source · Not checked · Version 2/)).toBeVisible();
  const groups = pane.getByRole("region", { name: "Cited sources" }).querySelectorAll(".evidence-source-group");
  expect(groups[0]).toHaveTextContent("Lab notes");
  expect(groups[0]).toHaveTextContent("Passage 1");
  expect(groups[1]).toHaveTextContent("Web record");
  expect(within(groups[1] as HTMLElement).getAllByRole("button")).toHaveLength(2);
  await user.click(within(groups[0] as HTMLElement).getByRole("button", { name: "+1 more" }));
  await user.click(pane.getByRole("button", { name: "Inspect citation 5" }));
  expect(pane.getByText("[5 / 14]")).toBeVisible();
  await user.click(pane.getByRole("button", { name: "Next source" }));
  expect(pane.getByText("[6 / 14]")).toBeVisible();
  await user.click(pane.getByRole("button", { name: "All 14 sources" }));

  const scroller = reader.closest<HTMLElement>(".library-middle-page")!;
  const sections = reader.querySelectorAll<HTMLElement>("[data-section]");
  vi.spyOn(scroller, "getBoundingClientRect").mockReturnValue({ top: 0 } as DOMRect);
  vi.spyOn(sections[0]!, "getBoundingClientRect").mockReturnValue({ bottom: -1 } as DOMRect);
  vi.spyOn(sections[1]!, "getBoundingClientRect").mockReturnValue({ bottom: 100 } as DOMRect);
  fireEvent.scroll(scroller);
  await user.click(pane.getByRole("button", { name: "This section" }));
  expect(pane.queryByRole("button", { name: "Inspect citation 1" })).toBeNull();
  expect(pane.getByRole("button", { name: "Inspect citation 6" })).toBeVisible();
  await user.click(within(reader).getByRole("button", { name: "Inspect citation 8" }));
  expect(pane.getByText("[8 / 14]")).toBeVisible();
  await user.click(pane.getByRole("button", { name: "Next source" }));
  expect(pane.getByText("[9 / 14]")).toBeVisible();

  await user.click(screen.getByRole("button", { name: "Output actions" }));
  await user.click(screen.getByRole("menuitem", { name: "Versions" }));
  await user.click(screen.getByRole("button", { name: "Version 1" }));
  await waitFor(() => expect(screen.getByRole("tab", { name: "Evidence (1)" })).toHaveAttribute("aria-selected", "true"));
  expect(pane.queryByText("[9 / 14]")).toBeNull();
  expect(pane.getByText(/Version 1/)).toBeVisible();
});

it("shows the source's eligible key results and opens their original passages", async () => {
  const user = userEvent.setup();
  const read = vi.spyOn(knowledgeApi, "keyResults").mockImplementation(async (_base, _signal, ids) =>
    ids?.length === 1 && ids[0] === "s1" ? [finding] : []);
  vi.spyOn(knowledgeApi, "source").mockImplementation(async (id) => ({ ...makeSource({ id }), content: "", assertions: [], webSnapshot: null }));
  vi.spyOn(knowledgeApi, "sourceStructure").mockResolvedValue({ sourceId: "s1", revisionId: "revision-1",
    processing: { state: "ready", jobId: null, revisionId: "revision-1", warning: null }, parser: null, nextOffset: null, blocks: [] });
  setup();
  await user.click(await screen.findByRole("button", { name: /^Lab notes/ }));
  expect(screen.getByRole("tab", { name: "Studio" })).toHaveAttribute("aria-selected", "true");
  const evidenceTab = await screen.findByRole("tab", { name: "Evidence (1)" });
  expect(evidenceTab).toHaveAttribute("aria-selected", "false");
  await user.click(evidenceTab);
  const pane = within(screen.getByRole("tabpanel", { name: "Evidence" }));
  await user.click(await pane.findByRole("button", { name: finding.text }));
  expect(read).toHaveBeenCalledWith(base.id, expect.any(AbortSignal), ["s1"]);
  expect(pane.getByText("The air warmed after noon.")).toBeVisible();
  expect(pane.getByRole("combobox", { name: "Passage" })).toBeVisible();
});

it("clears a source Evidence count while reloading and after a load error", async () => {
  const user = userEvent.setup();
  let rejectReload!: (error: Error) => void;
  let sourceReads = 0;
  vi.spyOn(knowledgeApi, "keyResults").mockImplementation(async (_base, _signal, ids) => {
    if (!ids?.includes("s1")) return [];
    sourceReads += 1;
    if (sourceReads === 1) return [finding];
    return new Promise<KeyResult[]>((_resolve, reject) => { rejectReload = reject; });
  });
  setup();
  await user.click(await screen.findByRole("button", { name: /^Lab notes/ }));
  await screen.findByRole("tab", { name: "Evidence (1)" });
  act(() => keyResultsChanged(base.id));
  await screen.findByRole("tab", { name: "Evidence (0)" });
  await user.click(screen.getByRole("tab", { name: "Evidence (0)" }));
  expect(screen.getByText("Loading key results…")).toBeVisible();
  await act(async () => rejectReload(new Error("Unavailable")));
  expect(await screen.findByText(/Could not load key results/)).toBeVisible();
  expect(screen.getByRole("tab", { name: "Evidence (0)" })).toHaveAttribute("aria-selected", "true");
});

it("follows the open slide when filtering output Evidence", async () => {
  const user = userEvent.setup();
  const citations = Array.from({ length: 13 }, (_, index) => ({ id: `slide-cite-${index}`, sourceId: `s${index}`,
    sourceTitle: `Slide source ${index + 1}`, assertionId: null, quote: `Slide passage ${index + 1}`,
    locator: "", status: "verified" as const, confidence: 1 }));
  const deck = version({ kind: "slides", format: "slides", style: "slides", outputType: "Slides", title: "Field deck",
    content: "# First slide\n[1]\n\n---\n\n# Second slide\n[13]", citations });
  setup({ artifacts: [deck] });
  await user.click(await screen.findByRole("button", { name: /Field deck Slides Version 1/ }));
  await user.click(await screen.findByRole("tab", { name: "Evidence (13)" }));
  const pane = within(screen.getByRole("tabpanel", { name: "Evidence" }));
  await user.click(pane.getByRole("button", { name: "This section" }));
  expect(pane.getByRole("button", { name: "Inspect citation 1" })).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Show second slide" }));
  expect(pane.queryByRole("button", { name: "Inspect citation 1" })).toBeNull();
  expect(pane.getByRole("button", { name: "Inspect citation 13" })).toBeVisible();
});

it("opens a source independently of scope and keeps the Chat draft and scroll position", async () => {
  const user = userEvent.setup();
  setup();
  await screen.findByRole("button", { name: /^Original discussion/ });
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
  const evidence = within(screen.getByRole("tabpanel", { name: "Evidence" }));
  expect(evidence.getByText(quote)).toBeVisible();
  expect(structure).toHaveBeenCalledWith(lab.id, 0, digest.revisionId, null, 500);
  await user.click(evidence.getByRole("button", { name: "All 1 source" }));
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
  const evidence = within(screen.getByRole("tabpanel", { name: "Evidence" }));
  expect(evidence.getByText("Readings were recorded at noon.")).toBeVisible();
  await user.click(evidence.getByRole("button", { name: "All 1 source" }));
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
  const evidence = within(screen.getByRole("tabpanel", { name: "Evidence" }));
  expect(evidence.getByText("The air warmed after noon.")).toBeVisible();
  expect(evidence.getByRole("heading", { name: "Page 3" })).toBeVisible();
  await user.selectOptions(evidence.getByRole("combobox", { name: "Passage" }), "passage-2");
  expect(evidence.getByText("Humidity fell during the afternoon.")).toBeVisible();
  expect(evidence.getByRole("heading", { name: "Page 5" })).toBeVisible();
  expect(evidence.getByRole("option", { name: /Field notes · Page 5/ })).toBeInTheDocument();
  expect(structure).toHaveBeenLastCalledWith("s2", 0, "revision-2", null, 500);
  await user.click(evidence.getByRole("button", { name: "All 2 sources" }));
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
  const { view } = setup();
  const user = userEvent.setup();
  const group = within(await screen.findByRole("region", { name: "Key results" }));
  const header = group.getByRole("button", { name: "Key results (6)" });
  expect(screen.getByRole("region", { name: "Sources" })).not.toContainElement(header);
  expect(header).toHaveAttribute("aria-expanded", "false");
  await user.type(screen.getByRole("textbox", { name: "Filter contents" }), "Finding 3");
  expect(header).toHaveAttribute("aria-expanded", "false");
  await user.click(header);
  expect(window.localStorage.getItem(`gunther:library-kr-collapsed:${base.id}`)).toBe("0");
  expect(group.getByRole("button", { name: /Finding 1 from your question/ })).toBeVisible();
  await user.type(group.getByRole("textbox", { name: "Filter key results" }), "Finding 3");
  expect(group.getByRole("button", { name: /Finding 3 from your question/ })).toBeVisible();
  expect(group.queryByRole("button", { name: /Finding 1/ })).not.toBeInTheDocument();
  await user.click(group.getByText("Saved answers awaiting key results"));
  expect(group.getByText("Stored wording.")).toBeVisible();
  pending.mockResolvedValue([]);
  act(() => keyResultsChanged(base.id));
  await waitFor(() => expect(group.queryByText("Saved answers awaiting key results")).not.toBeInTheDocument());
  view.unmount();
  setup();
  const reopened = await screen.findByRole("button", { name: "Key results (6)" });
  expect(reopened).toHaveAttribute("aria-expanded", "true");
  await user.click(reopened);
  expect(window.localStorage.getItem(`gunther:library-kr-collapsed:${base.id}`)).toBe("1");
});

it("keeps pending saved answers accessible when zero key results are collapsed", async () => {
  const user = userEvent.setup();
  vi.spyOn(knowledgeApi, "keyResults").mockResolvedValue([]);
  vi.spyOn(knowledgeApi, "pendingSavedAnswers").mockResolvedValue([
    { id: "saved-1", title: "Prior answer", content: "Stored wording." },
  ]);
  setup();
  const group = within(await screen.findByRole("region", { name: "Key results" }));
  expect(await group.findByRole("button", { name: "Key results (0)" })).toHaveAttribute("aria-expanded", "false");
  await user.click(group.getByText("Saved answers awaiting key results"));
  expect(group.getByText("Stored wording.")).toBeVisible();
  expect(group.getByRole("button", { name: "Key results (0)" })).toHaveAttribute("aria-expanded", "false");
});

it.each([0, 1, 3, 4, 5, 6])("uses independent default disclosure and filter rules for %s key results", async (count) => {
  const rows = Array.from({ length: count }, (_, index): KeyResult => ({ ...finding, id: `result-${index}` }));
  vi.spyOn(knowledgeApi, "keyResults").mockResolvedValue(rows);
  setup();
  const header = await screen.findByRole("button", { name: `Key results (${count})` });
  const group = within(screen.getByRole("region", { name: "Key results" }));
  expect(header).toHaveAttribute("aria-expanded", count > 0 && count <= 3 ? "true" : "false");
  if (header.getAttribute("aria-expanded") === "false") await userEvent.click(header);
  expect(Boolean(group.queryByRole("textbox", { name: "Filter key results" }))).toBe(count > 5);
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
  const beforeRefresh = keyResults.mock.calls.length;
  act(() => window.dispatchEvent(new Event("gunther:sources-updated")));
  await waitFor(() => expect(keyResults.mock.calls.length).toBeGreaterThan(beforeRefresh));
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
  expect(column("Collapse Inspector")).toHaveAttribute("aria-expanded", "true");
  resizeWindow(1079);
  expect(column("Expand Studio")).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("button", { name: "Slides" })).not.toBeInTheDocument();
  await user.click(column("Expand Studio"));
  expect(screen.getByRole("button", { name: "Slides" })).toBeVisible();
  await user.click(column("Collapse Inspector"));
  resizeWindow(840);
  expect(column("Collapse Library")).toBeVisible();
  resizeWindow(839);
  expect(column("Expand Library")).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("checkbox", { name: "Select Lab notes" })).not.toBeInTheDocument();
  await user.click(column("Expand Library"));
  expect(screen.getByRole("checkbox", { name: "Select Lab notes" })).toBeChecked();
  expect(document.querySelector(".library-sources")).toHaveClass("is-narrow");
  expect(screen.queryByRole("separator", { name: "Resize Library" })).not.toBeInTheDocument();
});

it("closes the narrow Library drawer on Escape, outside clicks, and selecting a source or chat", async () => {
  const user = userEvent.setup();
  resizeWindow(800);
  setup();
  const expand = () => screen.getByRole("button", { name: "Expand Library" });
  await user.click(expand());
  await screen.findByRole("checkbox", { name: "Select Lab notes" });
  await user.click(screen.getByRole("button", { name: "Actions for Original discussion" }));
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("menuitem", { name: "Rename" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Collapse Library" })).toHaveAttribute("aria-expanded", "true");
  const filter = screen.getByRole("textbox", { name: "Filter contents" });
  await user.type(filter, "Lab");
  await user.keyboard("{Escape}");
  expect(filter).toHaveValue("");
  expect(screen.getByRole("button", { name: "Collapse Library" })).toHaveAttribute("aria-expanded", "true");
  await user.keyboard("{Escape}");
  expect(expand()).toHaveAttribute("aria-expanded", "false");

  await user.click(expand());
  await user.click(screen.getByRole("textbox", { name: "Message Gunther" }));
  expect(expand()).toHaveAttribute("aria-expanded", "false");
  await user.click(expand());
  await user.click(within(screen.getByRole("region", { name: "Sources" })).getByRole("button", { name: /^Lab notes/ }));
  expect(expand()).toHaveAttribute("aria-expanded", "false");
  expect(await screen.findByRole("button", { name: "Back to chat" })).toBeVisible();
  await user.click(expand());
  await user.click(within(screen.getByRole("region", { name: "Chats" })).getByRole("button", { name: /^History discussion/ }));
  expect(expand()).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByRole("textbox", { name: "Message Gunther" })).toBeVisible();
});

it("keeps the narrow Library open while choosing a destination in its portaled picker", async () => {
  const user = userEvent.setup();
  resizeWindow(800);
  const move = vi.spyOn(knowledgeApi, "moveContents").mockResolvedValue(contents);
  setup();
  await user.click(screen.getByRole("button", { name: "Expand Library" }));
  await user.click(await screen.findByRole("button", { name: "Actions for Lab notes" }));
  await user.click(screen.getByRole("menuitem", { name: "Move to…" }));
  const picker = screen.getByRole("dialog", { name: "Move to…" });
  expect(document.querySelector(".library-workspace")).not.toContainElement(picker);
  await user.click(within(picker).getByRole("option", { name: "Fieldwork" }));
  expect(move).toHaveBeenCalledWith(base.id, { items: [{ kind: "source", id: "s1" }], parentId: "fieldwork", index: null });
  expect(screen.getByRole("button", { name: "Collapse Library" })).toBeVisible();
  await user.click(screen.getByRole("textbox", { name: "Message Gunther" }));
  expect(screen.getByRole("button", { name: "Expand Library" })).toBeVisible();
});

it("closes BuildOptions before the narrow Inspector on Escape", async () => {
  const user = userEvent.setup();
  resizeWindow(900);
  setup();
  await user.click(screen.getByRole("button", { name: "Expand Studio" }));
  await user.click(await screen.findByRole("button", { name: "Report options" }));
  expect(screen.getByRole("form", { name: "Report options" })).toBeVisible();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("form", { name: "Report options" })).toBeNull();
  expect(screen.getByRole("button", { name: "Collapse Inspector" })).toBeVisible();
  await user.keyboard("{Escape}");
  expect(screen.getByRole("button", { name: "Expand Studio" })).toBeVisible();
});

it("reopens Sources when the composer asks to change its scope", async () => {
  const user = userEvent.setup();
  setup();
  await screen.findByRole("checkbox", { name: "Select Lab notes" });
  await user.click(screen.getByRole("button", { name: "Sources" }));
  expect(screen.queryByRole("textbox", { name: "Filter contents" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "All sources" }));
  expect(screen.getByRole("textbox", { name: "Filter contents" })).toBeVisible();
});

it("handles chat and key result controls before closing the narrow Library drawer on Escape", async () => {
  const user = userEvent.setup();
  resizeWindow(800);
  vi.spyOn(knowledgeApi, "keyResults").mockResolvedValue(Array.from({ length: 6 }, (_, index): KeyResult => ({
    ...finding, id: `result-${index}`, text: `Finding ${index}`,
  })));
  setup();

  await user.click(screen.getByRole("button", { name: "Expand Library" }));
  await user.click(await screen.findByRole("button", { name: "Search chats" }));
  expect(screen.getByRole("textbox", { name: "Search chats" })).toHaveFocus();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("textbox", { name: "Search chats" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Collapse Library" })).toHaveAttribute("aria-expanded", "true");
  await user.click(screen.getByRole("button", { name: "Search chats" }));
  const search = screen.getByRole("textbox", { name: "Search chats" });
  await user.type(search, "Original");
  await user.keyboard("{Escape}");
  expect(search).toHaveValue("");
  expect(screen.getByRole("button", { name: "Collapse Library" })).toHaveAttribute("aria-expanded", "true");
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("textbox", { name: "Search chats" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Collapse Library" })).toHaveAttribute("aria-expanded", "true");

  await user.click(screen.getByRole("button", { name: "Actions for Original discussion" }));
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("menuitem", { name: "Rename" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Collapse Library" })).toHaveAttribute("aria-expanded", "true");
  await user.click(screen.getByRole("button", { name: "Actions for Original discussion" }));
  await user.click(screen.getByRole("menuitem", { name: "Rename" }));
  const title = screen.getByRole("textbox", { name: "Chat title" });
  await user.clear(title);
  await user.type(title, "Uncommitted title");
  await user.tab();
  expect(screen.getByRole("button", { name: "Save chat title" })).toHaveFocus();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("textbox", { name: "Chat title" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Collapse Library" })).toHaveAttribute("aria-expanded", "true");

  const group = within(await screen.findByRole("region", { name: "Key results" }));
  await user.click(await group.findByRole("button", { name: "Key results (6)" }));
  const filter = group.getByRole("textbox", { name: "Filter key results" });
  await user.type(filter, "Finding 3{Escape}");
  expect(filter).toHaveValue("");
  expect(screen.getByRole("button", { name: "Collapse Library" })).toHaveAttribute("aria-expanded", "true");
  await user.keyboard("{Escape}");
  expect(screen.getByRole("button", { name: "Expand Library" })).toHaveAttribute("aria-expanded", "false");
});

it("closes inspected evidence before the narrow Library drawer on Escape", async () => {
  const user = userEvent.setup();
  resizeWindow(800);
  vi.spyOn(knowledgeApi, "keyResults").mockResolvedValue([finding]);
  vi.spyOn(knowledgeApi, "source").mockImplementation(async (id) => ({
    ...makeSource({ id }), content: "", assertions: [], webSnapshot: null,
  }));
  vi.spyOn(knowledgeApi, "sourceStructure").mockResolvedValue({
    sourceId: "s1", revisionId: "revision-1",
    processing: { state: "ready", jobId: null, revisionId: "revision-1", warning: null },
    parser: null, nextOffset: null, blocks: [],
  });
  setup();
  await user.click(screen.getByRole("button", { name: "Expand Library" }));
  const group = within(await screen.findByRole("region", { name: "Key results" }));
  await user.click(await group.findByRole("button", { name: /Field conditions changed/ }));
  expect(screen.getByRole("tabpanel", { name: "Evidence" })).toBeVisible();
  expect(screen.getByRole("button", { name: "All 2 sources" })).toBeVisible();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("button", { name: "All 2 sources" })).not.toBeInTheDocument();
  expect(screen.getByRole("tabpanel", { name: "Evidence" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Collapse Library" })).toHaveAttribute("aria-expanded", "true");
  await user.keyboard("{Escape}");
  expect(screen.getByRole("button", { name: "Expand Evidence" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Collapse Library" })).toHaveAttribute("aria-expanded", "true");
  await user.keyboard("{Escape}");
  expect(screen.getByRole("button", { name: "Expand Library" })).toHaveAttribute("aria-expanded", "false");
});

it("resizes the columns from their inner edges and remembers the widths", async () => {
  const user = userEvent.setup();
  setup();
  await screen.findByRole("checkbox", { name: "Select Lab notes" });
  const sourcesWidth = screen.getByRole("separator", { name: "Resize Library" });
  const studioWidth = screen.getByRole("separator", { name: "Resize Inspector" });
  expect(sourcesWidth).toHaveAttribute("aria-valuemin", "200");
  expect(sourcesWidth).toHaveAttribute("aria-valuemax", "360");
  expect(sourcesWidth).toHaveAttribute("aria-valuenow", "260");
  const widen = (separator: HTMLElement, key: string) => {
    const before = Number(separator.getAttribute("aria-valuenow"));
    fireEvent.keyDown(separator, { key });
    const after = Number(separator.getAttribute("aria-valuenow"));
    expect(after).toBeGreaterThan(before);
    return after;
  };
  const widerSources = widen(sourcesWidth, "ArrowRight");
  const widerStudio = widen(studioWidth, "ArrowLeft");
  await user.click(screen.getByRole("button", { name: "Collapse Library" }));
  await user.click(screen.getByRole("button", { name: "Expand Library" }));
  expect(screen.getByRole("separator", { name: "Resize Library" })).toHaveAttribute("aria-valuenow", String(widerSources));
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
  expect(screen.getByRole("button", { name: "Collapse Library" })).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Back to chat" }));
  resizeWindow(1199);
  expect(screen.getByRole("button", { name: "Collapse Library" })).toBeVisible();
  await user.click(screen.getByRole("button", { name: /Field findings Slides Version 1/ }));
  await user.click(await screen.findByRole("button", { name: "Expand Library" }));
  expect(screen.getByRole("checkbox", { name: "Select Lab notes" })).toBeChecked();
  await user.click(screen.getByRole("button", { name: "Collapse Library" }));
  await user.click(screen.getByRole("button", { name: "Back to chat" }));
  expect(screen.getByRole("button", { name: "Collapse Library" })).toBeVisible();
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
