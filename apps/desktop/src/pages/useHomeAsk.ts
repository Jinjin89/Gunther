import { persistentStorage } from "../persistentStorage";
import type { CreateSessionMessageInput, KnowledgeSession, KnowledgeSessionSummary, SessionMessage } from "@gunther/contracts";
import { useCallback, useEffect, useRef, useState } from "react";
import { readAloud } from "../speech/readAloud";
import { knowledgeApi } from "../api";
import { useAnswerRun } from "./useAnswerRun";

/** Home's conversations belong to no library; the service files them under this id. */
const HOME_SCOPE = "@home";
const HOME_SESSION_KEY = "gunther:home-session";
/** Set (and announced as a window event of the same name) when Activity asks Home to show a conversation. */
export const OPEN_HOME_SESSION_KEY = "gunther:open-home-session";

type AskOptions = Omit<CreateSessionMessageInput, "content">;

const remembered = () => {
  try {
    return persistentStorage.getItem(HOME_SESSION_KEY);
  } catch {
    return null;
  }
};
const remember = (id: string | null) => {
  try {
    if (id) persistentStorage.setItem(HOME_SESSION_KEY, id);
    else persistentStorage.removeItem(HOME_SESSION_KEY);
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
  const [error, setError] = useState<string | null>(null);
  // Whether the conversation is on screen; closing it leaves it saved under Recent.
  const [shown, setShown] = useState(false);
  const navigation = useRef(0);

  const reloadRecent = useCallback(() => {
    void Promise.resolve()
      .then(() => knowledgeApi.sessions(HOME_SCOPE))
      .then((items) => setRecent(items.filter((item) => item.messageCount > 0).slice(0, 5)))
      .catch(() => undefined);
  }, []);

  const { pending, live, follow, send, cancel, detach } = useAnswerRun({
    readSession: knowledgeApi.session,
    refreshAfterFollow: true,
    onSaved(id, turn, fresh) {
      if (fresh) setSession(fresh);
      else if (turn) setSession((current) => current?.id === id
        ? { ...current, ...turn.session, messages: [...current.messages, turn.userMessage, turn.assistantMessage] }
        : current);
      reloadRecent();
    },
    onFailure({ fresh, message }) {
      if (fresh) { setSession(fresh); reloadRecent(); }
      if (message) setError(message);
    },
  });

  useEffect(() => {
    reloadRecent();
    // Activity asked for a particular conversation: that one opens instead (see below).
    const id = persistentStorage.getItem(OPEN_HOME_SESSION_KEY) ? null : remembered();
    const version = navigation.current;
    if (id) {
      void Promise.resolve()
        .then(() => knowledgeApi.session(id))
        .then((loaded) => {
          if (version !== navigation.current) return;
          if (loaded.knowledgeBaseId !== HOME_SCOPE) { remember(null); return; }
          setSession(loaded);
          follow(loaded.id);
        })
        .catch(() => { if (version === navigation.current) remember(null); });
    }
    return () => { navigation.current += 1; };
  }, [follow, reloadRecent]);

  const ask = useCallback(async (question: string, options: AskOptions = {}) => {
    const content = question.trim();
    if (!content || pending) return;
    navigation.current += 1;
    setError(null);
    setShown(true);
    await send(session?.id ?? (async (signal) => {
      const created = await knowledgeApi.createSession(HOME_SCOPE, { selectedSourceIds: [] });
      if (!signal.aborted) {
        remember(created.id);
        setSession(created);
      }
      return created.id;
    }), { content, ...options });
  }, [pending, send, session]);

  const close = useCallback(() => {
    navigation.current += 1;
    detach();
    setShown(false);
    setError(null);
  }, [detach]);

  const fresh = useCallback(() => {
    navigation.current += 1;
    detach();
    remember(null);
    setSession(null);
    setError(null);
    setShown(false);
  }, [detach]);

  const open = useCallback(async (id: string) => {
    const version = ++navigation.current;
    try {
      const loaded = await knowledgeApi.session(id);
      if (version !== navigation.current) return;
      setError(null);
      remember(loaded.id);
      setSession(loaded);
      setShown(true);
      follow(loaded.id);
    } catch (reason) {
      if (version === navigation.current) {
        setError(reason instanceof Error ? reason.message : "That conversation could not be opened.");
      }
    }
  }, [follow]);
  // Activity opens a conversation: from a key left for a Home that was not on screen, or an event.
  useEffect(() => {
    const take = (id: string | null) => {
      persistentStorage.removeItem(OPEN_HOME_SESSION_KEY);
      if (id) void open(id);
    };
    take(persistentStorage.getItem(OPEN_HOME_SESSION_KEY));
    const heard = (event: Event) => take((event as CustomEvent<{ sessionId?: string }>).detail?.sessionId ?? null);
    window.addEventListener(OPEN_HOME_SESSION_KEY, heard);
    return () => window.removeEventListener(OPEN_HOME_SESSION_KEY, heard);
  }, [open]);
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
  return { session, messages, recent, pending: pending?.question ?? null, live, error, shown, ask, cancel, close, fresh, open, file };
}
