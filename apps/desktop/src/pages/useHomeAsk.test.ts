import type { KnowledgeSession } from "@gunther/contracts";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
vi.mock("../speech/readAloud", () => ({ readAloud: { auto: vi.fn() } }));

const { AnswerStoppedError } = await import("../api");

const home = (patch: Partial<KnowledgeSession> = {}) => ({ id: "ses_home", knowledgeBaseId: "@home", messages: [], messageCount: 0, ...patch }) as unknown as KnowledgeSession;

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
    window.localStorage.clear();
    api.sessions.mockResolvedValue([]);
    api.createSession.mockResolvedValue(home());
    api.session.mockResolvedValue(home());
    api.followAnswer.mockResolvedValue(null);
    api.stopAnswer.mockResolvedValue(undefined);
  });

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
});
