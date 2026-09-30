import type {
  ConversationCitation,
  ConversationContext,
  KnowledgeSession,
  KnowledgeSessionSummary,
  KnowledgeUnit,
  SessionMessage,
  SourceSummary,
} from "@gunther/contracts";
import {
  Archive,
  ArrowRight,
  ArrowUp,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Clock3,
  Copy,
  Download,
  FileSearch,
  FileText,
  Filter,
  FolderOpen,
  GitBranch,
  Globe2,
  Info,
  Library,
  MessageSquareText,
  Mic,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Paperclip,
  Pin,
  Plus,
  Quote,
  Search,
  ShieldCheck,
  Sparkles,
  Square,
  SquarePen,
  UserRound,
  X,
} from "lucide-react";
import { useAutosize } from "../services/useAutosize";
import { InterruptedNote, endsWithInterrupted, interruptedQuestion, isInterrupted } from "./interrupted";
import { useDictatedField } from "../services/useDictatedField";
import { withShortcut } from "../shortcuts/shortcuts";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { KnowledgeBase, KnowledgeSource } from "../atlas";
import { knowledgeApi } from "../api";
import { lastAnswerChoice, readAnswerStyle, usableChoice, useDeviceChoice, useModelMenu, useWebSearch, type AskChoice } from "../models/askModel";
import { StylePicker } from "../models/StylePicker";
import { AgentSteps, AnswerBody, LiveAnswer, citationNumbers } from "./AnswerBody";
import { useLiveAnswer } from "./liveAnswer";
import { ModelPicker } from "../models/ModelPicker";
import { BrandMark } from "../design/BrandMark";
import { LibraryGlyph } from "../design/LibraryGlyph";
import { EvidencePanel } from "../components/evidence/EvidencePanel";
import "../knowledge.css";
import "../session.css";

interface SessionWorkspaceProps {
  selectedChapterId: string;
  base: KnowledgeBase;
  onAdd: () => void;
  onNotify: (message: string) => void;
  /** Open a source on its own page. */
  onOpenSource?: (sourceId: string) => void;
}

type InspectorTab = "context" | "sources";
type ScopedKnowledgeSource = KnowledgeSource & { indexed: boolean };

const emptyContext: ConversationContext = {
  sourcesConsidered: 0,
  assertionsConsidered: 0,
  verifiedAssertions: 0,
  retrievalMode: "all",
  responderMode: "local",
};

function localSession(base: KnowledgeBase): KnowledgeSession {
  const now = new Date().toISOString();
  return {
    id: `local-${base.id}`,
    knowledgeBaseId: base.id,
    title: "New session",
    summary: "",
    focusChapterId: null,
    selectedSourceIds: [],
    pinned: false,
    archived: false,
    parentSessionId: null,
    branchedFromMessageId: null,
    messageCount: 0,
    createdAt: now,
    updatedAt: now,
    messages: [],
  };
}

function relativeTime(value: string) {
  const elapsed = Date.now() - new Date(value).getTime();
  if (elapsed < 60_000) return "Now";
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)}m`;
  if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)}h`;
  if (elapsed < 604_800_000) return `${Math.floor(elapsed / 86_400_000)}d`;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(value));
}

function sourceKindLabel(kind: KnowledgeSource["kind"]) {
  return ({ guide: "Guide", documentation: "Docs", ontology: "Ontology", dataset: "Dataset" } as const)[kind];
}

function renderInline(value: string, onCitation?: () => void) {
  return value.split(/(\*\*[^*]+\*\*|\[\d+\])/g).filter(Boolean).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (/^\[\d+\]$/.test(part)) return <sup key={index}>{onCitation ? <button onClick={(event) => { event.stopPropagation(); onCitation(); }} aria-label={`Inspect citation ${part.slice(1, -1)}`}>{part}</button> : part}</sup>;
    return <span key={index}>{part}</span>;
  });
}

/** A reply that searched nothing and cited nothing needed none: small talk, or a request about an earlier answer. */
function noSourcesNeeded(context: ConversationContext): boolean {
  if (context.intent) return context.intent === "chat" || context.intent === "followup" || context.intent === "clarify";
  return !(context.steps?.length);
}

function MessageContent({ content, onCitation }: { content: string; onCitation?: () => void }) {
  return <>{content.split("\n").map((line, index) => line.trim() ? <p key={index}>{renderInline(line, onCitation)}</p> : <span className="message-spacer" key={index} />)}</>;
}

function SessionRow({
  session,
  active,
  onOpen,
  onPin,
  onArchive,
  archived = false,
}: {
  session: KnowledgeSessionSummary;
  active: boolean;
  onOpen: () => void;
  onPin: () => void;
  onArchive: () => void;
  archived?: boolean;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <div className={`session-row ${active ? "is-active" : ""}`} onKeyDown={(event) => { if (event.key === "Escape") setMenuOpen(false); }}>
      <button className="session-row-main" onClick={onOpen} aria-current={active ? "page" : undefined}>
        <MessageSquareText size={14} />
        <span><strong>{session.title}</strong><small>{session.messageCount ? `${Math.ceil(session.messageCount / 2)} ${session.messageCount === 2 ? "turn" : "turns"}` : "Empty session"}</small></span>
        <time>{relativeTime(session.updatedAt)}</time>
      </button>
      {session.pinned && <Pin className="session-pin-mark" size={10} fill="currentColor" />}
      <button className="session-more" aria-label={`Actions for ${session.title}`} aria-expanded={menuOpen} aria-haspopup="menu" onClick={() => setMenuOpen((value) => !value)}><MoreHorizontal size={14} /></button>
      {menuOpen && <div className="session-menu" role="menu" onMouseLeave={() => setMenuOpen(false)}>
        <button role="menuitem" onClick={() => { onPin(); setMenuOpen(false); }}><Pin size={12} />{session.pinned ? "Unpin" : "Pin session"}</button>
        <button role="menuitem" onClick={() => { onArchive(); setMenuOpen(false); }}><Archive size={12} />{archived ? "Restore session" : "Archive"}</button>
      </div>}
    </div>
  );
}

