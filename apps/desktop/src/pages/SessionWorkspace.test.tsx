import type { ConversationTurn, KnowledgeProposal, KnowledgeSession, KnowledgeUnit, SessionMessage } from "@gunther/contracts";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnswerStoppedError, ServiceUnavailableError, knowledgeApi, type AnswerEvent } from "../api";
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
      <Composer value="" sending={false} sourceCount={0} onChange={vi.fn()} onSend={vi.fn()} onStop={vi.fn()} onSources={vi.fn()} readOnly={false} ready web={{ available: false, enabled: false, onChange }} />,
    );
    expect(screen.getByRole("button", { name: "Web" })).toBeDisabled();
    rerender(
      <Composer value="" sending={false} sourceCount={0} onChange={vi.fn()} onSend={vi.fn()} onStop={vi.fn()} onSources={vi.fn()} readOnly={false} ready web={{ available: true, enabled: false, onChange }} />,
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

function renderWorkspace(base: ReturnType<typeof makeBase>, reactStrictMode = false) {
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  return render(<SessionWorkspace base={base} onNotify={vi.fn()} />, { reactStrictMode });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

describe("Library Chat actions", () => {
  const base = makeBase({ id: "chat-actions" });
  const first = sessionWithAnswer(base.id);
  const second = { ...first, id: "second", title: "Reading notes", messageCount: 0, messages: [] };

  beforeEach(() => {
    const saved = new Map([first, second].map((chat) => [chat.id, chat]));
    vi.spyOn(knowledgeApi, "sessions").mockImplementation(async (_id, includeArchived) =>
      [...saved.values()].filter((chat) => includeArchived || !chat.archived));
    vi.spyOn(knowledgeApi, "session").mockImplementation(async (id) => saved.get(id)!);
    vi.spyOn(knowledgeApi, "updateSession").mockImplementation(async (id, patch) => {
      const chat = saved.get(id)!;
      const updated = { ...chat, title: patch.title ?? chat.title, pinned: patch.pinned ?? chat.pinned, archived: patch.archived ?? chat.archived };
      saved.set(id, updated);
      return updated;
    });
    vi.spyOn(knowledgeApi, "followAnswer").mockResolvedValue(null);
    vi.spyOn(knowledgeApi, "modelMenu").mockResolvedValue({ models: [], default: null, efforts: [] });
    vi.stubGlobal("fetch", vi.fn(async () => Response.json([])));
  });

  it("starts a new chat and opens the previous chat from searchable, pinnable history", async () => {
    const user = userEvent.setup();
    const created = { ...second, id: "new", title: "New conversation" };
    const create = vi.spyOn(knowledgeApi, "createSession").mockResolvedValue(created);
    renderWorkspace(base);
    const composer = screen.getByRole("textbox", { name: "Message Gunther" });
    await waitFor(() => expect(composer).toBeEnabled());
    await user.type(composer, "A draft in the first chat");

    const actions = screen.getByRole("button", { name: "Chat actions" });
    await user.click(actions);
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "New chat", "History", "Rename", "Export Markdown", "Archive",
    ]);
    await user.click(screen.getByRole("menuitem", { name: "New chat" }));
    expect(await screen.findByText(created.title)).toBeVisible();
    expect(create).toHaveBeenCalledExactlyOnceWith(base.id, { focusChapterId: null, selectedSourceIds: [] });
    expect(composer).toHaveValue("");

    await user.click(actions);
    await user.click(screen.getByRole("menuitem", { name: "History" }));
    const drawer = within(screen.getByRole("dialog", { name: "Session history" }));
    await user.type(drawer.getByRole("textbox", { name: "Search sessions" }), first.title);
    expect(drawer.queryByRole("button", { name: /^New conversation/ })).not.toBeInTheDocument();
    await user.click(drawer.getByRole("button", { name: "Actions for Markers" }));
    await user.click(drawer.getByRole("menuitem", { name: "Pin session" }));
    expect(await drawer.findByText("Pinned")).toBeVisible();
    expect(knowledgeApi.updateSession).toHaveBeenCalledExactlyOnceWith(first.id, { pinned: true });
    await user.click(drawer.getByRole("button", { name: /^Markers/ }));
    expect(await screen.findByText(first.title)).toBeVisible();
  });

  it("renames, archives and restores the active chat through its menu and history", async () => {
    const user = userEvent.setup();
    renderWorkspace(base);
    expect(await screen.findByText(first.title)).toBeVisible();
    const actions = screen.getByRole("button", { name: "Chat actions" });
    await user.click(actions);
    await user.click(screen.getByRole("menuitem", { name: "Rename" }));
    const title = screen.getByRole("textbox", { name: "Session title" });
    await user.clear(title);
    await user.type(title, "Lab discussion{Enter}");
    expect(await screen.findByText("Lab discussion")).toBeVisible();
    expect(knowledgeApi.updateSession).toHaveBeenLastCalledWith(first.id, { title: "Lab discussion" });

    await user.click(actions);
    await user.click(screen.getByRole("menuitem", { name: "Archive" }));
    expect(await screen.findByText(second.title)).toBeVisible();
    expect(knowledgeApi.updateSession).toHaveBeenLastCalledWith(first.id, { archived: true });
    await user.click(actions);
    await user.click(screen.getByRole("menuitem", { name: "History" }));
    const drawer = within(screen.getByRole("dialog", { name: "Session history" }));
    await user.click(drawer.getByRole("button", { name: /^View archive/ }));
    const archived = await drawer.findByRole("button", { name: "Actions for Lab discussion" });
    await user.click(archived);
    await user.click(drawer.getByRole("menuitem", { name: "Restore session" }));
    await waitFor(() => expect(drawer.getByText("No archived sessions yet.")).toBeVisible());
    expect(knowledgeApi.updateSession).toHaveBeenLastCalledWith(first.id, { archived: false });
    await user.click(drawer.getByRole("button", { name: /^Lab discussion/ }));
    expect(await screen.findByText("Lab discussion")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "Message Gunther" })).toBeEnabled();
  });

  it("exports the active chat as Markdown with its citations", async () => {
    const user = userEvent.setup();
    const createObjectURL = vi.fn((_: Blob) => "blob:session-export");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    });
    const download = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      download(this.download);
    });
    renderWorkspace(base);
    expect(await screen.findByText(first.title)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Chat actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Export Markdown" }));
    expect(download).toHaveBeenCalledExactlyOnceWith("markers.md");
    const blob = createObjectURL.mock.calls[0]![0];
    const markdown = await new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.readAsText(blob);
    });
    expect(markdown).toContain("# Markers");
    expect(markdown).toContain("> CD3D is a T-cell marker.");
    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledWith("blob:session-export"), { timeout: 1500 });
  });
});

