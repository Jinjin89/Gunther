import type { Artifact, ArtifactConversationDetail, ArtifactConversationMessage, ArtifactSummary, ArtifactTurnInput, ArtifactTurnOut, ArtifactWork, ConversationCitation, KnowledgeUnit, ModelMenu } from "@gunther/contracts";
import { invoke } from "@tauri-apps/api/core";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi, OutputStoppedError, type ArtifactTurnEvent } from "../api";
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

const library = { mode: "library" as const, sourceIds: [], unitIds: [], sessionIds: [] };
const emptyBrief = { goal: null, audience: null, constraints: [], open: [] };
const AT = "2026-10-01T00:05:00.000Z";
const workOf = (artifact: Artifact): ArtifactWork => ({ key: artifact.lineageId, kind: artifact.kind, title: artifact.title, versionId: artifact.id, spec: { kind: artifact.kind, use: null, length: null, structure: null }, approach: { state: "ok", why: "" }, outline: { state: "ok", why: "" }, parts: [] });
const conversation = (id: string, overrides: Partial<ArtifactConversationDetail> = {}): ArtifactConversationDetail => ({ id, title: "Make notes", updatedAt: AT, artifactCount: overrides.works?.length ?? 0, waiting: false, scope: library, brief: emptyBrief, messages: [], works: [], active: null, pending: null, archived: false, createdAt: AT, ...overrides });
const said = (id: string, text: string, overrides: Partial<ArtifactConversationMessage> = {}): ArtifactConversationMessage => ({ id, role: "user", kind: "message", text, payload: {}, artifactId: null, clientRequestId: id, createdAt: AT, ...overrides });
const replied = (id: string, kind: string, text: string, overrides: Partial<ArtifactConversationMessage> = {}): ArtifactConversationMessage => ({ id, role: "assistant", kind, text, payload: {}, artifactId: null, clientRequestId: null, createdAt: AT, ...overrides });
const turnOut = (outcome: ArtifactTurnOut["outcome"], reply: string, artifact: Artifact | null = null): ArtifactTurnOut => ({ outcome, reply, question: null, artifact, changes: [], message: replied("m", "result", reply) });

const deckContent = "<!-- layout: title -->\n# Deck\n\nOne line\n\n---\n\n<!-- layout: takeaways -->\n## Costs\n\n- Labour is most of the cost [1]\n\n---\n\n## Results\n\n- A point [1]";
const deck = (number = 1, overrides: Partial<Artifact> = {}) => version(number, {
  id: `deck_${number}`, lineageId: "deck_lineage", kind: "slides", format: "slides", style: null, title: `Deck v${number}`, content: deckContent, conversationId: "conv", conversationTitle: "A talk",
  outline: [{ heading: "Deck", goal: "" }, { heading: "Costs", goal: "" }, { heading: "Results", goal: "" }],
  sections: [{ index: 0, heading: "Deck", checked: true, issues: [] }, { index: 1, heading: "Costs", checked: true, issues: [] }, { index: 2, heading: "Results", checked: true, issues: [] }],
  ...overrides,
});
const withChanges = (artifact: Artifact, changes: NonNullable<Artifact["provenance"]["changes"]>): Artifact => ({ ...artifact, provenance: { ...artifact.provenance, changes } });

type Service = Map<string, ArtifactConversationDetail>;
/** The service keeps a turn: the message, the reply, and the version it saved. */
function keep(service: Service, id: string, payload: ArtifactTurnInput, answer: { kind: string; text: string; payload?: Record<string, unknown>; artifactId?: string }, artifact?: Artifact) {
  const found = service.get(id)!; const n = found.messages.length;
  const options = (answer.payload?.options ?? []) as string[];
  service.set(id, {
    ...found, artifactCount: artifact ? Math.max(found.artifactCount, 1) : found.artifactCount,
    messages: [...found.messages, said(`u${n}`, payload.text ?? "", { kind: payload.kind ?? "message", payload: payload.focus ? { focus: payload.focus } : {}, clientRequestId: answer.kind === "failed" || answer.kind === "stopped" ? null : payload.clientRequestId }), replied(`a${n}`, answer.kind, answer.text, { payload: answer.payload ?? {}, artifactId: answer.artifactId ?? null })],
    works: artifact ? [...found.works.filter((work) => work.key !== artifact.lineageId), workOf(artifact)] : found.works,
    active: artifact ? artifact.lineageId : found.active,
    pending: answer.kind === "question" ? { question: answer.text, options, messageId: `a${n}` } : null,
  });
}