function SessionsSidebar({
  base,
  indexedCount,
  referenceCount,
  sessions,
  archivedSessions,
  showArchived,
  activeId,
  loading,
  query,
  onQuery,
  onNew,
  onOpen,
  onPin,
  onArchive,
  onToggleArchived,
  onRestore,
}: {
  base: KnowledgeBase;
  indexedCount: number;
  referenceCount: number;
  sessions: KnowledgeSessionSummary[];
  archivedSessions: KnowledgeSessionSummary[];
  showArchived: boolean;
  activeId: string | null;
  loading: boolean;
  query: string;
  onQuery: (value: string) => void;
  onNew: () => void;
  onOpen: (id: string) => void;
  onPin: (session: KnowledgeSessionSummary) => void;
  onArchive: (session: KnowledgeSessionSummary) => void;
  onToggleArchived: () => void;
  onRestore: (session: KnowledgeSessionSummary) => void;
}) {
  const filtered = sessions.filter((session) => `${session.title} ${session.summary}`.toLowerCase().includes(query.toLowerCase()));
  const pinned = filtered.filter((session) => session.pinned);
  const recent = filtered.filter((session) => !session.pinned);
  const filteredArchived = archivedSessions.filter((session) => `${session.title} ${session.summary}`.toLowerCase().includes(query.toLowerCase()));
  return (
    <aside className="sessions-sidebar" aria-label="Session history">
      <div className="sessions-heading">
        <span className={`base-dot color-${base.color}`} />
        <span><small>Library</small><strong>{base.title}</strong></span>
      </div>
      <button className="new-session-button" onClick={onNew}><SquarePen size={15} /><span>New session</span><kbd>⌘N</kbd></button>
      <label className="session-search"><Search size={13} /><input value={query} onChange={(event) => onQuery(event.target.value)} placeholder="Search sessions" aria-label="Search sessions" />{query && <button onClick={() => onQuery("")} aria-label="Clear search"><X size={11} /></button>}</label>
      <div className="session-list" aria-busy={loading}>
        {loading && <div className="session-list-loading"><i /><i /><i /></div>}
        {!loading && pinned.length > 0 && <section><header><span>Pinned</span><small>{pinned.length}</small></header>{pinned.map((session) => <SessionRow key={session.id} session={session} active={activeId === session.id} onOpen={() => onOpen(session.id)} onPin={() => onPin(session)} onArchive={() => onArchive(session)} />)}</section>}
        {!loading && <section><header><span>Recent</span><small>{recent.length}</small></header>{recent.map((session) => <SessionRow key={session.id} session={session} active={activeId === session.id} onOpen={() => onOpen(session.id)} onPin={() => onPin(session)} onArchive={() => onArchive(session)} />)}</section>}
        {!loading && showArchived && <section className="archived-session-section"><header><span>Archived</span><small>{filteredArchived.length}</small></header>{filteredArchived.map((session) => <SessionRow key={session.id} session={session} archived active={activeId === session.id} onOpen={() => onOpen(session.id)} onPin={() => onPin(session)} onArchive={() => onRestore(session)} />)}{filteredArchived.length === 0 && <p>No archived sessions yet.</p>}</section>}
        {!loading && filtered.length === 0 && !(showArchived && filteredArchived.length) && <div className="no-sessions"><MessageSquareText size={19} /><strong>{query ? "No matching sessions" : "Start a line of inquiry"}</strong><span>{query ? "Try a different phrase." : "Each session keeps its own questions, context, and evidence."}</span></div>}
      </div>
      <button className={`archive-toggle ${showArchived ? "is-open" : ""}`} onClick={onToggleArchived}><Archive size={12} /><span>{showArchived ? "Hide archive" : "View archive"}</span>{archivedSessions.length > 0 && <small>{archivedSessions.length}</small>}<ChevronDown size={11} /></button>
      <footer className="sidebar-scope-summary"><Library size={13} /><span><strong>{indexedCount} indexed · {referenceCount} references</strong><small>{base.chapterCount} chapters · {base.progress}% shaped</small></span><ChevronDown size={12} /></footer>
    </aside>
  );
}

function WelcomePanel({ base, onPrompt }: { base: KnowledgeBase; onPrompt: (value: string) => void }) {
  const prompts = [
    { icon: FileSearch, label: "Synthesize", value: `What are the strongest claims in ${base.title}, and what evidence supports them?` },
    { icon: GitBranch, label: "Find tensions", value: "Where do the current sources disagree or leave important gaps?" },
    { icon: ShieldCheck, label: "Audit", value: "Which conclusions are provisional, and what should I verify next?" },
  ];
  return (
    <div className="chat-welcome">
      <div className="welcome-glyph"><LibraryGlyph base={base} size="lg" /></div>
      <span className="welcome-eyebrow"><Sparkles size={12} />New grounded session</span>
      <h1>Ask the knowledge,<br />not just the model.</h1>
      <p>{base.question}</p>
      <div className="welcome-prompts">{prompts.map(({ icon: Icon, label, value }) => <button key={label} onClick={() => onPrompt(value)}><Icon size={15} /><span><strong>{label}</strong><small>{value}</small></span><ArrowUp size={13} /></button>)}</div>
      <div className="grounding-promise"><ShieldCheck size={13} /><span>Answers stay inside the chosen source scope. Missing evidence remains visible.</span></div>
    </div>
  );
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
    {notes.length > 0 && <small className="message-model-note">· {notes.join(" ")}</small>}
  </span>;
}