describe("Library Ask startup selection", () => {
  beforeEach(() => {
    vi.spyOn(knowledgeApi, "followAnswer").mockResolvedValue(null);
    vi.spyOn(knowledgeApi, "modelMenu").mockResolvedValue({ models: [], default: null, efforts: [] });
    vi.stubGlobal("fetch", vi.fn(async () => Response.json([])));
  });

  it("opens saved history when StrictMode replays a pending startup read", async () => {
    const base = makeBase({ id: "strict-library" });
    const first = sessionWithAnswer(base.id);
    const remembered = { ...first, id: "remembered", title: "Remembered discussion", messages: [] };
    const history = deferred<KnowledgeSession[]>();
    vi.spyOn(knowledgeApi, "sessions").mockReturnValue(history.promise);
    vi.spyOn(knowledgeApi, "session").mockImplementation(async (id) => id === first.id ? first : remembered);
    persistentStorage.setItem(`gunther:active-session:${base.id}`, remembered.id);
    persistentStorage.setItem(`gunther:ask-draft:${base.id}`, "Continue our discussion.");

    renderWorkspace(base, true);
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    await act(async () => history.resolve([first, remembered]));
    expect(await screen.findByText(remembered.title)).toBeVisible();
    expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled();
  });

  it("opens the first session if an Activity lookup fails during startup", async () => {
    const base = makeBase({ id: "activity-library" });
    const first = sessionWithAnswer(base.id);
    const history = deferred<KnowledgeSession[]>();
    vi.spyOn(knowledgeApi, "sessions").mockReturnValue(history.promise);
    const detail = vi.spyOn(knowledgeApi, "session").mockImplementation(async (id) => {
      if (id === first.id) return first;
      throw new Error("Conversation unavailable");
    });

    renderWorkspace(base);
    await act(async () => {
      window.dispatchEvent(new CustomEvent("gunther:open-session", { detail: { libraryId: base.id, sessionId: "missing" } }));
    });
    expect(detail.mock.calls.map(([id]) => id)).toContain("missing");
    await act(async () => history.resolve([first]));
    expect(await screen.findByText(first.title)).toBeVisible();
  });

  it("skips a remembered archived session when opening Library Ask", async () => {
    const base = makeBase({ id: "archived-library" });
    const first = sessionWithAnswer(base.id);
    const archived = { ...first, id: "archived", title: "Archived discussion", archived: true, messages: [] };
    vi.spyOn(knowledgeApi, "sessions").mockResolvedValue([first]);
    const detail = vi.spyOn(knowledgeApi, "session").mockImplementation(async (id) => id === archived.id ? archived : first);
    persistentStorage.setItem(`gunther:active-session:${base.id}`, archived.id);

    renderWorkspace(base);
    expect(await screen.findByText(first.title)).toBeVisible();
    expect(detail.mock.calls.map(([id]) => id)).toEqual([first.id]);
    expect(screen.getByRole("textbox", { name: "Message Gunther" })).toBeEnabled();
  });

  it("opens a later Home selection after leaving a pending Activity lookup", async () => {
    const base = makeBase({ id: "home-selection-library" });
    const first = sessionWithAnswer(base.id);
    const later = { ...first, id: "from-home", title: "Selected from Home", messages: [] };
    const activity = { ...first, id: "activity", title: "Activity discussion", messages: [] };
    const pending = deferred<KnowledgeSession>();
    const activeKey = `gunther:active-session:${base.id}`;
    vi.spyOn(knowledgeApi, "sessions").mockResolvedValue([first, later, activity]);
    const detail = vi.spyOn(knowledgeApi, "session").mockImplementation(async (id) => {
      if (id === activity.id) return pending.promise;
      return id === later.id ? later : first;
    });

    const view = renderWorkspace(base);
    await screen.findByText(first.title);
    act(() => {
      window.dispatchEvent(new CustomEvent("gunther:open-session", { detail: { libraryId: base.id, sessionId: activity.id } }));
    });
    expect(detail.mock.calls.map(([id]) => id)).toContain(activity.id);
    view.unmount();

    persistentStorage.setItem(activeKey, later.id);
    renderWorkspace(base);
    await act(async () => pending.resolve(activity));
    expect(await screen.findByText(later.title)).toBeVisible();
    expect(persistentStorage.getItem(activeKey)).toBe(later.id);
  });
});

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

