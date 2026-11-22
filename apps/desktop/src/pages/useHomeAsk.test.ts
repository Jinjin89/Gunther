import type { KnowledgeSession } from "@gunther/contracts";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AnswerEvent } from "../api";
import { useHomeAsk } from "./useHomeAsk";

const api = vi.hoisted(() => ({
  sessions: vi.fn(),
  session: vi.fn(),
  createSession: vi.fn(),
  sendMessageStream: vi.fn(),
  followAnswer: vi.fn(),
  stopAnswer: vi.fn(),
}));
vi.mock("../api", async (importOriginal) => ({ ...(await importOriginal<typeof import("../api")>()), knowledgeApi: api }));
const reading = vi.hoisted(() => ({ auto: vi.fn(), focusSession: vi.fn() }));
vi.mock("../speech/readAloud", () => ({ readAloud: reading }));

const { AnswerStoppedError } = await import("../api");

const home = (patch: Partial<KnowledgeSession> = {}) => ({ id: "ses_home", knowledgeBaseId: "@home", messages: [], messageCount: 0, ...patch }) as unknown as KnowledgeSession;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

/** A stream that waits until the test ends it, as a real answer being written does. */
function held() {
  let finish!: (value: unknown) => void;
  let fail!: (reason: unknown) => void;
  let signal: AbortSignal | undefined;
  const promise = new Promise((resolve, reject) => { finish = resolve; fail = reject; });
  api.sendMessageStream.mockImplementation((_id: string, _body: unknown, _hear: unknown, given?: AbortSignal) => {
    signal = given;
    given?.addEventListener("abort", () => fail(new DOMException("Aborted", "AbortError")));
    return promise;
  });
  return { finish, fail, aborted: () => Boolean(signal?.aborted) };
}

