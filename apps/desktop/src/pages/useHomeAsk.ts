import type { Effort, KnowledgeSession, KnowledgeSessionSummary, SessionMessage } from "@gunther/contracts";
import { useCallback, useEffect, useRef, useState } from "react";
import { readAloud } from "../speech/readAloud";
import { knowledgeApi } from "../api";
import { useLiveAnswer } from "./liveAnswer";
import { interruptedQuestion } from "./interrupted";

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

  const reloadRecent = useCallback(() => {
    void Promise.resolve()
      .then(() => knowledgeApi.sessions(HOME_SCOPE))
      .then((items) => setRecent(items.filter((item) => item.messageCount > 0).slice(0, 5)))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    reloadRecent();
    const id = remembered();
    if (!id) return;
    void Promise.resolve()
      .then(() => knowledgeApi.session(id))
      .then((loaded) => { if (loaded.knowledgeBaseId === HOME_SCOPE) setSession(loaded); else remember(null); })
      .catch(() => remember(null));
  }, [reloadRecent]);

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
      if (controller.current === abort) controller.current = null;
      if (abort.signal.aborted) {
        // Stopped: the question stays in the conversation, marked, until the saved copy arrives.
        const stopped = current;
        if (stopped) {
          setSession({ ...stopped, messages: [...stopped.messages, interruptedQuestion(stopped.id, content, "stopped")] });
          window.setTimeout(() => void reload(), 900);
        }
      } else {
        setError(reason instanceof Error ? reason.message : "The answer could not be written.");
        void reload();
      }
    } finally {
      if (controller.current === abort) controller.current = null;
      stop();
      setPending(null);
    }
  }, [hear, pending, reloadRecent, session, start, stop]);

  const cancel = useCallback(() => controller.current?.abort(), []);

  const close = useCallback(() => {
    controller.current?.abort();
    setShown(false);
    setError(null);
  }, []);

  const fresh = useCallback(() => {
    controller.current?.abort();
    remember(null);
    setSession(null);
    setError(null);
    setShown(false);
  }, []);

  const open = useCallback(async (id: string) => {
    setError(null);
    try {
      const loaded = await knowledgeApi.session(id);
      remember(loaded.id);
      setSession(loaded);
      setShown(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "That conversation could not be opened.");
    }
  }, []);

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

  const messages: SessionMessage[] = session?.messages ?? [];
  return { session, messages, recent, pending, live, error, shown, ask, cancel, close, fresh, open, file };
}