it("keeps listening when Research is asked to write and when it is asked to stop", async () => {
  const base = makeBase();
  const question: SessionMessage = {
    ...answer, role: "user", content: "Find the evidence", citations: [], context: {
      ...answer.context, interrupted: "failed",
      askFailure: { needsModel: false, hasTrace: false, options: { skill: "research", budget: "deep" } },
    },
  };
  const session = { ...sessionWithAnswer(base.id), messages: [question] };
  const stopped = { ...question, context: { ...question.context, interrupted: "stopped" as const } };
  const turn = deferred<ConversationTurn>();
  let hear!: (event: AnswerEvent) => void;
  vi.spyOn(knowledgeApi, "sessions").mockResolvedValue([session]);
  vi.spyOn(knowledgeApi, "session").mockResolvedValueOnce(session)
    .mockResolvedValue({ ...session, messages: [stopped] });
  vi.spyOn(knowledgeApi, "followAnswer").mockResolvedValue(null);
  vi.spyOn(knowledgeApi, "modelMenu").mockResolvedValue({ models: [], default: null, efforts: [] });
  const send = vi.spyOn(knowledgeApi, "sendMessageStream").mockImplementation((_id, _body, onEvent) => {
    hear = onEvent;
    return turn.promise;
  });
  const stop = vi.spyOn(knowledgeApi, "stopAnswer").mockResolvedValue(undefined);
  vi.stubGlobal("fetch", vi.fn(async () => Response.json([])));

  renderWorkspace(base);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Retry" }));
  await user.click(await screen.findByRole("button", { name: "Stop and write" }));
  expect(stop).toHaveBeenCalledWith(session.id);
  expect(send.mock.calls[0]?.[3]?.aborted).toBe(false);

  act(() => hear({ type: "text", text: "Writing from what was found" }));
  await user.click(await screen.findByRole("button", { name: "Stop waiting for response" }));
  expect(stop.mock.calls).toEqual([[session.id], [session.id]]);
  expect(send.mock.calls[0]?.[3]?.aborted).toBe(false);

  await act(async () => { turn.reject(new AnswerStoppedError()); });
  expect(await screen.findByRole("button", { name: "Ask again" })).toBeVisible();
  expect(screen.queryByText("Writing from what was found")).not.toBeInTheDocument();
});

