import type { KnowledgeProposal, KnowledgeSession, KnowledgeUnit, SessionMessage, SourceSummary } from "@gunther/contracts";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ServiceUnavailableError, knowledgeApi } from "../api";
import { WEB_PREFERENCE_KEY } from "../models/askModel";
import { persistentStorage } from "../persistentStorage";
import { makeBase, makeSource } from "../test/fixtures";
import { Composer, ConversationMessage, SessionWorkspace } from "./SessionWorkspace";
import { useSavedAnswers } from "./savedAnswers";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); window.localStorage.clear(); });

const renderComposer = (ready: boolean) => {
  const onChange = vi.fn();
  const onSend = vi.fn();
  render(
    <Composer
      value="What does this source support?"
      sending={false}
      sourceCount={0}
      chapterTitle={undefined}
      onChange={onChange}
      onSend={onSend}
      onStop={vi.fn()}
      onSources={vi.fn()}
      readOnly={false}
      ready={ready}
    />,
  );
  return { onChange, onSend };
};

describe("Session composer durability boundary", () => {
  it("does not accept a question until its durable session is ready", async () => {
    const user = userEvent.setup();
    const { onSend } = renderComposer(false);

    expect(screen.getByRole("textbox", { name: "Message Gunther" })).toBeDisabled();
    expect(screen.getByText("Preparing a durable conversation before accepting questions…")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(onSend).not.toHaveBeenCalled();
  });

  it("enables sending after the durable session is ready", async () => {
    const user = userEvent.setup();
    const { onSend } = renderComposer(true);

    expect(screen.getByRole("textbox", { name: "Message Gunther" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(onSend).toHaveBeenCalledOnce();
  });

  it("has a Web switch that reflects whether web search is set up", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = render(
      <Composer value="" sending={false} sourceCount={0} chapterTitle={undefined} onChange={vi.fn()} onSend={vi.fn()} onStop={vi.fn()} onSources={vi.fn()} readOnly={false} ready web={{ available: false, enabled: false, onChange }} />,
    );
    expect(screen.getByRole("button", { name: "Web" })).toBeDisabled();
    rerender(
      <Composer value="" sending={false} sourceCount={0} chapterTitle={undefined} onChange={vi.fn()} onSend={vi.fn()} onStop={vi.fn()} onSources={vi.fn()} readOnly={false} ready web={{ available: true, enabled: false, onChange }} />,
    );
    await user.click(screen.getByRole("button", { name: "Web" }));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});


const answer: SessionMessage = {
  id: "msg_1",
  sessionId: "ses_1",
  role: "assistant",
  content: "CD3D marks T cells. [1]",
  citations: [{ id: "cit_1", sourceId: "src_1", sourceTitle: "Marker paper", assertionId: null, quote: "CD3D is a T-cell marker.", locator: "p. 2", status: "verified", confidence: 0.9, ref: 1 }],
  context: { sourcesConsidered: 1, assertionsConsidered: 1, verifiedAssertions: 1, retrievalMode: "all", responderMode: "model" },
  createdAt: "2026-10-01T08:00:00.000Z",
};

const sessionWithAnswer = (knowledgeBaseId: string): KnowledgeSession => ({
  id: answer.sessionId, knowledgeBaseId, title: "Markers", summary: "", focusChapterId: null,
  selectedSourceIds: [], pinned: false, archived: false, parentSessionId: null, branchedFromMessageId: null,
  messageCount: 1, createdAt: answer.createdAt, updatedAt: answer.createdAt,
  brief: { goal: "", constraints: [], settled: [], open: [], edited: [], error: null },
  messages: [answer],
});

function renderWorkspace(base: ReturnType<typeof makeBase>) {
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  render(<SessionWorkspace base={base} onAdd={vi.fn()} onNotify={vi.fn()} />);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

it("keeps an offline draft editable and opens saved history on reconnect", async () => {
  const user = userEvent.setup();
  const base = makeBase({ id: "reconnect-library" });
  const section = {
    id: "topic-1", knowledgeBaseId: base.id, title: "Field observations", description: "", parentId: null,
    position: 0, version: 1, blockIds: [], sourceIds: [],
  };
  const session = { ...sessionWithAnswer(base.id), focusChapterId: section.id };
  const activeKey = `gunther:active-session:${base.id}`;
  persistentStorage.setItem(activeKey, session.id);
  persistentStorage.setItem(`gunther:ask-draft:${base.id}`, "What does this source support?");
  vi.spyOn(knowledgeApi, "sessions").mockRejectedValueOnce(new ServiceUnavailableError()).mockResolvedValue([session]);
  vi.spyOn(knowledgeApi, "session").mockResolvedValue(session);
  vi.spyOn(knowledgeApi, "topics").mockRejectedValueOnce(new ServiceUnavailableError()).mockResolvedValue([section]);
  const follow = vi.spyOn(knowledgeApi, "followAnswer").mockResolvedValue(null);
  vi.spyOn(knowledgeApi, "modelMenu").mockResolvedValue({ models: [], default: null, efforts: [] });
  const create = vi.spyOn(knowledgeApi, "createSession");
  const send = vi.spyOn(knowledgeApi, "sendMessageStream");
  vi.stubGlobal("fetch", vi.fn(async () => Response.json([])));

  renderWorkspace(base);
  const reconnect = await screen.findByRole("button", { name: "Reconnect" });
  const draft = screen.getByRole("textbox", { name: "Message Gunther" });
  expect(draft).toBeEnabled();
  expect(draft).toHaveValue("What does this source support?");
  await user.type(draft, " Cite the relevant passage.{Enter}");
  expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  expect(persistentStorage.getItem(activeKey)).toBe(session.id);
  expect(create).not.toHaveBeenCalled();
  expect(follow).not.toHaveBeenCalled();
  expect(send).not.toHaveBeenCalled();

  await user.click(reconnect);
  expect(await screen.findByText("Markers")).toBeVisible();
  expect(await screen.findByText(section.title)).toBeVisible();
  expect(draft).toHaveValue("What does this source support? Cite the relevant passage.");
  expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Reconnect" })).not.toBeInTheDocument();
  expect(follow).toHaveBeenCalledWith(session.id, expect.any(Function), expect.any(AbortSignal));
  expect(send).not.toHaveBeenCalled();
});

it("restores Web and model settings after reconnect while saved answers load independently", async () => {
  const user = userEvent.setup();
  const base = makeBase({ id: "offline-library" });
  const session = sessionWithAnswer(base.id);
  const question = "Compare the field notes with current research.";
  const unreachable = new ServiceUnavailableError();
  const savedAnswers = deferred<KnowledgeProposal[]>();
  const settings = deferred<Awaited<ReturnType<typeof knowledgeApi.health>>>();
  const model = { ref: "provider/field-model", label: "Field model", display: "Field model", provider: "Provider", vision: false, levels: [] };
  persistentStorage.setItem(WEB_PREFERENCE_KEY, "on");
  persistentStorage.setItem(`gunther:ask-draft:${base.id}`, question);
  vi.spyOn(knowledgeApi, "sessions").mockRejectedValueOnce(unreachable).mockResolvedValue([session]);
  vi.spyOn(knowledgeApi, "session").mockResolvedValue(session);
  vi.spyOn(knowledgeApi, "followAnswer").mockResolvedValue(null);
  vi.spyOn(knowledgeApi, "modelMenu").mockRejectedValueOnce(unreachable).mockResolvedValue({
    models: [model], default: { model: model.ref, effort: "high" }, efforts: [],
  });
  vi.spyOn(knowledgeApi, "health").mockRejectedValueOnce(unreachable).mockReturnValue(settings.promise);
  vi.spyOn(knowledgeApi, "proposals").mockRejectedValueOnce(unreachable).mockReturnValue(savedAnswers.promise);
  vi.spyOn(knowledgeApi, "knowledgeUnits").mockRejectedValueOnce(unreachable).mockResolvedValue([{ ...unit("trusted"), knowledgeBaseId: base.id }]);
  const send = vi.spyOn(knowledgeApi, "sendMessageStream").mockResolvedValue({
    session,
    userMessage: { ...answer, id: "question", role: "user", content: question, citations: [] },
    assistantMessage: { ...answer, id: "new-answer", content: "More context from the field notes." },
  });
  vi.spyOn(knowledgeApi, "refreshBrief").mockResolvedValue(session.brief!);
  vi.stubGlobal("fetch", vi.fn(async () => Response.json([])));

  renderWorkspace(base);
  await user.click(await screen.findByRole("button", { name: "Reconnect" }));
  expect(await screen.findByRole("button", { name: "Model: Field model" })).toBeVisible();
  expect(await screen.findByText("Markers")).toBeVisible();
  const sendButton = screen.getByRole("button", { name: "Send message" });
  expect(sendButton).toBeDisabled();
  expect(send).not.toHaveBeenCalled();

  await act(async () => settings.resolve({
    status: "ok", extractionMode: "local", webSearchMode: "tavily", transcriptionMode: "not_configured",
    transcriptionProvider: "none", transcriptionModel: "", summaryMode: "off",
  }));
  expect(screen.getByRole("button", { name: "Web" })).toHaveAttribute("aria-pressed", "true");
  expect(sendButton).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Save as knowledge" })).not.toBeInTheDocument();

  await act(async () => savedAnswers.resolve([{ ...proposal("accepted"), knowledgeBaseId: base.id }]));
  expect(await screen.findByRole("button", { name: "Saved as knowledge" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Remove" })).toBeEnabled();
  expect(screen.getByRole("textbox", { name: "Message Gunther" })).toHaveValue(question);
  expect(sendButton).toBeEnabled();
  await user.click(sendButton);
  expect(send).toHaveBeenCalledWith(session.id, expect.objectContaining({ content: question, model: model.ref, web: true }), expect.any(Function), expect.any(AbortSignal));
});

it("opens saved history and can send when proposals fail, with a retry for just that read", async () => {
  const user = userEvent.setup();
  const base = makeBase({ id: "saved-history-library" });
  const session = sessionWithAnswer(base.id);
  const proposals = vi.fn(async () => Response.json([{ ...proposal("accepted"), knowledgeBaseId: base.id }]))
    .mockResolvedValueOnce(Response.json({ detail: "Saved answers temporarily unavailable" }, { status: 503 }));
  const history = vi.spyOn(knowledgeApi, "sessions").mockResolvedValue([session]);
  const units = vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue([]);
  vi.spyOn(knowledgeApi, "session").mockResolvedValue(session);
  vi.spyOn(knowledgeApi, "followAnswer").mockResolvedValue(null);
  vi.spyOn(knowledgeApi, "modelMenu").mockResolvedValue({ models: [], default: null, efforts: [] });
  vi.stubGlobal("fetch", vi.fn(async (path: string) => path.endsWith("/proposals") ? proposals() : Response.json([])));
  persistentStorage.setItem(`gunther:ask-draft:${base.id}`, "Which passages are relevant?");

  renderWorkspace(base);
  const retry = await screen.findByRole("button", { name: "Retry saved answers" });
  expect(await screen.findByRole("article")).toHaveTextContent("CD3D marks T cells.");
  expect(screen.queryByRole("button", { name: "Reconnect" })).not.toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled());

  await user.click(retry);
  expect(await screen.findByRole("button", { name: "Saved as knowledge" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Retry saved answers" })).not.toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Message Gunther" })).toHaveValue("Which passages are relevant?");
  expect(history).toHaveBeenCalledTimes(1);
  expect(units).toHaveBeenCalledTimes(1);
  expect(proposals).toHaveBeenCalledTimes(2);
});

it("refreshes sources once after capture notifications arrive during a pending read", async () => {
  const user = userEvent.setup();
  const base = makeBase({ id: "capture-library", sourceCount: 0 });
  const session = sessionWithAnswer(base.id);
  const captured = makeSource({ title: "New field recording" });
  const firstRead = deferred<SourceSummary[]>();
  const sources = vi.spyOn(knowledgeApi, "sources").mockReturnValueOnce(firstRead.promise).mockResolvedValue([captured]);
  vi.spyOn(knowledgeApi, "sessions").mockResolvedValue([session]);
  vi.spyOn(knowledgeApi, "session").mockResolvedValue(session);
  vi.spyOn(knowledgeApi, "followAnswer").mockResolvedValue(null);
  vi.spyOn(knowledgeApi, "modelMenu").mockResolvedValue({ models: [], default: null, efforts: [] });
  const update = vi.spyOn(knowledgeApi, "updateSession").mockResolvedValue({ ...session, selectedSourceIds: [captured.id] });
  vi.stubGlobal("fetch", vi.fn(async () => Response.json([])));

  renderWorkspace(base);
  await user.click(screen.getByRole("button", { name: "All sources" }));
  act(() => {
    window.dispatchEvent(new CustomEvent("gunther:sources-updated"));
    window.dispatchEvent(new CustomEvent("gunther:sources-updated"));
  });
  expect(sources).toHaveBeenCalledTimes(1);

  await act(async () => firstRead.resolve([]));
  expect(sources.mock.calls).toEqual([[base.id], [base.id]]);
  await user.click(await screen.findByRole("button", { name: /New field recording/ }));
  expect(update).toHaveBeenCalledWith(session.id, { selectedSourceIds: [captured.id] });
});

it("requires a successful history reload after an early stream close leaves the service unreachable", async () => {
  const user = userEvent.setup();
  const base = makeBase({ id: "interrupted-library" });
  const session = sessionWithAnswer(base.id);
  const question = "Which notes support this conclusion?";
  const history = deferred<KnowledgeSession>();
  persistentStorage.setItem(`gunther:ask-draft:${base.id}`, question);
  vi.spyOn(knowledgeApi, "sessions").mockResolvedValue([session]);
  const load = vi.spyOn(knowledgeApi, "session").mockResolvedValueOnce(session)
    .mockRejectedValueOnce(new ServiceUnavailableError()).mockReturnValueOnce(history.promise);
  vi.spyOn(knowledgeApi, "followAnswer").mockResolvedValue(null);
  vi.spyOn(knowledgeApi, "modelMenu").mockResolvedValue({ models: [], default: null, efforts: [] });
  const send = vi.spyOn(knowledgeApi, "sendMessageStream");
  vi.stubGlobal("fetch", vi.fn(async (path: string) => path.endsWith("/messages/stream")
    ? new Response('data: {"type":"text","text":"Checking the notes"}\n\n', { headers: { "Content-Type": "text/event-stream" } })
    : Response.json([])));

  renderWorkspace(base);
  const sendButton = () => screen.getByRole("button", { name: "Send message" });
  await waitFor(() => expect(sendButton()).toBeEnabled());
  await user.click(sendButton());

  const reconnect = await screen.findByRole("button", { name: "Reconnect" });
  expect(await screen.findByRole("alert")).toHaveTextContent("The connection closed before the answer was finished.");
  const draft = screen.getByRole("textbox", { name: "Message Gunther" });
  expect(draft).toHaveValue(question);
  expect(sendButton()).toBeDisabled();
  await user.type(draft, "{Enter}");
  expect(send).toHaveBeenCalledTimes(1);

  await user.click(reconnect);
  await waitFor(() => expect(load).toHaveBeenCalledTimes(3));
  expect(sendButton()).toBeDisabled();
  await act(async () => history.resolve({ ...session, title: "Reloaded field notes discussion" }));
  expect(await screen.findByText("Reloaded field notes discussion")).toBeVisible();
  expect(draft).toHaveValue(question);
  expect(sendButton()).toBeEnabled();
  expect(send).toHaveBeenCalledTimes(1);
});

it("opens Models settings and offers Retry on the saved question that needs a key", async () => {
  const user = userEvent.setup();
  const retry = vi.fn();
  const message: SessionMessage = { ...answer, role: "user", content: "What is supported?", citations: [], context: {
    ...answer.context, interrupted: "failed", modelError: "Ask needs a language model or provider key.",
    askFailure: { needsModel: true, hasTrace: false, options: {} },
  } };
  render(<ConversationMessage message={message} retryDisabled={false} onRetry={retry} onEdit={vi.fn()} onCite={vi.fn()} selected={false} promoting={false} promoted={false} branching={false} onSelect={vi.fn()} onCopy={vi.fn()} onPromote={vi.fn()} onRemove={vi.fn()} onBranch={vi.fn()} />);
  expect(screen.getByText("Ask needs a language model or provider key.")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Open Settings" }));
  expect(window.localStorage.getItem("gunther:settings-pane")).toBe("models");
  await user.click(screen.getByRole("button", { name: "Retry" }));
  expect(retry).toHaveBeenCalledOnce();
});

it("shows a same-question retry's HTTP 409 and restores the composer draft", async () => {
  const base = makeBase();
  const question = "What does this source support?";
  const detail = "Gunther is still answering the last question here. Wait, or stop it first.";
  const session: KnowledgeSession = {
    ...sessionWithAnswer(base.id),
    messages: [{ ...answer, role: "user", content: question, citations: [], context: {
      ...answer.context, interrupted: "failed", modelError: "Earlier failure",
    } }],
  };
  vi.spyOn(knowledgeApi, "sessions").mockResolvedValue([session]);
  vi.spyOn(knowledgeApi, "session").mockResolvedValue(session);
  vi.spyOn(knowledgeApi, "followAnswer").mockResolvedValue(null);
  vi.spyOn(knowledgeApi, "modelMenu").mockResolvedValue({ models: [], default: null, efforts: [] });
  vi.stubGlobal("fetch", vi.fn(async (path: string) => path.endsWith("/messages/stream")
    ? Response.json({ detail }, { status: 409 }) : Response.json([])));
  renderWorkspace(base);
  await userEvent.setup().click(await screen.findByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(detail);
  expect(screen.getByRole("textbox", { name: "Message Gunther" })).toHaveValue(question);
});

const proposal = (status: KnowledgeProposal["status"]): KnowledgeProposal => ({
  id: "prp_1",
  knowledgeBaseId: "biology",
  sessionId: "ses_1",
  messageId: "msg_1",
  targetChapterId: null,
  kind: "knowledge_unit",
  title: "T-cell identity",
  content: answer.content,
  status,
  decisionReason: null,
  knowledgeUnitId: "unt_1",
  sourceSessionTitle: "Markers",
  createdAt: "2026-10-01T08:01:00.000Z",
  updatedAt: "2026-10-01T08:01:00.000Z",
});

const unit = (status: KnowledgeUnit["status"]): KnowledgeUnit => ({
  id: "unt_1",
  knowledgeBaseId: "biology",
  title: "T-cell identity",
  kind: "knowledge_unit",
  status,
  content: answer.content,
  revisionCount: 1,
  sourceProposalId: "prp_1",
  sourceSessionId: "ses_1",
  sourceMessageId: "msg_1",
  targetChapterId: null,
  evidenceCount: 1,
  createdAt: "2026-10-01T08:01:00.000Z",
  updatedAt: "2026-10-01T08:01:00.000Z",
});

/** One answer wired the way the workspace wires it. */
function SavableAnswer({ onNotify, onUnits }: { onNotify: (message: string) => void; onUnits: (units: KnowledgeUnit[]) => void }) {
  const answers = useSavedAnswers("biology", onNotify, vi.fn());
  onUnits(answers.units);
  return <ConversationMessage
    message={answer}
    retryDisabled={false}
    onRetry={vi.fn()}
    onEdit={vi.fn()}
    onCite={vi.fn()}
    selected={false}
    promoting={answers.busyId === answer.id}
    promoted={answers.isSaved(answer.id)}
    branching={false}
    onSelect={vi.fn()}
    onCopy={vi.fn()}
    onPromote={() => void answers.save({ id: "ses_1", focusChapterId: null }, answer)}
    onRemove={() => void answers.remove(answer)}
    onBranch={vi.fn()}
  />;
}

describe("Saving an answer as knowledge", () => {
  it("saves with one click, shows it saved, and takes it back out with Remove", async () => {
    const user = userEvent.setup();
    const onNotify = vi.fn();
    const units = vi.fn();
    vi.spyOn(knowledgeApi, "proposals").mockResolvedValue([]);
    const listUnits = vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue([]);
    const create = vi.spyOn(knowledgeApi, "createProposal").mockResolvedValue(proposal("accepted"));
    const update = vi.spyOn(knowledgeApi, "updateProposal").mockResolvedValue(proposal("rejected"));
    render(<SavableAnswer onNotify={onNotify} onUnits={units} />);

    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
    listUnits.mockResolvedValue([unit("trusted")]);
    await user.click(screen.getByRole("button", { name: "Save as knowledge" }));

    expect(await screen.findByRole("button", { name: "Saved as knowledge" })).toBeDisabled();
    expect(create).toHaveBeenCalledWith("ses_1", "msg_1", { targetChapterId: null });
    expect(onNotify).toHaveBeenLastCalledWith("Saved as knowledge.");
    await waitFor(() => expect(units).toHaveBeenLastCalledWith([expect.objectContaining({ id: "unt_1" })]));

    listUnits.mockResolvedValue([unit("deprecated")]);
    await user.click(screen.getByRole("button", { name: "Remove" }));

    expect(await screen.findByRole("button", { name: "Save as knowledge" })).toBeEnabled();
    expect(update).toHaveBeenCalledWith("prp_1", { status: "rejected", reason: "Removed from knowledge" });
    expect(onNotify).toHaveBeenLastCalledWith("Removed from knowledge.");
    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
    // Only trusted units are listed, so the removed answer's unit is gone from the list.
    await waitFor(() => expect(units).toHaveBeenLastCalledWith([]));

    // Saving it again goes through the same single step.
    update.mockClear();
    create.mockResolvedValue(proposal("accepted"));
    await user.click(screen.getByRole("button", { name: "Save as knowledge" }));
    expect(await screen.findByRole("button", { name: "Saved as knowledge" })).toBeVisible();
    expect(create).toHaveBeenCalledTimes(2);
  });

  it("shows a saved answer as saved when the conversation opens", async () => {
    vi.spyOn(knowledgeApi, "proposals").mockResolvedValue([proposal("accepted")]);
    vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue([unit("trusted")]);
    render(<SavableAnswer onNotify={vi.fn()} onUnits={vi.fn()} />);

    expect(await screen.findByRole("button", { name: "Saved as knowledge" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Remove" })).toBeEnabled();
  });

  it("treats an answer that was removed earlier as not saved", async () => {
    vi.spyOn(knowledgeApi, "proposals").mockResolvedValue([proposal("rejected")]);
    vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue([unit("deprecated")]);
    render(<SavableAnswer onNotify={vi.fn()} onUnits={vi.fn()} />);

    await waitFor(() => expect(knowledgeApi.proposals).toHaveBeenCalled());
    expect(await screen.findByRole("button", { name: "Save as knowledge" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
  });

  it("says why it could not save, and leaves the answer unsaved", async () => {
    const user = userEvent.setup();
    const onError = vi.fn();
    vi.spyOn(knowledgeApi, "proposals").mockResolvedValue([]);
    vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue([]);
    vi.spyOn(knowledgeApi, "createProposal").mockRejectedValue(new Error("The service is offline"));
    function Failing() {
      const answers = useSavedAnswers("biology", vi.fn(), onError);
      return <button onClick={() => void answers.save({ id: "ses_1", focusChapterId: null }, answer)}>{answers.isSaved("msg_1") ? "Saved" : "Not saved"}</button>;
    }
    render(<Failing />);

    await user.click(screen.getByRole("button", { name: "Not saved" }));

    await waitFor(() => expect(onError).toHaveBeenLastCalledWith("The service is offline"));
    expect(screen.getByRole("button", { name: "Not saved" })).toBeVisible();
  });

  it("requires an open conversation to save an answer", async () => {
    const user = userEvent.setup();
    const onNotify = vi.fn();
    vi.spyOn(knowledgeApi, "proposals").mockResolvedValue([]);
    vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue([]);
    const create = vi.spyOn(knowledgeApi, "createProposal");
    function Disconnected() {
      const answers = useSavedAnswers("biology", onNotify, vi.fn());
      return <button onClick={() => void answers.save(null, answer)}>Save</button>;
    }
    render(<Disconnected />);

    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(onNotify).toHaveBeenCalledWith("Reconnect the knowledge service to save this answer.");
    expect(create).not.toHaveBeenCalled();
  });
});
