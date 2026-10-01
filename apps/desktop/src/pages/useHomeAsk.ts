import type { Effort, KnowledgeSession, KnowledgeSessionSummary, SessionMessage } from "@gunther/contracts";
import { useCallback, useEffect, useRef, useState } from "react";
import { readAloud } from "../speech/readAloud";
import { AnswerStoppedError, knowledgeApi } from "../api";
import { useLiveAnswer } from "./liveAnswer";

/** Home's conversations belong to no library; the service files them under this id. */
const HOME_SCOPE = "@home";
const HOME_SESSION_KEY = "gunther:home-session";

export interface AskOptions {
  model?: string | null;
  effort?: Effort;
  web?: boolean;
  /** How the answer is worded; the balanced style when left out. */
  style?: string;
  /** The libraries pointed at with @; none means all of them. */
  libraryIds?: string[];
}

const remembered = () => {
  try {
    return window.localStorage.getItem(HOME_SESSION_KEY);
  } catch {
    return null;
  }
};
const remember = (id: string | null) => {
  try {
    if (id) window.localStorage.setItem(HOME_SESSION_KEY, id);
    else window.localStorage.removeItem(HOME_SESSION_KEY);
  } catch {
    // The conversation is still saved; it just will not reopen by itself.
  }
};

/**
 * Questions asked from Home: one conversation at a time, kept by the service,
 * answered by the same agent as a library's Ask but over every library.
 */
export function useHomeAsk() {
  const [session, setSession] = useState<KnowledgeSession | null>(null);
  const [recent, setRecent] = useState<KnowledgeSessionSummary[]>([]);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Whether the conversation is on screen; closing it leaves it saved under Recent.
  const [shown, setShown] = useState(false);
  const { live, start, stop, hear } = useLiveAnswer();
  const controller = useRef<AbortController | null>(null);
  // The conversation whose answer is being listened to, for Stop.
  const answeringId = useRef<string | null>(null);

  const reloadRecent = useCallback(() => {
    void Promise.resolve()
      .then(() => knowledgeApi.sessions(HOME_SCOPE))
      .then((items) => setRecent(items.filter((item) => item.messageCount > 0).slice(0, 5)))
      .catch(() => undefined);
  }, []);

  /** Follow an answer still being written in a Home conversation, e.g. after closing and reopening it. */
  const follow = useCallback((id: string) => {
    if (controller.current) return;
    const abort = new AbortController();
    let following = false;
    void knowledgeApi.followAnswer(id, (event) => {
      if (event.type === "resumed") {
        if (controller.current) { abort.abort(); return; }
        following = true;
        controller.current = abort;
        answeringId.current = id;
        setPending(event.question);
        start();
        return;
      }
      if (following) hear(event);
    }, abort.signal).then(async (turn) => {
      if (!following) return;
      controller.current = null;
      setSession(await knowledgeApi.session(id));
      reloadRecent();
      if (turn) void readAloud.auto(turn.assistantMessage);
    }).catch(async (reason: unknown) => {
      if (!following || abort.signal.aborted) return;
      controller.current = null;
      if (!(reason instanceof AnswerStoppedError)) setError(reason instanceof Error ? reason.message : "The answer could not be written.");
      await knowledgeApi.session(id).then(setSession).catch(() => undefined);
    }).finally(() => {
      if (!following || abort.signal.aborted) return;
      if (controller.current === abort) controller.current = null;
      stop();
      setPending(null);
    });
  }, [hear, reloadRecent, start, stop]);

  useEffect(() => {
    reloadRecent();
    const id = remembered();
    if (!id) return;
    void Promise.resolve()
      .then(() => knowledgeApi.session(id))
      .then((loaded) => {
        if (loaded.knowledgeBaseId !== HOME_SCOPE) { remember(null); return; }
        setSession(loaded);
        follow(loaded.id);
      })
      .catch(() => remember(null));
  }, [follow, reloadRecent]);

  const ask = useCallback(async (question: string, options: AskOptions = {}) => {
    const content = question.trim();
    if (!content || pending !== null) return;
    setError(null);
    setPending(content);
    setShown(true);
    start();
    const abort = new AbortController();
    controller.current = abort;
    let current = session;
    /** The saved conversation, where the service kept a question it did not answer. */
    const reload = async () => {
      if (!current || controller.current) return null;
      try {
        const fresh = await knowledgeApi.session(current.id);
        setSession(fresh);
        return fresh;
      } catch {
        return null;
      }
    };
    try {
      if (!current) {
        current = await knowledgeApi.createSession(HOME_SCOPE, { selectedSourceIds: [] });
        remember(current.id);
      }
      answeringId.current = current.id;
      const turn = await knowledgeApi.sendMessageStream(current.id, {
        content,
        ...(options.model ? { model: options.model, ...(options.effort ? { effort: options.effort } : {}) } : {}),
        ...(options.web ? { web: true } : {}),
        ...(options.style ? { style: options.style } : {}),
        ...(options.libraryIds?.length ? { knowledgeBaseIds: options.libraryIds } : {}),
      }, hear, abort.signal);
      setSession({ ...current, ...turn.session, messages: [...current.messages, turn.userMessage, turn.assistantMessage] });
      reloadRecent();
      void readAloud.auto(turn.assistantMessage);
    } catch (reason) {
      // Closed or started afresh: only the listening stopped. The answer is still saved.
      if (abort.signal.aborted) return;
      if (controller.current === abort) controller.current = null;
      // Stopped: the service kept the question, marked; show its copy.
      if (!(reason instanceof AnswerStoppedError)) setError(reason instanceof Error ? reason.message : "The answer could not be written.");
      void reload();
    } finally {
      if (controller.current === abort) controller.current = null;
      if (!abort.signal.aborted) {
        stop();
        setPending(null);
      }
    }
  }, [hear, pending, reloadRecent, session, start, stop]);

  /** Stop the answer where it is; the question stays in the conversation, marked. */
  const cancel = useCallback(() => {
    const id = answeringId.current;
    const listening = controller.current;
    if (!id || !listening) return;
    knowledgeApi.stopAnswer(id).catch(() => listening.abort());
  }, []);

  /** Put the conversation away. An answer being written goes on and is there when it is opened again. */
  const detach = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
    stop();
    setPending(null);
  }, [stop]);

  const close = useCallback(() => {
    detach();
    setShown(false);
    setError(null);
  }, [detach]);

  const fresh = useCallback(() => {
    detach();
    remember(null);
    setSession(null);
    setError(null);
    setShown(false);
  }, [detach]);

  const open = useCallback(async (id: string) => {
    setError(null);
    try {
      const loaded = await knowledgeApi.session(id);
      remember(loaded.id);
      setSession(loaded);
      setShown(true);
      follow(loaded.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "That conversation could not be opened.");
    }
  }, [follow]);
  /** Move the conversation into a library; it stops being Home's. */
  const file = useCallback(async (libraryId: string): Promise<string | null> => {
    if (!session) return null;
    try {
      const filed = await knowledgeApi.fileSession(session.id, libraryId);
      remember(null);
      setSession(null);
      setShown(false);
      reloadRecent();
      return filed.id;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The conversation could not be filed.");
      return null;
    }
  }, [reloadRecent, session]);

  // Reading aloud goes on while its conversation is open, and stops when another one is.
  const shownId = shown ? session?.id : undefined;
  useEffect(() => { if (shownId) readAloud.focusSession(shownId); }, [shownId]);

  const messages: SessionMessage[] = session?.messages ?? [];
  return { session, messages, recent, pending, live, error, shown, ask, cancel, close, fresh, open, file };
}