interface Context { service: Service; history: Artifact[]; created: Array<{ id?: string; scope?: unknown }>; imports: Array<{ conversations: Array<{ id: string; turns: unknown[] }> }> }
async function setup(options: { from?: string; history?: Artifact[]; conversations?: ArtifactConversationDetail[]; menu?: ModelMenu; sources?: number; units?: KnowledgeUnit[]; follow?: typeof knowledgeApi.followOutputBuild; web?: boolean } = {}) {
  const history = options.history ?? [];
  const service: Service = new Map();
  for (const found of options.conversations ?? []) service.set(found.id, found);
  // A saved version that names a conversation the service does not have yet is recovered into one, as the service does.
  for (const artifact of history) {
    const id = artifact.conversationId;
    if (id && !service.has(id)) service.set(id, conversation(id, { title: artifact.conversationTitle ?? "Recovered" }));
    if (id && !service.get(id)!.works.some((work) => work.key === artifact.lineageId)) { const found = service.get(id)!; service.set(id, { ...found, works: [...found.works, workOf(artifact)], active: found.active ?? artifact.lineageId, artifactCount: found.artifactCount + 1 }); }
  }
  const created: Context["created"] = []; const imports: Context["imports"] = [];
  vi.spyOn(knowledgeApi, "modelMenu").mockResolvedValue(options.menu ?? menu);
  const sources = Array.from({ length: options.sources ?? 2 }, (_, index) => makeSource({ id: `src_${index + 1}`, title: index === 0 ? "Markers" : `Source ${index + 1}`, kind: "note" }));
  vi.spyOn(knowledgeApi, "sources").mockResolvedValue(sources);
  vi.spyOn(knowledgeApi, "contents").mockResolvedValue({ sections: [], placements: [], sources: sources.map((source) => ({ ...source, durationSeconds: null, authors: null, year: null, pages: null, mediaType: null, host: null, processing: "ready" })) });
  vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue(options.units ?? [unit("unt_1", "T-cell identity"), unit("unt_2", "Removed answer", "deprecated")]);
  vi.spyOn(knowledgeApi, "sessions").mockResolvedValue([]);
  vi.spyOn(knowledgeApi, "artifacts").mockImplementation(async () => history.map(summary));
  vi.spyOn(knowledgeApi, "artifact").mockImplementation(async (_base, id) => history.find((item) => item.id === id) ?? version(1));
  vi.spyOn(knowledgeApi, "followOutputBuild").mockImplementation(options.follow ?? (async () => null));
  if (options.web) vi.spyOn(knowledgeApi, "health").mockResolvedValue({ status: "ok", webSearchMode: "tavily" } as never); else vi.spyOn(knowledgeApi, "health").mockRejectedValue(new Error("No web search in these tests."));
  vi.spyOn(knowledgeApi, "artifactConversations").mockImplementation(async () => [...service.values()].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)).map(({ id, title, updatedAt, artifactCount, waiting, pending }) => ({ id, title, updatedAt, artifactCount, waiting: waiting || pending !== null })));
  vi.spyOn(knowledgeApi, "artifactConversation").mockImplementation(async (_base, id) => { const found = service.get(id); if (!found) throw new Error("There is no such conversation."); return found; });
  vi.spyOn(knowledgeApi, "createArtifactConversation").mockImplementation(async (_base, payload) => { created.push(payload); const made = conversation(payload.id!, { title: "New conversation", scope: payload.scope ?? library }); service.set(made.id, made); return made; });
  vi.spyOn(knowledgeApi, "updateArtifactConversation").mockImplementation(async (_base, id) => service.get(id)!);
  vi.spyOn(knowledgeApi, "importArtifactConversations").mockImplementation(async (_base, payload) => {
    imports.push(payload as never);
    for (const entry of payload.conversations) {
      const turns = entry.turns.map((turn) => [said(`u_${turn.id}`, turn.instruction, { clientRequestId: turn.id ?? null }), replied(`a_${turn.id}`, turn.status === "saved" ? "result" : "failed", "", { artifactId: turn.artifactId ?? null, payload: { imported: true } })]).flat();
      const works = entry.turns.flatMap((turn) => { const found = history.find((artifact) => artifact.id === turn.artifactId); return found ? [workOf(found)] : []; });
      service.set(entry.id, conversation(entry.id, { title: entry.title ?? entry.turns[0]?.instruction ?? "Imported", messages: turns, works, active: works.at(-1)?.key ?? null, artifactCount: works.length }));
    }
    return [];
  });
  const onNotify = vi.fn(); const onAdd = vi.fn(); const onFromDiscussion = vi.fn();
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(<OutputsPage base={makeBase({ id: "biology", title: "Biology" })} workspaceId="wsp_primary" onNotify={onNotify} onAdd={onAdd} fromDiscussion={options.from ?? null} onFromDiscussion={onFromDiscussion} />); });
  await waitFor(() => expect(screen.getByRole("button", { name: "New conversation" })).toBeEnabled());
  return { view, onNotify, onAdd, onFromDiscussion, service, history, created, imports };
}
function held<T>() { let release!: (value: T) => void; let fail!: (reason: Error) => void; const promise = new Promise<T>((resolve, reject) => { release = resolve; fail = reject; }); return { promise, release, fail }; }
/** A turn that saves this version, kept by the service as it would be. */
function saves(context: { service: Service; history: Artifact[] }, artifactOf: (conversationId: string) => Artifact, text = "Done.", changes: unknown[] = []) {
  return async (_base: string, id: string, payload: ArtifactTurnInput) => {
    const artifact = artifactOf(id); context.history.unshift(artifact);
    keep(context.service, id, payload, { kind: "result", text, artifactId: artifact.id, payload: { changes } }, artifact);
    return turnOut("saved", text, artifact);
  };
}
const chat = () => within(screen.getByRole("region", { name: "Artifact conversation" }));
const sourcePanel = () => within(screen.getByRole("complementary", { name: "Source selection" }));
const showControls = async (user = userEvent.setup()) => { if (screen.queryByRole("button", { name: "Show preview controls" })) await user.click(screen.getByRole("button", { name: "Show preview controls" })); };
const send = async (user: ReturnType<typeof userEvent.setup>, instruction: string) => { const box = chat().getByRole("textbox", { name: "Artifact message" }); await user.clear(box); await user.type(box, instruction); await user.click(chat().getByRole("button", { name: "Send message" })); };
const select = (node: Node) => { const range = document.createRange(); range.selectNodeContents(node); const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range); document.dispatchEvent(new Event("selectionchange")); };
beforeEach(() => window.localStorage.clear());
afterEach(async () => { cleanup(); window.getSelection()?.removeAllRanges(); await act(async () => { await new Promise((resolve) => setTimeout(resolve, 120)); }); });

