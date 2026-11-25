import type { BriefSettled, ConversationBrief, ConversationBriefInput, ConversationCitation } from "@gunther/contracts";
import { Check, CheckCircle2, ChevronDown, CircleDashed, SlidersHorizontal, Target, UserCheck, X } from "lucide-react";
import { useId, useRef, useState, type RefObject } from "react";
import { persistentStorage } from "../persistentStorage";
import { useAutosize } from "../services/useAutosize";

const STAGES = [
  { part: "goal", title: "Goal", label: "goal", add: "Set a goal…", icon: Target },
  { part: "constraints", title: "Constraints", label: "constraint", add: "+ Add constraint", icon: SlidersHorizontal },
  { part: "settled", title: "Settled", label: "conclusion", add: "+ Add conclusion", icon: CheckCircle2 },
  { part: "open", title: "Open", label: "open question", add: "+ Add open question", icon: CircleDashed },
] as const;
type Stage = (typeof STAGES)[number];
type Part = Stage["part"];
export type BriefCitation = { citations: ConversationCitation[]; index: number };

const COLLAPSED_KEY = "gunther:brief-collapsed";
const SETTLED_PREVIEW = 3;
const EDITED_TITLE = "Edited by you — Gunther keeps your wording";
const EMPTY_BRIEF: ConversationBrief = { goal: "", constraints: [], settled: [], open: [], edited: [], error: null };

function hasBriefToShow(brief: ConversationBrief, updating: boolean, hasMessages: boolean): boolean {
  return Boolean(updating || hasMessages || brief.error || brief.lastChange || brief.goal || brief.constraints.length || brief.settled.length || brief.open.length);
}

function replace<T>(items: T[], at: number, next: T | null): T[] {
  return [...items.slice(0, at), ...(next === null ? [] : [next]), ...items.slice(at + 1)];
}

const userConclusion = (text: string): BriefSettled => ({ text, refs: [], by: "you", because: "" });
const lineKey = (part: Part, at: number, text: string) => `${part}:${at}:${text}`;

function BriefField({ value, label, cancelledRef, returnFocus, onChange, onFinish }: {
  value: string;
  label: string;
  cancelledRef: RefObject<boolean>;
  returnFocus: () => void;
  onChange: (value: string) => void;
  onFinish: (text?: string) => void;
}) {
  const field = useRef<HTMLTextAreaElement>(null);
  useAutosize(field, value, Infinity);

  return <textarea ref={field} className="brief-field" aria-label={label} rows={1} maxLength={300} autoFocus value={value}
    onChange={(event) => onChange(event.currentTarget.value)}
    onBlur={() => { if (!cancelledRef.current) onFinish(value.trim()); }}
    onKeyDown={(event) => {
      if (event.nativeEvent.isComposing || event.keyCode === 229) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        cancelledRef.current = true;
        onFinish();
        returnFocus();
      } else if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        returnFocus();
      }
    }} />;
}