it("detaches the previous Library session when Activity opens another answer", async () => {
  const base = makeBase();
  const first = sessionWithAnswer(base.id);
  const second = { ...first, id: "ses_other", title: "Later discussion", messages: [] };
  const replacement = deferred<KnowledgeSession>();
  const old = deferred<ConversationTurn | null>();
  const selected = deferred<ConversationTurn | null>();
  let oldEvent!: (event: AnswerEvent) => void;
  vi.spyOn(knowledgeApi, "sessions").mockResolvedValue([first, second]);
  vi.spyOn(knowledgeApi, "session").mockImplementation(async (id) => id === first.id ? first : replacement.promise);
  const follow = vi.spyOn(knowledgeApi, "followAnswer").mockImplementation((id, hear) => {
    if (id === first.id) {
      oldEvent = hear;
      hear({ type: "resumed", question: "Question from the first session", startedAt: 1 });
      return old.promise;
    }
    hear({ type: "resumed", question: "Question from Activity", startedAt: 2 });
    return selected.promise;
  });
  const stop = vi.spyOn(knowledgeApi, "stopAnswer").mockResolvedValue(undefined);
  vi.spyOn(knowledgeApi, "modelMenu").mockResolvedValue({ models: [], default: null, efforts: [] });
  vi.stubGlobal("fetch", vi.fn(async () => Response.json([])));

  renderWorkspace(base);
  await screen.findByText("Question from the first session");
  await act(async () => {
    window.dispatchEvent(new CustomEvent("gunther:open-session", { detail: { libraryId: base.id, sessionId: second.id } }));
  });
  expect(follow.mock.calls[0]?.[2]?.aborted).toBe(false);
  expect(screen.getByText("Question from the first session")).toBeVisible();
  expect(follow).toHaveBeenCalledTimes(1);
  act(() => oldEvent({ type: "text", text: "Progress while loading" }));
  expect(screen.getByText("Progress while loading")).toBeVisible();
  await act(async () => { replacement.resolve(second); });
  expect(follow.mock.calls[0]?.[2]?.aborted).toBe(true);
  await screen.findByText("Question from Activity");

  await act(async () => {
    oldEvent({ type: "text", text: "Late progress" });
    old.reject(new AnswerStoppedError());
  });
  expect(screen.queryByText("Late progress")).not.toBeInTheDocument();
  expect(stop).not.toHaveBeenCalled();
  await userEvent.setup().click(screen.getByRole("button", { name: "Stop waiting for response" }));
  expect(stop).toHaveBeenCalledExactlyOnceWith(second.id);
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
