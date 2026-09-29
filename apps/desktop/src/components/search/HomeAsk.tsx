import type { ConversationCitation, KnowledgeSessionSummary, SessionMessage } from "@gunther/contracts";
import { ArrowUp, FolderInput, Globe2, MessageSquareText, Mic, Plus, Sparkles, Square, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import type { KnowledgeBase } from "../../atlas";
import { AgentSteps, AnswerBody, LiveAnswer } from "../../pages/AnswerBody";
import { EvidencePanel } from "../evidence/EvidencePanel";
import type { LiveAnswerState } from "../../pages/liveAnswer";
import type { ItemRef } from "../../items/itemRef";
import { useDictatedField } from "../../services/useDictatedField";
import { withShortcut } from "../../shortcuts/shortcuts";

const QUESTION_WORDS = /^(what|why|how|when|where|who|whom|which|whose|is|are|was|were|do|does|did|can|could|should|would|will|explain|compare|summari[sz]e|tell me|find out|list|show me|什么|为什么|为何|如何|怎么|怎样|是否|哪|谁|能否|请问|帮我|比较|总结|解释)/i;

/** A guess at whether a search is really a question; it only decides how loudly Ask is offered. */
export function looksLikeQuestion(query: string): boolean {
  const text = query.trim();
  if (!text) return false;
  if (/[?？]\s*$/.test(text)) return true;
  if (QUESTION_WORDS.test(text)) return true;
  return text.split(/\s+/).length >= 7;
}

interface HomeAskProps {
  /** What is in the search box, the question Ask would take. */
  query: string;
  bases: KnowledgeBase[];
  messages: SessionMessage[];
  pending: string | null;
  live: LiveAnswerState | null;
  error: string | null;
  /** A conversation is on screen (or being started). */
  active: boolean;
  onAsk: (question: string) => void;
  onCancel: () => void;
  onClose: () => void;
  onNew: () => void;
  onFile: (libraryId: string) => void;
  onOpenSource: (item: ItemRef) => void;
}

function Sources({ citations, onOpen }: { citations: ConversationCitation[]; onOpen: (citation: ConversationCitation, index: number) => void }) {
  if (!citations.length) return null;
  return <ol className="gx-home-sources" aria-label="Sources cited">
    {citations.map((citation, index) => <li key={citation.id}>
      <i>{index + 1}</i>
      <button type="button" onClick={() => onOpen(citation, index)} title={citation.quote} aria-label={`Show evidence ${index + 1}: ${citation.sourceTitle}`}>
        {citation.kind === "web" && <Globe2 size={12} />}<span>{citation.sourceTitle}</span><small>{citation.locator}</small>
      </button>
    </li>)}
  </ol>;
}

/** Ask Gunther from Home: an offer beside a search, and the conversation once it starts. */
export function HomeAsk({ query, bases, messages, pending, live, error, active, onAsk, onCancel, onClose, onNew, onFile, onOpenSource }: HomeAskProps) {
  const [followUp, setFollowUp] = useState("");
  const [evidence, setEvidence] = useState<{ citation: ConversationCitation; index: number } | null>(null);
  const busy = pending !== null;
  const dictation = useDictatedField(followUp, setFollowUp, { enabled: active });
  const tail = useRef<HTMLDivElement>(null);
  // A new question brings the latest exchange into view once; after that the page is the reader's.
  useEffect(() => { if (pending !== null) tail.current?.scrollIntoView({ block: "nearest" }); }, [pending]);
  const suggested = looksLikeQuestion(query);

  if (!active) {
    if (!query.trim()) return null;
    return <section className={`gx-home-ask ${suggested ? "is-suggested" : "is-quiet"}`} aria-label="Ask Gunther">
      <button type="button" className="gx-home-ask-offer" onClick={() => onAsk(query)}>
        <span className="gx-ask-icon"><Sparkles size={16} /></span>
        <span className="gx-ask-copy">
          <strong>{suggested ? "Ask Gunther" : "Or ask Gunther about this"}</strong>
          <small>{suggested ? "Get an answer from all your libraries, with the sources it used." : `Answer “${query.trim().slice(0, 80)}” from your libraries.`}</small>
        </span>
        <kbd>⌘↵</kbd>
      </button>
    </section>;
  }

  const send = (event: FormEvent) => {
    event.preventDefault();
    const text = followUp.trim();
    if (!text || busy) return;
    setFollowUp("");
    onAsk(text);
  };

  return <section className="gx-home-ask is-open" aria-label="Conversation with Gunther">
    <header>
      <span><MessageSquareText size={14} />Conversation</span>
      <div>
        <label className="gx-home-file" title="Move this conversation into a library">
          <FolderInput size={13} />
          <select aria-label="File in a library" value="" disabled={busy || !messages.length} onChange={(event) => { if (event.target.value) onFile(event.target.value); }}>
            <option value="">File in library…</option>
            {bases.map((base) => <option key={base.id} value={base.id}>{base.title}</option>)}
          </select>
        </label>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => { setEvidence(null); onNew(); }} disabled={busy}><Plus size={13} />New</button>
        <button type="button" className="gx-icon-button" onClick={() => { setEvidence(null); onClose(); }} aria-label="Close conversation" title="Close (it stays under Recent)"><X size={14} /></button>
      </div>
    </header>
    <div className="gx-home-thread">
      {messages.map((message) => message.role === "user"
        ? <article key={message.id} className="gx-home-question"><p>{message.content}</p></article>
        : <article key={message.id} className="gx-home-answer">
            <AgentSteps steps={message.context.steps ?? []} />
            <AnswerBody content={message.content} citationCount={message.citations.length} onCitation={(index) => { const citation = message.citations[index]; if (citation) setEvidence({ citation, index }); }} />
            {message.context.modelError && <p className="message-model-error" role="note">{message.context.modelError}{message.citations.length > 0 && " The quotes stand in for its answer."}</p>}
            <Sources citations={message.citations} onOpen={(citation, index) => setEvidence({ citation, index })} />
            <footer><small>{[message.context.modelLabel, message.context.effortLabel].filter(Boolean).join(" · ")}</small></footer>
          </article>)}
      {pending !== null && <>
        <article className="gx-home-question"><p>{pending}</p></article>
        {live && <LiveAnswer state={live} className="gx-home-live" />}
      </>}
      {error && <p className="gx-inline-alert" role="alert">{error}</p>}
      <div ref={tail} />
    </div>
    <form className="gx-home-followup" onSubmit={send}>
      <input value={followUp} onChange={(event) => setFollowUp(event.target.value)} placeholder={messages.length ? "Ask a follow-up…" : "Ask a question…"} aria-label="Ask a follow-up" disabled={busy} onFocus={dictation.claim} />
      <button type="button" className={`gx-tool gx-tool-icon gx-mic ${dictation.dictating ? "is-on" : ""}`} onClick={dictation.toggle} disabled={busy} aria-pressed={dictation.listening} aria-label={dictation.listening ? "Stop voice input" : "Voice input"} title={dictation.error ?? withShortcut(dictation.listening ? "Stop voice input" : "Speak instead of typing", "dictate")}><Mic size={15} /></button>
      {busy
        ? <button type="button" className="gx-send is-stop" onClick={onCancel} aria-label="Stop"><Square size={12} fill="currentColor" /></button>
        : <button type="submit" className="gx-send" disabled={!followUp.trim()} aria-label="Send"><ArrowUp size={16} /></button>}
    </form>
    {evidence && <EvidencePanel citation={evidence.citation} index={evidence.index} onClose={() => setEvidence(null)} onOpenSource={(id) => { setEvidence(null); onOpenSource({ type: "source", id }); }} />}
  </section>;
}

/** The latest conversations, to pick one back up. */
export function RecentQuestions({ items, onOpen }: { items: KnowledgeSessionSummary[]; onOpen: (id: string) => void }) {
  if (!items.length) return null;
  return <div className="gx-home-column gx-home-recent-questions">
    <header><h2>Recent questions</h2></header>
    {items.map((item) => <button type="button" className="gx-row" key={item.id} onClick={() => onOpen(item.id)}>
      <span className="gx-kind-icon"><MessageSquareText size={15} /></span>
      <span className="gx-row-body"><strong>{item.title}</strong><small>{item.summary || "Conversation"}</small></span>
    </button>)}
  </div>;
}

