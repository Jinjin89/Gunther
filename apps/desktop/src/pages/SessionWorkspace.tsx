import { persistentStorage } from "../persistentStorage";
import type {
  AskRetryOptions,
  ConversationBrief,
  ConversationBriefInput,
  ConversationCitation,
  ConversationContext,
  KnowledgeSession,
  SessionMessage,
} from "@gunther/contracts";
import {
  Archive,
  ArrowRight,
  ArrowUp,
  Check,
  ChevronRight,
  CircleAlert,
  Copy,
  Download,
  FolderOpen,
  GitBranch,
  Globe2,
  MoreHorizontal,
  Plus,
  ShieldCheck,
  Square,
  SquarePen,
  UserRound,
  X,
} from "lucide-react";
import { useAutosize } from "../services/useAutosize";
import { BriefPanel } from "./ConversationBrief";
import { SkillChip, useAskSkills, useSkillMenu, type SkillBudget } from "../components/search/skills";
import { withoutTrigger } from "../components/search/TriggerMenu";
import { InterruptedNote, isInterrupted } from "./interrupted";
import { DictationStatus, MicGlyph } from "../components/Dictation";
import { useDictatedField } from "../services/useDictatedField";
import { useEscape, withShortcut } from "../shortcuts/shortcuts";
import { useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode, type Ref } from "react";
import type { KnowledgeBase } from "../atlas";
import { ServiceUnavailableError, knowledgeApi } from "../api";
import { AnswerTrace } from "../developer/AnswerTrace";
import { useTraces } from "../developer/useTraces";
import { lastAnswerChoice, MODELS_CHANGED_EVENT, readAnswerStyle, usableChoice, useDeviceChoice, useModelMenu, useWebSearch, type AskChoice } from "../models/askModel";
import { SpeakerButton } from "../speech/SpeakerButton";
import { readAloud } from "../speech/readAloud";
import { AgentSteps, AnswerBody, LiveAnswer, ResearchPlan, citationNumbers, noSourcesNeeded, usedSkill } from "./AnswerBody";
import { useAnswerRun } from "./useAnswerRun";
import { KeyResultsStatus } from "../keyResults/KeyResultsStatus";
import { ModelPicker } from "../models/ModelPicker";
import { BrandMark } from "../design/BrandMark";
import { createPortal } from "react-dom";
import { PopMenu } from "../contents/SectionMenu";
import { CitationDetails } from "../components/evidence/CitationButton";
import { LibraryWelcome } from "./LibraryWelcome";
import type { UseLibraryChats } from "./useLibraryChats";
import "../session.css";

export interface LibraryChatActions {
  ask: (text: string, submit: boolean) => void;
}

interface SessionWorkspaceProps {
  base: KnowledgeBase;
  chats: UseLibraryChats;
  sourceIds?: string[];
  sourceScopeEmpty?: boolean;
  visible?: boolean;
  onFocusSources?: () => void;
  chatActions?: Ref<LibraryChatActions>;
  onNotify: (message: string) => void;
  onCite: (citation: ConversationCitation | null, index?: number) => void;
  onInspectSources: (message: SessionMessage) => void;
  onInspectCitation: (message: SessionMessage, index: number) => void;
  onWebStatus?: ((enabled: boolean) => void) | undefined;
}

function renderInline(value: string, onCitation?: () => void) {
  return value.split(/(\*\*[^*]+\*\*|\[\d+\])/g).filter(Boolean).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (/^\[\d+\]$/.test(part)) return <sup key={index}>{onCitation ? <button onClick={(event) => { event.stopPropagation(); onCitation(); }} aria-label={`Inspect citation ${part.slice(1, -1)}`}>{part}</button> : part}</sup>;
    return <span key={index}>{part}</span>;
  });
}

function MessageContent({ content, onCitation }: { content: string; onCitation?: () => void }) {
  return <>{content.split("\n").map((line, index) => line.trim() ? <p key={index}>{renderInline(line, onCitation)}</p> : <span className="message-spacer" key={index} />)}</>;
}