export function BriefPanel({ brief = EMPTY_BRIEF, selectedMessageId, updating, readOnly, hasMessages, citations, onSave, onRetry, onOpenCitation, onAsk }: {
  brief: ConversationBrief | undefined;
  selectedMessageId?: string | undefined;
  updating: boolean;
  readOnly: boolean;
  hasMessages: boolean;
  citations: ReadonlyMap<number, BriefCitation>;
  onSave: (brief: ConversationBriefInput) => void;
  onRetry: () => void;
  onOpenCitation: (citations: ConversationCitation[], index: number) => void;
  onAsk?: ((text: string) => void) | undefined;
}) {
  const [collapsed, setCollapsed] = useState(() => persistentStorage.getItem(COLLAPSED_KEY) === "1");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<{ key: string; value: string } | null>(null);
  const [showAll, setShowAll] = useState(false);
  const cancelledRef = useRef(false);
  const editControl = useRef<HTMLButtonElement>(null);
  const bodyId = useId();
  if (!hasBriefToShow(brief, updating, hasMessages)) return null;

  const change = !selectedMessageId || selectedMessageId === brief.throughMessageId ? brief.lastChange : null;
  const olderAnswerId = brief.throughMessageId && selectedMessageId !== brief.throughMessageId ? selectedMessageId : undefined;
  const fromSelectedAnswer = (item?: BriefSettled) => olderAnswerId !== undefined && item?.messageId === olderAnswerId;
  const closed = change?.removed.open ?? [];
  const changes = change ? [
    change.goalChanged && "goal updated",
    change.added.constraints.length > 0 && `+${change.added.constraints.length} ${change.added.constraints.length === 1 ? "constraint" : "constraints"}`,
    change.added.settled.length > 0 && `+${change.added.settled.length} settled`,
    closed.length > 0 && `${closed.length} ${closed.length === 1 ? "question" : "questions"} closed`,
    change.added.open.length > 0 && `+${change.added.open.length} open`,
  ].filter(Boolean).join(" · ") : "";
  const summary = changes && <p className="brief-change">After the last answer: {changes}</p>;
  const inEditMode = editing && !readOnly;
  const returnFocus = () => editControl.current?.focus();
  const startField = (key: string, value = "") => {
    cancelledRef.current = false;
    setDraft({ key, value });
  };
  const stopEditing = () => { setEditing(false); setDraft(null); };
  const toggleCollapsed = (next: boolean) => {
    setCollapsed(next);
    persistentStorage.setItem(COLLAPSED_KEY, next ? "1" : "0");
    if (next) stopEditing();
  };
  const save = (change: Partial<ConversationBriefInput>) => {
    const { goal, constraints, settled, open } = brief;
    onSave({ goal, constraints, settled, open, ...change });
  };
  const saveLine = (part: Part, at: number, text: string) => {
    if (part === "goal") save({ goal: text });
    else if (part === "settled") {
      const item = brief.settled[at] ?? userConclusion(text);
      save({ settled: replace(brief.settled, at, text ? { ...item, text } : null) });
    } else save({ [part]: replace(brief[part], at, text || null) });
  };
  const settle = (text: string, at: number, value: string) => {
    save({ open: replace(brief.open, at, null), settled: [...brief.settled, userConclusion(text)] });
    startField(lineKey("settled", brief.settled.length, text), value);
  };
  const editedTitle = (part: Part, text: string) => brief.edited.includes(part === "goal" ? part : `${part}:${text}`) ? EDITED_TITLE : undefined;
  const renderField = (label: string, commit: (text: string) => void) => draft && <BriefField value={draft.value}
    label={label} cancelledRef={cancelledRef} returnFocus={returnFocus}
    onChange={(value) => setDraft({ ...draft, value })} onFinish={(text) => {
      setDraft(null);
      if (text !== undefined) commit(text);
    }} />;

  const renderLine = ({ part, label }: Stage, text: string, at: number, item?: BriefSettled) => {
    const key = lineKey(part, at, text);
    const active = inEditMode && draft?.key === key;
    const isNew = change && (part === "goal" ? change.goalChanged : change.added[part].includes(text));
    const fromAnswer = fromSelectedAnswer(item);
    const annotation = isNew ? <span className="brief-new">New</span>
      : fromAnswer ? <span className="brief-origin">from this answer</span> : null;
    const Icon = part === "open" ? CircleDashed : item?.by === "sources" ? Check : item ? UserCheck : null;
    const refs = item?.by === "sources" && item.refs.length > 0 && <span className={`brief-refs${active ? " is-muted" : ""}`}>
      {item.refs.map((ref) => {
        const target = citations.get(ref);
        return target ? <button key={ref} type="button" className="brief-ref" title={target.citations[target.index]?.sourceTitle}
          onClick={() => onOpenCitation(target.citations, target.index)}>[{ref}]</button>
          : <span key={ref} className="brief-ref">[{ref}]</span>;
      })}
    </span>;
    return <li key={key} className={`brief-row brief-row-${part}${annotation ? " is-accented" : ""}`} title={editedTitle(part, text)}>
      {Icon && <Icon size={12} className={`brief-row-icon${item?.by === "sources" ? " is-sourced" : ""}`} aria-hidden />}
      <div className="brief-line">
        {active ? renderField(`Edit ${label}`, (next) => { if (next !== text) saveLine(part, at, next); }) : inEditMode
          ? <button className="brief-text" type="button" onClick={() => startField(key, text)}>{text}</button>
          : <p className="brief-text">{text} {refs} {annotation}</p>}
        {item?.by === "you" && <p className="brief-because">{item.because ? `You agreed: “${item.because}”` : "Added by you"}</p>}
        {inEditMode && <>{refs}{annotation}</>}
      </div>
      {inEditMode ? <div className="brief-row-actions">
        {part === "open" && <button type="button" className="gx-icon-button" title="Settle" aria-label={`Settle open question: ${text}`}
          onMouseDown={(event) => { if (active) event.preventDefault(); }} onClick={() => {
            if (active) { cancelledRef.current = true; returnFocus(); }
            settle(text, at, active ? draft.value : text);
          }}><Check size={13} /></button>}
        <button type="button" className="gx-icon-button" title="Remove" aria-label={`Remove ${label}: ${text}`}
          onMouseDown={(event) => { if (active) event.preventDefault(); }} onClick={() => {
            if (active) cancelledRef.current = true;
            setDraft(null);
            returnFocus();
            saveLine(part, at, "");
          }}><X size={13} /></button>
      </div> : part === "open" && onAsk && <button type="button" className="gx-link brief-ask" onClick={() => onAsk(text)}
        aria-label={`Ask: ${text}`}>Ask →</button>}
    </li>;
  };

  const renderStage = (stage: Stage) => {
    const { part, title, add, icon: Icon } = stage;
    const items = part === "goal" ? (brief.goal ? [brief.goal] : []) : brief[part];
    const count = items.length;
    const showClosed = part === "open" && closed.length > 0;
    if (!inEditMode && !count && !showClosed) return null;
    const recentStart = part === "settled" && !inEditMode ? Math.max(0, count - SETTLED_PREVIEW) : 0;
    const inPreview = (item: string | BriefSettled, index: number) => index >= recentStart || (typeof item !== "string" && fromSelectedAnswer(item));
    const clamped = items.some((item, index) => !inPreview(item, index));
    return <section className="brief-part" key={part}>
      <h4><Icon size={14} aria-hidden />{title}{part !== "goal" && <span className="brief-count">{count}</span>}</h4>
      <div className="brief-part-body">
        {count > 0 && <ul>{items.map((item, index) => {
          if (!showAll && !inPreview(item, index)) return null;
          return typeof item === "string" ? renderLine(stage, item, index) : renderLine(stage, item.text, index, item);
        })}</ul>}
        {clamped && <button type="button" className="gx-link brief-stage-action" aria-expanded={showAll}
          onClick={() => setShowAll(!showAll)}>{showAll ? "Show fewer" : `Show all ${count}`}</button>}
        {inEditMode && (part !== "goal" || !count) && (draft?.key === part
          ? renderField(part === "goal" ? "Set a goal" : `Add ${stage.label}`, (text) => { if (text) saveLine(part, count, text); })
          : <button type="button" className="gx-link brief-stage-action" onClick={() => startField(part)}>{add}</button>)}
        {showClosed && <details className="brief-closed" key={change?.messageId} open>
          <summary>Closed <span className="brief-count">({closed.length})</span></summary>
          <ul>{closed.map((text, index) => <li className="brief-row" key={index}>
            <Check size={12} className="brief-row-icon is-sourced" aria-hidden />
            <p className="brief-text">{text}</p>
          </li>)}</ul>
        </details>}
      </div>
    </section>;
  };

  return <section className="brief-section" aria-label="Brief" onKeyDown={(event) => {
    if (event.key !== "Escape" || event.nativeEvent.isComposing || !inEditMode) return;
    event.preventDefault();
    event.stopPropagation();
    stopEditing();
    returnFocus();
  }}>
    <header className="brief-heading">
      <h3>Brief</h3>
      {updating && <span role="status">Updating…</span>}
      {!readOnly && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" ref={editControl} onClick={() => {
        if (inEditMode) stopEditing();
        else {
          if (collapsed) toggleCollapsed(false);
          setEditing(true);
        }
      }}>{inEditMode ? "Done" : "Edit"}</button>}
      <button type="button" className="gx-icon-button brief-collapse" aria-expanded={!collapsed} aria-controls={bodyId}
        aria-label={collapsed ? "Expand Brief" : "Collapse Brief"} onClick={() => toggleCollapsed(!collapsed)}><ChevronDown size={15} /></button>
    </header>
    {brief.error && !updating && <p className="brief-error" role="alert" title={brief.error}>Couldn’t update the brief · <button type="button" onClick={onRetry}>Retry</button></p>}
    {!collapsed && summary}
    <div id={bodyId} className="brief-content" hidden={collapsed}>{!collapsed && STAGES.map(renderStage)}</div>
    {collapsed && <div className="brief-preview">
      {brief.goal && <p className="brief-text" title={editedTitle("goal", brief.goal)}>{brief.goal}</p>}
      <p className="brief-count">{brief.constraints.length} {brief.constraints.length === 1 ? "constraint" : "constraints"} · {brief.settled.length} settled · {brief.open.length} open</p>
      {summary}
    </div>}
  </section>;
}
