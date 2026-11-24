import type { ConversationBrief, ConversationBriefInput, ConversationTurn, KnowledgeSession, KnowledgeSessionSummary, SessionMessage } from "@gunther/contracts";
import { useCallback, useEffect, useRef, useState } from "react";
import { ServiceUnavailableError, knowledgeApi } from "../api";
import { persistentStorage } from "../persistentStorage";

export interface UseLibraryChats {
  sessions: KnowledgeSessionSummary[];
  archivedSessions: KnowledgeSessionSummary[];
  showArchived: boolean;
  archivedLoading: boolean;
  archivedLoaded: boolean;
  loading: boolean;
  online: boolean;
  error: string | null;
  readError: Error | null;
  activeId: string | null;
  newSessionId: string | null;
  activeSession: KnowledgeSession | null;
  selectedMessageId: string | null;
  setSelectedMessageId: (id: string | null) => void;
  load: () => Promise<void>;
  loadSession: (id: string) => Promise<void>;
  createSession: () => Promise<KnowledgeSession | null>;
  patchSession: (
    id: string,
    patch: Partial<Pick<KnowledgeSessionSummary, "title" | "pinned" | "archived">>,
  ) => Promise<KnowledgeSessionSummary>;
  pinSession: (session: KnowledgeSessionSummary) => Promise<void>;
  archiveSession: (session: KnowledgeSessionSummary) => Promise<void>;
  restoreSession: (session: KnowledgeSessionSummary) => Promise<void>;
  toggleArchived: () => Promise<void>;
  branchSession: (sessionId: string, messageId: string) => Promise<KnowledgeSession | null>;
  saveTurn: (turn: ConversationTurn) => void;
  mergeSession: (session: KnowledgeSession, messages?: SessionMessage[]) => void;
  briefBusy: string | null;
  refreshBrief: (sessionId: string) => Promise<void>;
  saveBrief: (sessionId: string, brief: ConversationBriefInput) => Promise<void>;
  reportReadError: (error: unknown) => void;
}

const sortSessions = (items: KnowledgeSessionSummary[]) =>
  [...items].sort((left, right) => Number(right.pinned) - Number(left.pinned)
    || new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime());

const messageError = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;
const lastAnswerId = (messages: SessionMessage[]) => [...messages].reverse().find((message) => message.role === "assistant")?.id ?? null;

