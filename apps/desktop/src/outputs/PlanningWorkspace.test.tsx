import type {
  ArtifactConversationDetail, ArtifactConversationMessage, ArtifactTurnOut, ArtifactWork, CreationTurn, ModelMenu, OutputScope,
  OutputType, PlanLine, PlanOptions, PlanPart, WorkPlan, WorkPlanPatch,
} from "@gunther/contracts";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError } from "../api";
import { ApprovedPlan, PlanDetailReader, PlanningWorkspace, SavedPlanPreferences, type PlanningWorkspaceProps } from "./PlanningWorkspace";
import type { LocalSetup, StudioPlanning } from "./useStudio";

afterEach(cleanup);

const library: OutputScope = { mode: "library", sourceIds: [], unitIds: [], sessionIds: [] };
const selection: OutputScope = { mode: "selection", sourceIds: ["source1", "source2"], unitIds: [], sessionIds: [] };
const address = { conversationId: "conversation_1", planId: "draft_1" };
const now = "2026-11-26T14:00:00Z";
const models: ModelMenu = {
  models: [{ ref: "studio", provider: "Local", label: "Studio", display: "Local Studio", vision: false, levels: [{ id: "high", label: "High" }] }],
  default: { model: "studio", effort: "high" }, efforts: [{ id: "high", label: "High" }],
};
const flashcards: OutputType = {
  id: "flashcards", label: "Flashcards", shape: "cards", icon: "Layers", description: "Practice recalling ideas.",
  partLimit: 40, minParts: 1, options: ["focus", "audience", "length", "language"],
  capabilities: { outline: false, design: false, exports: [], unitLabel: "card" },
};
const slides: OutputType = {
  id: "slides", label: "Slides", shape: "slides", icon: "Presentation", description: "Present the findings.",
  partLimit: 14, options: ["focus", "audience", "length", "language", "use"], outlineLayouts: ["title", "text", "figure"], firstPartRole: "Title slide",
  capabilities: { outline: true, design: true, exports: [], unitLabel: "slide" },
};
const audio: OutputType = {
  id: "audio_overview", label: "Audio overview", shape: "audio", icon: "AudioLines", description: "Discuss the sources.",
  partLimit: 120, minParts: 2, options: ["focus", "voices"],
  capabilities: { outline: false, design: false, exports: [], unitLabel: "line" },
  voices: { choices: [{ value: "one", label: "Voice One" }, { value: "two", label: "Voice Two" }],
    defaults: ["one", "two"], allowCustom: false },
};
const part = (id: string, heading: string, layout: string | null): PlanPart => ({ id, heading, goal: "", notes: "", layout });
const topic = (id: string, text: string): PlanLine => ({ id, text });
const firstTopic = topic("topic_a", "Main finding");
const secondTopic = topic("topic_b", "Limits of the evidence");

const preferences = { emphasize: [], avoid: [] };
function options(type: OutputType): PlanOptions {
  return {
    type: type.id, scope: library, title: null, focus: "", audience: "scientist", length: null, language: null,
    use: null, theme: null, designVersion: null, voices: type.voices?.defaults ?? null, web: false, model: "studio", effort: null,
  };
}

function planFor(type: OutputType, override: Partial<WorkPlan> = {}): WorkPlan {
  return {
    planId: address.planId, planRevision: "p7", planStatus: "ready", basis: "assistant", blocker: null,
    options: options(type), preferences,
    proposal: { title: "Evidence review", summary: "Review the sources.", angle: "Tradeoffs",
      coverage: [firstTopic, secondTopic], count: { mode: "exact", value: 2 }, targetMinutes: null },
    outline: type.capabilities.outline ? [part("cover", "Evidence review", "title"), part("finding", "The findings", null)] : null,
    pending: null, attempt: null, approvedRevision: null, ...override,
  };
}

function detailFor(type: OutputType, plan: WorkPlan | null = planFor(type), editVersion = 7,
  messages: ArtifactConversationMessage[] = []): ArtifactConversationDetail {
  const work: ArtifactWork = {
    key: address.planId, kind: type.shape, title: plan?.proposal?.title || "Evidence review", versionId: null,
    spec: { kind: type.shape, use: null, length: null, outputType: null },
    approach: { state: "ok", why: "" }, outline: { state: "ok", why: "" }, parts: [], plan,
  };
  return {
    id: address.conversationId, title: work.title, shape: type.shape, scope: library, outputType: type.label,
    active: work.key, works: [work], lineageId: null, artifactCount: 0, waiting: Boolean(plan?.pending), pending: null,
    brief: { goal: null, audience: null, constraints: [], open: [], ...(plan?.preferences ?? preferences) },
    messages, lastTurn: null, archived: false, createdAt: now, updatedAt: now, editVersion,
  };
}

const message = (id: string, role: ArtifactConversationMessage["role"], kind: string, text: string,
  payload: Record<string, unknown> = {}): ArtifactConversationMessage => ({
  id, role, kind, text, payload, artifactId: null, clientRequestId: null, createdAt: now,
});

const finished: ArtifactTurnOut = {
  outcome: "planned", reply: "", question: null, artifact: null, changes: [], message: null,
};