describe("artifact conversations", () => {
  it("starts with one message box and keeps artifacts on the right", async () => {
    await setup();
    expect(chat().getByRole("textbox", { name: "Artifact message" })).toHaveAttribute("placeholder", "Ask for a change, a question, or a new report or deck…");
    expect(chat().queryByRole("combobox", { name: "Conversation action" })).not.toBeInTheDocument();
    expect(chat().queryByRole("combobox", { name: "Which part" })).not.toBeInTheDocument();
    expect(chat().queryByRole("combobox", { name: "New artifact format" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Conversations" })).toBeVisible();
    expect(sourcePanel().getByRole("checkbox", { name: /Markers/ })).toBeChecked();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New artifact" })).not.toBeInTheDocument();
    expect(chat().getByRole("button", { name: "Voice input" })).toBeVisible();
    expect(chat().getByRole("button", { name: "Model: DeepSeek · Flash" })).toBeVisible();
  });

  it("sends a message to the agent, shows its steps and its plan, and opens the saved version", async () => {
    const user = userEvent.setup(); const running = held<ArtifactTurnOut>(); let conversationId = "";
    const turn = vi.spyOn(knowledgeApi, "artifactTurnStream").mockImplementation(async (_base, id, _payload, _workspace, hear) => {
      conversationId = id;
      hear({ type: "turn", conversationId: id, messageId: "m1" });
      hear({ type: "decision", actions: [{ id: "a1", do: "search_library", label: "Searching your library for “markers”" }, { id: "a2", do: "write", label: "Writing section 1" }] });
      hear({ type: "action", id: "a1", state: "done", label: "Searched your library for “markers”", note: "6 found" });
      hear({ type: "action", id: "a2", state: "running", label: "Writing section 1", note: "" });
      hear({ type: "stage", id: "write", label: "Writing", state: "running" });
      hear({ type: "outline", title: "Immune markers", sections: [{ heading: "T cells", goal: "How T cells are told apart" }] });
      hear({ type: "section", index: 0, state: "writing" });
      hear({ type: "text", section: 0, text: "## T cells\n\nCD3D marks T cells [1]." });
      return running.promise;
    });
    const context = await setup(); await send(user, "For the lab"); await showControls(user);
    const plan = within(screen.getByRole("region", { name: "Agent plan" }));
    expect(plan.getByText("Searched your library for “markers”")).toBeVisible();
    expect(plan.getByText("Writing section 1", { selector: "strong" })).toBeVisible();
    const steps = chat().getByRole("list", { name: "What the agent did" });
    expect(within(steps).getByText("Searched your library for “markers”")).toBeVisible(); expect(within(steps).getByText("6 found")).toBeVisible(); expect(within(steps).getByText("Writing section 1")).toBeVisible();
    expect(chat().getByText("For the lab", { selector: "p" })).toBeVisible();
    expect(chat().getByRole("button", { name: "Stop generation" })).toBeEnabled();
    await user.click(chat().getByText("Generation details", { selector: "summary" }));
    expect(chat().getByText(/CD3D marks T cells\./)).toBeVisible();
    const artifact = version(1, { conversationId });
    await act(async () => { context.history.unshift(artifact); keep(context.service, conversationId, turn.mock.calls[0]![2], { kind: "result", text: "Made the report.", artifactId: artifact.id, payload: { steps: [{ id: "a1", state: "done", label: "Searched your library for “markers”", note: "6 found" }, { id: "a2", state: "done", label: "Wrote section 1", note: "" }] } }, artifact); running.release(turnOut("saved", "Made the report.", artifact)); });
    expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v1" })).toBeVisible();
    expect(context.onNotify).toHaveBeenCalledWith("Report version 1 saved.");
    // The finished turn's steps fold into one line.
    expect(await chat().findByText("Worked: 2 steps")).toBeVisible(); expect(chat().getByText("Made the report.")).toBeVisible();
    expect(chat().queryByRole("list", { name: "What the agent did" })).not.toBeVisible();
    expect(turn.mock.calls[0]?.[2]).toEqual({ clientRequestId: expect.any(String), kind: "message", text: "For the lab", model: "deepseek/deepseek-flash", effort: "high" });
    // The conversation reached the service with its first message, under the id the device made, and with the chosen sources.
    expect(context.created).toEqual([{ id: conversationId, scope: library }]);
  });

  it("makes a report and then a deck in one conversation", async () => {
    const user = userEvent.setup(); const context = await setup();
    vi.spyOn(knowledgeApi, "artifactTurnStream").mockImplementationOnce(saves(context, (id) => version(1, { conversationId: id }))).mockImplementationOnce(saves(context, (id) => ({ ...deck(1), conversationId: id, title: "A talk" })));
    await send(user, "Create notes"); await screen.findByRole("heading", { level: 2, name: "Immune markers v1" });
    await send(user, "Make a slide deck"); await screen.findByText("Slide 1 of 3"); await showControls(user);
    const files = within(screen.getByRole("complementary", { name: "Saved artifacts" }));
    expect(files.getByRole("button", { name: "Preview Immune markers v1" })).toBeVisible(); expect(files.getByRole("button", { name: "Preview A talk" })).toBeVisible();
    const calls = vi.mocked(knowledgeApi.artifactTurnStream).mock.calls;
    expect(calls[1]?.[1]).toBe(calls[0]?.[1]); expect(calls[1]?.[2]).not.toHaveProperty("focus");
    expect(context.created).toHaveLength(1);
    expect(within(screen.getByRole("region", { name: "Conversations" })).getAllByRole("button")).toHaveLength(2);
  }, 10_000);

  it("applies the model selected in the composer to the message", async () => {
    const user = userEvent.setup(); const context = await setup({ menu: { ...menu, models: [...menu.models, { ref: "other/model", provider: "Other", label: "Model", display: "Other · Model", vision: false, levels: [] }] } });
    const turn = vi.spyOn(knowledgeApi, "artifactTurnStream").mockImplementation(saves(context, (id) => version(1, { conversationId: id })));
    await user.click(chat().getByRole("button", { name: "Model: DeepSeek · Flash" }));
    await user.click(screen.getByRole("menuitemradio", { name: /^Model/ }));
    await send(user, "Create a report");
    await waitFor(() => expect(turn).toHaveBeenCalled());
    expect(turn.mock.calls[0]?.[2].model).toBe("other/model");
  });

  it("offers the web only when it is set up, and sends it with the message when it is on", async () => {
    const user = userEvent.setup(); const context = await setup({ web: true });
    const turn = vi.spyOn(knowledgeApi, "artifactTurnStream").mockImplementation(saves(context, (id) => version(1, { conversationId: id })));
    await user.click(chat().getByRole("button", { name: "Artifact settings" }));
    expect(chat().queryByRole("combobox", { name: "Audience" })).not.toBeInTheDocument(); expect(chat().queryByRole("combobox", { name: "Style" })).not.toBeInTheDocument();
    await user.click(await chat().findByRole("switch", { name: "Search the web too" }));
    await send(user, "Create a report");
    await waitFor(() => expect(turn).toHaveBeenCalled());
    expect(turn.mock.calls[0]?.[2]).toEqual(expect.objectContaining({ web: true }));
    window.localStorage.clear();
  });

  it("shows a failed turn the service kept, and sends the same words again with Retry", async () => {
    const user = userEvent.setup(); const context = await setup({ history: [version()] });
    const turn = vi.spyOn(knowledgeApi, "artifactTurnStream").mockImplementationOnce(async (_base, id, payload) => { keep(context.service, id, payload, { kind: "failed", text: "The API key was rejected." }); throw new Error("The API key was rejected."); }).mockImplementationOnce(saves(context, (id) => version(2, { conversationId: id })));
    await send(user, "Make it shorter");
    expect(await chat().findByRole("alert")).toHaveTextContent("The API key was rejected.");
    await user.click(chat().getByRole("button", { name: "Retry" }));
    await screen.findByRole("heading", { level: 2, name: "Immune markers v2" });
    expect(turn.mock.calls[1]?.[2]).toEqual(expect.objectContaining({ kind: "message", text: "Make it shorter" }));
  });

  it("says why when the service did not handle a message, and gives the words back", async () => {
    const user = userEvent.setup(); await setup({ history: [version()] });
    vi.spyOn(knowledgeApi, "artifactTurnStream").mockRejectedValue(new Error("Another output is already being made."));
    await send(user, "Make it shorter");
    expect(await screen.findByRole("alert")).toHaveTextContent("Another output is already being made.");
    expect(chat().getByRole("textbox", { name: "Artifact message" })).toHaveValue("Make it shorter");
  });

  it("stops a turn and keeps the previous version", async () => {
    const user = userEvent.setup(); const running = held<ArtifactTurnOut>(); const context = await setup({ history: [version()] });
    vi.spyOn(knowledgeApi, "artifactTurnStream").mockImplementation(async (_base, id, payload, _workspace, hear) => { hear({ type: "decision", actions: [{ id: "a1", do: "write", label: "Writing section 1" }] }); keep(context.service, id, payload, { kind: "stopped", text: "" }); return running.promise; });
    const stop = vi.spyOn(knowledgeApi, "stopOutputBuild").mockImplementation(async () => running.fail(new OutputStoppedError()));
    await send(user, "Make it shorter");
    await user.click(await chat().findByRole("button", { name: "Stop generation" }));
    await waitFor(() => expect(context.onNotify).toHaveBeenCalledWith("Stopped. Nothing was saved."));
    expect(stop).toHaveBeenCalledWith("biology", "wsp_primary"); expect(screen.getByRole("heading", { level: 2, name: "Immune markers v1" })).toBeVisible();
    expect(await chat().findByText("Stopped. Nothing was saved.")).toBeVisible();
  });

  it("follows an existing build and associates its result with the conversation", async () => {
    const running = held<Artifact | null>(); let context!: Awaited<ReturnType<typeof setup>>;
    context = await setup({ follow: async (_base, _workspace, hear) => { hear({ type: "resumed", question: "Cover B cells", startedAt: 1 }); hear({ type: "outline", title: "Immune markers", sections: [{ heading: "B cells", goal: "" }] }); hear({ type: "section", index: 0, state: "researching" }); return running.promise; } });
    expect(chat().getAllByText("Cover B cells")[0]).toBeVisible();
    expect(chat().getByRole("button", { name: "Stop generation" })).toBeEnabled();
    const artifact = version(1, { conversationId: "recovered", conversationTitle: "Recovered request", clientRequestId: "original_request", request: "Cover B cells" });
    await act(async () => { context.history.unshift(artifact); context.service.set("recovered", conversation("recovered", { title: "Recovered request", works: [workOf(artifact)], active: artifact.lineageId, artifactCount: 1 })); running.release(artifact); });
    expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v1" })).toBeVisible();
    expect(within(screen.getByRole("region", { name: "Conversations" })).getByRole("button", { name: /Recovered request/ })).toHaveAttribute("aria-current", "page");
  });

  it("follows a turn that is still running, with its steps, and shows how it ended", async () => {
    const running = held<ArtifactTurnOut>();
    const mine = conversation("conv_a", { title: "Costs deck", messages: [said("old", "Earlier request"), replied("old_a", "reply", "Earlier answer.")], updatedAt: "2026-10-01T00:09:00.000Z" });
    const other = conversation("conv_b", { title: "Another conversation", updatedAt: "2026-10-01T00:10:00.000Z" });
    const context = await setup({ conversations: [other, mine], follow: async (_base, _workspace, hear) => {
      hear({ type: "resumed", question: "Add a slide on costs", startedAt: 1 });
      hear({ type: "turn", conversationId: "conv_a", messageId: "m" });
      hear({ type: "decision", actions: [{ id: "a1", do: "plan", label: "Adding a slide" }] });
      hear({ type: "action", id: "a1", state: "running", label: "Adding a slide", note: "" });
      return running.promise;
    } });
    // The turn belongs to the conversation it names, which opens.
    expect(within(screen.getByRole("region", { name: "Conversations" })).getByRole("button", { name: /Costs deck/ })).toHaveAttribute("aria-current", "page");
    expect(chat().getAllByText("Add a slide on costs")[0]).toBeVisible(); expect(chat().getByText("Adding a slide")).toBeVisible();
    const artifact = version(1, { conversationId: "conv_a" });
    await act(async () => { context.history.unshift(artifact); keep(context.service, "conv_a", { clientRequestId: "x", text: "Add a slide on costs" }, { kind: "result", text: "Added it.", artifactId: artifact.id }, artifact); running.release(turnOut("saved", "Added it.", artifact)); });
    expect(await chat().findByText("Added it.")).toBeVisible();
    expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v1" })).toBeVisible();
  });

  it("keeps a request in its own conversation when another conversation is opened", async () => {
    const user = userEvent.setup(); const running = held<ArtifactTurnOut>(); const context = await setup();
    vi.spyOn(knowledgeApi, "artifactTurnStream").mockReturnValue(running.promise);
    await send(user, "First request"); await user.click(screen.getByRole("button", { name: "New conversation" }));
    await user.type(chat().getByRole("textbox", { name: "Artifact message" }), "My next request");
    await act(async () => { context.history.unshift(version(1)); running.release(turnOut("saved", "Done.", version(1))); });
    expect(chat().getByRole("textbox", { name: "Artifact message" })).toHaveValue("My next request");
    expect(screen.queryByRole("heading", { level: 2, name: "Immune markers v1" })).not.toBeInTheDocument();
  });

  it("lists the conversations the service has and opens the one used last", async () => {
    const user = userEvent.setup();
    await setup({ conversations: [conversation("conv_a", { title: "Newer", updatedAt: "2026-10-02T00:00:00.000Z", messages: [said("q", "Newest question")] }), conversation("conv_b", { title: "Older", messages: [said("r", "Older question")] })] });
    const list = within(screen.getByRole("region", { name: "Conversations" }));
    expect(list.getByRole("button", { name: /Newer/ })).toHaveAttribute("aria-current", "page");
    expect(chat().getByText("Newest question")).toBeVisible();
    await user.click(list.getByRole("button", { name: /Older/ }));
    expect(await chat().findByText("Older question")).toBeVisible();
  });

  it("sends this device's earlier conversations to the service once", async () => {
    window.localStorage.setItem("gunther:artifact-conversations:wsp_primary:biology", JSON.stringify({ version: 1, activeId: "device_one", problem: null, conversations: [{ id: "device_one", title: "Notes on markers", untitled: false, scope: library, choice: null, draft: "", lineageIds: [], selectedArtifactId: null, createdAt: "2026-09-01T00:00:00.000Z", turns: [{ id: "t1", instruction: "Notes on markers", request: null, status: "failed", createdAt: "2026-09-01T00:00:00.000Z", result: null, error: "No key" }] }] }));
    const context = await setup();
    expect(context.imports).toHaveLength(1); expect(context.imports[0]!.conversations[0]).toMatchObject({ id: "device_one", turns: [{ id: "t1", status: "failed" }] });
    expect(within(screen.getByRole("region", { name: "Conversations" })).getByRole("button", { name: /Notes on markers/ })).toBeVisible();
    cleanup();
    await setup();
    expect(context.imports).toHaveLength(1);
  });
});

describe("pointing and the one message box", () => {
  const deckContext = (extra: Partial<Parameters<typeof setup>[0]> = {}) => setup({ history: [deck(1)], ...extra });
  const sentFocus = () => vi.mocked(knowledgeApi.artifactTurnStream).mock.calls.map((call) => call[2].focus);

  it("sends the slide on screen as a weak focus, and the kind of message", async () => {
    const user = userEvent.setup(); const context = await deckContext();
    vi.spyOn(knowledgeApi, "artifactTurnStream").mockImplementation(saves(context, (id) => deck(2, { conversationId: id })));
    await screen.findByText("Slide 1 of 3");
    await send(user, "Shorten this"); await waitFor(() => expect(sentFocus()).toHaveLength(1));
    await screen.findByRole("heading", { level: 2, name: "Deck v2" }); await screen.findByText("Slide 1 of 3");
    await user.click(await screen.findByRole("button", { name: "Go to slide 3" })); expect(screen.getByText("Slide 3 of 3")).toBeVisible();
    await send(user, "And this one"); await waitFor(() => expect(sentFocus()).toHaveLength(2));
    expect(sentFocus()).toEqual([{ section: 1, selected: false }, { section: 3, selected: false }]);
    expect(vi.mocked(knowledgeApi.artifactTurnStream).mock.calls[0]?.[2]).toEqual(expect.objectContaining({ kind: "message", text: "Shorten this" }));
    expect(chat().queryByRole("group", { name: "About" })).not.toBeInTheDocument();
  });

  it("points at a slide with Ask about this slide, shows it as a chip, and clears it after sending", async () => {
    const user = userEvent.setup(); const context = await deckContext(); const running = held<ArtifactTurnOut>();
    vi.spyOn(knowledgeApi, "artifactTurnStream").mockImplementation(async () => running.promise);
    await user.click(await screen.findByRole("button", { name: "Go to slide 2" }));
    await user.click(screen.getByRole("button", { name: "Ask about this slide" }));
    const chip = chat().getByRole("group", { name: "About" }); expect(chip).toHaveTextContent("Slide 2 · Costs");
    expect(chat().getByRole("textbox", { name: "Artifact message" })).toHaveFocus();
    await user.click(within(chip).getByRole("button", { name: "Clear what this is about" })); expect(chat().queryByRole("group", { name: "About" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Ask about this slide" }));
    await send(user, "Make it shorter");
    await waitFor(() => expect(sentFocus()).toEqual([{ section: 2, selected: true }]));
    expect(chat().queryByRole("group", { name: "About" })).not.toBeInTheDocument();
    await act(async () => { running.release(turnOut("replied", "Fine.")); });
    expect(context.history).toHaveLength(1);
  });

  it("points at selected words in a report, and at a section from its heading", async () => {
    const user = userEvent.setup(); const running = held<ArtifactTurnOut>();
    await setup({ history: [version(1, { conversationId: "conv" })] });
    vi.spyOn(knowledgeApi, "artifactTurnStream").mockImplementation(async () => running.promise);
    const article = within(screen.getByRole("article"));
    expect(screen.queryByRole("button", { name: "Ask about this" })).not.toBeInTheDocument();
    await act(async () => select(article.getByText(/CD19 marks B cells/)));
    await user.click(await screen.findByRole("button", { name: "Ask about this" }));
    expect(chat().getByRole("group", { name: "About" })).toHaveTextContent(/“CD19 marks B cells/);
    expect(screen.queryByRole("button", { name: "Ask about this" })).not.toBeInTheDocument();
    await send(user, "Say it more plainly");
    await waitFor(() => expect(sentFocus()).toHaveLength(1));
    expect(sentFocus()[0]).toEqual({ section: 2, quote: expect.stringContaining("CD19 marks B cells"), selected: true });
    expect(chat().queryByRole("group", { name: "About" })).not.toBeInTheDocument();
    await act(async () => { running.release(turnOut("replied", "Fine.")); });
    await user.click(screen.getAllByRole("button", { name: "Ask about this section" })[0]!);
    expect(chat().getByRole("group", { name: "About" })).toHaveTextContent("Section 1 · T cells");
  });

  it("ignores words selected across two sections", async () => {
    await setup({ history: [version(1, { conversationId: "conv" })] });
    const sections = document.querySelectorAll(".outputs-section");
    await act(async () => { const range = document.createRange(); range.setStart(sections[0]!.querySelector("p")!, 0); range.setEnd(sections[1]!.querySelector("p")!, 1); const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range); document.dispatchEvent(new Event("selectionchange")); });
    expect(screen.queryByRole("button", { name: "Ask about this" })).not.toBeInTheDocument();
  });

  it("shows a question with its options, and sends the chosen one as the answer", async () => {
    const user = userEvent.setup();
    const asked = conversation("conv_q", { title: "Roofs", messages: [said("q1", "Slides about roofs"), replied("q2", "question", "Focus on the trial results, or the whole debate?", { payload: { options: ["Trial", "Whole debate"] } })], pending: { question: "Focus on the trial results, or the whole debate?", options: ["Trial", "Whole debate"], messageId: "q2" } });
    const context = await setup({ conversations: [asked] });
    const turn = vi.spyOn(knowledgeApi, "artifactTurnStream").mockImplementation(async (_base, id, payload) => { keep(context.service, id, payload, { kind: "reply", text: "Going with the trial." }); return turnOut("replied", "Going with the trial."); });
    const options = chat().getByRole("group", { name: "Answers" });
    expect(within(options).getAllByRole("button").map((button) => button.textContent)).toEqual(["Trial", "Whole debate", "Just start"]);
    expect(within(screen.getByRole("region", { name: "Conversations" })).getByText("Waiting for your answer")).toBeVisible();
    await user.click(within(options).getByRole("button", { name: "Whole debate" }));
    await waitFor(() => expect(turn).toHaveBeenCalled());
    expect(turn.mock.calls[0]?.[2]).toEqual(expect.objectContaining({ kind: "answer", text: "Whole debate" }));
    expect(await chat().findByText("Going with the trial.")).toBeVisible();
    // Answered: the options are gone.
    expect(chat().queryByRole("group", { name: "Answers" })).not.toBeInTheDocument();
  });

  it("starts without more questions on Just start, and sends typed words as the answer", async () => {
    const user = userEvent.setup();
    const asked = () => conversation("conv_q", { title: "Roofs", messages: [said("q1", "Slides about roofs"), replied("q2", "question", "Which focus?", { payload: { options: ["Trial"] } })], pending: { question: "Which focus?", options: ["Trial"], messageId: "q2" } });
    const context = await setup({ conversations: [asked()] });
    const turn = vi.spyOn(knowledgeApi, "artifactTurnStream").mockImplementation(async (_base, id, payload) => { keep(context.service, id, payload, { kind: "reply", text: "Starting." }); return turnOut("replied", "Starting."); });
    await user.click(chat().getByRole("button", { name: "Just start" }));
    await waitFor(() => expect(turn).toHaveBeenCalledTimes(1));
    expect(turn.mock.calls[0]?.[2]).toEqual(expect.objectContaining({ kind: "start" })); expect(turn.mock.calls[0]?.[2]).not.toHaveProperty("text"); expect(turn.mock.calls[0]?.[2]).not.toHaveProperty("focus");
    context.service.set("conv_q", asked()); cleanup();
    await setup({ conversations: [asked()] });
    await send(user, "Costs, for first-years");
    await waitFor(() => expect(turn).toHaveBeenCalledTimes(2));
    expect(turn.mock.calls[1]?.[2]).toEqual(expect.objectContaining({ kind: "answer", text: "Costs, for first-years" }));
  });

  it("continues a paused turn", async () => {
    const user = userEvent.setup();
    const context = await setup({ conversations: [conversation("conv_p", { title: "Big deck", messages: [said("p1", "Make everything"), replied("p2", "paused", "I ran out of room; 4 slides are not written yet.")] })] });
    const turn = vi.spyOn(knowledgeApi, "artifactTurnStream").mockImplementation(async (_base, id, payload) => { keep(context.service, id, payload, { kind: "reply", text: "Carrying on." }); return turnOut("replied", "Carrying on."); });
    await user.click(chat().getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(turn).toHaveBeenCalled());
    expect(turn.mock.calls[0]?.[2]).toEqual(expect.objectContaining({ kind: "continue" })); expect(turn.mock.calls[0]?.[2]).not.toHaveProperty("text");
    expect(await chat().findByText("Carrying on.")).toBeVisible();
    expect(chat().queryByRole("button", { name: "Continue" })).not.toBeInTheDocument();
  });

  const changes = [{ position: 2, was: 2, heading: "Costs", what: "rewritten", note: "shortened" }, { position: 3, was: null, heading: "Results", what: "added", note: "" }, { position: null, was: 4, heading: "Old news", what: "deleted", note: "" }];
  const result = (artifact: Artifact, id = "r") => [said(`${id}1`, "Shorten the costs and add results"), replied(`${id}2`, "result", "Shortened the costs slide and added one on results.", { artifactId: artifact.id, payload: { changes, steps: [{ id: "a1", state: "done", label: "Wrote slide 2", note: "" }, { id: "a2", state: "failed", label: "Searched the web for “prices”", note: "The web is off" }] } })];

  it("lists what a result changed, and a row takes the preview to its slide", async () => {
    const user = userEvent.setup(); const second = withChanges(deck(2), changes as never);
    await setup({ history: [second, deck(1)], conversations: [conversation("conv", { title: "A talk", messages: result(second) })] });
    await screen.findByText("Slide 1 of 3");
    const list = chat().getByRole("list", { name: "What changed" });
    expect(within(list).getByRole("button", { name: /Slide 2 · Costs — shortened/ })).toBeVisible();
    expect(within(list).getByRole("button", { name: /Added slide 3 · Results/ })).toBeVisible();
    expect(within(list).getByRole("button", { name: /Removed slide 4 · Old news/ })).toBeVisible();
    expect(within(list).queryByText(/Not done/)).toBeNull(); // a saved turn put right what it was refused
    await user.click(within(list).getByRole("button", { name: /Added slide 3 · Results/ }));
    await waitFor(() => expect(screen.getByText("Slide 3 of 3")).toBeVisible());
    expect(screen.getByRole("button", { name: "Go to slide 3" })).toHaveAttribute("aria-current", "true");
    await user.click(within(list).getByRole("button", { name: /Slide 2 · Costs — shortened/ }));
    await waitFor(() => expect(screen.getByText("Slide 2 of 3")).toBeVisible());
    // The version that made the change is one click away.
    expect(chat().getByRole("button", { name: "Open v2" })).toBeVisible();
  });

  it("opens the version a row belongs to before taking the preview to its part", async () => {
    const user = userEvent.setup(); const first = withChanges(deck(1), [changes[0]] as never);
    await setup({ history: [deck(2), first], conversations: [conversation("conv", { title: "A talk", messages: result(first) })] });
    expect(await screen.findByRole("heading", { level: 2, name: "Deck v2" })).toBeVisible();
    await user.click(chat().getByRole("button", { name: /Slide 2 · Costs — shortened/ }));
    expect(await screen.findByRole("heading", { level: 2, name: "Deck v1" })).toBeVisible();
    await waitFor(() => expect(screen.getByText("Slide 2 of 3")).toBeVisible());
  });

  it("marks the slides and sections a version changed", async () => {
    const second = withChanges(deck(2), changes as never);
    await setup({ history: [second, deck(1)] });
    await screen.findByText("Slide 1 of 3");
    const strip = within(screen.getByRole("list", { name: "Slides" }));
    expect(strip.getAllByText("Changed")).toHaveLength(2);
    expect(within(strip.getByRole("button", { name: "Go to slide 2" })).getByText("Changed")).toBeVisible();
    expect(within(strip.getByRole("button", { name: "Go to slide 1" }).closest("li")!).queryByText("Changed")).not.toBeInTheDocument();
    cleanup();
    await setup({ history: [withChanges(version(2, { conversationId: "conv_r" }), [{ position: 2, was: 2, heading: "B cells", what: "edited", note: "plainer" }])] });
    const sections = document.querySelectorAll(".outputs-section");
    expect(within(sections[1] as HTMLElement).getByText("Changed")).toBeVisible(); expect(within(sections[0] as HTMLElement).queryByText("Changed")).not.toBeInTheDocument();
  });

  it("answers with sources that open their evidence", async () => {
    const user = userEvent.setup();
    await setup({ history: [version(1, { conversationId: "conv_r" })], conversations: [conversation("conv_r", { title: "Notes", messages: [said("w1", "Which source says CD3D?"), replied("w2", "answer", "The Marker review says CD3D marks T cells [1].")] })] });
    await user.click(within(chat().getByRole("log")).getByRole("button", { name: "Inspect citation 1" }));
    expect(await screen.findByRole("link", { name: /Open example\.org/ })).toBeVisible();
  });
});

describe("the brief", () => {
  const brief = { goal: { text: "Show the Velmora trial results", mark: "you" as const }, audience: { text: "Lab members", mark: "guessed" as const }, constraints: [{ text: "At most 10 slides", mark: "edited" as const }], open: [] };
  const work = (artifact: Artifact) => ({ ...workOf(artifact), spec: { kind: "slides" as const, use: { text: "talk", mark: "you" as const }, length: { text: "10 minutes", mark: "guessed" as const }, structure: null } });
  const withBrief = (overrides: Partial<ArtifactConversationDetail> = {}) => conversation("conv", { title: "A talk", brief, works: [work(deck(1))], active: "deck_lineage", ...overrides });

  it("shows what the conversation is for and what the deck should be, and whose each line is", async () => {
    const user = userEvent.setup(); await setup({ history: [deck(1)], conversations: [withBrief()] }); await showControls(user);
    const panel = within(screen.getByRole("region", { name: "Brief" }));
    expect(panel.getByText("Show the Velmora trial results")).toBeVisible(); expect(panel.getByText("Lab members")).toBeVisible(); expect(panel.getByText("At most 10 slides")).toBeVisible(); expect(panel.getByText("10 minutes")).toBeVisible();
    expect(panel.getAllByText("you said")).toHaveLength(2); expect(panel.getAllByText("guessed")).toHaveLength(2); expect(panel.getByText("edited")).toBeVisible();
    expect(panel.getByRole("combobox", { name: "Use" })).toHaveValue("talk");
    // Above the approach and outline panels.
    const brief = screen.getByRole("region", { name: "Brief" }); const outline = screen.getByRole("button", { name: /Outline/ });
    expect(brief.compareDocumentPosition(outline) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("changes a line and marks it as the person's", async () => {
    const user = userEvent.setup(); await setup({ history: [deck(1)], conversations: [withBrief()] }); await showControls(user);
    const edit = vi.spyOn(knowledgeApi, "editArtifactBrief").mockResolvedValue({ brief: { ...brief, audience: { text: "First-year students", mark: "edited" } }, spec: work(deck(1)).spec });
    const panel = within(screen.getByRole("region", { name: "Brief" }));
    await user.click(panel.getByRole("button", { name: "Lab members" }));
    const box = panel.getByRole("textbox", { name: "Edit audience" }); await user.clear(box); await user.type(box, "First-year students{Enter}");
    expect(edit).toHaveBeenCalledWith("biology", "conv", { audience: "First-year students" }, "wsp_primary");
    expect(await panel.findByText("First-year students")).toBeVisible(); expect(panel.queryByText("guessed", { selector: "small" })).toBeInTheDocument();
    await user.selectOptions(panel.getByRole("combobox", { name: "Use" }), "read");
    expect(edit).toHaveBeenLastCalledWith("biology", "conv", { spec: { use: "read" } }, "wsp_primary");
  });

  it("adds a constraint as the whole new list", async () => {
    const user = userEvent.setup(); await setup({ history: [deck(1)], conversations: [withBrief()] }); await showControls(user);
    const edit = vi.spyOn(knowledgeApi, "editArtifactBrief").mockResolvedValue({ brief, spec: null });
    const panel = within(screen.getByRole("region", { name: "Brief" }));
    await user.type(panel.getByRole("textbox", { name: "Add to the brief" }), "No jargon{Enter}");
    expect(edit).toHaveBeenCalledWith("biology", "conv", { constraints: ["At most 10 slides", "No jargon"] }, "wsp_primary");
  });

  it("says so when a change was refused, and is locked while the agent works", async () => {
    const user = userEvent.setup(); const running = held<ArtifactTurnOut>();
    await setup({ history: [deck(1)], conversations: [withBrief()] }); await showControls(user);
    vi.spyOn(knowledgeApi, "editArtifactBrief").mockRejectedValue(new Error("A deck's use is talk or read."));
    const panel = within(screen.getByRole("region", { name: "Brief" }));
    await user.click(panel.getByRole("button", { name: "Lab members" })); await user.type(panel.getByRole("textbox", { name: "Edit audience" }), "x{Enter}");
    expect(await panel.findByRole("alert")).toHaveTextContent("A deck's use is talk or read.");
    vi.spyOn(knowledgeApi, "artifactTurnStream").mockImplementation(async () => running.promise);
    await send(user, "Shorten it");
    expect(await panel.findByText("Locked while the agent works")).toBeVisible();
    expect(panel.getByRole("button", { name: "Lab members" })).toBeDisabled(); expect(panel.getByRole("combobox", { name: "Use" })).toBeDisabled();
    await act(async () => { running.release(turnOut("replied", "Nothing to change.")); });
  });
});

describe("sources and configuration", () => {
  it("keeps working selection independent of the saved artifact sources", async () => {
    const user = userEvent.setup(); const context = await setup(); vi.spyOn(knowledgeApi, "artifactTurnStream").mockImplementation(saves(context, (id) => version(1, { conversationId: id })));
    await user.click(sourcePanel().getByRole("checkbox", { name: /Source 2/ }));
    expect(chat().getByText("1 source · 1 saved")).toBeVisible(); await send(user, "Build from my selection");
    await waitFor(() => expect(context.created).toHaveLength(1));
    expect(context.created[0]?.scope).toEqual({ mode: "selection", sourceIds: ["src_1"], unitIds: ["unt_1"], sessionIds: [] });
    expect(sourcePanel().getByRole("checkbox", { name: /Source 2/ })).not.toBeChecked();
    expect(await within(await screen.findByRole("article")).findByText(/Built from 2 sources · 1 saved/)).toBeVisible();
  });
  it("uses a chosen Ask discussion for a new artifact", async () => {
    const user = userEvent.setup(); const context = await setup({ history: [version()], from: "ses_1" }); vi.spyOn(knowledgeApi, "artifactTurnStream").mockImplementation(saves(context, (id) => version(2, { conversationId: id })));
    expect(chat().getByText("1 discussion")).toBeVisible(); await send(user, "Make a report from the discussion");
    await waitFor(() => expect(context.created).toHaveLength(1));
    expect(context.onFromDiscussion).toHaveBeenCalledOnce(); expect(context.created[0]?.scope).toEqual({ mode: "selection", sourceIds: [], unitIds: [], sessionIds: ["ses_1"] });
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
  it("opens earlier versions and requires the latest before editing them by hand", async () => {
    const user = userEvent.setup(); await setup({ history: [version(2), version(1)] });
    await user.selectOptions(screen.getByRole("combobox", { name: "Artifact versions" }), "art_1");
    expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v1" })).toBeVisible();
    await user.type(chat().getByRole("textbox", { name: "Artifact message" }), "Change it"); expect(chat().getByRole("button", { name: "Send message" })).toBeEnabled(); // the agent works on the latest; an older one on screen points at nothing
    await user.click(chat().getByRole("button", { name: "Open latest version 2" })); expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v2" })).toBeVisible();
  });
  it("rebuilds from an edited outline and preserves the parent version", async () => {
    const user = userEvent.setup(); const build = vi.spyOn(knowledgeApi, "buildOutputStream").mockResolvedValue(version(2)); await setup({ history: [version()] }); await showControls(user);
    await user.click(screen.getByRole("button", { name: /Outline/ })); await user.click(screen.getByRole("button", { name: "Edit outline" }));
    await user.clear(screen.getByRole("textbox", { name: "Heading 2" })); await user.type(screen.getByRole("textbox", { name: "Heading 2" }), "Plasma cells"); await user.click(screen.getByRole("button", { name: "Move 2 up" })); await user.click(screen.getByRole("button", { name: "Rebuild with this outline" }));
    expect(build.mock.calls[0]?.[1]).toEqual(expect.objectContaining({ supersedesArtifactId: "art_1", outline: [{ heading: "Plasma cells", goal: "", layoutLocked: false, designLocked: false, designAttempted: false }, { heading: "T cells", goal: "How T cells are told apart", layoutLocked: false, designLocked: false, designAttempted: false }] }));
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
  const accentDeck = (number = 1, overrides: Partial<Artifact> = {}) => version(number, { kind: "slides", format: "slides", style: null, title: `Deck v${number}`, content: "<!-- layout: title -->\n# Deck\n\nOne line\n\n---\n\n<!-- layout: takeaways -->\n## Done\n\n- A point [1]", ...overrides });
  it("saves a deck's accent as a new version, written in its title slide's marker", async () => {
    const user = userEvent.setup(); const edit = vi.spyOn(knowledgeApi, "editOutput").mockResolvedValue(accentDeck(2, { origin: "edit", content: "<!-- layout: title; accent: blue -->\n# Deck\n\nOne line" }));
    const { onNotify } = await setup({ history: [accentDeck()] });
    await user.click(await screen.findByRole("button", { name: "Accent colour" })); await user.click(screen.getByRole("menuitem", { name: "Blue" }));
    await waitFor(() => expect(edit).toHaveBeenCalledOnce());
    expect(edit.mock.calls[0]?.[1]).toBe("art_1"); expect(edit.mock.calls[0]?.[2].content).toBe("<!-- layout: title; accent: blue -->\n# Deck\n\nOne line\n\n---\n\n<!-- layout: takeaways -->\n## Done\n\n- A point [1]");
    expect(await screen.findByRole("heading", { level: 2, name: "Deck v2" })).toBeVisible(); expect(onNotify).toHaveBeenCalledWith("Saved as version 2. The accent is blue.");
  });
  it("saves a deck's Style choice as a new version without changing slide content", async () => {
    const original = accentDeck();
    const styled = "<!-- layout: title; theme: editorial; design: v1 -->\n# Deck\n\nOne line\n\n---\n\n<!-- layout: takeaways -->\n## Done\n\n- A point [1]";
    const user = userEvent.setup();
    const edit = vi.spyOn(knowledgeApi, "editOutput").mockResolvedValue(accentDeck(2, { origin: "edit", content: styled }));
    await setup({ history: [original] });
    await user.click(await screen.findByRole("button", { name: "Slide style" }));
    await user.click(screen.getByRole("button", { name: /Editorial/ }));
    await waitFor(() => expect(edit).toHaveBeenCalledOnce());
    expect(edit.mock.calls[0]?.[2].content).toBe(styled);
  });
  it("saves nothing when the deck already has the accent that was chosen", async () => {
    const user = userEvent.setup(); const edit = vi.spyOn(knowledgeApi, "editOutput");
    await setup({ history: [accentDeck(1, { content: "<!-- layout: title; accent: teal -->\n# Deck" })] });
    await user.click(await screen.findByRole("button", { name: "Accent colour" })); await user.click(screen.getByRole("menuitem", { name: "Teal" }));
    expect(edit).not.toHaveBeenCalled();
  });
  it("offers the accent only for the latest version of a deck made by the agents", async () => {
    const user = userEvent.setup();
    await setup({ history: [accentDeck(2), accentDeck(1)] });
    // Version 2 is the latest.
    expect(await screen.findByRole("button", { name: "Accent colour" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Slide style" })).toBeEnabled();
    await user.selectOptions(screen.getByRole("combobox", { name: "Artifact versions" }), "art_1");
    expect(await screen.findByRole("button", { name: "Accent" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Accent colour" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Slide style" })).toBeDisabled();
  });
  it("does not offer the accent for a version from the old builder", async () => {
    await setup({ history: [accentDeck(1, { origin: "legacy" })] });
    await screen.findByText("Slide 1 of 2"); expect(screen.queryByRole("button", { name: /^Accent/ })).not.toBeInTheDocument();
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
    await setup({ history: [version(1, { kind: "slides", format: "slides", style: null, content: "# Deck\n\n---\n\n## One\n\n- a [1]\n\nNote: Say a." })] }); await screen.findByText("Slide 1 of 2"); const paper = document.getElementById("print-root")!; await waitFor(() => expect(paper.querySelectorAll(".outputs-print-slide")).toHaveLength(3)); expect(paper.textContent).not.toContain("Say a.");
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