function ConversationMessage({ message, retryDisabled, onRetry, onEdit, onCite, selected, promoting, promoted, branching, onSelect, onCopy, onPromote, onBranch }: { message: SessionMessage; retryDisabled: boolean; onRetry: () => void; onEdit: () => void; onCite: (citation: ConversationCitation, index: number) => void; selected: boolean; promoting: boolean; promoted: boolean; branching: boolean; onSelect: () => void; onCopy: () => void; onPromote: () => void; onBranch: () => void }) {
  const isAssistant = message.role === "assistant";
  // Small talk and follow-ups on the conversation itself need no sources.
  const conversational = noSourcesNeeded(message.context);
  return (
    <article className={`conversation-message role-${message.role} ${selected ? "is-selected" : ""} ${isInterrupted(message) ? "is-interrupted" : ""}`} onClick={isAssistant ? onSelect : undefined}>
      <div className="message-author">{isAssistant ? <span className="assistant-mark"><BrandMark size={14} /></span> : <span className="user-mark"><UserRound size={13} /></span>}<span>{isAssistant ? "Gunther" : "You"}</span><time>{new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(message.createdAt))}</time></div>
      {isAssistant && <AgentSteps steps={message.context.steps ?? []} />}
      <div className="message-body">{isAssistant ? <AnswerBody content={message.content} numbers={citationNumbers(message.citations)} onCitation={(index) => { onSelect(); const citation = message.citations[index]; if (citation) onCite(citation, index); }} /> : <MessageContent content={message.content} />}</div>
      {message.context.interrupted && <InterruptedNote reason={message.context.interrupted} disabled={retryDisabled} onRetry={onRetry} onEdit={onEdit} />}
      {isAssistant && message.context.modelError && <p className="message-model-error" role="note"><CircleAlert size={13} /><span>{message.context.modelError}{message.citations.length > 0 && " The quotes stand in for its answer."}</span></p>}
      {isAssistant && message.context.reasoning && <details className="message-thinking" onClick={(event) => event.stopPropagation()}><summary><ChevronRight size={12} />Thinking</summary><pre>{message.context.reasoning}</pre></details>}
      {isAssistant && <footer className="message-footer">
        <div className="message-actions">{message.citations.length > 0 ? <button className="citation-count" onClick={(event) => { event.stopPropagation(); onSelect(); }}><Quote size={12} />{message.citations.length} {message.citations.length === 1 ? "source" : "sources"}</button> : conversational ? null : <span className="no-citation-state"><CircleAlert size={12} />Not from your sources</span>}<button className="copy-answer" onClick={(event) => { event.stopPropagation(); onCopy(); }}><Copy size={12} />Copy</button><button className="branch-answer" disabled={branching} onClick={(event) => { event.stopPropagation(); onBranch(); }}><GitBranch size={12} />{branching ? "Branching…" : "Branch"}</button>{!(conversational && message.citations.length === 0) && <button className={`promote-answer ${promoted ? "is-promoted" : ""}`} disabled={promoting || promoted || message.citations.length === 0} title={message.citations.length === 0 ? "Add or retrieve supporting evidence before proposing this answer as knowledge." : undefined} onClick={(event) => { event.stopPropagation(); onPromote(); }}><Sparkles size={12} />{promoting ? "Creating proposal…" : promoted ? "Proposal created" : message.citations.length === 0 ? "Needs evidence" : "Propose as knowledge"}</button>}</div>
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
  chapterTitle,
  onChange,
  onSend,
  onStop,
  onSources,
  readOnly,
  ready,
}: {
  /** The model and effort chips. */
  picker?: ReactNode;
  /** The Web switch: whether web search is set up, and whether it is on for questions. */
  web?: { available: boolean; enabled: boolean; onChange: (next: boolean) => void };
  value: string;
  sending: boolean;
  sourceCount: number;
  chapterTitle: string | undefined;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onSources: () => void;
  readOnly: boolean;
  ready: boolean;
}) {
  const composerDisabled = !ready || readOnly;
  const dictation = useDictatedField(value, onChange, { enabled: !composerDisabled });
  const field = useRef<HTMLTextAreaElement>(null);
  useAutosize(field, value, 220);
  const submit = () => { dictation.discard(); onSend(); };
  return (
    <div className="composer-wrap">
      <div className="composer">
        <textarea ref={field} value={value} rows={1} disabled={composerDisabled} onChange={(event) => onChange(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); submit(); } }} placeholder={!ready ? "Opening a durable session…" : readOnly ? "Restore this session to continue the conversation." : "Ask, compare, challenge, or trace a claim…"} aria-label="Message Gunther" onFocus={dictation.claim} />
        <div className="composer-toolbar">
          <div><button title="Attach sources" aria-label="Attach sources" onClick={onSources}><Paperclip size={15} /></button><button className="scope-chip" onClick={onSources}><FolderOpen size={13} /><span>{sourceCount ? `${sourceCount} selected` : "All sources"}</span><ChevronDown size={11} /></button>{chapterTitle && <span className="chapter-chip"><BookOpen size={12} />{chapterTitle}</span>}{picker}{web && <button type="button" className={`web-toggle ${web.enabled ? "is-on" : ""}`} disabled={!web.available} aria-pressed={web.enabled} onClick={() => web.onChange(!web.enabled)} title={web.available ? (web.enabled ? "Ask may search the web. Click to keep it to your library." : "Ask is limited to your library. Click to let it search the web.") : "Web search is not set up. Add a Tavily key in Settings."}><Globe2 size={13} /><span>Web</span></button>}</div>
          <div className="composer-actions"><button type="button" className={`mic-button ${dictation.dictating ? "is-on" : ""}`} disabled={composerDisabled || sending} onClick={dictation.toggle} aria-pressed={dictation.listening} aria-label={dictation.listening ? "Stop voice input" : "Voice input"} title={dictation.error ?? withShortcut(dictation.listening ? "Stop voice input" : "Speak instead of typing", "dictate")}><Mic size={15} /></button><button className={`send-button ${sending ? "is-stop" : ""}`} disabled={composerDisabled || (!sending && !value.trim())} onClick={sending ? onStop : submit} aria-label={sending ? "Stop waiting for response" : "Send message"}>{sending ? <Square size={12} fill="currentColor" /> : <ArrowUp size={16} />}</button></div>
        </div>
      </div>
      <p className="composer-note" role={dictation.error ? "alert" : undefined}>{dictation.error ? dictation.error : dictation.dictating ? (dictation.state === "finishing" ? "Finishing…" : "Listening… press the mic or ⌘⇧M to stop") : !ready ? "Preparing a durable conversation before accepting questions…" : readOnly ? "Archived sessions are read-only. Restore this session from its options to continue." : "Gunther can be wrong. Verify important conclusions in the cited source."}</p>
    </div>
  );
}

function CitationCard({ citation, index, onOpen }: { citation: ConversationCitation; index: number; onOpen: () => void }) {
  const web = citation.kind === "web";
  return (
    <button className={`citation-card ${web ? "is-web" : ""}`} onClick={onOpen} aria-label={`Show evidence: ${citation.sourceTitle}`}>
      <header><span>{String(citation.ref ?? index + 1).padStart(2, "0")}</span><small>{web ? "web" : citation.status}</small><strong>{web ? citation.locator : citation.assertionId ? `${Math.round(citation.confidence * 100)}%` : "Source"}</strong></header>
      <h3>{citation.sourceTitle}</h3>
      <blockquote>“{citation.quote}”</blockquote>
      {web
        ? <footer><span><Globe2 size={11} />Outside your library</span><span className="citation-status"><i />Show passage</span></footer>
        : <footer><span><FileText size={11} />{citation.locator}</span><span className={`citation-status is-${citation.status}`}><i />{citation.status === "verified" ? "Trusted" : "Review"}</span></footer>}
    </button>
  );
}

/** The sources a conversation has cited so far, each once, under the number it keeps in every answer. */
export function conversationSources(session: KnowledgeSession | null): ConversationCitation[] {
  const held = new Map<number, ConversationCitation>();
  for (const message of session?.messages ?? []) {
    for (const citation of message.citations) {
      if (citation.ref != null && !held.has(citation.ref)) held.set(citation.ref, citation);
    }
  }
  return [...held.values()].sort((a, b) => (a.ref ?? 0) - (b.ref ?? 0));
}

