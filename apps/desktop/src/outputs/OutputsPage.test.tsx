import type { Artifact, ArtifactSummary, ConversationCitation, KnowledgeUnit, LibraryContents, ModelMenu } from "@gunther/contracts";
import { invoke } from "@tauri-apps/api/core";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi, OutputStoppedError, type OutputEvent } from "../api";
import { OPEN_SETTINGS_EVENT } from "../models/askModel";
import { makeBase, makeSource } from "../test/fixtures";
import { OutputsPage } from "./OutputsPage";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

// CodeMirror is a contenteditable surface; a plain textarea stands in for it here.
vi.mock("../components/markdown/MarkdownEditor", () => ({
  MarkdownEditor: ({ value, onChange, ariaLabel }: { value: string; onChange: (value: string) => void; ariaLabel: string }) => (
    <textarea aria-label={ariaLabel} value={value} onChange={(event) => onChange(event.target.value)} />
  ),
}));

const menu: ModelMenu = {
  models: [{ ref: "deepseek/deepseek-flash", provider: "DeepSeek", label: "Flash", display: "DeepSeek · Flash", vision: false, levels: [] }],
  default: { model: "deepseek/deepseek-flash", effort: "high" },
  efforts: [],
};

const citation: ConversationCitation = {
  id: "cit_1", kind: "web", url: "https://www.example.org/markers", sourceId: "", sourceTitle: "Marker review", assertionId: null,
  quote: "CD3D is a marker of T cells.", locator: "example.org", status: "provisional", confidence: 0, ref: 1,
};

const unit = (id: string, title: string, status: KnowledgeUnit["status"] = "trusted"): KnowledgeUnit => ({
  id, knowledgeBaseId: "biology", title, kind: "knowledge_unit", status, content: "x", revisionCount: 1, sourceProposalId: "prp_1",
  sourceSessionId: "ses_1", sourceMessageId: "msg_1", targetChapterId: null, evidenceCount: 1, createdAt: "2026-10-01T00:00:00.000Z", updatedAt: "2026-10-01T00:00:00.000Z",
});

function version(number = 1, overrides: Partial<Artifact> = {}): Artifact {
  return {
    id: `art_${number}`, workspaceId: "wsp_primary", knowledgeBaseId: "biology", lineageId: "arl_1", versionNumber: number,
    supersedesArtifactId: number > 1 ? `art_${number - 1}` : null, format: "report", audience: "scientist", title: `Immune markers v${number}`,
    contentHash: "a".repeat(64), manifestHash: "ab".repeat(32), acceptedUnitIds: [], unitCount: 0, createdAt: `2026-10-01T00:0${number}:00.000Z`,
    kind: "report", style: "overview", origin: "build", content: "# Immune markers\n\n_For the lab_\n\n## T cells\n\nCD3D marks T cells [1].\n\n## B cells\n\nCD19 marks B cells [?].",
    unitSnapshots: [],
    provenance: { schemaVersion: 2, generator: "gunther.output-agents.v1", workspaceId: "wsp_primary", knowledgeBaseId: "biology", knowledgeBaseQuestion: "What marks cells?", acceptedOnly: false, acceptedUnitIds: [], revisionIds: [] },
    brief: "For the lab", outline: [{ heading: "T cells", goal: "How T cells are told apart" }, { heading: "B cells", goal: "" }],
    citations: [citation], scope: { mode: "library", sourceIds: [], unitIds: [], sessionIds: [] }, inputs: { sources: 2, units: 1, sessions: 0 }, modelLabel: "DeepSeek · Flash",
    sections: [{ index: 0, heading: "T cells", checked: true, issues: [] }, { index: 1, heading: "B cells", checked: true, issues: [] }],
    ...overrides,
  };
}

