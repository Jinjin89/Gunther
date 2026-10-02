import type { BriefSettled, ConversationBrief, ConversationBriefInput, ConversationCitation } from "@gunther/contracts";
import { X } from "lucide-react";
import { useState } from "react";

type Part = "goal" | "constraints" | "settled" | "open";

const PART_LABEL: Record<Part, string> = { goal: "Goal", constraints: "Constraints", settled: "Settled", open: "Open" };

/** What the page sends back: the four parts, nothing the user cannot edit. */
const inputOf = (brief: ConversationBrief): ConversationBriefInput => ({ goal: brief.goal, constraints: brief.constraints, settled: brief.settled, open: brief.open });

/** One line: click to edit, Enter saves, Escape or leaving the field cancels. */
function Line({ text, label, readOnly, onSave, onRemove, children }: { text: string; label: string; readOnly: boolean; onSave: (text: string) => void; onRemove: () => void; children?: React.ReactNode }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(text);
  if (editing) {
    return <input className="brief-input" autoFocus aria-label={`Edit ${label}`} value={value} maxLength={300}
      onChange={(event) => setValue(event.target.value)}
      onBlur={() => setEditing(false)}
      onKeyDown={(event) => {
        if (event.key === "Escape") setEditing(false);
        if (event.key !== "Enter") return;
        event.preventDefault();
        const next = value.trim();
        setEditing(false);
        if (!next) onRemove();
        else if (next !== text) onSave(next);
      }} />;
  }
  return <span className="brief-line">
    <button type="button" className="brief-text" disabled={readOnly} title={readOnly ? undefined : "Click to edit"} onClick={() => { setValue(text); setEditing(true); }}>{text}</button>
    {children}
    {!readOnly && <button type="button" className="brief-remove" aria-label={`Remove ${label}: ${text}`} onClick={onRemove}><X size={11} /></button>}
  </span>;
}

/** What this conversation is for: goal, constraints, what is settled and what is open. The model keeps it up to date; every line can be changed here. */
export function BriefPanel({ brief, updating, readOnly, hasMessages, citations, onSave, onRetry, onOpenCitation }: {
  brief: ConversationBrief | undefined;
  updating: boolean;
  readOnly: boolean;
  hasMessages: boolean;
  /** The selected answer's sources: a settled line's numbers open the one they name. */
  citations: ConversationCitation[];
  onSave: (brief: ConversationBriefInput) => void;
  onRetry: () => void;
  onOpenCitation: (citation: ConversationCitation) => void;
}) {
  const [part, setPart] = useState<Part>("constraints");
  const [adding, setAdding] = useState("");
  const current: ConversationBrief = brief ?? { goal: "", constraints: [], settled: [], open: [], edited: [], error: null };
  const filled = Boolean(current.goal || current.constraints.length || current.settled.length || current.open.length);
  if (!filled && !updating && !current.error && !hasMessages) return null;
  const save = (change: Partial<ConversationBriefInput>) => onSave({ ...inputOf(current), ...change });
  const replace = <T,>(items: T[], at: number, next: T | null) => (next === null ? items.filter((_, index) => index !== at) : items.map((item, index) => (index === at ? next : item)));
  const add = () => {
    const text = adding.trim();
    if (!text) return;
    setAdding("");
    if (part === "goal") save({ goal: text });
    else if (part === "constraints") save({ constraints: [...current.constraints, text] });
    else if (part === "open") save({ open: [...current.open, text] });
    else save({ settled: [...current.settled, { text, refs: [], by: "you", because: "" }] });
  };
  const numbers = (item: BriefSettled) => item.by === "sources" ? item.refs.map((ref) => {
    const citation = citations.find((candidate) => candidate.ref === ref);
    return citation
      ? <button key={ref} type="button" className="brief-ref" title={citation.sourceTitle} onClick={() => onOpenCitation(citation)}>[{ref}]</button>
      : <span key={ref} className="brief-ref is-plain">[{ref}]</span>;
  }) : null;
  return <section className="brief-section" aria-label="Brief">
    <header><span className="section-label">Brief</span>{updating && <small role="status">Updating…</small>}</header>
    {current.error && !updating && <p className="brief-error" role="alert">Couldn’t update the brief · <button type="button" onClick={onRetry}>Retry</button></p>}
    {current.goal && <div className="brief-part"><small>{PART_LABEL.goal}</small><Line text={current.goal} label="goal" readOnly={readOnly} onSave={(goal) => save({ goal })} onRemove={() => save({ goal: "" })} /></div>}
    {current.constraints.length > 0 && <div className="brief-part"><small>{PART_LABEL.constraints}</small><ul>{current.constraints.map((text, at) => <li key={`${at}-${text}`}><Line text={text} label="constraint" readOnly={readOnly} onSave={(next) => save({ constraints: replace(current.constraints, at, next) })} onRemove={() => save({ constraints: replace(current.constraints, at, null) })} /></li>)}</ul></div>}
    {current.settled.length > 0 && <div className="brief-part"><small>{PART_LABEL.settled}</small><ul>{current.settled.map((item, at) => <li key={`${at}-${item.text}`}><Line text={item.text} label="settled line" readOnly={readOnly} onSave={(next) => save({ settled: replace(current.settled, at, { ...item, text: next }) })} onRemove={() => save({ settled: replace(current.settled, at, null) })}>{numbers(item)}</Line></li>)}</ul></div>}
    {current.open.length > 0 && <div className="brief-part"><small>{PART_LABEL.open}</small><ul>{current.open.map((text, at) => <li key={`${at}-${text}`}><Line text={text} label="open question" readOnly={readOnly} onSave={(next) => save({ open: replace(current.open, at, next) })} onRemove={() => save({ open: replace(current.open, at, null) })} /></li>)}</ul></div>}
    {!readOnly && <form className="brief-add" onSubmit={(event) => { event.preventDefault(); add(); }}>
      <select aria-label="Brief part" value={part} onChange={(event) => setPart(event.target.value as Part)}>{(Object.keys(PART_LABEL) as Part[]).map((key) => <option key={key} value={key}>{PART_LABEL[key]}</option>)}</select>
      <input aria-label="Add to the brief" placeholder="Add a line" maxLength={300} value={adding} onChange={(event) => setAdding(event.target.value)} />
    </form>}
  </section>;
}