/** Which model wrote an answer, at what effort, and anything it has to admit. */
function AnswerModel({ context }: { context: ConversationContext }) {
  const byModel = context.responderMode !== "local";
  const name = context.modelLabel ?? (context.responderMode === "deepseek" ? "DeepSeek" : "Language model");
  const label = byModel
    ? [name, context.effortLabel].filter(Boolean).join(" · ")
    : context.modelError ? "Quotes only" : "Local evidence synthesis";
  const notes = context.notes ?? [];
  return <span className="message-method message-model">
    <i className={byModel ? "is-ai" : ""} />{label}
    {context.skill && <small className="message-model-note">· {usedSkill(context.skill)}</small>}
    {notes.length > 0 && <small className="message-model-note">· {notes.join(" ")}</small>}
  </span>;
}

export function ConversationMessage({ message, libraryId, visible, retryDisabled, onRetry, onEdit, onCite,
  selected, branching, onSelect, onSources, onCopy, onBranch, onTrace }: {
  message: SessionMessage;
  libraryId?: string;
  visible?: boolean;
  retryDisabled: boolean;
  onRetry: () => void;
  onEdit: () => void;
  onCite: (citation: ConversationCitation, index: number) => void;
  selected: boolean;
  branching: boolean;
  onSelect: () => void;
  onSources?: (() => void) | undefined;
  onCopy: () => void;
  onBranch: () => void;
  onTrace?: (() => void) | undefined;
}) {
  const isAssistant = message.role === "assistant";
  // Small talk and follow-ups on the conversation itself need no sources.
  const conversational = noSourcesNeeded(message.context);
  const sources = new Set<string>();
  const webSources = new Set<string>();
  message.citations.forEach((citation) => {
    const key = citation.sourceId || citation.url || citation.id;
    sources.add(key);
    if (citation.kind === "web") webSources.add(key);
  });
  return (
    <article className={`conversation-message role-${message.role} ${selected ? "is-selected" : ""} ${isInterrupted(message) ? "is-interrupted" : ""}`} onClick={isAssistant ? onSelect : undefined}>
      <div className="message-author">{isAssistant ? <span className="assistant-mark"><BrandMark size={14} /></span> : <span className="user-mark"><UserRound size={13} /></span>}<span>{isAssistant ? "Gunther" : "You"}</span><time>{new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(message.createdAt))}</time></div>
      {isAssistant && <ResearchPlan context={message.context} />}
      {isAssistant && <AgentSteps steps={message.context.steps ?? []} />}
      <div className="message-body">{isAssistant ? <CitationDetails.Provider value={message.citations}>
        <AnswerBody content={message.content} numbers={citationNumbers(message.citations)} onCitation={(index) => { onSelect(); const citation = message.citations[index]; if (citation) onCite(citation, index); }} supportNotes={message.context.supportNotes} coverage={message.context.coverage} />
      </CitationDetails.Provider> : <MessageContent content={message.content} />}</div>
      {message.context.interrupted && <InterruptedNote message={message} disabled={retryDisabled} onRetry={onRetry} onEdit={onEdit} />}
      {isAssistant && message.context.modelError && <p className="message-model-error" role="note"><CircleAlert size={13} /><span>{message.context.modelError}{message.citations.length > 0 && " The quotes stand in for its answer."}</span></p>}
      {isAssistant && message.context.reasoning && <details className="message-thinking" onClick={(event) => event.stopPropagation()}><summary><ChevronRight size={12} />Thinking</summary><pre>{message.context.reasoning}</pre></details>}
      {isAssistant && <footer className="message-footer">
        <div className="message-actions">{sources.size > 0 ? <button type="button" className="message-sources-button" data-opens-evidence onClick={(event) => {
          event.stopPropagation(); onSelect(); onSources?.();
        }}>{sources.size} {sources.size === 1 ? "source" : "sources"}{webSources.size > 0 ? ` · ${webSources.size} web` : ""}</button>
          : conversational ? null : <span className="no-citation-state"><CircleAlert size={12} />Not from your sources</span>}
        {onTrace && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={(event) => { event.stopPropagation(); onTrace(); }}>Trace</button>}
        <button className="copy-answer" onClick={(event) => { event.stopPropagation(); onCopy(); }}><Copy size={12} />Copy</button>
        <SpeakerButton message={message} />
        <button className="branch-answer" disabled={branching} onClick={(event) => { event.stopPropagation(); onBranch(); }}><GitBranch size={12} />{branching ? "Branching…" : "Branch"}</button>
        {libraryId && message.keyResults && <KeyResultsStatus key={message.keyResults.id} baseId={libraryId}
          initial={message.keyResults} visible={visible ?? true} />}
        </div>
        <AnswerModel context={message.context} />
      </footer>}
    </article>
  );
}