const summary = (value: Artifact): ArtifactSummary => ({
  id: value.id, workspaceId: value.workspaceId, knowledgeBaseId: value.knowledgeBaseId, lineageId: value.lineageId, versionNumber: value.versionNumber,
  supersedesArtifactId: value.supersedesArtifactId, format: value.format, audience: value.audience, title: value.title, contentHash: value.contentHash,
  manifestHash: value.manifestHash, acceptedUnitIds: value.acceptedUnitIds, unitCount: value.unitCount, createdAt: value.createdAt, kind: value.kind, style: value.style, origin: value.origin, conversationId: value.conversationId ?? null, conversationTitle: value.conversationTitle ?? null, clientRequestId: value.clientRequestId ?? null, request: value.request ?? null,
});

async function setup(options: { from?: string; history?: Artifact[]; menu?: ModelMenu; sources?: number; units?: KnowledgeUnit[]; follow?: typeof knowledgeApi.followOutputBuild } = {}) {
  const history = options.history ?? [];
  vi.spyOn(knowledgeApi, "modelMenu").mockResolvedValue(options.menu ?? menu);
  const sources = Array.from({ length: options.sources ?? 2 }, (_, index) => makeSource({ id: `src_${index + 1}`, title: index === 0 ? "Markers" : `Source ${index + 1}`, kind: "note" }));
  vi.spyOn(knowledgeApi, "sources").mockResolvedValue(sources);
  vi.spyOn(knowledgeApi, "contents").mockResolvedValue({ sections: [], placements: [], sources: sources.map((source) => ({ ...source, durationSeconds: null, authors: null, year: null, pages: null, mediaType: null, host: null, processing: "ready" })) });
  vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue(options.units ?? [unit("unt_1", "T-cell identity"), unit("unt_2", "Removed answer", "deprecated")]);
  vi.spyOn(knowledgeApi, "sessions").mockResolvedValue([]);
  vi.spyOn(knowledgeApi, "artifacts").mockResolvedValue(history.map(summary));
  vi.spyOn(knowledgeApi, "artifact").mockImplementation(async (_base, id) => history.find((item) => item.id === id) ?? version(1));
  vi.spyOn(knowledgeApi, "followOutputBuild").mockImplementation(options.follow ?? (async () => null));
  vi.spyOn(knowledgeApi, "health").mockRejectedValue(new Error("No web search in these tests."));
  const onNotify = vi.fn(); const onAdd = vi.fn(); const onFromDiscussion = vi.fn();
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(<OutputsPage base={makeBase({ id: "biology", title: "Biology" })} workspaceId="wsp_primary" onNotify={onNotify} onAdd={onAdd} fromDiscussion={options.from ?? null} onFromDiscussion={onFromDiscussion} />); });
  await waitFor(() => expect(screen.getByRole("button", { name: "New conversation" })).toBeEnabled());
  return { view, onNotify, onAdd, onFromDiscussion };
}
function held<T>() { let release!: (value: T) => void; let fail!: (reason: Error) => void; const promise = new Promise<T>((resolve, reject) => { release = resolve; fail = reject; }); return { promise, release, fail }; }
const chat = () => within(screen.getByRole("region", { name: "Artifact conversation" }));
const sourcePanel = () => within(screen.getByRole("complementary", { name: "Source selection" }));
const showControls = async (user = userEvent.setup()) => { if (screen.queryByRole("button", { name: "Show preview controls" })) await user.click(screen.getByRole("button", { name: "Show preview controls" })); };
const send = async (user: ReturnType<typeof userEvent.setup>, instruction: string) => { const box = chat().getByRole("textbox", { name: "Artifact message" }); await user.clear(box); await user.type(box, instruction); await user.click(chat().getByRole("button", { name: "Send message" })); };
beforeEach(() => window.localStorage.clear());
afterEach(async () => { cleanup(); await act(async () => { await new Promise((resolve) => setTimeout(resolve, 120)); }); });