function applyPatch(detail: ArtifactConversationDetail, input: WorkPlanPatch): ArtifactConversationDetail {
  const work = detail.works[0]!;
  const editVersion = (detail.editVersion ?? 0) + 1;
  const toPreferences = (field: "emphasize" | "avoid") => input[field]?.map((row) =>
    detail.brief[field].find((old) => old.id === row.id && old.text === row.text) ?? { ...row, mark: "edited" as const }) ?? detail.brief[field];
  const brief = { ...detail.brief, emphasize: toPreferences("emphasize"), avoid: toPreferences("avoid") };
  if (!work.plan) return { ...detail, editVersion, brief };
  const previous = work.plan;
  const options = { ...previous.options, ...input.options };
  const outline = input.outline ?? previous.outline;
  const title = outline?.[0]?.heading !== previous.outline?.[0]?.heading ? outline?.[0]?.heading ?? work.title : work.title;
  const countEdit = input.count ?? previous.proposal?.count;
  const count = input.outline ? { mode: "exact" as const, value: input.outline.length } : countEdit?.mode === "auto"
    ? { mode: "auto" as const, value: options.length === "short" ? 10 : options.length === "long" ? 40 : 20 }
    : countEdit;
  const proposal = previous.proposal && count ? {
    ...previous.proposal, count, coverage: input.coverage ?? previous.proposal.coverage, title: options.title || title,
  } : null;
  const changedMaterial = Boolean(input.options?.scope || (input.options?.web && !previous.options.web));
  const plan: WorkPlan = {
    ...previous, options, outline, proposal, preferences: { emphasize: brief.emphasize, avoid: brief.avoid }, basis: "user_edited", planRevision: `p${editVersion}`,
    planStatus: changedMaterial ? "planning" : previous.planStatus,
    blocker: changedMaterial ? "Plan with these sources before generating." : previous.blocker,
    pending: previous.pending ? { ...previous.pending, planRevision: `p${editVersion}` } : null,
  };
  return { ...detail, editVersion, brief, works: [{ ...work, title, plan }] };
}

function mount({ type = flashcards, initial = detailFor(type), local, ...overrides }: Partial<PlanningWorkspaceProps> & {
  initial?: ArtifactConversationDetail | null;
  local?: LocalSetup;
} = {}) {
  let server = initial;
  let project: (detail: ArtifactConversationDetail | null) => void = () => {};
  let updateProps: (props: Partial<PlanningWorkspaceProps>) => void = () => {};
  let lastLocal = local;
  const dirty = { current: false };
  const patch = vi.fn(async (_conversation: string, _work: string, input: WorkPlanPatch) => {
    if (!server) throw new Error("No work");
    if (input.baseEditVersion !== server.editVersion) throw new ApiRequestError("Plan changed", 409, "edit_conflict");
    server = applyPatch(server, input);
    project(server);
    return server;
  });
  const read = vi.fn(async () => { project(server); return server; });
  const turn = vi.fn(async (_conversation: string, _turn: CreationTurn) => finished);
  const setLocal = vi.fn((draft: LocalSetup) => { lastLocal = draft; });
  const stop = vi.fn();

  function Screen() {
    const [detail, setDetail] = useState(initial);
    const [draft, setDraft] = useState(local);
    const [changed, setChanged] = useState<Partial<PlanningWorkspaceProps>>({});
    project = setDetail;
    updateProps = (props) => setChanged((current) => ({ ...current, ...props }));
    const controller: StudioPlanning = {
      locals: { [address.planId]: draft }, targetFor: () => address, discardLocal: () => setDraft(undefined),
      setLocal: (next) => { setLocal(next); setDraft(next); }, patch, turn,
    };
    return <PlanDetailReader.Provider value={read}>
      <PlanningWorkspace {...{ address, type, controller, detail, work: detail?.works[0] ?? null, dirty }}
        sourceState={(scope) => scope.mode === "selection" && !scope.sourceIds.length && !scope.unitIds.length && !scope.sessionIds.length
          ? "empty" : "ready"} currentScope={library} modelMenu={models} webAvailable busy={false} stage={null} onStop={stop}
        {...overrides} {...changed} />
    </PlanDetailReader.Provider>;
  }

  const view = render(<Screen />);
  return {
    patch, read, turn, setLocal, stop, dirty, unmount: view.unmount,
    props: (props: Partial<PlanningWorkspaceProps>) => act(() => updateProps(props)),
    setServer: (detail: ArtifactConversationDetail, show = true) => {
      server = detail;
      if (show) act(() => project(detail));
    },
    getServer: () => server!,
    getLocal: () => lastLocal!,
  };
}

function setupFor(type: OutputType, scope = library): LocalSetup {
  return { ...address, setup: { type: type.id, scope }, firstRequestId: "initial_request", updatedAt: now, touched: false };
}

const approve = () => screen.getByRole("button", { name: "Approve & Generate" });
const text = (name: string) => screen.getByRole<HTMLInputElement>("textbox", { name });
const change = (name: string, value: string) => fireEvent.change(text(name), { target: { value } });
const question = (id: string, revision = "p7") => ({
  planId: address.planId, planRevision: revision, messageId: id, question: `Question for round ${id}`, options: ["First choice", "Second choice"],
});
const useQuestion = (id: string, revision = "p7") => planFor(flashcards, {
  planRevision: revision, planStatus: "needs_answer", blocker: "Answer the pending question or choose Use current settings.",
  pending: question(id, revision),
});