export function Composer({
  picker,
  web,
  value,
  sending,
  sourceCount,
  sourceScopeEmpty = false,
  onChange,
  onSend,
  onStop,
  onSources,
  readOnly,
  ready,
  allowDraft = false,
  offline = false,
  skill = null,
  onSkillChange,
  budget,
  onBudgetChange,
  stopLabel = "Stop",
}: {
  /** The skill chosen with `/` (its command); it applies to the next message. */
  skill?: string | null;
  onSkillChange?: (command: string | null) => void;
  /** The depth chosen for a skill that has budgets (research). */
  budget?: SkillBudget;
  onBudgetChange?: (budget: SkillBudget) => void;
  /** What Stop does while an answer is worked out: "Stop and write" for research that is still gathering. */
  stopLabel?: string;
  /** The model and effort chips. */
  picker?: ReactNode;
  /** The Web switch: whether web search is set up, and whether it is on for questions. */
  web?: { available: boolean; enabled: boolean; onChange: (next: boolean) => void };
  value: string;
  sending: boolean;
  sourceCount: number;
  sourceScopeEmpty?: boolean;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onSources?: (() => void) | undefined;
  readOnly: boolean;
  ready: boolean;
  allowDraft?: boolean;
  offline?: boolean;
}) {
  const canSend = ready && !readOnly && !sourceScopeEmpty;
  const composerDisabled = (!ready && !allowDraft && !offline) || readOnly;
  const dictation = useDictatedField(value, onChange, { enabled: canSend });
  const field = useRef<HTMLTextAreaElement>(null);
  useAutosize(field, value, 220);
  const submit = () => { if (!canSend || dictation.dictating) return; dictation.discard(); onSend(); };
  const [caret, setCaret] = useState(0);
  const pendingCaret = useRef<number | null>(null);
  const skills = useAskSkills();
  const slash = useSkillMenu(value, caret, skills, (chosen, at) => pick(chosen.command, at), Boolean(onSkillChange) && !skill && !composerDisabled);
  const pick = (command: string, at: { start: number; query: string }) => {
    onChange(withoutTrigger(value, at, field.current?.selectionStart ?? caret));
    onSkillChange?.(command);
    slash.reset();
    pendingCaret.current = at.start;
    field.current?.focus();
  };
  // Put the caret back once React has written the edited text into the field.
  useLayoutEffect(() => {
    const element = field.current;
    if (!element || pendingCaret.current === null) return;
    const position = Math.min(pendingCaret.current, element.value.length);
    pendingCaret.current = null;
    element.setSelectionRange(position, position);
    setCaret(position);
  }, [value]);
  const onKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing) return;
    if (slash.keyDown(event)) return;
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(); return; }
    // Backspace at the very start takes the skill off.
    if (event.key === "Backspace" && skill && event.currentTarget.selectionStart === 0 && event.currentTarget.selectionEnd === 0) {
      event.preventDefault();
      onSkillChange?.(null);
    }
  };
  return (
    <div className="composer-wrap">
      <div className={`composer ${dictation.dictating ? "is-dictating" : ""}`}>
        {skill && <div className="composer-skill"><SkillChip command={skill} {...(budget ? { budget } : {})} {...(onBudgetChange ? { onBudgetChange } : {})} onRemove={() => { onSkillChange?.(null); field.current?.focus(); }} /></div>}
        <textarea ref={field} value={value} rows={1} disabled={composerDisabled} onChange={(event) => { onChange(event.target.value); setCaret(event.target.selectionStart ?? event.target.value.length); }} onSelect={(event) => setCaret(event.currentTarget.selectionStart ?? 0)} onKeyDown={onKeyDown} onBlur={slash.dismiss} aria-autocomplete="list" aria-controls={slash.open ? slash.listId : undefined} aria-activedescendant={slash.activeId} placeholder={readOnly ? "Restore this session to continue the conversation." : (offline || (allowDraft && !ready)) ? "Draft a question…" : !ready ? "Opening a durable session…" : "Ask, compare, challenge, or trace a claim…"} aria-label="Message Gunther" onFocus={() => { dictation.claim(); slash.reset(); }} />
        <div className="composer-toolbar">
          <div className="composer-options">{onSources && <button className="scope-chip" onClick={onSources} aria-describedby={sourceScopeEmpty ? "empty-source-scope" : undefined}>
            <FolderOpen size={13} /><span>{sourceScopeEmpty || sourceCount ? `${sourceCount} selected` : "All sources"}</span>
          </button>}{picker}{web && <button type="button" className={`web-toggle ${web.enabled ? "is-on" : ""}`} disabled={!web.available} aria-pressed={web.enabled} onClick={() => web.onChange(!web.enabled)} title={web.available ? (web.enabled ? "Ask may search the web. Click to keep it to your library." : "Ask is limited to your library. Click to let it search the web.") : "Web search is not set up. Add a Tavily key in Settings."}><Globe2 size={13} /><span>Web</span></button>}</div>
          <div className="composer-actions"><button type="button" className={`mic-button ${dictation.dictating ? "is-on" : ""} is-${dictation.state}`} disabled={!canSend || sending} onClick={dictation.toggle} aria-pressed={dictation.listening} aria-label={dictation.listening ? "Stop voice input" : "Voice input"} title={dictation.error ?? withShortcut(dictation.listening ? "Stop voice input" : "Speak instead of typing", "dictate")}><MicGlyph state={dictation.state} /></button><button className={`send-button ${sending ? "is-stop" : ""} ${sending && stopLabel !== "Stop" ? "has-label" : ""}`} disabled={!sending && (!canSend || !value.trim() || dictation.dictating)} onClick={sending ? onStop : submit} aria-label={sending ? (stopLabel === "Stop" ? "Stop waiting for response" : stopLabel) : "Send message"}>{sending ? <><Square size={12} fill="currentColor" />{stopLabel !== "Stop" && <span>{stopLabel}</span>}</> : <ArrowUp size={16} />}</button></div>
        </div>
        {slash.menu}
      </div>
      <p className="composer-note" role={dictation.error ? "alert" : undefined}>{dictation.error ? dictation.error : dictation.dictating ? <DictationStatus state={dictation.state} level={dictation.level} /> : offline ? "Reconnect to send your draft." : !ready ? (allowDraft ? "Load the Ask settings to send your draft." : "Preparing a durable conversation before accepting questions…") : readOnly ? "Archived sessions are read-only. Restore this session from its options to continue." : "Gunther can be wrong. Verify important conclusions in the cited source."}</p>
    </div>
  );
}

