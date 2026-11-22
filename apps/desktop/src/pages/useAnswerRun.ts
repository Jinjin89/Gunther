import type { ConversationTurn, CreateSessionMessageInput, KnowledgeSession } from "@gunther/contracts";
import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { AnswerStoppedError, knowledgeApi, type AnswerEvent } from "../api";
import { readAloud } from "../speech/readAloud";
import { hasSavedQuestion } from "./interrupted";
import { applyAnswerEvent, type LiveAnswerState } from "./liveAnswer";

interface PendingAnswer {
  question: string;
  skill: string | null;
}

interface Listener {
  controller: AbortController;
  sessionId: string | null;
  pending: PendingAnswer | null;
  streamEnded?: boolean;
}

interface AnswerFailure {
  question: string;
  fresh: KnowledgeSession | null;
  readError: unknown;
  stopped: AnswerStoppedError | null;
  message: string | null;
}

interface AnswerRunOptions {
  readSession: (id: string) => Promise<KnowledgeSession>;
  refreshAfterFollow?: boolean;
  onStart?: () => void;
  onSaved: (id: string, turn: ConversationTurn | null, fresh: KnowledgeSession | null) => void;
  onFailure: (failure: AnswerFailure) => void;
}

const EMPTY: LiveAnswerState = { steps: [], text: "" };

/** The active listener owns progress, recovery and Stop for one conversation. */
export function useAnswerRun(options: AnswerRunOptions) {
  const latest = useRef(options);
  latest.current = options;
  const current = useRef<Listener | null>(null);
  const [pending, setPending] = useState<PendingAnswer | null>(null);
  const [live, setLive] = useState<LiveAnswerState | null>(null);

  const clear = useCallback(() => {
    current.current = null;
    setPending(null);
    setLive(null);
  }, []);

  const detach = useCallback(() => {
    const listener = current.current;
    clear();
    listener?.controller.abort();
  }, [clear]);
  useLayoutEffect(() => detach, [detach]);

  const show = useCallback((listener: Listener, answer: PendingAnswer) => {
    listener.pending = answer;
    setPending(answer);
    setLive(EMPTY);
    latest.current.onStart?.();
  }, []);

  const hear = useCallback((listener: Listener, event: AnswerEvent) => {
    if (current.current !== listener) return;
    if (event.type === "resumed") {
      if (!listener.pending) show(listener, { question: event.question, skill: null });
    } else if (listener.pending) {
      setLive((state) => applyAnswerEvent(state ?? EMPTY, event));
    }
  }, [show]);

  const listen = useCallback(async (
    listener: Listener, stream: () => Promise<ConversationTurn | null>, following = false,
  ) => {
    try {
      const turn = await stream().finally(() => { listener.streamEnded = true; });
      const id = listener.sessionId;
      if (current.current !== listener || !listener.pending || !id) return;
      const fresh = following && latest.current.refreshAfterFollow
        ? await latest.current.readSession(id) : null;
      if (current.current !== listener) return;
      if (turn || fresh) latest.current.onSaved(id, turn, fresh);
      if (turn) void readAloud.auto(turn.assistantMessage);
    } catch (reason) {
      if (current.current !== listener || !listener.pending) return;
      let fresh: KnowledgeSession | null = null;
      let readError: unknown;
      try {
        if (listener.sessionId) fresh = await latest.current.readSession(listener.sessionId);
      } catch (error) {
        readError = error;
      }
      if (current.current !== listener) return;
      const stopped = reason instanceof AnswerStoppedError ? reason : null;
      const message = stopped || hasSavedQuestion(fresh?.messages, reason) ? null
        : reason instanceof Error ? reason.message : "The answer could not be written.";
      latest.current.onFailure({ question: listener.pending.question, fresh, readError, stopped, message });
    } finally {
      if (current.current === listener) clear();
    }
  }, [clear]);

  const send = useCallback(async (
    target: string | ((signal: AbortSignal) => Promise<string>), payload: CreateSessionMessageInput,
  ) => {
    if (current.current?.pending) return;
    detach();
    const listener: Listener = {
      controller: new AbortController(), sessionId: typeof target === "string" ? target : null, pending: null,
    };
    current.current = listener;
    show(listener, { question: payload.content, skill: payload.skill ?? null });
    await listen(listener, async () => {
      const signal = listener.controller.signal;
      const id = typeof target === "string" ? target : await target(signal);
      if (current.current !== listener) return null;
      listener.sessionId = id;
      return knowledgeApi.sendMessageStream(id, payload, (event) => hear(listener, event), signal);
    });
  }, [detach, hear, listen, show]);

  const follow = useCallback((id: string) => {
    if (current.current?.sessionId === id) return;
    detach();
    const listener: Listener = { controller: new AbortController(), sessionId: id, pending: null };
    current.current = listener;
    void listen(listener, () => knowledgeApi.followAnswer(
      id, (event) => hear(listener, event), listener.controller.signal,
    ), true);
  }, [detach, hear, listen]);

  const cancel = useCallback(() => {
    const listener = current.current;
    if (!listener?.pending || !listener.sessionId || listener.streamEnded) return;
    // Stop can ask Research to write from what it found. Keep the listener on success.
    void knowledgeApi.stopAnswer(listener.sessionId).catch(() => {
      // The same listener can be refreshing history after its stream ends.
      if (current.current === listener && !listener.streamEnded) detach();
    });
  }, [detach]);

  return { pending, live, send, follow, cancel, detach };
}