describe("asking from Home", () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset());
    Object.values(reading).forEach((mock) => mock.mockClear());
    window.localStorage.clear();
    api.sessions.mockResolvedValue([]);
    api.createSession.mockResolvedValue(home());
    api.session.mockResolvedValue(home());
    api.followAnswer.mockResolvedValue(null);
    api.stopAnswer.mockResolvedValue(undefined);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("closing the conversation only stops listening; nothing is marked stopped", async () => {
    const stream = held();
    const { result } = renderHook(() => useHomeAsk());
    act(() => { void result.current.ask("What marks T cells?"); });
    await waitFor(() => expect(api.sendMessageStream).toHaveBeenCalled());
    act(() => result.current.close());
    expect(stream.aborted()).toBe(true);
    await waitFor(() => expect(result.current.pending).toBeNull());
    expect(api.stopAnswer).not.toHaveBeenCalled();
    expect(result.current.messages).toEqual([]);
    expect(result.current.error).toBeNull();
  });

  it("Stop asks the service to stop and then shows the question it kept", async () => {
    const stream = held();
    const kept = home({ messages: [{ id: "m1", role: "user", content: "Stop me?", context: { interrupted: "stopped" } }] as never });
    const { result } = renderHook(() => useHomeAsk());
    act(() => { void result.current.ask("Stop me?"); });
    await waitFor(() => expect(api.sendMessageStream).toHaveBeenCalled());
    act(() => result.current.cancel());
    expect(api.stopAnswer).toHaveBeenCalledWith("ses_home");
    expect(stream.aborted()).toBe(false);
    api.session.mockResolvedValue(kept);
    await act(async () => { stream.fail(new AnswerStoppedError()); });
    await waitFor(() => expect(result.current.messages).toHaveLength(1));
    expect(result.current.error).toBeNull();
    expect(result.current.pending).toBeNull();
  });

  it("reopening a conversation follows the answer still being written", async () => {
    let finish!: (value: unknown) => void;
    api.followAnswer.mockImplementation((_id: string, hear: (event: AnswerEvent) => void) => {
      hear({ type: "resumed", question: "Still going?", startedAt: 1 });
      hear({ type: "text", text: "Half of it" });
      return new Promise((resolve) => { finish = resolve; });
    });
    const { result } = renderHook(() => useHomeAsk());
    await act(async () => { await result.current.open("ses_home"); });
    expect(api.followAnswer).toHaveBeenCalledWith("ses_home", expect.any(Function), expect.any(AbortSignal));
    expect(result.current.pending).toBe("Still going?");
    expect(result.current.live?.text).toBe("Half of it");
    api.session.mockResolvedValue(home({ messageCount: 2 }));
    await act(async () => { finish({ assistantMessage: { id: "a1" } }); });
    await waitFor(() => expect(result.current.pending).toBeNull());
    expect(result.current.session?.messageCount).toBe(2);
  });

  it("replaces the Home listener only when the selected conversation loads", async () => {
    const stream = held();
    const missing = deferred<KnowledgeSession>();
    const superseded = deferred<KnowledgeSession>();
    const replacement = deferred<KnowledgeSession>();
    api.session.mockReturnValueOnce(missing.promise).mockReturnValueOnce(superseded.promise)
      .mockReturnValueOnce(replacement.promise);
    const { result } = renderHook(() => useHomeAsk());
    act(() => { void result.current.ask("Keep this answer going"); });
    await waitFor(() => expect(api.sendMessageStream).toHaveBeenCalled());

    act(() => { void result.current.open("ses_missing"); });
    expect(stream.aborted()).toBe(false);
    await act(async () => { missing.reject(new Error("Conversation was removed")); });
    expect(result.current.error).toBe("Conversation was removed");
    expect(result.current.session?.id).toBe("ses_home");
    expect(result.current.pending).toBe("Keep this answer going");
    expect(stream.aborted()).toBe(false);

    act(() => {
      void result.current.open("ses_earlier");
      void result.current.open("ses_other");
    });
    await act(async () => { superseded.resolve(home({ id: "ses_earlier" })); });
    expect(result.current.session?.id).toBe("ses_home");
    expect(stream.aborted()).toBe(false);
    expect(api.followAnswer).not.toHaveBeenCalled();
    await act(async () => { replacement.resolve(home({ id: "ses_other" })); });
    expect(result.current.session?.id).toBe("ses_other");
    expect(stream.aborted()).toBe(true);
    expect(api.followAnswer).toHaveBeenCalledWith("ses_other", expect.any(Function), expect.any(AbortSignal));
  });

  it("keeps the saved answer when Stop fails during the follow history refresh", async () => {
    const stop = deferred<void>();
    const turn = deferred<unknown>();
    const history = deferred<KnowledgeSession>();
    api.stopAnswer.mockReturnValueOnce(stop.promise);
    api.session.mockResolvedValueOnce(home()).mockReturnValueOnce(history.promise);
    api.followAnswer.mockImplementationOnce((_id: string, hear: (event: AnswerEvent) => void) => {
      hear({ type: "resumed", question: "What was saved?", startedAt: 1 });
      return turn.promise;
    });
    const { result } = renderHook(() => useHomeAsk());
    await act(async () => { await result.current.open("ses_home"); });
    act(() => result.current.cancel());
    expect(api.stopAnswer).toHaveBeenCalledWith("ses_home");

    const assistantMessage = { id: "ans_saved", role: "assistant", content: "The saved answer" } as KnowledgeSession["messages"][number];
    await act(async () => { turn.resolve({ assistantMessage }); });
    expect(api.session).toHaveBeenCalledTimes(2);
    await act(async () => { stop.reject(new Error("Stop request failed")); });
    const signal = api.followAnswer.mock.calls[0]?.[2] as AbortSignal;
    expect(signal.aborted).toBe(false);

    await act(async () => { history.resolve(home({ messageCount: 1, messages: [assistantMessage] })); });
    expect(result.current.messages).toEqual([assistantMessage]);
    expect(result.current.pending).toBeNull();
  });

  it("ignores a previous follow's delayed history when another conversation is answering", async () => {
    let complete!: (turn: unknown) => void;
    let reload!: (session: KnowledgeSession) => void;
    const history = new Promise<KnowledgeSession>((resolve) => { reload = resolve; });
    api.session.mockResolvedValueOnce(home()).mockReturnValueOnce(history)
      .mockResolvedValueOnce(home({ id: "ses_other" }));
    api.followAnswer.mockImplementationOnce((_id, hear: (event: AnswerEvent) => void) => {
      hear({ type: "resumed", question: "The old question", startedAt: 1 });
      return new Promise((resolve) => { complete = resolve; });
    });
    const { result } = renderHook(() => useHomeAsk());
    await act(async () => { await result.current.open("ses_home"); });
    await act(async () => { complete({ assistantMessage: { id: "old_answer" } }); });
    expect(api.session).toHaveBeenCalledTimes(2);
    const following = api.followAnswer.mock.calls[0]?.[2] as AbortSignal;
    act(() => result.current.cancel());
    expect(api.stopAnswer).not.toHaveBeenCalled();

    await act(async () => { await result.current.open("ses_other"); });
    expect(following.aborted).toBe(true);
    held();
    act(() => { void result.current.ask("The new question"); });
    await act(async () => { reload(home({ messageCount: 2 })); });
    expect(result.current.session?.id).toBe("ses_other");
    expect(result.current.pending).toBe("The new question");
    expect(reading.auto).not.toHaveBeenCalled();
    expect(api.stopAnswer).not.toHaveBeenCalled();
    act(() => result.current.cancel());
    expect(api.stopAnswer).toHaveBeenCalledWith("ses_other");
  });

  it("releases Home's listener when the Stop request fails", async () => {
    const stream = held();
    api.stopAnswer.mockRejectedValueOnce(new Error("Service unavailable"));
    const { result } = renderHook(() => useHomeAsk());
    act(() => { void result.current.ask("Stop me?"); });
    await waitFor(() => expect(api.sendMessageStream).toHaveBeenCalled());
    await act(async () => { result.current.cancel(); });
    expect(stream.aborted()).toBe(true);
    expect(result.current.pending).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("shows the saved failed question and retries its request in the same conversation", async () => {
    const options = { skill: "research", budget: "deep" } as const;
    const failed = { id: "m1", role: "user", content: "Research it", context: { interrupted: "failed", modelError: "Provider failed", askFailure: { needsModel: false, hasTrace: false, options } } } as KnowledgeSession["messages"][number];
    const { knowledgeApi } = await vi.importActual<typeof import("../api")>("../api");
    const detail = "Gunther is still answering the last question here.";
    const event = JSON.stringify({ status: 502, detail: "Provider failed", questionId: failed.id });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(`event: error\ndata: ${event}\n\n`))
      .mockResolvedValueOnce(Response.json({ detail }, { status: 409 })));
    api.session.mockResolvedValue(home({ messages: [failed] }));
    api.sendMessageStream.mockImplementationOnce(knowledgeApi.sendMessageStream)
      .mockImplementationOnce(knowledgeApi.sendMessageStream)
      .mockResolvedValueOnce({ session: home(), userMessage: failed, assistantMessage: { id: "a1" } });
    const { result } = renderHook(() => useHomeAsk());
    await act(async () => { await result.current.ask(failed.content, options); });
    expect(result.current.messages).toEqual([failed]);
    expect(result.current.error).toBeNull();
    await act(async () => { await result.current.ask(failed.content, failed.context.askFailure?.options); });
    expect(result.current.error).toBe(detail);
    expect(result.current.messages).toEqual([failed]);
    await act(async () => { await result.current.ask(failed.content, failed.context.askFailure?.options); });
    expect(api.createSession).toHaveBeenCalledOnce();
    expect(api.sendMessageStream).toHaveBeenLastCalledWith("ses_home", { content: failed.content, ...options }, expect.any(Function), expect.any(AbortSignal));
  });

  it("stops reading another conversation's answer when one is opened, not when it is only remembered", async () => {
    window.localStorage.setItem("gunther:home-session", "ses_home");
    const { result } = renderHook(() => useHomeAsk());
    await waitFor(() => expect(result.current.session?.id).toBe("ses_home"));
    expect(reading.focusSession).not.toHaveBeenCalled();
    api.session.mockResolvedValue(home({ id: "ses_other" }));
    await act(async () => { await result.current.open("ses_other"); });
    expect(reading.focusSession).toHaveBeenCalledWith("ses_other");
  });
});