function ContextInspector({
  base,
  sources,
  session,
  knowledgeUnits,
  selectedMessage,
  tab,
  sourceQuery,
  collapsed,
  onTab,
  onSourceQuery,
  onToggleSource,
  onUseAll,
  onAdd,
  onOpenSource,
  onOpenUnit,
  onCollapse,
  readOnly,
}: {
  base: KnowledgeBase;
  sources: ScopedKnowledgeSource[];
  session: KnowledgeSession | null;
  knowledgeUnits: KnowledgeUnit[];
  selectedMessage: SessionMessage | null;
  tab: InspectorTab;
  sourceQuery: string;
  collapsed: boolean;
  onTab: (tab: InspectorTab) => void;
  onSourceQuery: (value: string) => void;
  onToggleSource: (id: string) => void;
  onUseAll: () => void;
  onAdd: () => void;
  onOpenSource: (id: string, citation?: ConversationCitation) => void;
  onOpenUnit: (unit: KnowledgeUnit) => void;
  onCollapse: () => void;
  readOnly: boolean;
}) {
  if (collapsed) return <button className="inspector-reopen" onClick={onCollapse} aria-label="Open context panel"><PanelRightOpen size={16} /></button>;
  const selectedIds = session?.selectedSourceIds ?? [];
  const pooledSources = conversationSources(session);
  const filteredSources = sources.filter((source) => `${source.title} ${source.publisher}`.toLowerCase().includes(sourceQuery.toLowerCase()));
  const indexedCount = sources.filter((source) => source.indexed).length;
  const referenceCount = sources.length - indexedCount;
  const context = selectedMessage?.context ?? emptyContext;
  return (
    <aside className="context-inspector" aria-label="Session evidence and sources">
      <header className="inspector-heading"><span><small>Session intelligence</small><strong>{tab === "context" ? "Evidence context" : "Source scope"}</strong></span><button onClick={onCollapse} aria-label="Close context panel"><PanelRightClose size={15} /></button></header>
      <nav className="inspector-tabs"><button className={tab === "context" ? "is-active" : ""} onClick={() => onTab("context")}>Context</button><button className={tab === "sources" ? "is-active" : ""} onClick={() => onTab("sources")}>Sources <span>{selectedIds.length || indexedCount}</span></button></nav>
      {tab === "context" ? <div className="inspector-scroll">
        {selectedMessage ? <>
          <section className="answer-scope"><span className="section-label">Answer scope</span><div className="scope-metrics"><span><strong>{context.assertionsConsidered}</strong><small>claims scanned</small></span><span><strong>{context.sourcesConsidered}</strong><small>sources searched</small></span><span><strong>{context.verifiedAssertions}</strong><small>trusted scanned</small></span></div><p><Info size={12} />This is the retrieval snapshot for the selected answer—not the current library state.</p></section>
          <section className="citation-section"><header><span className="section-label">Sources cited</span><small>{selectedMessage.citations.length} {selectedMessage.citations.length === 1 ? "source" : "sources"}</small></header>{selectedMessage.citations.length ? selectedMessage.citations.map((citation, index) => <CitationCard key={citation.id} citation={citation} index={index} onOpen={() => onOpenSource(citation.sourceId, citation)} />) : <div className="empty-citations"><CircleAlert size={20} /><strong>{noSourcesNeeded(context) ? "No sources needed" : "Nothing cited"}</strong><span>{noSourcesNeeded(context) ? "This reply came from the conversation itself." : "Nothing in the searches supported a citation, so this answer is not grounded."}</span></div>}</section>
          {pooledSources.length > selectedMessage.citations.length && <section className="conversation-sources"><header><span className="section-label">All sources in this conversation</span><small>{pooledSources.length}</small></header><ol>{pooledSources.map((citation) => <li key={citation.id} className={selectedMessage.citations.some((item) => item.ref === citation.ref) ? "is-cited" : ""}><i>{citation.ref}</i><button type="button" onClick={() => onOpenSource(citation.sourceId, citation)} title={citation.quote}>{citation.kind === "web" && <Globe2 size={11} />}<span>{citation.sourceTitle}</span></button></li>)}</ol></section>}
        </> : <>
          <section className="context-overview"><div className="context-glyph"><LibraryGlyph base={base} size="lg" /></div><span className="section-label">Current library</span><h2>{base.title}</h2><p>{base.description}</p></section>
          <section className="knowledge-health"><header><span className="section-label">Knowledge health</span><strong>{base.progress}%</strong></header><div><i style={{ width: `${base.progress}%` }} /></div><ul><li><Check size={12} />{base.chapters.filter((chapter) => chapter.status === "grounded").length} grounded chapters</li><li><Clock3 size={12} />{base.chapters.filter((chapter) => chapter.status !== "grounded").length} chapters still growing</li><li><FileText size={12} />{indexedCount} indexed {indexedCount === 1 ? "source" : "sources"} · {referenceCount} curated {referenceCount === 1 ? "reference" : "references"}</li></ul></section>
          {knowledgeUnits.length > 0 && <section className="accepted-units"><header><span className="section-label">Knowledge units</span><small>{knowledgeUnits.length} {knowledgeUnits.length === 1 ? "unit" : "units"}</small></header>{knowledgeUnits.slice(0, 3).map((unit) => <button className="unit-origin" key={unit.id} onClick={() => onOpenUnit(unit)} aria-label={`Open source session for ${unit.title}`}><span><ShieldCheck size={12} /><strong>{unit.title}</strong><small>{unit.evidenceCount} {unit.evidenceCount === 1 ? "citation" : "citations"} · {unit.revisionCount} {unit.revisionCount === 1 ? "revision" : "revisions"} · {unit.status}</small></span><p>{unit.content.replace(/\*\*/g, "").replace(/\s+/g, " ")}</p><ArrowRight size={11} /></button>)}</section>}
          <div className="inspector-tip"><Sparkles size={14} /><span><strong>Inspect any answer</strong><small>Select a response to see exactly what Gunther searched and cited.</small></span></div>
        </>}
      </div> : <div className="inspector-scroll source-scope-panel">
        <div className="scope-policy"><ShieldCheck size={15} /><span><strong>Retrieval boundary</strong><small>Choose all sources for discovery, or isolate a smaller evidence set for careful comparison.</small></span></div>
        <button className={`all-sources-option ${selectedIds.length === 0 ? "is-active" : ""}`} disabled={readOnly} onClick={onUseAll}><span><Library size={14} /></span><span><strong>Use all indexed sources</strong><small>{indexedCount} searchable · {referenceCount} reference only</small></span>{selectedIds.length === 0 && <Check size={14} />}</button>
        <label className="source-filter"><Filter size={12} /><input value={sourceQuery} onChange={(event) => onSourceQuery(event.target.value)} placeholder="Filter sources" aria-label="Filter session sources" /></label>
        <div className="scope-source-list">{filteredSources.map((source) => {
          const selected = selectedIds.includes(source.id);
          return <button key={source.id} className={`${selected ? "is-selected" : ""} ${!source.indexed ? "is-reference-only" : ""}`} disabled={!source.indexed || readOnly} title={!source.indexed ? "Reference metadata only. Import this source before using it in answers." : readOnly ? "Restore this session before changing its source scope." : undefined} onClick={() => onToggleSource(source.id)}><span className="source-check">{selected ? <Check size={11} /> : !source.indexed ? <span>—</span> : null}</span><span><small>{sourceKindLabel(source.kind)} · {source.publisher} · {source.indexed ? "indexed" : "reference only"}</small><strong>{source.title}</strong><em>{source.scope}</em></span></button>;
        })}</div>
        <button className="add-source-inline" onClick={() => onAdd()}><Plus size={13} />Add a new source</button>
      </div>}
    </aside>
  );
}

const DOCK_QUERY = "(min-width: 1100px)";
const HISTORY_CHOICE_KEY = "gunther:ask-history";

function readHistoryChoice(): "open" | "closed" | null {
  try {
    const stored = window.localStorage.getItem(HISTORY_CHOICE_KEY);
    return stored === "open" || stored === "closed" ? stored : null;
  } catch {
    return null;
  }
}

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    update();
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, [query]);
  return matches;
}