describe("PlanningWorkspace", () => {
  it("keeps Setup usable during a blocker and starts planning only from the explicit button", async () => {
    const user = userEvent.setup();
    const app = mount({ initial: null, local: setupFor(slides), type: slides, modelMenu: null, currentScope: selection });
    expect(screen.getByRole("heading", { name: "Set up Slides" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Plan" })).toBeDisabled();
    expect(screen.getByText("Choose an available model in Settings.")).toBeVisible();
    expect(app.turn).not.toHaveBeenCalled();

    await user.type(text("Focus"), "Show the conclusion");
    await user.selectOptions(screen.getByRole("combobox", { name: "Use" }), "read");
    await user.selectOptions(screen.getByRole("combobox", { name: "Theme" }), "editorial");
    await user.click(screen.getByRole("button", { name: "Use current selection" }));
    await user.type(text("New item for What to emphasize"), "Primary result{Enter}");
    await user.type(text("New item for What to avoid"), " primary  RESULT {Enter}");
    expect(screen.getByText("The same instruction appears in both preference lists.")).toBeVisible();
    await user.type(text("Language"), "French{Enter}");
    expect(app.turn).not.toHaveBeenCalled();
    expect(app.getLocal()).toMatchObject({
      touched: true, setup: { scope: selection, focus: "Show the conclusion", language: "French", use: "read", theme: "editorial" },
    });
    expect(new Set([app.getLocal().setup.emphasize?.[0]?.id, app.getLocal().setup.avoid?.[0]?.id]).size).toBe(2);
    expect(app.getLocal().setup.avoid?.[0]?.id).toMatch(/^[A-Za-z][A-Za-z0-9_-]{0,63}$/);

    app.props({ modelMenu: models, sourceState: () => "error" });
    expect(screen.getByText(/sources couldn’t load/)).toBeVisible();
    expect(text("Focus")).toHaveValue("Show the conclusion");
    app.props({ sourceState: () => "ready" });
    await user.click(screen.getByRole("button", { name: "Plan" }));
    expect(app.turn).toHaveBeenCalledExactlyOnceWith(address.conversationId, {
      kind: "plan", planId: address.planId, clientRequestId: "initial_request", setup: app.getLocal().setup,
    });
    expect(app.patch).not.toHaveBeenCalled();
  });

  it("uses the preserved initial request ID after a lost response", async () => {
    const user = userEvent.setup();
    const app = mount({ initial: null, local: setupFor(flashcards) });
    app.turn.mockRejectedValueOnce(new Error("Connection closed"));
    await user.click(screen.getByRole("button", { name: "Plan" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Connection closed");
    await user.click(screen.getByRole("button", { name: "Plan" }));
    expect(app.turn.mock.calls[1]).toEqual(app.turn.mock.calls[0]);
  });

  it("restores an older headless work and reuses its identity at the next explicit Plan", async () => {
    const detail = detailFor(flashcards, null);
    detail.brief.goal = { text: "Keep the prior focus", mark: "you" };
    detail.brief.avoid = [{ id: "prior", text: "Unverified claims", mark: "you" }];
    const app = mount({ initial: detail });
    expect(text("Focus")).toHaveValue("Keep the prior focus");
    expect(text("What to avoid, item 1")).toHaveValue("Unverified claims");
    expect(app.turn).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Plan" }));
    expect(app.turn.mock.calls[0]?.[1]).toMatchObject({
      kind: "plan", planId: address.planId, setup: { focus: "Keep the prior focus", avoid: [{ id: "prior", text: "Unverified claims" }] },
    });
  });

  it("reopens legacy Setup with the work's saved options and leaves its outline intact", async () => {
    const detail = detailFor(slides, null);
    detail.brief.goal = { text: "Conversation-level focus", mark: "you" };
    const restored = { ...options(slides), scope: selection, focus: "Show comparability", language: "French", use: "read",
      theme: "ink", designVersion: 1, web: true } as const;
    const outline = [
      { ...part("legacy_cover", "Evidence review", "title"), goal: "Frame the comparison", notes: "Use the original title." },
      { ...part("legacy_findings", "Comparable observations", "text"), goal: "State the limits", notes: "Keep the caveat." },
    ];
    const work = detail.works[0]!;
    work.options = restored;
    work.parts = outline.map((row, index) => ({
      position: index + 1, itemId: row.id, heading: row.heading, layout: row.layout ?? "", state: "not_written", why: "",
    }));
    const app = mount({ type: slides, initial: detail });
    app.turn.mockImplementationOnce(async () => {
      const plan = planFor(slides, { options: restored, outline, planRevision: "p8" });
      app.setServer(detailFor(slides, plan, 8));
      return { ...finished, plan };
    });

    expect(text("Focus")).toHaveValue(restored.focus);
    expect(text("Language")).toHaveValue(restored.language);
    expect(screen.getByRole("combobox", { name: "Theme" })).toHaveValue(restored.theme);
    expect(app.turn).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Plan" }));
    expect(app.turn.mock.calls[0]?.[1]).toMatchObject({ kind: "plan", planId: address.planId, setup: restored });
    expect(app.turn.mock.calls[0]?.[1]).not.toHaveProperty("initialOutline");
    outline.forEach((row, index) => {
      expect(text(`Heading ${index + 1}`)).toHaveValue(row.heading);
      expect(text(`Goal ${index + 1}`)).toHaveValue(row.goal);
      expect(text(`Writer notes ${index + 1}`)).toHaveValue(row.notes);
    });
    expect(app.patch).not.toHaveBeenCalled();
  });

  it("collects ordered voices and blocks an empty captured selection", async () => {
    const app = mount({ initial: null, local: setupFor(audio, { ...selection, sourceIds: [] }), type: audio, currentScope: selection });
    const user = userEvent.setup();
    expect(screen.getByRole("button", { name: "Plan" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Use current selection" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Speaker A voice" }), "two");
    await user.selectOptions(screen.getByRole("combobox", { name: "Speaker B voice" }), "one");
    await user.click(screen.getByRole("button", { name: "Plan" }));
    expect(app.turn).toHaveBeenCalledWith(address.conversationId, expect.objectContaining({
      kind: "plan", setup: expect.objectContaining({ scope: selection, voices: ["two", "one"] }),
    }));
  });

  it("adds, reorders and edits coverage by ID, then derives or preserves count as requested", async () => {
    const app = mount();
    const user = userEvent.setup();
    await user.type(text("New item for Coverage topics"), "Method{Enter}");
    expect(app.getServer().works[0]?.plan?.proposal?.coverage[2]?.text).toBe("Method");
    const id = app.getServer().works[0]?.plan?.proposal?.coverage[2]?.id;
    await user.tab({ shift: true });
    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Move up Coverage topics, item 3" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(text("Coverage topics, item 2")).toHaveValue("Method");
    expect(text("Coverage topics, item 2")).toHaveFocus();
    change("Coverage topics, item 2", "Study method");
    await user.keyboard("{Enter}");
    expect(app.getServer().works[0]?.plan?.proposal?.coverage[1]).toEqual({ id, text: "Study method" });
    await user.selectOptions(screen.getByRole("combobox", { name: "Count" }), "exact");
    const count = screen.getByRole("spinbutton", { name: "Number of cards" });
    fireEvent.change(count, { target: { value: "12" } });
    fireEvent.blur(count);
    await waitFor(() => expect(app.getServer().works[0]?.plan?.proposal?.count).toEqual({ mode: "exact", value: 12 }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Length" }), "long");
    expect(count).toHaveValue(12);
    expect(screen.getByText("Exact · 12 cards")).toBeVisible();
    await user.selectOptions(screen.getByRole("combobox", { name: "Count" }), "auto");
    expect(screen.getByText("Aims for 40 cards.")).toBeVisible();
    expect(screen.getByText("Automatic · 40 cards")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Remove Coverage topics, item 3" }));
    await user.click(screen.getByRole("button", { name: "Remove Coverage topics, item 2" }));
    expect(screen.getByRole("button", { name: "Remove Coverage topics, item 1" })).toBeDisabled();
    expect(approve()).toBeEnabled();
    expect(app.turn).not.toHaveBeenCalled();
  });

  it("marks an explicitly adopted suggestion as a new authored entry without changing adjacent rows", async () => {
    const plan = planFor(flashcards, { preferences: {
      emphasize: [{ id: "guess", text: "Study design", mark: "guessed" }, { id: "human", text: "Sample size", mark: "you" }],
      avoid: [],
    } });
    const app = mount({ initial: detailFor(flashcards, plan) });
    const user = userEvent.setup();
    expect(screen.getByText("Suggested")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Accept suggestion for What to emphasize, item 1" }));
    const rows = app.getServer().brief.emphasize;
    expect(rows[0]).toMatchObject({ text: "Study design", mark: "edited" });
    expect(rows[0]?.id).not.toBe("guess");
    expect(rows[1]).toEqual(plan.preferences.emphasize[1]);
    expect(text("What to emphasize, item 1")).toHaveFocus();
    expect(app.turn).not.toHaveBeenCalled();
  });

  it("validates outline headings inline and saves layout and writer notes with stable identities", async () => {
    const app = mount({ type: slides });
    const user = userEvent.setup();
    expect(screen.getByText("Title slide")).toBeVisible();
    expect(screen.getByRole("button", { name: "Remove slide 1" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Add slide" }));
    expect(text("Heading 3")).toHaveFocus();
    expect(text("Heading 3")).toHaveAttribute("aria-invalid", "true");
    expect(approve()).toBeDisabled();
    expect(app.patch).not.toHaveBeenCalled();
    await user.type(text("Heading 3"), "Uncertainty{Enter}");
    await user.selectOptions(screen.getByRole("combobox", { name: "Layout 3" }), "figure");
    await user.type(text("Writer notes 3"), "Show the confidence interval.");
    await user.tab();
    await waitFor(() => expect(app.getServer().works[0]?.plan?.outline?.[2]).toMatchObject({
      heading: "Uncertainty", layout: "figure", notes: "Show the confidence interval.",
    }));
    const id = app.getServer().works[0]?.plan?.outline?.[2]?.id;
    await user.click(screen.getByRole("button", { name: "Move up slide 3" }));
    expect(text("Heading 2")).toHaveFocus();
    expect(app.getServer().works[0]?.plan?.outline?.[1]?.id).toBe(id);
    expect(screen.getByText("Edited · Ready to generate")).toBeVisible();
    expect(approve()).toBeEnabled();
    expect(app.turn).not.toHaveBeenCalled();
  });

  it("runs multiple structured question rounds and a feedback turn without approving from Enter", async () => {
    const user = userEvent.setup();
    const history = [message("one", "assistant", "question", "Round one", { options: ["First choice", "Second choice"] })];
    const app = mount({ initial: detailFor(flashcards, useQuestion("one"), 7, history) });
    app.turn.mockImplementation(async (_, request) => {
      const round = app.turn.mock.calls.length;
      if (request.kind === "answer") {
        history.push(message(`answer_${round}`, "user", "answer", "", { answer: request.answer, questionMessageId: request.questionMessageId }));
      } else if (request.kind === "plan" && "planRevision" in request) {
        history.push(message("feedback", "user", "plan", request.text ?? ""));
      }
      const next = round === 1 ? useQuestion("two", "p8") : round === 2 ? useQuestion("three", "p9") :
        planFor(flashcards, { planRevision: `p${7 + round}` });
      history.push(next.pending ? message(next.pending.messageId, "assistant", "question", next.pending.question, { options: next.pending.options }) :
        message(`ready_${round}`, "assistant", "planned", "Updated the proposal."));
      app.setServer(detailFor(flashcards, next, 7 + round, [...history]));
      return finished;
    });

    expect(screen.getByText("Needs answer")).toBeVisible();
    expect(approve()).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Second choice" }));
    expect(app.turn.mock.calls[0]?.[1]).toMatchObject({
      kind: "answer", planRevision: "p7", questionMessageId: "one", answer: { type: "choice", index: 1 },
    });
    expect(screen.getByRole("heading", { name: "Question" })).toHaveFocus();
    await user.type(text("Your answer"), "Use examples{Enter}");
    expect(app.turn.mock.calls[1]?.[1]).toMatchObject({
      kind: "answer", planRevision: "p8", questionMessageId: "two", answer: { type: "text", text: "Use examples" },
    });
    await user.click(screen.getByRole("button", { name: "Use current settings" }));
    expect(app.turn.mock.calls[2]?.[1]).toMatchObject({
      kind: "answer", planRevision: "p9", questionMessageId: "three", answer: { type: "use_settings" },
    });
    expect(approve()).toBeEnabled();
    await user.type(text("What should change in this plan?"), "Expand the tradeoffs{Enter}");
    expect(app.turn.mock.calls[3]?.[1]).toMatchObject({
      kind: "plan", planId: address.planId, planRevision: "p10", text: "Expand the tradeoffs",
    });
    app.turn.mock.calls.forEach(([, input]) => expect(input.kind).not.toBe("approve"));
    await user.click(screen.getByText("Conversation history"));
    const log = within(screen.getByRole("log", { name: "Artifact messages" }));
    expect(log.getByText("Second choice")).toBeVisible();
    expect(log.getByText("Use examples")).toBeVisible();
    expect(log.getByText("Use current settings")).toBeVisible();
    expect(log.getByText("Expand the tradeoffs")).toBeVisible();
    expect(log.getAllByText("Updated the proposal.")).toHaveLength(2);
  });

  it("flushes the visible edit and approves its acknowledged revision with one explicit click", async () => {
    const app = mount();
    change("Coverage topics, item 1", "  The main evidence ");
    expect(app.patch).not.toHaveBeenCalled();
    expect(app.dirty.current).toBe(true);
    fireEvent.click(approve());
    await waitFor(() => expect(app.turn).toHaveBeenCalledTimes(1));
    expect(app.patch).toHaveBeenCalledExactlyOnceWith(address.conversationId, address.planId, {
      baseEditVersion: 7, coverage: [{ ...firstTopic, text: "The main evidence" }, secondTopic],
    });
    expect(app.turn.mock.calls[0]?.[1]).toMatchObject({ kind: "approve", planId: address.planId, planRevision: "p8" });
    expect(app.dirty.current).toBe(false);
  });

  it.each([
    { label: "Heading 2", field: "heading", clearsBindings: true },
    { label: "Goal 2", field: "goal", clearsBindings: true },
    { label: "Writer notes 2", field: "notes", clearsBindings: false },
  ] as const)("approves with one click after editing $label on an evidence-bound row", async ({ label, field, clearsBindings }) => {
    const cover = { ...part("cover", "Evidence review", "title"), refs: [1], requiredRefs: [1] };
    const finding: PlanPart = {
      ...part("finding", "The findings", "text"), refs: [2, 3], requiredRefs: [2], requires: ["cover"],
      layoutLocked: true, editedFields: ["layout"],
    };
    const app = mount({ type: slides, initial: detailFor(slides, planFor(slides, { outline: [cover, finding] })) });
    const value = "Describe the sampling limits";
    app.patch.mockImplementationOnce(async (_id, _key, input) => {
      const accepted = applyPatch(app.getServer(), input);
      accepted.works[0]!.plan!.outline = [
        cover,
        { ...finding, [field]: value, editedFields: ["layout", field],
          ...(clearsBindings ? { refs: [], requiredRefs: [] } : {}) },
      ];
      app.setServer(accepted);
      return accepted;
    });

    change(label, value);
    fireEvent.click(approve());
    await waitFor(() => expect(app.turn).toHaveBeenCalledTimes(1));
    expect(app.patch).toHaveBeenCalledExactlyOnceWith(address.conversationId, address.planId, {
      baseEditVersion: 7, outline: [
        { id: "cover", heading: cover.heading, goal: cover.goal, layout: cover.layout, notes: cover.notes },
        { id: "finding", heading: finding.heading, goal: finding.goal, layout: finding.layout, notes: finding.notes, [field]: value },
      ],
    });
    expect(app.turn.mock.calls[0]?.[1]).toMatchObject({ kind: "approve", planId: address.planId, planRevision: "p8" });
    expect(screen.queryByText("The plan changed. Review the current plan before generating.")).not.toBeInTheDocument();
  });

  it("flushes a preference typed without Add before approving", async () => {
    const user = userEvent.setup();
    const app = mount();
    await user.type(text("New item for What to emphasize"), "Explain the sampling limits");
    expect(app.dirty.current).toBe(true);
    expect(app.patch).not.toHaveBeenCalled();

    await user.click(approve());
    await waitFor(() => expect(app.turn).toHaveBeenCalledTimes(1));
    expect(app.patch).toHaveBeenCalledExactlyOnceWith(address.conversationId, address.planId, {
      baseEditVersion: 7, emphasize: [{ id: expect.any(String), text: "Explain the sampling limits" }],
    });
    expect(app.turn.mock.calls[0]?.[1]).toMatchObject({ kind: "approve", planRevision: "p8" });
    expect(text("What to emphasize, item 1")).toHaveValue("Explain the sampling limits");
    expect(app.dirty.current).toBe(false);
  });

  it("validates a duplicate pending entry inline before taking a plan action", () => {
    const app = mount();
    change("New item for Coverage topics", "  MAIN finding ");
    expect(text("New item for Coverage topics")).toHaveAccessibleDescription("This item is already in the list.");
    fireEvent.click(approve());
    expect(app.patch).not.toHaveBeenCalled();
    expect(app.turn).not.toHaveBeenCalled();
    expect(app.dirty.current).toBe(true);
    expect(text("New item for Coverage topics")).toHaveValue("  MAIN finding ");
  });

  it("requires a second approval when a version-7 PATCH returns version 9 with another writer's constraint", async () => {
    const app = mount();
    app.patch.mockImplementationOnce(async (_id, _key, input) => {
      const accepted = applyPatch(app.getServer(), input);
      accepted.editVersion = 9;
      accepted.works[0]!.plan!.planRevision = "p9";
      accepted.brief = { ...accepted.brief, constraints: [{ text: "Use the adjusted counts", mark: "edited" }] };
      app.setServer(accepted);
      return accepted;
    });
    change("Coverage topics, item 1", "Reviewed evidence");
    fireEvent.click(approve());
    expect(await screen.findByText("Use the adjusted counts")).toBeVisible();
    expect(screen.getByText("The plan changed. Review the current plan before generating.")).toBeVisible();
    expect(app.patch.mock.calls[0]?.[2].baseEditVersion).toBe(7);
    expect(app.turn).not.toHaveBeenCalled();

    fireEvent.click(approve());
    await waitFor(() => expect(app.turn.mock.calls[0]?.[1]).toMatchObject({ kind: "approve", planRevision: "p9" }));
  });

  it("requires review when the shared brief changes in a single-version PATCH", async () => {
    const app = mount();
    app.patch.mockImplementationOnce(async (_id, _key, input) => {
      const accepted = applyPatch(app.getServer(), input);
      accepted.brief = { ...accepted.brief, goal: { text: "Cover the replication", mark: "edited" } };
      app.setServer(accepted);
      return accepted;
    });
    change("Coverage topics, item 1", "Reviewed evidence");
    fireEvent.click(approve());
    expect(await screen.findByText("The plan changed. Review the current plan before generating.")).toBeVisible();
    expect(screen.getByText(/Cover the replication/)).toBeVisible();
    expect(app.turn).not.toHaveBeenCalled();
  });

  it.each([{ refs: [3] }, { requiredRefs: [3] }, { requires: ["cover"] }])(
    "requires review when an outline row's metadata changes in a single-version PATCH (%j)", async (metadata) => {
      const app = mount({ type: slides });
      app.patch.mockImplementationOnce(async (_id, _key, input) => {
        const accepted = applyPatch(app.getServer(), input);
        const serverPlan = accepted.works[0]!.plan!;
        serverPlan.outline = serverPlan.outline!.map((row, index) => index === 1 ? { ...row, ...metadata } : row);
        app.setServer(accepted);
        return accepted;
      });
      change("Focus", "Evidence of change");
      fireEvent.click(approve());
      expect(await screen.findByText("The plan changed. Review the current plan before generating.")).toBeVisible();
      expect(app.turn).not.toHaveBeenCalled();
    },
  );

  it.each([
    { model: "removed", effort: null, blocker: "Choose an available model." },
    { model: "studio", effort: "max", blocker: "Choose a supported effort for this model." },
  ] as const)("recovers a provider-blocked plan from its Model settings ($blocker)", async ({ model, effort, blocker }) => {
    const app = mount({ initial: detailFor(flashcards, planFor(flashcards, {
      options: { ...options(flashcards), model, effort }, planStatus: "planning", blocker,
    })) });
    app.patch.mockImplementationOnce(async (_id, _key, input) => {
      const accepted = applyPatch(app.getServer(), input);
      const plan = accepted.works[0]!.plan!;
      plan.planStatus = "ready";
      plan.blocker = null;
      app.setServer(accepted);
      return accepted;
    });
    expect(approve()).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Plan with these sources" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Use current model" }));
    expect(app.patch.mock.calls[0]?.[2]).toMatchObject({ options: { effort: "high", ...(model === "removed" ? { model: "studio" } : {}) } });
    expect(app.turn).not.toHaveBeenCalled();
    await waitFor(() => expect(approve()).toBeEnabled());
  });

  it("requires a new review when a disjoint 409 is merged during an approval click", async () => {
    const app = mount();
    const remote = applyPatch(app.getServer(), { baseEditVersion: 7, coverage: [firstTopic, { ...secondTopic, text: "Limits of study design" }] });
    app.setServer(remote, false);
    change("Coverage topics, item 1", "Findings and comparisons");
    fireEvent.click(approve());

    await waitFor(() => expect(app.patch).toHaveBeenCalledTimes(2));
    expect(app.read).toHaveBeenCalledWith(address.conversationId);
    expect(app.patch.mock.calls[1]?.[2]).toEqual({ baseEditVersion: 8, coverage: [
      { ...firstTopic, text: "Findings and comparisons" }, { ...secondTopic, text: "Limits of study design" },
    ] });
    expect(screen.getByText("The plan changed. Review the current plan before generating.")).toBeVisible();
    expect(text("Coverage topics, item 2")).toHaveValue("Limits of study design");
    expect(app.turn).not.toHaveBeenCalled();
    await waitFor(() => expect(approve()).toBeEnabled());
    fireEvent.click(approve());
    await waitFor(() => expect(app.turn.mock.calls[0]?.[1]).toMatchObject({ kind: "approve", planRevision: "p9" }));
  });

  it("shows local and saved same-field values and preserves disjoint edits when using the latest value", async () => {
    const app = mount();
    const remote = applyPatch(app.getServer(), { baseEditVersion: 7, coverage: [{ ...firstTopic, text: "Server version of topic" }, secondTopic] });
    app.setServer(remote, false);
    change("Coverage topics, item 1", "My version of topic");
    change("Coverage topics, item 2", "My second topic");
    fireEvent.click(approve());

    const conflict = within(await screen.findByRole("region", { name: "Resolve editing conflict" }));
    expect(conflict.getByText("My version of topic")).toBeVisible();
    expect(conflict.getByText("Server version of topic")).toBeVisible();
    expect(approve()).toBeDisabled();
    expect(app.turn).not.toHaveBeenCalled();
    await userEvent.click(conflict.getByRole("button", { name: "Use latest values" }));
    expect(text("Coverage topics, item 1")).toHaveValue("Server version of topic");
    expect(text("Coverage topics, item 2")).toHaveValue("My second topic");
    expect(app.patch.mock.calls[1]?.[2].coverage?.[1]?.text).toBe("My second topic");
    expect(app.turn).not.toHaveBeenCalled();
  });

  it("prompts when a removed row was edited on the server and can keep the local removal", async () => {
    const app = mount();
    app.setServer(applyPatch(app.getServer(), { baseEditVersion: 7, coverage: [firstTopic, { ...secondTopic, text: "Server edit" }] }), false);
    await userEvent.click(screen.getByRole("button", { name: "Remove Coverage topics, item 2" }));
    const conflict = within(await screen.findByRole("region", { name: "Resolve editing conflict" }));
    expect(conflict.getByText("Server edit")).toBeVisible();
    expect(approve()).toBeDisabled();
    await userEvent.click(conflict.getByRole("button", { name: "Keep my edits" }));
    expect(app.patch.mock.calls[1]?.[2]).toMatchObject({ baseEditVersion: 8, coverage: [firstTopic] });
    expect(screen.queryByRole("region", { name: "Resolve editing conflict" })).toBeNull();
    expect(app.turn).not.toHaveBeenCalled();
  });

  it("detects incompatible list ordering by ID", async () => {
    const three = { id: "c", text: "Study design" };
    const plan = planFor(flashcards);
    const initial = detailFor(flashcards, { ...plan, proposal: { ...plan.proposal!, coverage: [firstTopic, secondTopic, three] } });
    const app = mount({ initial });
    app.setServer(applyPatch(initial, { baseEditVersion: 7, coverage: [firstTopic, three, secondTopic] }), false);
    await userEvent.click(screen.getByRole("button", { name: "Move down Coverage topics, item 1" }));
    const conflict = within(await screen.findByRole("region", { name: "Resolve editing conflict" }));
    expect(conflict.getByText("Coverage topics order")).toBeVisible();
    expect(app.turn).not.toHaveBeenCalled();
  });

  it("holds the input and cursor through a disjoint background update and a stale detail response", async () => {
    const app = mount();
    const input = text("Coverage topics, item 1");
    input.focus();
    change("Coverage topics, item 1", "Main results and comparisons");
    input.setSelectionRange(4, 4);
    const current = app.getServer();
    app.setServer(applyPatch(current, { baseEditVersion: 7, options: { language: "Spanish" } }));
    app.setServer({ ...current });
    expect(text("Coverage topics, item 1")).toBe(input);
    expect(input).toHaveFocus();
    expect(input.selectionStart).toBe(4);
    expect(input).toHaveValue("Main results and comparisons");
    expect(text("Language")).toHaveValue("Spanish");
    expect(app.turn).not.toHaveBeenCalled();
  });

  it("does not approve if local input arrives while the captured patch is in flight", async () => {
    const app = mount();
    let finish: ((detail: ArtifactConversationDetail) => void) | undefined;
    app.patch.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    change("Coverage topics, item 1", "First edit");
    fireEvent.click(approve());
    await waitFor(() => expect(screen.getByText("Saving changes…")).toBeVisible());
    await waitFor(() => expect(finish).toBeDefined());
    change("Coverage topics, item 1", "Second edit");
    await act(async () => {
      const accepted = applyPatch(app.getServer(), app.patch.mock.calls[0]![2]);
      app.setServer(accepted);
      finish?.(accepted);
    });
    expect(text("Coverage topics, item 1")).toHaveValue("Second edit");
    expect(app.turn).not.toHaveBeenCalled();
    expect(approve()).toBeEnabled();
    fireEvent.click(approve());
    await waitFor(() => expect(app.turn.mock.calls[0]?.[1]).toMatchObject({ kind: "approve", planRevision: "p9" }));
  });

  it("asks for review when the patch returns a different proposal contract", async () => {
    const app = mount();
    app.patch.mockImplementationOnce(async (_id, _key, input) => {
      const accepted = applyPatch(app.getServer(), input);
      const serverPlan = accepted.works[0]!.plan!;
      serverPlan.proposal = { ...serverPlan.proposal!, summary: "The source review changed." };
      app.setServer(accepted);
      return accepted;
    });
    change("Coverage topics, item 1", "Reviewed evidence");
    fireEvent.click(approve());
    expect(await screen.findByText("The source review changed.")).toBeVisible();
    expect(screen.getByText("The plan changed. Review the current plan before generating.")).toBeVisible();
    expect(app.turn).not.toHaveBeenCalled();
  });

  it("does not send a deferred approval after leaving the editor during its patch", async () => {
    const app = mount();
    let finish: ((detail: ArtifactConversationDetail) => void) | undefined;
    app.patch.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    change("Coverage topics, item 1", "One more revision");
    fireEvent.click(approve());
    await waitFor(() => expect(app.patch).toHaveBeenCalledOnce());
    app.unmount();
    await act(async () => finish?.(applyPatch(app.getServer(), app.patch.mock.calls[0]![2])));
    expect(app.turn).not.toHaveBeenCalled();
  });

  it("keeps a server-rejected draft visible and blocks approval until a valid edit commits", async () => {
    const app = mount();
    app.patch.mockRejectedValueOnce(new ApiRequestError("Outline evidence changed. Review the sources.", 422));
    change("Focus", "Use selected evidence.");
    fireEvent.click(approve());
    expect(await screen.findByRole("alert")).toHaveTextContent("Outline evidence changed");
    expect(text("Focus")).toHaveValue("Use selected evidence.");
    expect(app.turn).not.toHaveBeenCalled();
    change("Focus", "Use the reviewed evidence.");
    fireEvent.click(approve());
    await waitFor(() => expect(app.turn.mock.calls[0]?.[1]).toMatchObject({ kind: "approve", planRevision: "p8" }));
  });

  it("requires a new plan when the source scope changes and keeps the captured scope while the Library changes", async () => {
    const app = mount({ currentScope: selection });
    expect(approve()).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Use current selection" }));
    expect(app.patch.mock.calls[0]?.[2]).toMatchObject({ baseEditVersion: 7, options: { scope: selection } });
    expect(approve()).toBeDisabled();
    expect(app.turn).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Plan with these sources" }));
    expect(app.turn.mock.calls[0]?.[1]).toMatchObject({ kind: "plan", planId: address.planId, planRevision: "p8" });
  });

  it("collapses the approved plan during generation, stops the current attempt, and resumes only on request", async () => {
    const plan = planFor(flashcards, {
      attempt: { requestId: "request_gen", phase: "generate", state: "running", detail: null }, planStatus: "generating",
    });
    const app = mount({ initial: detailFor(flashcards, plan), stage: "Writing cards" });
    expect(screen.getByRole("progressbar", { name: "Generation progress" })).toBeVisible();
    expect(screen.getByText("Approved plan").closest("details")).not.toHaveAttribute("open");
    expect(screen.queryByRole("button", { name: "Approve & Generate" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(app.stop).toHaveBeenCalledOnce();
    const recovered = { ...plan, planStatus: "ready" as const, attempt: { ...plan.attempt!, state: "interrupted" as const, detail: "The app closed." } };
    app.setServer(detailFor(flashcards, recovered));
    expect(screen.getByText("Generation interrupted.")).toBeVisible();
    expect(app.turn).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Review & resume" }));
    expect(app.turn.mock.calls[0]?.[1]).toMatchObject({ kind: "approve", planRevision: "p7" });
  });

  it("retries stopped planning from its persisted revision with a fresh request ID", async () => {
    const stopped = planFor(flashcards, {
      planStatus: "planning", proposal: null, blocker: "Plan this output before generating.",
      attempt: { requestId: "previous_request", phase: "plan", state: "stopped", detail: null },
    });
    const app = mount({ initial: detailFor(flashcards, stopped) });
    await userEvent.click(screen.getByRole("button", { name: "Retry planning" }));
    expect(app.turn.mock.calls[0]?.[1]).toMatchObject({ kind: "plan", planRevision: "p7" });
    expect(app.turn.mock.calls[0]?.[1]?.clientRequestId).not.toBe("previous_request");
  });
});

describe("saved planning controls", () => {
  it("keeps the approved snapshot read-only and patches preferences on the current saved work", async () => {
    const clean = detailFor(flashcards, null);
    const saved = { ...clean, works: [{ ...clean.works[0]!, key: "lineage", versionId: "version" }] };
    const plan = planFor(flashcards);
    const patch = vi.fn(async (_id: string, _key: string, input: WorkPlanPatch) => applyPatch(saved, input));
    const controller: StudioPlanning = { locals: {}, setLocal: vi.fn(), discardLocal: vi.fn(), targetFor: () => address,
      patch, turn: vi.fn() };
    render(<>
      <ApprovedPlan type={flashcards} value={{ planId: plan.planId, revision: plan.planRevision, approvedAt: now, approvalMessageId: "approved",
        materialKey: "material", options: plan.options, preferences: plan.preferences, outline: plan.outline, proposal: plan.proposal! }} />
      <SavedPlanPreferences detail={saved} work={saved.works[0]!} controller={controller} disabled={false} />
    </>);
    await userEvent.click(screen.getByText("Approved plan"));
    expect(screen.getByText(firstTopic.text)).toBeVisible();
    expect(screen.queryByRole("textbox", { name: "Coverage topics, item 1" })).toBeNull();
    await userEvent.click(screen.getByText("Preferences for future changes"));
    await userEvent.type(text("New item for What to avoid"), "Speculation{Enter}");
    expect(patch).toHaveBeenCalledExactlyOnceWith(address.conversationId, "lineage", {
      baseEditVersion: 7, avoid: [expect.objectContaining({ text: "Speculation" })],
    });
    expect(controller.turn).not.toHaveBeenCalled();
  });
});