export function useLibraryChats(
  baseId: string,
  onNotify: (message: string) => void,
  onCite: () => void,
  canOpenChat: () => boolean = () => true,
): UseLibraryChats {
  const [sessions, setSessions] = useState<KnowledgeSessionSummary[]>([]);
  const [archivedSessions, setArchivedSessions] = useState<KnowledgeSessionSummary[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [archivedLoading, setArchivedLoading] = useState(false);
  const [archivedLoaded, setArchivedLoaded] = useState(false);
  const [read, setRead] = useState<{ pending: boolean; error: Error | null }>({ pending: true, error: null });
  const [error, setError] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [activeSession, setActiveSession] = useState<KnowledgeSession | null>(null);
  const [selectedMessageId, setSelectedMessageId] = useState<string | null>(null);
  const [newSessionId, setNewSessionId] = useState<string | null>(null);
  const [briefBusy, setBriefBusy] = useState<string | null>(null);
  const mounted = useRef(false);
  const creatingSession = useRef(false);
  const loadingSessionList = useRef(false);
  const canOpenChatRef = useRef(canOpenChat);

  useEffect(() => {
    canOpenChatRef.current = canOpenChat;
  }, [canOpenChat]);

  const load = useCallback(async () => {
    if (loadingSessionList.current) return;
    loadingSessionList.current = true;
    setRead({ pending: true, error: null });
    setError(null);
    try {
      let loaded = (await knowledgeApi.sessions(baseId)).filter((session) => !session.archived);
      if (!loaded.length) {
        loaded = [await knowledgeApi.createSession(baseId, { focusChapterId: null, selectedSourceIds: [] })];
      }
      const remembered = persistentStorage.getItem(`gunther:active-session:${baseId}`);
      const first = loaded.find((session) => session.id === remembered) ?? loaded[0]!;
      const detail = await knowledgeApi.session(first.id);
      if (!mounted.current) return;
      setSessions(sortSessions(loaded));
      setActiveId(detail.id);
      setActiveSession(detail);
      const selectedKey = `gunther:selected-message:${baseId}`;
      const selectedId = persistentStorage.getItem(selectedKey);
      const answers = detail.messages.filter((message) => message.role === "assistant");
      setSelectedMessageId(answers.find((answer) => answer.id === selectedId)?.id ?? lastAnswerId(detail.messages));
      if (selectedId) persistentStorage.removeItem(selectedKey);
      setRead({ pending: false, error: null });
    } catch (reason) {
      if (!mounted.current) return;
      const readError = reason instanceof Error ? reason : new Error("The request failed.");
      setRead({ pending: false, error: readError });
      setError("Could not load chats");
    } finally {
      loadingSessionList.current = false;
    }
  }, [baseId]);

  const reportReadError = useCallback((reason: unknown) => {
    if (!(reason instanceof ServiceUnavailableError)) return;
    setRead((current) => ({ ...current, error: reason }));
  }, []);

  const openDetail = useCallback((detail: KnowledgeSession, preferredSelectedId?: string | null) => {
    setActiveId(detail.id);
    setActiveSession(detail);
    setSelectedMessageId(preferredSelectedId ?? lastAnswerId(detail.messages));
  }, []);

  const loadSession = useCallback(async (id: string) => {
    setActiveId(id);
    setError(null);
    try {
      openDetail(await knowledgeApi.session(id));
    } catch (reason) {
      reportReadError(reason);
      setError(messageError(reason, "Could not open this chat"));
    }
  }, [openDetail, reportReadError]);

  const applySummary = useCallback((updated: KnowledgeSessionSummary) => {
    const updateList = (current: KnowledgeSessionSummary[], archived: boolean) => sortSessions([
      ...(updated.archived === archived ? [updated] : []),
      ...current.filter((item) => item.id !== updated.id),
    ]);
    setSessions((current) => updateList(current, false));
    setArchivedSessions((current) => updateList(current, true));
    setActiveSession((current) => current?.id === updated.id ? { ...current, ...updated } : current);
  }, []);

  const patchSession = useCallback(async (
    id: string,
    patch: Partial<Pick<KnowledgeSessionSummary, "title" | "pinned" | "archived">>,
  ) => {
    const updated = await knowledgeApi.updateSession(id, patch);
    applySummary(updated);
    return updated;
  }, [applySummary]);

  const createSession = useCallback(async () => {
    if (!mounted.current || read.pending || read.error instanceof ServiceUnavailableError || creatingSession.current) return null;
    creatingSession.current = true;
    setError(null);
    try {
      const created = await knowledgeApi.createSession(baseId, { focusChapterId: null, selectedSourceIds: [] });
      if (!mounted.current) return null;
      setSessions((current) => sortSessions([created, ...current]));
      openDetail(created, null);
      setNewSessionId(created.id);
      onCite();
      return created;
    } catch (reason) {
      if (mounted.current) setError(messageError(reason, "Could not create a chat"));
      return null;
    } finally {
      creatingSession.current = false;
    }
  }, [baseId, onCite, openDetail, read.error, read.pending]);

  const pinSession = useCallback(async (session: KnowledgeSessionSummary) => {
    try {
      await patchSession(session.id, { pinned: !session.pinned });
    } catch (reason) {
      setError(messageError(reason, "Could not update the chat pin"));
    }
  }, [patchSession]);

  const archiveSession = useCallback(async (session: KnowledgeSessionSummary) => {
    try {
      await patchSession(session.id, { archived: true });
      const remaining = sessions.filter((item) => item.id !== session.id && !item.archived);
      if (activeId === session.id) {
        if (remaining[0]) await loadSession(remaining[0].id);
        else await createSession();
      }
      onNotify("Session archived. Its history is still available from the archive.");
    } catch (reason) {
      setError(messageError(reason, "Could not archive the chat"));
    }
  }, [activeId, createSession, loadSession, onNotify, patchSession, sessions]);

  const restoreSession = useCallback(async (session: KnowledgeSessionSummary) => {
    try {
      await patchSession(session.id, { archived: false });
      onNotify("Session restored to recent history.");
    } catch (reason) {
      setError(messageError(reason, "Could not restore the chat"));
    }
  }, [onNotify, patchSession]);

  const toggleArchived = useCallback(async () => {
    const next = !showArchived;
    setShowArchived(next);
    if (!next) {
      setArchivedLoading(false);
      return;
    }
    if (read.error instanceof ServiceUnavailableError) return;
    setArchivedLoading(true);
    try {
      const allSessions = await knowledgeApi.sessions(baseId, true);
      if (mounted.current) {
        setArchivedSessions(sortSessions(allSessions.filter((session) => session.archived)));
        setArchivedLoaded(true);
      }
    } catch (reason) {
      if (mounted.current) setError(messageError(reason, "Could not load archived chats"));
    } finally {
      if (mounted.current) setArchivedLoading(false);
    }
  }, [baseId, read.error, showArchived]);

  const setBrief = useCallback((sessionId: string, updater: (brief: ConversationBrief | undefined) => ConversationBrief) => {
    setActiveSession((current) => current?.id === sessionId ? { ...current, brief: updater(current.brief) } : current);
  }, []);

  const refreshBrief = useCallback(async (sessionId: string) => {
    setBriefBusy(sessionId);
    try {
      const brief = await knowledgeApi.refreshBrief(sessionId);
      setBrief(sessionId, () => brief);
    } catch (reason) {
      setBrief(sessionId, (brief) => ({ goal: "", constraints: [], settled: [], open: [], edited: [], ...brief,
        error: messageError(reason, "The brief could not be updated") }));
    } finally {
      setBriefBusy((current) => current === sessionId ? null : current);
    }
  }, [setBrief]);

  const saveBrief = useCallback(async (sessionId: string, input: ConversationBriefInput) => {
    try {
      const brief = await knowledgeApi.editBrief(sessionId, input);
      setBrief(sessionId, () => brief);
    } catch (reason) {
      onNotify(messageError(reason, "The brief could not be saved"));
    }
  }, [onNotify, setBrief]);

  const mergeSession = useCallback((session: KnowledgeSession, messages?: SessionMessage[]) => {
    setSessions((current) => sortSessions(current.some((item) => item.id === session.id)
      ? current.map((item) => item.id === session.id ? session : item)
      : [session, ...current]));
    setActiveSession((current) => current?.id === session.id
      ? { ...current, ...session, messages: messages ?? current.messages }
      : current);
  }, []);

  const saveTurn = useCallback((turn: ConversationTurn) => {
    setSessions((current) => sortSessions(current.some((item) => item.id === turn.session.id)
      ? current.map((item) => item.id === turn.session.id ? turn.session : item)
      : [turn.session, ...current]));
    setActiveSession((current) => current?.id === turn.session.id
      ? { ...current, ...turn.session, messages: [...current.messages, turn.userMessage, turn.assistantMessage] }
      : current);
    setSelectedMessageId(turn.assistantMessage.id);
  }, []);

  const branchSession = useCallback(async (sessionId: string, messageId: string) => {
    setError(null);
    try {
      const branch = await knowledgeApi.branchSession(sessionId, messageId);
      if (!mounted.current) return null;
      setSessions((current) => sortSessions([branch, ...current]));
      openDetail(branch);
      onNotify("Branched into a new session without changing the original.");
      return branch;
    } catch (reason) {
      if (mounted.current) setError(messageError(reason, "Could not branch this chat"));
      return null;
    }
  }, [onNotify, openDetail]);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => { mounted.current = false; };
  }, [load]);

  useEffect(() => {
    const heard = (event: Event) => {
      const detail = (event as CustomEvent<{ libraryId?: string; sessionId?: string }>).detail;
      if (detail?.libraryId === baseId && detail.sessionId && canOpenChatRef.current()) void loadSession(detail.sessionId);
    };
    window.addEventListener("gunther:open-session", heard);
    return () => window.removeEventListener("gunther:open-session", heard);
  }, [baseId, loadSession]);

  useEffect(() => {
    const onNewSession = () => {
      if (canOpenChatRef.current()) void createSession();
    };
    window.addEventListener("gunther:new-session", onNewSession);
    return () => window.removeEventListener("gunther:new-session", onNewSession);
  }, [createSession]);

  useEffect(() => {
    if (activeId) persistentStorage.setItem(`gunther:active-session:${baseId}`, activeId);
  }, [activeId, baseId]);

  return {
    sessions,
    archivedSessions,
    showArchived,
    archivedLoading,
    archivedLoaded,
    loading: read.pending,
    online: !(read.error instanceof ServiceUnavailableError),
    error,
    readError: read.error,
    activeId,
    newSessionId,
    activeSession,
    selectedMessageId,
    setSelectedMessageId,
    load,
    loadSession,
    createSession,
    patchSession,
    pinSession,
    archiveSession,
    restoreSession,
    toggleArchived,
    branchSession,
    saveTurn,
    mergeSession,
    briefBusy,
    refreshBrief,
    saveBrief,
    reportReadError,
  };
}