const readLabels = { models: "models", settings: "Ask settings" };
type StartupRead = keyof typeof readLabels;
const startupReads = Object.keys(readLabels) as StartupRead[];
// The current session supplies the durable destination for a send.
const sendReads = ["models", "settings"] as const;

export function SessionWorkspace({ base, chats, sourceIds = [], sourceScopeEmpty = false, visible = true, onFocusSources, chatActions, onNotify, onCite, onInspectSources, onInspectCitation, onWebStatus }: SessionWorkspaceProps) {
  const { activeId, activeSession, selectedMessageId, setSelectedMessageId } = chats;
  const [draft, setDraft] = useState("");
  // The skill chosen with `/`; it applies to the next message only.
  const [skill, setSkill] = useState<string | null>(null);
  // How deep a skill that has budgets (research) goes; Standard unless the reader says Deep.
  const [budget, setBudget] = useState<SkillBudget>("standard");
  const askSkills = useAskSkills();
  const [branchingMessageId, setBranchingMessageId] = useState<string | null>(null);
  const [reads, setReads] = useState<Partial<Record<StartupRead, { pending: boolean; error: Error | null }>>>({});
  const inFlightReads = useRef(new Map<StartupRead, { refreshPending: boolean }>());
  const mounted = useRef(false);
  const readReady = (read: StartupRead) => reads[read]?.pending === false && !reads[read]?.error;
  const loading = chats.loading;
  const online = chats.online && !sendReads.some((read) => reads[read]?.error instanceof ServiceUnavailableError);
  const ready = activeSession !== null && activeSession.id === activeId
    && !chats.loading && !chats.readError && sendReads.every(readReady);
  const [error, setError] = useState<string | null>(null);
  const traces = useTraces();
  const [trace, setTrace] = useState<{ sessionId: string; messageId: string } | null>(null);
  const [briefBusy, setBriefBusy] = useState<string | null>(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const scrollPane = useRef<HTMLDivElement>(null);
  /** Whether the reader is at the bottom; only then does new text pull the view along. */
  const followTail = useRef(true);
  const scrollOffset = useRef(0);
  // Which model answers next, and how hard it thinks: what this conversation picked,
  // else the model of its last answer, else this device's last choice, else the Ask default.
  const { menu: modelMenu, reload: reloadModelMenu } = useModelMenu({ autoLoad: false });
  const { reload: reloadWebSearch, ...webSearch } = useWebSearch({ autoLoad: false });
  useEffect(() => { onWebStatus?.(webSearch.enabled); }, [onWebStatus, webSearch.enabled]);
  const [deviceChoice, setDeviceChoice] = useDeviceChoice();
  const [pickedChoices, setPickedChoices] = useState<Record<string, AskChoice>>({});
  const askChoice = usableChoice(
    modelMenu,
    activeId ? pickedChoices[activeId] : null,
    activeSession ? lastAnswerChoice(activeSession.messages) : null,
    deviceChoice,
  );
  const chooseModel = (next: AskChoice) => {
    if (activeId) setPickedChoices((current) => ({ ...current, [activeId]: next }));
    setDeviceChoice(next);
  };

  const finishRead = useCallback((read: StartupRead, error: Error | null) => {
    if (mounted.current) setReads((current) => ({ ...current, [read]: { pending: false, error } }));
  }, []);

  const { pending, live, follow, detach, send: sendAnswer, cancel: stopResponse } = useAnswerRun({
    readSession: knowledgeApi.session,
    onStart() { followTail.current = true; },
    onSaved(id, turn) {
      if (!turn) return;
      chats.saveTurn(turn);
      void refreshBrief(id);
    },
    onFailure({ question, fresh, readError, stopped, message }) {
      chats.reportReadError(readError);
      if (fresh) chats.mergeSession(fresh, fresh.messages);
      if (stopped) onNotify(stopped.message);
      if (message) {
        setDraft((current) => current || question);
        setError(message);
      }
    },
  });
  const sending = pending !== null;
  const asking = pending?.question;

  const loadWorkspace = useCallback(function loadWorkspace(...requested: StartupRead[]) {
    const load: Record<StartupRead, () => Promise<void>> = {
      models: reloadModelMenu,
      settings: reloadWebSearch,
    };
    for (const read of requested.length ? requested : startupReads) {
      if (inFlightReads.current.has(read)) continue;
      const request = { refreshPending: false };
      inFlightReads.current.set(read, request);
      setReads((current) => ({ ...current, [read]: { pending: true, error: current[read]?.error ?? null } }));
      void load[read]().then(
        () => finishRead(read, null),
        (reason: unknown) => finishRead(read, reason instanceof Error ? reason : new Error("The request failed.")),
      ).finally(() => {
        inFlightReads.current.delete(read);
        if (request.refreshPending && mounted.current) loadWorkspace(read);
      });
    }
  }, [reloadModelMenu, reloadWebSearch, finishRead]);

  useEffect(() => {
    mounted.current = true;
    const askDraftKey = `gunther:ask-draft:${base.id}`;
    const handedOff = persistentStorage.getItem(askDraftKey);
    if (handedOff) {
      persistentStorage.removeItem(askDraftKey);
      setDraft(handedOff);
    }
    loadWorkspace();
    const refreshReads = (...updated: StartupRead[]) => {
      for (const read of updated) {
        const request = inFlightReads.current.get(read);
        if (request) request.refreshPending = true;
        else loadWorkspace(read);
      }
    };
    const loadSettings = () => refreshReads("models", "settings");
    window.addEventListener(MODELS_CHANGED_EVENT, loadSettings);
    return () => {
      mounted.current = false;
      window.removeEventListener(MODELS_CHANGED_EVENT, loadSettings);
    };
  }, [base.id, loadWorkspace]);

  // Scroll only the conversation pane (scrollIntoView would move every ancestor too), and only
  // while the reader is at the bottom, so reading earlier messages is never yanked away.
  useEffect(() => {
    const pane = scrollPane.current;
    if (visible && pane && followTail.current) pane.scrollTop = pane.scrollHeight;
  }, [activeSession?.messages.length, sending, asking, live?.steps.length, live?.text.length, visible]);
  useEffect(() => {
    if (visible && scrollPane.current && !followTail.current) scrollPane.current.scrollTop = scrollOffset.current;
  }, [visible]);
  useEffect(() => {
    followTail.current = true;
    setRenameOpen(false);
    setTrace(null);
  }, [activeId]);
  useEffect(() => {
    if (!chats.newSessionId) return;
    setDraft("");
    setSkill(null);
    setBudget("standard");
  }, [chats.newSessionId]);

  const patchSession = chats.patchSession;

  const renameSession = async () => {
    const title = renameValue.trim();
    if (!activeSession || !title) return;
    try {
      await patchSession(activeSession.id, { title });
      setRenameOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not rename the session");
    }
  };

  const archiveSession = chats.archiveSession;
  const restoreSession = chats.restoreSession;

  const keepBrief = (sessionId: string, brief: ConversationBrief) => {
    chats.setBrief(sessionId, () => brief);
  };

  /** Ask the service to fold the last answer into the brief, without waiting for it: the panel says "Updating…" meanwhile. */
  const refreshBrief = async (sessionId: string) => {
    setBriefBusy(sessionId);
    try {
      keepBrief(sessionId, await knowledgeApi.refreshBrief(sessionId));
    } catch (reason) {
      chats.setBrief(sessionId, (brief) => ({ goal: "", constraints: [], settled: [], open: [], edited: [], ...brief, error: reason instanceof Error ? reason.message : "The brief could not be updated" }));
    } finally {
      setBriefBusy((current) => current === sessionId ? null : current);
    }
  };

  const saveBrief = async (brief: ConversationBriefInput) => {
    if (!activeSession) return;
    const sessionId = activeSession.id;
    try {
      keepBrief(sessionId, await knowledgeApi.editBrief(sessionId, brief));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The brief could not be saved");
    }
  };

  const canAsk = ready && !activeSession?.archived && !sending && !sourceScopeEmpty;
  const send = async (again?: string, retry?: AskRetryOptions) => {
    const question = (again ?? draft).trim();
    if (!question || !canAsk || !activeSession) return;
    const skillUsed = again === undefined ? skill : retry?.skill ?? null;
    const budgeted = skillUsed !== null && (askSkills.find((item) => item.command === skillUsed)?.budgets.length ?? 0) > 1;
    if (again === undefined) { setDraft(""); setSkill(null); setBudget("standard"); }
    setError(null);
    const session = activeSession;
    const chosen = askChoice.model && modelMenu?.models.length ? { model: askChoice.model, effort: askChoice.effort } : {};
    const options = retry ?? { style: readAnswerStyle(), ...(webSearch.enabled ? { web: true } : {}), ...(skillUsed ? { skill: skillUsed } : {}), ...(budgeted ? { budget } : {}) };
    await sendAnswer(session.id, { content: question, ...chosen, ...options, selectedSourceIds: sourceIds, focusChapterId: null });
  };

  useImperativeHandle(chatActions, () => ({
    ask(text, submit) {
      if (submit && canAsk) {
        void send(text);
      } else {
        setDraft(text);
        window.requestAnimationFrame(() => scrollPane.current?.parentElement?.querySelector<HTMLTextAreaElement>(".composer textarea")?.focus());
      }
    },
  }));

  // Reading aloud goes on while its conversation is open, and stops when another one is.
  useEffect(() => { if (activeId) readAloud.focusSession(activeId); }, [activeId]);

  // Keep listening until the selected conversation has loaded.
  const loadedSessionId = activeSession?.id ?? null;
  useLayoutEffect(() => {
    if (loadedSessionId !== activeId) return;
    if (activeId && online) follow(activeId);
    else detach();
  }, [activeId, loadedSessionId, online, follow, detach]);

  // Research that asked the reader something first: the next message goes back to it, at the same depth.
  const lastMessage = activeSession?.messages.at(-1);
  useEffect(() => {
    const research = lastMessage?.role === "assistant" ? lastMessage.context.research : null;
    if (research?.state !== "asking") return;
    setSkill(lastMessage?.context.skill?.name ?? "research");
    if (research.budget) setBudget(research.budget);
  }, [lastMessage?.id]);

  // While research is still gathering, Stop writes the answer from what it found; once writing, it stops.
  const researching = sending && (pending?.skill === "research" || Boolean(live?.research));
  const stopLabel = researching && !live?.text ? "Stop and write" : "Stop";
  const branchFromMessage = async (message: SessionMessage) => {
    if (!activeSession || !online) {
      onNotify("Reconnect the knowledge service to create a durable branch.");
      return;
    }
    setBranchingMessageId(message.id);
    setError(null);
    try {
      await chats.branchSession(activeSession.id, message.id);
    } finally {
      setBranchingMessageId(null);
    }
  };

  const copyMessage = async (message: SessionMessage) => {
    try {
      await navigator.clipboard.writeText(message.content);
      onNotify("Answer copied to the clipboard.");
    } catch {
      setError("Clipboard access was unavailable. Select the answer text to copy it manually.");
    }
  };

  useEscape(() => {
    if (trace) setTrace(null);
    else setRenameOpen(false);
  }, Boolean(trace || renameOpen));

  const exportSession = async () => {
    if (!activeSession) return;
    const chapter = activeSession.focusChapterId
      ? (await knowledgeApi.topics(base.id).catch(() => [])).find((item) => item.id === activeSession.focusChapterId)
      : undefined;
    const scope = activeSession.selectedSourceIds.length
      ? `${activeSession.selectedSourceIds.length} explicitly selected sources`
      : "All indexed knowledge-base sources";
    const transcript = activeSession.messages.map((message) => {
      const citations = message.citations.length
        ? `\n\n### Citations\n${message.citations.map((citation, index) => `${index + 1}. ${citation.sourceTitle} — ${citation.locator}\n   > ${citation.quote}`).join("\n")}`
        : "";
      return `## ${message.role === "user" ? "You" : "Gunther"}\n\n${message.content}${citations}`;
    }).join("\n\n---\n\n");
    const markdown = `# ${activeSession.title}\n\n- Library: ${base.title}\n- Chapter focus: ${chapter?.title ?? "None"}\n- Source scope: ${scope}\n- Exported: ${new Date().toISOString()}\n\n${transcript || "_This session has no messages yet._"}\n`;
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${activeSession.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "gunther-session"}.md`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
    onNotify("Session exported with its citation trail.");
  };

  const selectedMessage = activeSession?.messages.find((message) => message.id === selectedMessageId) ?? null;
  const currentChat = activeSession?.id === activeId ? activeSession : null;

  return (
    <div className="session-workspace">
      <section className="conversation-pane" aria-label="Conversation">
        <header className="conversation-header">
          <div className="conversation-title">
            {renameOpen ? <form onSubmit={(event) => { event.preventDefault(); void renameSession(); }}>
              <input autoFocus value={renameValue} onChange={(event) => setRenameValue(event.target.value)} aria-label="Session title" />
              <button type="submit" aria-label="Save session title"><Check size={13} /></button>
              <button type="button" onClick={() => setRenameOpen(false)} aria-label="Cancel renaming"><X size={13} /></button>
            </form> : <strong>{activeSession?.title ?? (loading ? "Opening session…" : "Conversation unavailable")}</strong>}
            <span><i className={online ? "is-online" : "is-offline"} />{!online ? "Knowledge service unavailable" : activeSession?.archived ? "Archived session · read only" : activeSession ? "Saved to local knowledge service" : ""}{activeSession?.parentSessionId && <><b>·</b><GitBranch size={10} />Branch lineage preserved</>}</span>
          </div>
          <div className="conversation-actions">
            {activeSession && <details className="ask-brief" onKeyDown={(event) => { if (event.key === "Escape" && event.currentTarget.open) { event.stopPropagation(); event.currentTarget.open = false; } }}>
              <summary className="gx-btn gx-btn-quiet gx-btn-sm">Brief</summary>
              <div className="ask-brief-panel">
                <BriefPanel brief={activeSession.brief} updating={briefBusy === activeId} readOnly={!online || activeSession.archived}
                  hasMessages={activeSession.messages.length > 0} citations={selectedMessage?.citations ?? []}
                  onSave={(brief) => void saveBrief(brief)} onRetry={() => void refreshBrief(activeSession.id)} onOpenCitation={onCite} />
              </div>
            </details>}

            <PopMenu label="Chat actions" triggerLabel="Chat actions" triggerClass="gx-icon-button" trigger={<MoreHorizontal size={16} />} items={[
              { label: "New chat", icon: Plus, disabled: !online || loading, onSelect: () => void chats.createSession() },
              { label: "Rename", icon: SquarePen, disabled: !online || !currentChat || currentChat.archived, onSelect: () => {
                if (currentChat) { setRenameValue(currentChat.title); setRenameOpen(true); }
              } },
              { label: "Export Markdown", icon: Download, disabled: !currentChat, onSelect: () => void exportSession() },
              { label: currentChat?.archived ? "Restore" : "Archive", icon: Archive, disabled: !online || !currentChat, onSelect: () => {
                if (currentChat) void (currentChat.archived ? restoreSession(currentChat) : archiveSession(currentChat));
              } },
            ]} />
          </div>
        </header>
        <div className="conversation-scroll" ref={scrollPane} onScroll={(event) => {
          if (!visible) return;
          const pane = event.currentTarget;
          scrollOffset.current = pane.scrollTop;
          followTail.current = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 80;
        }}>
          {!online && <div className="conversation-error" role="status"><CircleAlert size={14} /><span>Reconnect to the knowledge service to continue.</span><button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={sendReads.some((read) => reads[read]?.pending)} onClick={() => { setError(null); if (!chats.online) void chats.load(); loadWorkspace(); }}>Reconnect</button></div>}
          {startupReads.map((read) => {
            const status = reads[read];
            if (!status?.error) return null;
            return <div key={read} className="conversation-error" role="status">
              <CircleAlert size={14} /><span title={status.error.message}>Could not load {readLabels[read]}.</span>
              <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" aria-label={`Retry ${readLabels[read]}`} disabled={status.pending} onClick={() => loadWorkspace(read)}>Retry</button>
            </div>;
          })}
          {error && <div className="conversation-error" role="alert"><CircleAlert size={14} /><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError(null)}><X size={12} /></button></div>}
          {online && !loading && !sending && currentChat?.messages.length === 0
            ? <LibraryWelcome key={currentChat.id} base={base} visible={visible && !currentChat.archived} canAsk={canAsk} onAsk={(question) => void send(question)} />
            : <h1 className="gx-sr-only">Ask {base.title}</h1>}
          {activeSession?.messages.map((message) => <ConversationMessage key={message.id}
            message={message} libraryId={base.id} visible={visible}
            retryDisabled={!ready || sending || sourceScopeEmpty || Boolean(activeSession?.archived)}
            onRetry={() => void send(message.content, message.context.askFailure?.options)}
            onEdit={() => setDraft(message.content)} onCite={(_citation, index) => onInspectCitation(message, index)} selected={selectedMessageId === message.id}
            branching={branchingMessageId === message.id} onSelect={() => setSelectedMessageId(message.id)}
            onSources={() => onInspectSources(message)}
            onCopy={() => void copyMessage(message)} onBranch={() => void branchFromMessage(message)}
            onTrace={traces.on ? () => setTrace({ sessionId: activeSession.id, messageId: message.id }) : undefined} />)}
          {sending && asking && <article className="conversation-message role-user"><div className="message-author"><span className="user-mark"><UserRound size={13} /></span><span>You</span></div><div className="message-body"><MessageContent content={asking} /></div></article>}
          {sending && (live ? <LiveAnswer state={live} /> : <div className="thinking-row"><span className="assistant-mark"><BrandMark size={14} busy /></span><span><i /><i /><i /></span><small>Working out what to look up…</small></div>)}
        </div>
        <Composer picker={<ModelPicker menu={modelMenu} choice={askChoice} onChange={chooseModel} disabled={sending || Boolean(activeSession?.archived)} />} web={{ available: webSearch.available, enabled: webSearch.enabled, onChange: webSearch.setEnabled }} value={draft} sending={sending} sourceCount={sourceIds.length} sourceScopeEmpty={sourceScopeEmpty} readOnly={Boolean(activeSession?.archived)} ready={ready} allowDraft={activeSession !== null} offline={!online} skill={skill} onSkillChange={setSkill} budget={budget} onBudgetChange={setBudget} stopLabel={stopLabel} onChange={setDraft} onSend={() => void send()} onStop={stopResponse} onSources={onFocusSources} />
      </section>
      {trace && visible && createPortal(<div className="ask-trace-overlay" role="dialog" aria-modal="true" aria-label="Trace" onClick={() => setTrace(null)}>
        <aside className="ask-trace-sheet" onClick={(event) => event.stopPropagation()}>
          <header><strong>Trace</strong><button type="button" className="gx-icon-button" autoFocus aria-label="Close trace" onClick={() => setTrace(null)}><X size={16} /></button></header>
          <AnswerTrace {...trace} onCopy={(label, value) => void navigator.clipboard.writeText(value).then(() => onNotify(`${label} copied.`), () => onNotify(`${label} could not be copied.`))} />
        </aside>
      </div>, document.body)}
    </div>
  );
}
