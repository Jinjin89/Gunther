import type { ConversationTurn, KeyResultJob, KnowledgeSession, SessionMessage } from "@gunther/contracts";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnswerStoppedError, ServiceUnavailableError, knowledgeApi, type AnswerEvent } from "../api";
import { WEB_PREFERENCE_KEY } from "../models/askModel";
import { persistentStorage } from "../persistentStorage";
import { makeBase, makeSource } from "../test/fixtures";
import { Composer, ConversationMessage, SessionWorkspace } from "./SessionWorkspace";
import { useLibraryChats } from "./useLibraryChats";

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

function WorkspaceHarness({ base, inspect = false }: { base: ReturnType<typeof makeBase>; inspect?: boolean }) {
  const chats = useLibraryChats(base.id, () => {}, () => {});
  return <>
    <SessionWorkspace base={base} chats={chats} onNotify={() => {}} onCite={() => {}} onInspectSources={() => {}} onInspectCitation={() => {}} />
    {inspect && <output data-testid="active-session">{JSON.stringify(chats.activeSession)}</output>}
  </>;
}

function renderWorkspace(base: ReturnType<typeof makeBase>, reactStrictMode = false, inspect = false) {
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  return render(<WorkspaceHarness base={base} inspect={inspect} />, { reactStrictMode });
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

  it("starts a new chat without a history action", async () => {
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
      "New chat", "Rename", "Export Markdown", "Archive",
    ]);
    await user.click(screen.getByRole("menuitem", { name: "New chat" }));
    expect(await screen.findByText(created.title)).toBeVisible();
    expect(create).toHaveBeenCalledExactlyOnceWith(base.id, { focusChapterId: null, selectedSourceIds: [] });
    expect(composer).toHaveValue("");

    expect(screen.queryByRole("dialog", { name: "Session history" })).not.toBeInTheDocument();
  });

  it("renames and archives the active chat through its menu", async () => {
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
    expect(screen.queryByRole("dialog", { name: "Session history" })).not.toBeInTheDocument();
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

it.each(["success", "failure"])("preserves a saved turn and session fields when brief refresh returns %s", async (result) => {
  const user = userEvent.setup();
  const base = makeBase({ id: `brief-${result}` });
  const session = sessionWithAnswer(base.id);
  const question = { ...answer, id: "question", role: "user" as const, content: "What changed?", citations: [] };
  const reply = { ...answer, id: "new-answer", content: "The new answer." };
  const updated = { ...session, title: "Updated title", summary: "Updated summary", messageCount: 3 };
  vi.spyOn(knowledgeApi, "sessions").mockResolvedValue([session]);
  vi.spyOn(knowledgeApi, "session").mockResolvedValue(session);
  vi.spyOn(knowledgeApi, "followAnswer").mockResolvedValue(null);
  vi.spyOn(knowledgeApi, "modelMenu").mockResolvedValue({ models: [], default: null, efforts: [] });
  vi.spyOn(knowledgeApi, "sendMessageStream").mockResolvedValue({ session: updated, userMessage: question, assistantMessage: reply });
  const refresh = vi.spyOn(knowledgeApi, "refreshBrief");
  if (result === "success") refresh.mockResolvedValue({ ...session.brief!, goal: "A refreshed goal" });
  else refresh.mockRejectedValue(new Error("Brief unavailable"));
  vi.stubGlobal("fetch", vi.fn(async () => Response.json([])));
  renderWorkspace(base, false, true);

  const composer = screen.getByRole("textbox", { name: "Message Gunther" });
  await waitFor(() => expect(composer).toBeEnabled());
  await user.type(composer, `${question.content}{Enter}`);
  await waitFor(() => {
    const active = JSON.parse(screen.getByTestId("active-session").textContent!) as KnowledgeSession;
    expect(active).toMatchObject({ title: updated.title, summary: updated.summary, messageCount: 3 });
    expect(active.messages.map((message) => message.id)).toEqual([answer.id, question.id, reply.id]);
    expect(active.brief).toMatchObject(result === "success" ? { goal: "A refreshed goal", error: null } : { error: "Brief unavailable" });
  });
  expect(screen.getByText("The new answer.")).toBeVisible();
  await user.click(screen.getByText("Brief", { selector: "summary" }));
  if (result === "success") expect(screen.getByText("A refreshed goal")).toBeVisible();
  else expect(screen.getByRole("alert")).toHaveTextContent("Couldn’t update the brief");
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

it("restores Web and model settings after reconnect while history loads independently", async () => {
  const user = userEvent.setup();
  const base = makeBase({ id: "offline-library" });
  const session = sessionWithAnswer(base.id);
  const question = "Compare the field notes with current research.";
  const unreachable = new ServiceUnavailableError();
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

  expect(screen.getByRole("textbox", { name: "Message Gunther" })).toHaveValue(question);
  expect(sendButton).toBeEnabled();
  await user.click(sendButton);
  expect(send).toHaveBeenCalledWith(session.id, expect.objectContaining({ content: question, model: model.ref, web: true }), expect.any(Function), expect.any(AbortSignal));
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
  render(<ConversationMessage message={message} retryDisabled={false} onRetry={retry} onEdit={vi.fn()} onCite={vi.fn()} selected={false} branching={false} onSelect={vi.fn()} onCopy={vi.fn()} onBranch={vi.fn()} />);
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

const keyResultJob = (state: KeyResultJob["state"], changes: Partial<KeyResultJob> = {}): KeyResultJob => ({
  id: "job-1", originKind: "answer", originId: answer.id, state, error: null, keptCount: 0, keptIds: [],
  ...changes,
});

function renderAnswer(job: KeyResultJob) {
  return render(<ConversationMessage message={{ ...answer, keyResults: job }} libraryId="biology"
    retryDisabled={false} onRetry={vi.fn()} onEdit={vi.fn()} onCite={vi.fn()}
    selected={false} branching={false} onSelect={vi.fn()} onCopy={vi.fn()} onBranch={vi.fn()} />);
}

describe("Key results under a saved answer", () => {
  it("polls while queued and running and stops after showing the count", async () => {
    vi.useFakeTimers();
    try {
      const poll = vi.spyOn(knowledgeApi, "keyResultOrigin")
        .mockResolvedValueOnce(keyResultJob("running"))
        .mockResolvedValueOnce(keyResultJob("done", { keptCount: 2, keptIds: ["one", "two"] }));
      renderAnswer(keyResultJob("queued"));
      expect(screen.getByText("Checking key results…")).toBeVisible();
      await act(async () => vi.advanceTimersByTimeAsync(2_500));
      expect(screen.getByText("Checking key results…")).toBeVisible();
      await act(async () => vi.advanceTimersByTimeAsync(2_500));
      expect(screen.getByText("✓ 2 key results kept")).toBeVisible();
      await act(async () => vi.advanceTimersByTimeAsync(10_000));
      expect(poll).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("undoes the saved answer's contribution", async () => {
    const undo = vi.spyOn(knowledgeApi, "undoKeyResults").mockResolvedValue(keyResultJob("done"));
    renderAnswer(keyResultJob("done", { keptCount: 1, keptIds: ["one"] }));
    expect(screen.getByText("✓ 1 key result kept")).toBeVisible();
    await userEvent.setup().click(screen.getByRole("button", { name: "Undo" }));
    expect(await screen.findByText("Key results undone")).toBeVisible();
    expect(undo).toHaveBeenCalledWith("biology", expect.objectContaining({ originKind: "answer", originId: answer.id }));
    expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
  });

  it("keeps the actions quiet when the job found no results", () => {
    renderAnswer(keyResultJob("done"));
    expect(screen.queryByText(/key results/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save as knowledge" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
  });

  it("opens Models settings when Analysis is unavailable", async () => {
    const poll = vi.spyOn(knowledgeApi, "keyResultOrigin");
    renderAnswer(keyResultJob("needs_model"));
    expect(screen.getByText("Key results need an Analysis model")).toBeVisible();
    await userEvent.setup().click(screen.getByRole("button", { name: "Open Models settings" }));
    expect(persistentStorage.getItem("gunther:settings-pane")).toBe("models");
    expect(poll).not.toHaveBeenCalled();
  });

  it("shows the job error and retries that job", async () => {
    const retry = vi.spyOn(knowledgeApi, "retryKeyResults").mockResolvedValue(keyResultJob("queued"));
    renderAnswer(keyResultJob("failed", { error: "The checker was unavailable." }));
    expect(screen.getByText("The checker was unavailable.")).toBeVisible();
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Checking key results…")).toBeVisible();
    expect(retry).toHaveBeenCalledExactlyOnceWith("biology", "job-1");
  });
});