describe("artifact conversations", () => {
  it("starts with a conversation composer and keeps artifacts on the right", async () => {
    await setup();
    expect(chat().getByRole("textbox", { name: "Artifact message" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Conversations" })).toBeVisible();
    expect(sourcePanel().getByRole("checkbox", { name: /Markers/ })).toBeChecked();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New artifact" })).not.toBeInTheDocument();
    expect(chat().getByRole("button", { name: "Voice input" })).toBeVisible();
    expect(chat().getByRole("button", { name: "Model: DeepSeek · Flash" })).toBeVisible();
  });

  it("creates a report through chat, shows real stages, and opens the saved version", async () => {
    const user = userEvent.setup(); const running = held<Artifact>();
    const build = vi.spyOn(knowledgeApi, "buildOutputStream").mockImplementation(async (_base, _request, _workspace, hear) => {
      hear({ type: "stage", id: "understand", label: "Reading the material", state: "done" });
      hear({ type: "stage", id: "write", label: "Writing", state: "running" });
      hear({ type: "outline", title: "Immune markers", sections: [{ heading: "T cells", goal: "How T cells are told apart" }] });
      hear({ type: "section", index: 0, state: "writing" });
      hear({ type: "text", section: 0, text: "## T cells\n\nCD3D marks T cells [1]." });
      return running.promise;
    });
    const { onNotify } = await setup(); await send(user, "For the lab"); await showControls(user);
    const plan = within(screen.getByRole("region", { name: "Agent plan" }));
    expect(plan.getByText("Reading the material")).toBeVisible();
    expect(plan.getByText("Writing", { selector: "strong" })).toBeVisible();
    expect(chat().getByRole("button", { name: "Stop generation" })).toBeEnabled();
    await user.click(chat().getByText("Generation details", { selector: "summary" }));
    expect(chat().getByText(/CD3D marks T cells\./)).toBeVisible();
    await act(async () => running.release(version(1)));
    expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v1" })).toBeVisible();
    expect(onNotify).toHaveBeenCalledWith("Report version 1 saved.");
    expect(build.mock.calls[0]?.[1]).toEqual(expect.objectContaining({ kind: "report", style: "auto", audience: "scientist", brief: "For the lab", model: "deepseek/deepseek-flash", effort: "high", conversationTitle: "For the lab", conversationId: expect.any(String), scope: { mode: "library", sourceIds: [], unitIds: [], sessionIds: [] } }));
    expect(build.mock.calls[0]?.[1]).not.toHaveProperty("supersedesArtifactId");
  });

  it("creates independent reports and slides in one conversation", async () => {
    const user = userEvent.setup(); const report = version(1); const deck = version(1, { id: "deck", lineageId: "deck_lineage", kind: "slides", format: "slides", style: null, title: "A talk", content: "# A talk\n\n---\n\n## T cells\n\nCD3D [1]." });
    const build = vi.spyOn(knowledgeApi, "buildOutputStream").mockResolvedValueOnce(report).mockResolvedValueOnce(deck);
    await setup(); await send(user, "Create notes"); await screen.findByRole("heading", { level: 2, name: report.title });
    await user.selectOptions(chat().getByRole("combobox", { name: "Conversation action" }), "create");
    await user.selectOptions(chat().getByRole("combobox", { name: "New artifact format" }), "slides");
    await send(user, "Make a slide deck"); await screen.findByText("Slide 1 of 2"); await showControls(user);
    const files = within(screen.getByRole("complementary", { name: "Saved artifacts" }));
    expect(files.getByRole("button", { name: `Preview ${report.title}` })).toBeVisible(); expect(files.getByRole("button", { name: "Preview A talk" })).toBeVisible();
    expect(build.mock.calls[1]?.[1]).not.toHaveProperty("style"); expect(build.mock.calls[1]?.[1]).not.toHaveProperty("supersedesArtifactId");
    expect(build.mock.calls[1]?.[1].conversationId).toBe(build.mock.calls[0]?.[1].conversationId);
    expect(within(screen.getByRole("region", { name: "Conversations" })).getAllByRole("button")).toHaveLength(2);
  }, 10_000);

  it("applies the model selected in the composer to the build request", async () => {
    const user = userEvent.setup(); const build = vi.spyOn(knowledgeApi, "buildOutputStream").mockResolvedValue(version());
    await setup({ menu: { ...menu, models: [...menu.models, { ref: "other/model", provider: "Other", label: "Model", display: "Other · Model", vision: false, levels: [] }] } });
    await user.click(chat().getByRole("button", { name: "Model: DeepSeek · Flash" }));
    await user.click(screen.getByRole("menuitemradio", { name: /^Model/ }));
    await send(user, "Create a report");
    expect(build.mock.calls[0]?.[1].model).toBe("other/model");
  });

  it("retries the same failed request without creating a duplicate chat turn", async () => {
    const user = userEvent.setup(); const build = vi.spyOn(knowledgeApi, "buildOutputStream").mockRejectedValueOnce(new Error("The API key was rejected.")).mockResolvedValueOnce(version());
    await setup(); await send(user, "For the lab");
    expect(await screen.findByRole("alert")).toHaveTextContent("The API key was rejected.");
    await user.click(chat().getByRole("button", { name: "Retry" }));
    await screen.findByRole("heading", { level: 2, name: "Immune markers v1" });
    expect(build.mock.calls[1]?.[1]).toBe(build.mock.calls[0]?.[1]);
    expect(within(chat().getByRole("log")).getAllByText("For the lab")).toHaveLength(1);
  });

  it("stops generation and preserves the previous version", async () => {
    const user = userEvent.setup(); const running = held<Artifact>();
    vi.spyOn(knowledgeApi, "reviseOutputStream").mockImplementation(async (_base, _id, _payload, _workspace, hear) => { hear({ type: "section", index: 0, state: "writing" }); return running.promise; });
    const stop = vi.spyOn(knowledgeApi, "stopOutputBuild").mockImplementation(async () => running.fail(new OutputStoppedError()));
    const { onNotify } = await setup({ history: [version()] }); await send(user, "Make it shorter");
    await user.click(chat().getByRole("button", { name: "Stop generation" }));
    await waitFor(() => expect(onNotify).toHaveBeenCalledWith("Stopped. Nothing was saved."));
    expect(stop).toHaveBeenCalledWith("biology", "wsp_primary"); expect(screen.getByRole("heading", { level: 2, name: "Immune markers v1" })).toBeVisible();
  });

  it("follows an existing build and associates its result with the conversation", async () => {
    const running = held<Artifact | null>();
    await setup({ follow: async (_base, _workspace, hear) => { hear({ type: "resumed", question: "Cover B cells", startedAt: 1 }); hear({ type: "outline", title: "Immune markers", sections: [{ heading: "B cells", goal: "" }] }); hear({ type: "section", index: 0, state: "researching" }); return running.promise; } });
    expect(within(chat().getByRole("log")).getByText("Cover B cells")).toBeVisible();
    expect(chat().getByRole("button", { name: "Stop generation" })).toBeEnabled();
    await act(async () => running.release(version(1, { conversationId: "recovered", conversationTitle: "Recovered request", clientRequestId: "original_request", request: "Cover B cells" })));
    expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v1" })).toBeVisible();
    expect(within(screen.getByRole("region", { name: "Conversations" })).getByRole("button", { name: /Recovered request/ })).toHaveAttribute("aria-current", "page");
  });

  it("keeps a request in its own conversation when another conversation is opened", async () => {
    const user = userEvent.setup(); const running = held<Artifact>();
    vi.spyOn(knowledgeApi, "buildOutputStream").mockReturnValue(running.promise);
    await setup(); await send(user, "First request"); await user.click(screen.getByRole("button", { name: "New conversation" }));
    await user.type(chat().getByRole("textbox", { name: "Artifact message" }), "My next request");
    await act(async () => running.release(version()));
    expect(chat().getByRole("textbox", { name: "Artifact message" })).toHaveValue("My next request");
    expect(screen.queryByRole("heading", { level: 2, name: "Immune markers v1" })).not.toBeInTheDocument();
  });
});

describe("sources and configuration", () => {
  it("keeps working selection independent of the saved artifact sources", async () => {
    const user = userEvent.setup(); const build = vi.spyOn(knowledgeApi, "buildOutputStream").mockResolvedValue(version());
    await setup(); await user.click(sourcePanel().getByRole("checkbox", { name: /Source 2/ }));
    expect(chat().getByText("1 source · 1 saved")).toBeVisible(); await send(user, "Build from my selection");
    expect(build.mock.calls[0]?.[1].scope).toEqual({ mode: "selection", sourceIds: ["src_1"], unitIds: ["unt_1"], sessionIds: [] });
    expect(sourcePanel().getByRole("checkbox", { name: /Source 2/ })).not.toBeChecked();
    expect(within(screen.getByRole("article")).getByText(/Built from 2 sources · 1 saved/)).toBeVisible();
  });
  it("uses a chosen Ask discussion for a new artifact", async () => {
    const user = userEvent.setup(); const build = vi.spyOn(knowledgeApi, "buildOutputStream").mockResolvedValue(version(2));
    const { onFromDiscussion } = await setup({ history: [version()], from: "ses_1" });
    expect(chat().getByText("1 discussion")).toBeVisible(); await send(user, "Make a report from the discussion");
    expect(onFromDiscussion).toHaveBeenCalledOnce(); expect(build.mock.calls[0]?.[1].scope).toEqual({ mode: "selection", sourceIds: [], unitIds: [], sessionIds: ["ses_1"] }); expect(build.mock.calls[0]?.[1]).not.toHaveProperty("supersedesArtifactId");
  });
  it("requires material and a configured model, and offers the relevant setup action", async () => {
    const user = userEvent.setup(); const opened = vi.fn(); window.addEventListener(OPEN_SETTINGS_EVENT, opened);
    await setup({ menu: { models: [], default: null, efforts: [] } });
    expect(screen.getByText("Artifacts need a model. Set one up under Settings → Models.")).toBeVisible();
    await user.type(chat().getByRole("textbox", { name: "Artifact message" }), "Make a report"); expect(chat().getByRole("button", { name: "Send message" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Open Settings/ })); expect(opened).toHaveBeenCalledOnce(); window.removeEventListener(OPEN_SETTINGS_EVENT, opened);
  });
  it("disables generation after clearing all materials", async () => {
    const user = userEvent.setup(); await setup({ units: [] }); await user.click(sourcePanel().getByRole("button", { name: "Clear" }));
    await user.type(chat().getByRole("textbox", { name: "Artifact message" }), "Make a report"); expect(chat().getByRole("button", { name: "Send message" })).toBeDisabled();
  });
  it("can make slides for reading from the inline settings", async () => {
    const user = userEvent.setup(); const build = vi.spyOn(knowledgeApi, "buildOutputStream").mockResolvedValue(version(1, { kind: "slides", format: "slides", style: null, content: "# Deck" }));
    await setup(); await user.selectOptions(chat().getByRole("combobox", { name: "New artifact format" }), "slides"); await user.click(chat().getByRole("button", { name: "Artifact settings" }));
    await user.selectOptions(chat().getByRole("combobox", { name: "Deck use" }), "read"); await send(user, "Create a deck to read"); expect(build.mock.calls[0]?.[1]).toEqual(expect.objectContaining({ kind: "slides", use: "read" }));
  });
});

describe("reading, versions and editing", () => {
  it("shows historical artifacts without pretending they had a recorded conversation", async () => {
    await setup({ history: [version(2), version(1)] });
    expect(screen.getByRole("heading", { level: 2, name: "Immune markers v2" })).toBeVisible();
    expect(within(screen.getByRole("region", { name: "Conversations" })).getByRole("button", { name: /Earlier work/ })).toBeVisible();
    expect(chat().getByRole("log")).toBeEmptyDOMElement();
  });
  it("groups versions by artifact and exports the selected artifact's own content", async () => {
    const user = userEvent.setup(); const other = version(1, { id: "other", lineageId: "other_lineage", title: "Other artifact", content: "# Other\n\nIts own text." }); const copy = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    await setup({ history: [version(2), other, version(1)] }); await showControls(user);
    const files = within(screen.getByRole("complementary", { name: "Saved artifacts" })); expect(files.getAllByRole("listitem")).toHaveLength(2);
    await user.click(files.getByRole("button", { name: "Actions for Other artifact" })); await user.click(screen.getByRole("menuitem", { name: "Copy Markdown" })); await waitFor(() => expect(copy).toHaveBeenCalledWith(other.content));
  });
  it("opens earlier versions and requires the latest before changing them", async () => {
    const user = userEvent.setup(); await setup({ history: [version(2), version(1)] });
    await user.selectOptions(screen.getByRole("combobox", { name: "Artifact versions" }), "art_1");
    expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v1" })).toBeVisible();
    await user.type(chat().getByRole("textbox", { name: "Artifact message" }), "Change it"); expect(chat().getByRole("button", { name: "Send message" })).toBeDisabled();
    await user.click(chat().getByRole("button", { name: "Open latest version 2" })); expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v2" })).toBeVisible();
  });
  it("rebuilds from an edited outline and preserves the parent version", async () => {
    const user = userEvent.setup(); const build = vi.spyOn(knowledgeApi, "buildOutputStream").mockResolvedValue(version(2)); await setup({ history: [version()] }); await showControls(user);
    await user.click(screen.getByRole("button", { name: /Outline/ })); await user.click(screen.getByRole("button", { name: "Edit outline" }));
    await user.clear(screen.getByRole("textbox", { name: "Heading 2" })); await user.type(screen.getByRole("textbox", { name: "Heading 2" }), "Plasma cells"); await user.click(screen.getByRole("button", { name: "Move 2 up" })); await user.click(screen.getByRole("button", { name: "Rebuild with this outline" }));
    expect(build.mock.calls[0]?.[1]).toEqual(expect.objectContaining({ supersedesArtifactId: "art_1", outline: [{ heading: "Plasma cells", goal: "" }, { heading: "T cells", goal: "How T cells are told apart" }] }));
  });
  it("retains the skill's approach and rebuilds with the edited question", async () => {
    const user = userEvent.setup(); const first = version(1, { provenance: { ...version().provenance, skill: { name: "report", version: 1, skipped: [] }, approach: { question: "Which marker?", answer: "CD3D", purpose: "Scientists", structure: "which", structureReason: "A choice", pages: null, supplements: [], gaps: [] } } });
    const build = vi.spyOn(knowledgeApi, "buildOutputStream").mockResolvedValue(version(2)); await setup({ history: [first] }); await showControls(user);
    await user.click(screen.getByRole("button", { name: /Approach/ })); await user.click(screen.getByRole("button", { name: "Edit approach" })); await user.clear(screen.getByLabelText("Question")); await user.type(screen.getByLabelText("Question"), "Which panel?"); await user.click(screen.getByRole("button", { name: "Rebuild with this approach" }));
    expect(build.mock.calls[0]?.[1]).toEqual(expect.objectContaining({ supersedesArtifactId: "art_1", approach: { question: "Which panel?", answer: "CD3D", purpose: "Scientists" } }));
  });
  it("keeps citation checks and opens the cited evidence", async () => {
    const user = userEvent.setup(); await setup({ history: [version(1, { sections: [{ index: 0, heading: "T cells", checked: true, issues: [] }, { index: 1, heading: "B cells", checked: false, issues: [{ claim: "CD19 marks B cells", verdict: "unverified", note: "No source was found." }] }] })] });
    const page = within(screen.getByRole("article")); expect(page.getByText(/CD3D marks T cells/)).toBeVisible(); expect(page.getByText("Not re-checked")).toBeVisible(); expect(page.getByText("Not backed by a source")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Inspect citation 1" })); expect(await screen.findByRole("link", { name: /Open example\.org/ })).toBeVisible();
  });
  it("saves direct edits as a new version and keeps unsaved text after a failure", async () => {
    const user = userEvent.setup(); const edit = vi.spyOn(knowledgeApi, "editOutput").mockRejectedValueOnce(new Error("Not the latest version")).mockResolvedValueOnce(version(2, { origin: "edit" }));
    await setup({ history: [version()] }); await user.click(screen.getByRole("button", { name: "Edit" })); await user.type(screen.getByRole("textbox", { name: "Report, as Markdown" }), " More."); await user.click(screen.getByRole("button", { name: /Save as new version/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The edit wasn’t saved"); expect(screen.getByRole("textbox", { name: "Report, as Markdown" })).toHaveValue(`${version().content} More.`);
    await user.click(screen.getByRole("button", { name: /Save as new version/ })); expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v2" })).toBeVisible(); expect(edit.mock.calls[1]?.[2].content).toBe(`${version().content} More.`);
  });
  it("revises a selected section, preserves the others, and forwards the chosen model", async () => {
    const user = userEvent.setup(); const running = held<Artifact>(); const revise = vi.spyOn(knowledgeApi, "reviseOutputStream").mockImplementation(async (_base, _id, _payload, _workspace, hear) => { hear({ type: "outline", title: "Immune markers", sections: version().outline }); hear({ type: "section", index: 1, state: "writing" }); hear({ type: "text", section: 1, text: "## B cells\n\nShorter [1]." }); return running.promise; });
    await setup({ history: [version()] }); await user.selectOptions(chat().getByRole("combobox", { name: "Which part" }), "1"); await send(user, "Make it shorter"); await user.click(chat().getByText("Generation details", { selector: "summary" }));
    expect(chat().getByText(/Shorter\./)).toBeVisible(); expect(chat().getByText("Kept as it is")).toBeVisible(); await act(async () => running.release(version(2, { origin: "revise" })));
    expect(revise.mock.calls[0]?.[2]).toEqual(expect.objectContaining({ instruction: "Make it shorter", sectionIndex: 1, model: "deepseek/deepseek-flash", effort: "high", conversationId: "earlier-work" }));
  });
  it("leaves unchanged sections as kept and retries a failed whole-report revision", async () => {
    const user = userEvent.setup(); const revise = vi.spyOn(knowledgeApi, "reviseOutputStream").mockRejectedValueOnce(new Error("Rejected API key")).mockResolvedValueOnce(version(2, { origin: "revise" }));
    await setup({ history: [version()] }); await send(user, "Use plainer words"); expect(await screen.findByRole("alert")).toHaveTextContent("The change wasn’t saved"); await user.click(chat().getByRole("button", { name: "Retry" }));
    expect(revise.mock.calls[0]?.[2]).not.toHaveProperty("sectionIndex"); expect(revise.mock.calls[1]?.[2]).toBe(revise.mock.calls[0]?.[2]); await screen.findByRole("heading", { level: 2, name: "Immune markers v2" });
  });
  it("rechecks edited sections without changing the content", async () => {
    const user = userEvent.setup(); const check = vi.spyOn(knowledgeApi, "checkOutput").mockResolvedValue(version()); await setup({ history: [version(1, { sections: [{ index: 0, heading: "T cells", checked: false, issues: [] }] })] });
    await user.click(screen.getByRole("button", { name: "Check again" })); await waitFor(() => expect(screen.queryByText("Not re-checked")).not.toBeInTheDocument()); expect(check).toHaveBeenCalledWith("biology", "art_1", "wsp_primary");
  });
  it("keeps legacy versions readable and requires rebuilding to modify them", async () => {
    await setup({ history: [version(1, { origin: "legacy", citations: [], sections: [], scope: null, content: "# Legacy\n\nPinned text [1]." })] });
    expect(within(screen.getByRole("article")).getByText(/Pinned text \[1\]/)).toBeVisible(); expect(chat().getByText("This version came from the old builder. Rebuild it to edit it.")).toBeVisible(); expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });
  it("collapses preview controls and returns from focus mode", async () => {
    const user = userEvent.setup(); await setup({ history: [version()] }); expect(screen.queryByRole("region", { name: "Agent plan" })).not.toBeInTheDocument(); await showControls(user); expect(screen.getByRole("region", { name: "Agent plan" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Hide preview controls" })); await user.click(screen.getByRole("button", { name: "Expand preview" })); expect(screen.getByRole("button", { name: "Exit preview focus" })).toHaveAttribute("aria-pressed", "true"); await user.keyboard("{Escape}"); expect(screen.getByRole("button", { name: "Expand preview" })).toBeVisible(); expect(chat().getByRole("textbox", { name: "Artifact message" })).toBeVisible();
  });
});

describe("exporting artifacts", () => {
  it("keeps the separate print layout with the report and its sources", async () => {
    await setup({ history: [version()] }); const paper = document.getElementById("print-root")!; expect(paper.parentElement).toBe(document.body); expect(paper).toHaveAttribute("aria-hidden", "true"); expect(paper.querySelector("style")?.textContent).toContain("size: A4"); expect(paper.textContent).toContain("CD3D marks T cells"); expect(paper.querySelector(".outputs-print-sources")).toHaveTextContent("Marker review"); expect(paper.querySelector("button")).toBeNull();
  });
  it("prints each slide on its own page with sources and without speaker notes", async () => {
    await setup({ history: [version(1, { kind: "slides", format: "slides", style: null, content: "# Deck\n\n---\n\n## One\n\n- a [1]\n\nNote: Say a." })] }); await screen.findByText("Slide 1 of 2"); const paper = document.getElementById("print-root")!; expect(paper.querySelectorAll(".outputs-print-slide")).toHaveLength(3); expect(paper.textContent).not.toContain("Say a.");
  });
  it("exports PDF through browser printing", async () => {
    const user = userEvent.setup(); const print = vi.spyOn(window, "print").mockImplementation(() => undefined); await setup({ history: [version()] }); await user.click(screen.getByRole("button", { name: "Export artifact" })); await user.click(screen.getByRole("menuitem", { name: "Export PDF" })); await waitFor(() => expect(print).toHaveBeenCalledOnce());
  });
  it("uses native printing on macOS and reports failures", async () => {
    const user = userEvent.setup(); (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}; vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)"); vi.mocked(invoke).mockResolvedValue(undefined);
    try { await setup({ history: [version()] }); await user.click(screen.getByRole("button", { name: "Export artifact" })); await user.click(screen.getByRole("menuitem", { name: "Export PDF" })); await waitFor(() => expect(invoke).toHaveBeenCalledWith("print_output")); vi.mocked(invoke).mockRejectedValueOnce("Print unavailable"); await user.click(screen.getByRole("button", { name: "Export artifact" })); await user.click(screen.getByRole("menuitem", { name: "Export PDF" })); expect(await screen.findByRole("alert")).toHaveTextContent("Print unavailable"); } finally { delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__; }
  });
  it("prints only the standalone paper layout", () => { const css = readFileSync(resolve(process.cwd(), "src/outputs/outputs.css"), "utf8"); expect(css).toMatch(/@media screen\s*\{\s*#print-root\s*\{\s*display:\s*none;/); expect(css).toMatch(/@media print[\s\S]*body > :not\(#print-root\)\s*\{\s*display:\s*none/); });
});