export function SessionWorkspace({ base, selectedChapterId, onAdd, onNotify, onOpenSource }: SessionWorkspaceProps) {
  const [sessions, setSessions] = useState<KnowledgeSessionSummary[]>([]);
  const [archivedSessions, setArchivedSessions] = useState<KnowledgeSessionSummary[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [activeSession, setActiveSession] = useState<KnowledgeSession | null>(null);
  const [indexedSources, setIndexedSources] = useState<SourceSummary[]>([]);
  const [knowledgeUnits, setKnowledgeUnits] = useState<KnowledgeUnit[]>([]);
  const [selectedMessageId, setSelectedMessageId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [sessionQuery, setSessionQuery] = useState("");
  const [sourceQuery, setSourceQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [promotingMessageId, setPromotingMessageId] = useState<string | null>(null);
  const [branchingMessageId, setBranchingMessageId] = useState<string | null>(null);
  const [proposalMessageIds, setProposalMessageIds] = useState<Set<string>>(new Set());
  const [online, setOnline] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("context");
  // Open by default so the sources behind an answer are in view; the reader's choice is kept.
  const [inspectorCollapsed, setInspectorCollapsed] = useState(() => {
    if (window.innerWidth <= 980) return true;
    try { return window.localStorage.getItem("gunther:inspector-collapsed") === "1"; } catch { return false; }
  });
  useEffect(() => {
    if (window.innerWidth <= 980) return;
    try { window.localStorage.setItem("gunther:inspector-collapsed", inspectorCollapsed ? "1" : "0"); } catch { /* the choice just is not remembered */ }
  }, [inspectorCollapsed]);
  // The question just sent, shown in the conversation while it is being answered.
  const [asking, setAsking] = useState<string | null>(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [sessionOptionsOpen, setSessionOptionsOpen] = useState(false);
  const [mobileSessionsOpen, setMobileSessionsOpen] = useState(false);
  // The conversation list docks beside the chat when there is room and something to list;
  // someone's own fold or expand always wins.
  const roomForHistory = useMediaQuery(DOCK_QUERY);
  const [historyChoice, setHistoryChoice] = useState<"open" | "closed" | null>(readHistoryChoice);
  const bootstrapRequest = useRef<{ baseId: string; promise: Promise<{ sessions: KnowledgeSessionSummary[]; detail: KnowledgeSession }> } | null>(null);
  const scrollPane = useRef<HTMLDivElement>(null);
  /** Whether the reader is at the bottom; only then does new text pull the view along. */
  const followTail = useRef(true);
  const responseController = useRef<AbortController | null>(null);
  const localResponseTimer = useRef<number | null>(null);
  const pendingQuestion = useRef("");
  const [evidence, setEvidence] = useState<{ citation: ConversationCitation; index?: number } | null>(null);
  // Which model answers next, and how hard it thinks: what this conversation picked,
  // else the model of its last answer, else this device's last choice, else the Ask default.
  const { menu: modelMenu } = useModelMenu();
  const webSearch = useWebSearch();
  const liveAnswer = useLiveAnswer();
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

  const sortSessions =useCallback((items: KnowledgeSessionSummary[]) => [...items].sort((a, b) => Number(b.pinned) - Number(a.pinned) || new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()), []);

  const loadSession = useCallback(async (id: string) => {
    setActiveId(id);
    setSelectedMessageId(null);
    setError(null);
    if (id.startsWith("local-")) return;
    try {
      const loaded = await knowledgeApi.session(id);
      setActiveSession(loaded);
      setOnline(true);
      const lastAnswer = [...loaded.messages].reverse().find((message) => message.role === "assistant");
      setSelectedMessageId(lastAnswer?.id ?? null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not open this session");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const bootstrap = async (): Promise<{ sessions: KnowledgeSessionSummary[]; detail: KnowledgeSession }> => {
      let loaded = await knowledgeApi.sessions(base.id);
      if (!loaded.length) {
        const created = await knowledgeApi.createSession(base.id, { focusChapterId: null, selectedSourceIds: [] });
        loaded = [created];
      }
      const remembered = window.localStorage.getItem(`gunther:active-session:${base.id}`);
      const first = loaded.find((session) => session.id === remembered) ?? loaded[0]!;
      const detail = await knowledgeApi.session(first.id);
      return { sessions: loaded, detail };
    };
    const initialize = async () => {
      setLoading(true);
      setError(null);
      try {
        if (!bootstrapRequest.current || bootstrapRequest.current.baseId !== base.id) {
          bootstrapRequest.current = { baseId: base.id, promise: bootstrap() };
        }
        const { sessions: loaded, detail } = await bootstrapRequest.current.promise;
        if (cancelled) return;
        setOnline(true);
        setSessions(sortSessions(loaded));
        setActiveId(detail.id);
        setActiveSession(detail);
        const lastAnswer = [...detail.messages].reverse().find((message) => message.role === "assistant");
        const selectedMessageKey = `gunther:selected-message:${base.id}`;
        const rememberedMessage = window.localStorage.getItem(selectedMessageKey);
        const selectedAnswer = detail.messages.find(
          (message) => message.id === rememberedMessage && message.role === "assistant",
        );
        setSelectedMessageId(selectedAnswer?.id ?? lastAnswer?.id ?? null);
        if (rememberedMessage) window.localStorage.removeItem(selectedMessageKey);
      } catch {
        if (cancelled) return;
        const fallback = localSession(base);
        setOnline(false);
        setSessions([fallback]);
        setActiveId(fallback.id);
        setActiveSession(fallback);
      } finally {
        if (!cancelled) {
          setLoading(false);
          if (bootstrapRequest.current?.baseId === base.id) bootstrapRequest.current = null;
          // A question asked from Home arrives as a ready-to-send draft, never auto-sent.
          const askDraftKey = `gunther:ask-draft:${base.id}`;
          const handedOff = window.localStorage.getItem(askDraftKey);
          if (handedOff) {
            window.localStorage.removeItem(askDraftKey);
            setDraft(handedOff);
          }
        }
      }
    };
    void initialize();
    return () => { cancelled = true; };
  }, [base, selectedChapterId, sortSessions]);

  useEffect(() => {
    let cancelled = false;
    const loadSources = () => {
      void knowledgeApi.sources(base.id).then((items) => { if (!cancelled) setIndexedSources(items); }).catch(() => undefined);
    };
    loadSources();
    window.addEventListener("gunther:sources-updated", loadSources);
    return () => { cancelled = true; window.removeEventListener("gunther:sources-updated", loadSources); };
  }, [base.id]);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([knowledgeApi.proposals(base.id), knowledgeApi.knowledgeUnits(base.id)]).then(([proposals, units]) => {
      if (!cancelled) {
        setProposalMessageIds(new Set(proposals.map((item) => item.messageId)));
        setKnowledgeUnits(units);
      }
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [base.id]);

  useEffect(() => {
    if (activeId) window.localStorage.setItem(`gunther:active-session:${base.id}`, activeId);
  }, [activeId, base.id]);
  // Scroll only the conversation pane (scrollIntoView would move every ancestor too), and only
  // while the reader is at the bottom, so reading earlier messages is never yanked away.
  useEffect(() => {
    const pane = scrollPane.current;
    if (pane && followTail.current) pane.scrollTop = pane.scrollHeight;
  }, [activeSession?.messages.length, sending, asking, liveAnswer.live?.steps.length, liveAnswer.live?.text.length]);
  useEffect(() => { followTail.current = true; }, [activeId]);
  useEffect(() => {
    let wasNarrow = window.innerWidth <= 980;
    const onResize = () => {
      const isNarrow = window.innerWidth <= 980;
      if (isNarrow && !wasNarrow) setInspectorCollapsed(true);
      if (!isNarrow) setMobileSessionsOpen(false);
      wasNarrow = isNarrow;
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  useEffect(() => () => {
    responseController.current?.abort();
    if (localResponseTimer.current) window.clearTimeout(localResponseTimer.current);
  }, []);

  const createSession = async () => {
    setError(null);
    if (!online) {
      const fallback = { ...localSession(base), id: `local-${base.id}-${Date.now()}` };
      setSessions((current) => sortSessions([fallback, ...current]));
      setActiveId(fallback.id);
      setActiveSession(fallback);
      return;
    }
    try {
      const created = await knowledgeApi.createSession(base.id, { focusChapterId: null, selectedSourceIds: [] });
      setSessions((current) => sortSessions([created, ...current]));
      setActiveId(created.id);
      setActiveSession(created);
      setSelectedMessageId(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create a session");
    }
  };

  useEffect(() => {
    const onNewSession = () => { void createSession(); };
    window.addEventListener("gunther:new-session", onNewSession);
    return () => window.removeEventListener("gunther:new-session", onNewSession);
  });

  const patchSession = async (id: string, patch: Partial<Pick<KnowledgeSessionSummary, "title" | "pinned" | "archived" | "selectedSourceIds" | "focusChapterId">>) => {
    if (id.startsWith("local-")) {
      setSessions((current) => sortSessions(current.map((item) => item.id === id ? { ...item, ...patch, updatedAt: new Date().toISOString() } : item).filter((item) => !item.archived)));
      setActiveSession((current) => current?.id === id ? { ...current, ...patch } : current);
      return;
    }
    const updated = await knowledgeApi.updateSession(id, patch);
    setSessions((current) => sortSessions(current.map((item) => item.id === id ? updated : item).filter((item) => !item.archived)));
    setActiveSession((current) => current?.id === id ? { ...current, ...updated } : current);
  };

  const archiveSession = async (session: KnowledgeSessionSummary) => {
    try {
      await patchSession(session.id, { archived: true });
      setArchivedSessions((current) => sortSessions([{ ...session, archived: true, updatedAt: new Date().toISOString() }, ...current.filter((item) => item.id !== session.id)]));
      const remaining = sessions.filter((item) => item.id !== session.id);
      if (activeId === session.id) {
        if (remaining[0]) await loadSession(remaining[0].id);
        else await createSession();
      }
      onNotify("Session archived. Its history is still available from the archive.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not archive the session");
    }
  };

  const toggleArchived = async () => {
    const next = !showArchived;
    setShowArchived(next);
    if (!next || !online) return;
    try {
      const allSessions = await knowledgeApi.sessions(base.id, true);
      setArchivedSessions(sortSessions(allSessions.filter((session) => session.archived)));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load archived sessions");
    }
  };

  const restoreSession = async (session: KnowledgeSessionSummary) => {
    try {
      if (session.id.startsWith("local-")) {
        const restored = { ...session, archived: false, updatedAt: new Date().toISOString() };
        setArchivedSessions((current) => current.filter((item) => item.id !== session.id));
        setSessions((current) => sortSessions([restored, ...current]));
      } else {
        const restored = await knowledgeApi.updateSession(session.id, { archived: false });
        setArchivedSessions((current) => current.filter((item) => item.id !== session.id));
        setSessions((current) => sortSessions([restored, ...current.filter((item) => item.id !== session.id)]));
      }
      setActiveSession((current) => current?.id === session.id ? { ...current, archived: false, updatedAt: new Date().toISOString() } : current);
      onNotify("Session restored to recent history.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not restore the session");
    }
  };

  const localReply = (question: string, current: KnowledgeSession): { user: SessionMessage; assistant: SessionMessage } => {
    const now = new Date().toISOString();
    const chapter = base.chapters.find((item) => item.id === current.focusChapterId) ?? base.chapters[0];
    const chosenSources = current.selectedSourceIds.length ? base.sources.filter((source) => current.selectedSourceIds.includes(source.id)) : base.sources.slice(0, 3);
    const citations: ConversationCitation[] = chosenSources.slice(0, 3).map((source, index) => ({ id: `local-cit-${Date.now()}-${index}`, sourceId: source.id, sourceTitle: source.title, assertionId: `local-${chapter?.id ?? index}`, quote: source.scope, locator: "Source overview", status: "provisional", confidence: 0.72 }));
    const supported = chapter?.takeaways.slice(0, 3) ?? [];
    const content = supported.length ? `Here is the strongest answer available inside this library:\n\n${supported.map((item, index) => `${index + 1}. **${item}** [${Math.min(index + 1, Math.max(citations.length, 1))}]`).join("\n")}\n\n**Boundary.** This offline preview uses the curated chapter and source summaries. Reconnect the knowledge service for claim-level retrieval and persisted history.` : `I couldn’t find grounded material for “${question}” in this chapter yet. Add a source or widen the session scope.`;
    return {
      user: { id: `local-user-${Date.now()}`, sessionId: current.id, role: "user", content: question, citations: [], context: emptyContext, createdAt: now },
      assistant: { id: `local-assistant-${Date.now()}`, sessionId: current.id, role: "assistant", content, citations, context: { sourcesConsidered: chosenSources.length, assertionsConsidered: supported.length, verifiedAssertions: chapter?.status === "grounded" ? supported.length : 0, retrievalMode: current.selectedSourceIds.length ? "selected" : "all", responderMode: "local" }, createdAt: now },
    };
  };

  /** Bring the saved conversation in, unless another question is already under way. */
  const reloadSession = async (id: string) => {
    if (responseController.current) return null;
    try {
      const fresh = await knowledgeApi.session(id);
      setActiveSession((current) => current && current.id === fresh.id ? { ...current, messages: fresh.messages, messageCount: fresh.messageCount } : current);
      return fresh;
    } catch {
      return null;
    }
  };

  const send = async (again?: string) => {
    const question = (again ?? draft).trim();
    if (!question || !activeSession || sending) return;
    if (again === undefined) setDraft("");
    setSending(true);
    setAsking(question);
    followTail.current = true;
    setError(null);
    pendingQuestion.current = question;
    if (activeSession.id.startsWith("local-")) {
      localResponseTimer.current = window.setTimeout(() => {
        const turn = localReply(question, activeSession);
        const title = activeSession.title === "New session" ? question.slice(0, 62) : activeSession.title;
        const updated = { ...activeSession, title, summary: turn.assistant.content.slice(0, 180), updatedAt: turn.assistant.createdAt, messages: [...activeSession.messages, turn.user, turn.assistant], messageCount: activeSession.messageCount + 2 };
        setActiveSession(updated);
        setSessions((current) => sortSessions(current.map((item) => item.id === updated.id ? updated : item)));
        setSelectedMessageId(turn.assistant.id);
        setSending(false);
        setAsking(null);
        pendingQuestion.current = "";
        localResponseTimer.current = null;
      }, 420);
      return;
    }
    const controller = new AbortController();
    responseController.current = controller;
    try {
      const chosen = askChoice.model && modelMenu?.models.length ? { model: askChoice.model, effort: askChoice.effort } : {};
      liveAnswer.start();
      const turn = await knowledgeApi.sendMessageStream(activeSession.id, { content: question, selectedSourceIds: activeSession.selectedSourceIds, focusChapterId: activeSession.focusChapterId, ...chosen, style: readAnswerStyle(), ...(webSearch.enabled ? { web: true } : {}) }, liveAnswer.hear, controller.signal);
      const updated = { ...activeSession, ...turn.session, messages: [...activeSession.messages, turn.userMessage, turn.assistantMessage] };
      setActiveSession(updated);
      setSessions((current) => sortSessions(current.map((item) => item.id === updated.id ? turn.session : item)));
      setSelectedMessageId(turn.assistantMessage.id);
      setInspectorTab("context");
      pendingQuestion.current = "";
    } catch (reason) {
      if (!controller.signal.aborted) {
        // The service keeps a question it could not answer in the history; only one it refused goes back to the composer.
        if (responseController.current === controller) responseController.current = null;
        const fresh = await reloadSession(activeSession.id);
        if (!fresh || !endsWithInterrupted(fresh.messages, question)) setDraft(question);
        setError(reason instanceof Error ? reason.message : "The answer could not be generated");
      }
    } finally {
      if (responseController.current === controller) responseController.current = null;
      liveAnswer.stop();
      setSending(false);
      setAsking(null);
    }
  };

  const stopResponse = () => {
    responseController.current?.abort();
    responseController.current = null;
    if (localResponseTimer.current) {
      window.clearTimeout(localResponseTimer.current);
      localResponseTimer.current = null;
    }
    const question = pendingQuestion.current;
    pendingQuestion.current = "";
    setSending(false);
    setAsking(null);
    if (question && activeSession) {
      // The question stays in the conversation, marked; the service's copy replaces this one.
      const id = activeSession.id;
      setActiveSession((current) => current && current.id === id ? { ...current, messages: [...current.messages, interruptedQuestion(id, question, "stopped")] } : current);
      window.setTimeout(() => void reloadSession(id), 900);
    }
    onNotify("Stopped. Your question stays in the conversation.");
  };

  const toggleSource = async (id: string) => {
    if (!activeSession) return;
    const next = activeSession.selectedSourceIds.includes(id) ? activeSession.selectedSourceIds.filter((item) => item !== id) : [...activeSession.selectedSourceIds, id];
    try { await patchSession(activeSession.id, { selectedSourceIds: next }); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not update source scope"); }
  };

  const promoteMessage = async (message: SessionMessage) => {
    if (!activeSession || activeSession.id.startsWith("local-")) {
      onNotify("Reconnect the knowledge service to create a durable proposal.");
      return;
    }
    setPromotingMessageId(message.id);
    setError(null);
    try {
      const proposal = await knowledgeApi.createProposal(activeSession.id, message.id, { targetChapterId: activeSession.focusChapterId });
      setProposalMessageIds((current) => new Set(current).add(message.id));
      if (proposal.status === "pending") {
        window.dispatchEvent(new CustomEvent("gunther:proposal-created", { detail: proposal }));
        onNotify("Answer added to Inbox as a reviewable knowledge proposal.");
      } else {
        onNotify(`This answer already has a ${proposal.status} knowledge proposal.`);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create the proposal");
    } finally {
      setPromotingMessageId(null);
    }
  };

  const branchFromMessage = async (message: SessionMessage) => {
    if (!activeSession || activeSession.id.startsWith("local-")) {
      onNotify("Reconnect the knowledge service to create a durable branch.");
      return;
    }
    setBranchingMessageId(message.id);
    setError(null);
    try {
      const branch = await knowledgeApi.branchSession(activeSession.id, message.id);
      setSessions((current) => sortSessions([branch, ...current]));
      setActiveId(branch.id);
      setActiveSession(branch);
      const lastAnswer = [...branch.messages].reverse().find((item) => item.role === "assistant");
      setSelectedMessageId(lastAnswer?.id ?? null);
      onNotify("Branched into a new session without changing the original.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not branch this session");
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

  const openSourceDetail = async (sourceId: string, citation?: ConversationCitation) => {
    if (citation) {
      const index = selectedMessage?.citations.findIndex((item) => item.id === citation.id) ?? -1;
      setEvidence(index >= 0 ? { citation, index } : { citation });
      return;
    }
    try {
      const source = await knowledgeApi.source(sourceId);
      setEvidence({ citation: { id: `open-${sourceId}`, kind: "library", sourceId, sourceTitle: source.title, assertionId: null, quote: "", locator: "Source", status: "verified", confidence: 1 } });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not open the source");
    }
  };

  const openKnowledgeUnitOrigin = async (unit: KnowledgeUnit) => {
    await loadSession(unit.sourceSessionId);
    setSelectedMessageId(unit.sourceMessageId);
    setInspectorTab("context");
    setInspectorCollapsed(false);
    onNotify("Opened the session and evidence snapshot that created this Knowledge Unit.");
  };

  useEffect(() => {
    const sourceKey = `gunther:open-source:${base.id}`;
    const sourceId = window.localStorage.getItem(sourceKey);
    if (!sourceId) return;
    window.localStorage.removeItem(sourceKey);
    void openSourceDetail(sourceId);
  }, [base.id]);

  useEffect(() => {
    if (!mobileSessionsOpen && !sessionOptionsOpen && !renameOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (mobileSessionsOpen) setMobileSessionsOpen(false);
      else if (sessionOptionsOpen) setSessionOptionsOpen(false);
      else if (renameOpen) setRenameOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileSessionsOpen, renameOpen, sessionOptionsOpen]);

  const exportSession = () => {
    if (!activeSession) return;
    const chapter = base.chapters.find((item) => item.id === activeSession.focusChapterId);
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
    setSessionOptionsOpen(false);
    onNotify("Session exported with its citation trail.");
  };

  // A list of one conversation is not worth the room; two or more are.
  const historyDocked = roomForHistory && (historyChoice ? historyChoice === "open" : sessions.length >= 2);
  const toggleHistory = () => {
    if (!roomForHistory) {
      setMobileSessionsOpen(true);
      return;
    }
    const next = historyDocked ? "closed" : "open";
    setHistoryChoice(next);
    try {
      window.localStorage.setItem(HISTORY_CHOICE_KEY, next);
    } catch {
      // The choice still holds while this page is open.
    }
  };

  const selectedMessage = activeSession?.messages.find((message) => message.id === selectedMessageId && message.role === "assistant") ?? null;
  const focusedChapter = base.chapters.find((chapter) => chapter.id === activeSession?.focusChapterId);
  const scopeSources = useMemo<ScopedKnowledgeSource[]>(() => {
    const indexed = indexedSources.map((source) => ({
      id: source.id,
      title: source.title,
      publisher: "Local workbook",
      kind: source.kind === "table" ? "dataset" as const : source.kind === "paper" ? "documentation" as const : "guide" as const,
      url: "",
      scope: `${source.assertionCount} extracted claims · ${source.entityCount} connected entities`,
      usedIn: [],
      indexed: true,
    }));
    const indexedIds = new Set(indexed.map((source) => source.id));
    return [...indexed, ...base.sources.filter((source) => !indexedIds.has(source.id)).map((source) => ({ ...source, indexed: false }))];
  }, [base.sources, indexedSources]);

  return (
    <div className={`session-workspace page-enter ${historyDocked ? "history-is-docked" : "history-is-drawer"} ${inspectorCollapsed ? "inspector-is-collapsed" : ""}`}>
      {historyDocked && <SessionsSidebar base={base} indexedCount={scopeSources.filter((source) => source.indexed).length} referenceCount={scopeSources.filter((source) => !source.indexed).length} sessions={sessions} archivedSessions={archivedSessions} showArchived={showArchived} activeId={activeId} loading={loading} query={sessionQuery} onQuery={setSessionQuery} onNew={() => void createSession()} onOpen={(id) => void loadSession(id)} onPin={(session) => void patchSession(session.id, { pinned: !session.pinned })} onArchive={(session) => void archiveSession(session)} onToggleArchived={() => void toggleArchived()} onRestore={(session) => void restoreSession(session)} />}
      <section className="conversation-pane" aria-label="Conversation">
        <header className="conversation-header">
          <button className="mobile-session-toggle" onClick={toggleHistory} aria-label={historyDocked ? "Hide conversations" : "Show conversations"} aria-pressed={historyDocked} title={historyDocked ? "Hide conversations" : "Show conversations"}>{!roomForHistory ? <MessageSquareText size={15} /> : historyDocked ? <PanelLeftClose size={15} /> : <PanelLeftOpen size={15} />}</button>
          <div className="conversation-title">
            {renameOpen ? <form onSubmit={(event) => { event.preventDefault(); if (activeSession && renameValue.trim()) void patchSession(activeSession.id, { title: renameValue.trim() }).then(() => setRenameOpen(false)); }}><input autoFocus value={renameValue} onChange={(event) => setRenameValue(event.target.value)} aria-label="Session title" /><button type="submit" aria-label="Save session title"><Check size={13} /></button><button type="button" onClick={() => setRenameOpen(false)} aria-label="Cancel renaming"><X size={13} /></button></form> : <button onClick={() => { if (activeSession) { setRenameValue(activeSession.title); setRenameOpen(true); } }} aria-label="Rename session"><strong>{activeSession?.title ?? "Opening session…"}</strong><SquarePen size={12} /></button>}
            <span><i className={online ? "is-online" : "is-offline"} />{activeSession?.archived ? "Archived session · read only" : online ? "Saved to local knowledge service" : "Offline preview · reconnect to persist"}{activeSession?.parentSessionId && <><b>·</b><GitBranch size={10} />Branch lineage preserved</>}</span>
          </div>
          <div className="conversation-actions">
            <button title="Session options" aria-label="Session options" aria-expanded={sessionOptionsOpen} aria-haspopup="menu" onClick={() => setSessionOptionsOpen((value) => !value)}><MoreHorizontal size={15} /></button>
            {sessionOptionsOpen && activeSession && <div className="conversation-options-menu" role="menu" onMouseLeave={() => setSessionOptionsOpen(false)}>
              <button role="menuitem" onClick={() => { void patchSession(activeSession.id, { pinned: !activeSession.pinned }); setSessionOptionsOpen(false); }}><Pin size={12} />{activeSession.pinned ? "Unpin session" : "Pin session"}</button>
              <button role="menuitem" onClick={exportSession}><Download size={12} />Export Markdown</button>
              <button role="menuitem" onClick={() => { if (activeSession.archived) void restoreSession(activeSession); else void archiveSession(activeSession); setSessionOptionsOpen(false); }}><Archive size={12} />{activeSession.archived ? "Restore session" : "Archive session"}</button>
            </div>}
          </div>
        </header>
        <div className="conversation-scroll" ref={scrollPane} onScroll={(event) => { const pane = event.currentTarget; followTail.current = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 80; }}>
          {error && <div className="conversation-error" role="alert"><CircleAlert size={14} /><span>{error}</span><button onClick={() => setError(null)}><X size={12} /></button></div>}
          {!loading && activeSession?.messages.length === 0 ? <WelcomePanel base={base} onPrompt={setDraft} /> : <h1 className="gx-sr-only">Ask {base.title}</h1>}
          {activeSession?.messages.map((message) => <ConversationMessage key={message.id} message={message} retryDisabled={sending || Boolean(activeSession?.archived)} onRetry={() => void send(message.content)} onEdit={() => setDraft(message.content)} onCite={(citation, index) => setEvidence({ citation, index })} selected={selectedMessageId === message.id} promoting={promotingMessageId === message.id} promoted={proposalMessageIds.has(message.id)} branching={branchingMessageId === message.id} onSelect={() => { setSelectedMessageId(message.id); setInspectorTab("context"); setInspectorCollapsed(false); }} onCopy={() => void copyMessage(message)} onPromote={() => void promoteMessage(message)} onBranch={() => void branchFromMessage(message)} />)}
          {sending && asking && <article className="conversation-message role-user"><div className="message-author"><span className="user-mark"><UserRound size={13} /></span><span>You</span></div><div className="message-body"><MessageContent content={asking} /></div></article>}
          {sending && (liveAnswer.live ? <LiveAnswer state={liveAnswer.live} /> : <div className="thinking-row"><span className="assistant-mark"><BrandMark size={14} busy /></span><span><i /><i /><i /></span><small>Working out what to look up…</small></div>)}
        </div>
        <Composer picker={<><ModelPicker menu={modelMenu} choice={askChoice} onChange={chooseModel} disabled={sending || Boolean(activeSession?.archived)} /><StylePicker disabled={sending || Boolean(activeSession?.archived)} /></>} web={{ available: webSearch.available, enabled: webSearch.enabled, onChange: webSearch.setEnabled }} value={draft} sending={sending} sourceCount={activeSession?.selectedSourceIds.length ?? 0} chapterTitle={focusedChapter?.title} readOnly={Boolean(activeSession?.archived)} ready={!loading && activeSession !== null} onChange={setDraft} onSend={() => void send()} onStop={stopResponse} onSources={() => { setInspectorTab("sources"); setInspectorCollapsed(false); }} />
      </section>
      <ContextInspector base={base} sources={scopeSources} session={activeSession} knowledgeUnits={knowledgeUnits} selectedMessage={selectedMessage} tab={inspectorTab} sourceQuery={sourceQuery} collapsed={inspectorCollapsed} readOnly={Boolean(activeSession?.archived)} onTab={setInspectorTab} onSourceQuery={setSourceQuery} onToggleSource={(id) => void toggleSource(id)} onUseAll={() => { if (activeSession) void patchSession(activeSession.id, { selectedSourceIds: [] }); }} onAdd={onAdd} onOpenSource={(id, citation) => void openSourceDetail(id, citation)} onOpenUnit={(unit) => void openKnowledgeUnitOrigin(unit)} onCollapse={() => setInspectorCollapsed((value) => !value)} />
      {evidence && <EvidencePanel citation={evidence.citation} index={evidence.index} onClose={() => setEvidence(null)} onOpenSource={onOpenSource ? (id) => { setEvidence(null); onOpenSource(id); } : undefined} />}
      {mobileSessionsOpen && <div className="mobile-session-drawer" role="dialog" aria-modal="true" aria-label="Session history"><button className="mobile-session-scrim" onClick={() => setMobileSessionsOpen(false)} aria-label="Close session history" /><div className="mobile-session-sheet"><button className="mobile-session-close" autoFocus onClick={() => setMobileSessionsOpen(false)} aria-label="Close session history"><X size={15} /></button><SessionsSidebar base={base} indexedCount={scopeSources.filter((source) => source.indexed).length} referenceCount={scopeSources.filter((source) => !source.indexed).length} sessions={sessions} archivedSessions={archivedSessions} showArchived={showArchived} activeId={activeId} loading={loading} query={sessionQuery} onQuery={setSessionQuery} onNew={() => { setMobileSessionsOpen(false); void createSession(); }} onOpen={(id) => { setMobileSessionsOpen(false); void loadSession(id); }} onPin={(session) => void patchSession(session.id, { pinned: !session.pinned })} onArchive={(session) => void archiveSession(session)} onToggleArchived={() => void toggleArchived()} onRestore={(session) => void restoreSession(session)} /></div></div>}
    </div>
  );
}
