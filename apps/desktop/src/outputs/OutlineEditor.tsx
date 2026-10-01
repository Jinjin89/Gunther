import type { OutlineItem } from "@gunther/contracts";
import { ArrowDown, ArrowUp, ChevronRight, Plus, X } from "lucide-react";
import { useEffect, useState } from "react";

/**
 * The plan of an output: its sections and what each is for. Collapsed to one row; "Edit outline"
 * opens it for changes, and rebuilding with it skips the planner.
 */
export function OutlineEditor({ outline, label, max, busy, onRebuild }: {
  outline: OutlineItem[];
  /** "sections" or "slides". */
  label: string;
  max: number;
  busy: boolean;
  onRebuild: (outline: OutlineItem[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(outline);
  useEffect(() => {
    setDraft(outline);
    setEditing(false);
  }, [outline]);

  const change = (index: number, item: Partial<OutlineItem>) => setDraft((current) => current.map((entry, at) => (at === index ? { ...entry, ...item } : entry)));
  const move = (index: number, by: -1 | 1) => setDraft((current) => {
    const to = index + by;
    if (to < 0 || to >= current.length) return current;
    const next = [...current];
    [next[index], next[to]] = [next[to] as OutlineItem, next[index] as OutlineItem];
    return next;
  });
  const usable = draft.filter((item) => item.heading.trim());

  if (outline.length === 0) return null;
  return <section className="outputs-outline" aria-label="Outline">
    <header>
      <button type="button" className="outputs-outline-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        <ChevronRight size={14} aria-hidden="true" />
        <strong>Outline</strong>
        <small>{outline.length} {label}</small>
      </button>
      {open && !editing && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => setEditing(true)}>Edit outline</button>}
    </header>
    {open && !editing && <ol className="outputs-outline-list">{outline.map((item, index) => <li key={index}><strong>{item.heading}</strong>{item.goal && <small>{item.goal}</small>}</li>)}</ol>}
    {open && editing && <div className="outputs-outline-edit">
      <ol>
        {draft.map((item, index) => <li key={index}>
          <div>
            <input value={item.heading} onChange={(event) => change(index, { heading: event.target.value })} aria-label={`Heading ${index + 1}`} placeholder="Heading" maxLength={160} />
            <input value={item.goal} onChange={(event) => change(index, { goal: event.target.value })} aria-label={`Goal ${index + 1}`} placeholder="What it is for" maxLength={400} />
          </div>
          <span>
            <button type="button" className="gx-icon-button" aria-label={`Move ${index + 1} up`} disabled={index === 0} onClick={() => move(index, -1)}><ArrowUp size={13} /></button>
            <button type="button" className="gx-icon-button" aria-label={`Move ${index + 1} down`} disabled={index === draft.length - 1} onClick={() => move(index, 1)}><ArrowDown size={13} /></button>
            <button type="button" className="gx-icon-button" aria-label={`Remove ${index + 1}`} disabled={draft.length <= 1} onClick={() => setDraft((current) => current.filter((_, at) => at !== index))}><X size={13} /></button>
          </span>
        </li>)}
      </ol>
      <footer>
        <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={draft.length >= max} onClick={() => setDraft((current) => [...current, { heading: "", goal: "" }])}><Plus size={13} />Add</button>
        <span>
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => { setDraft(outline); setEditing(false); }}>Cancel</button>
          <button type="button" className="gx-btn gx-btn-primary gx-btn-sm" disabled={busy || usable.length === 0} onClick={() => onRebuild(usable.map((item) => ({ heading: item.heading.trim(), goal: item.goal.trim() })))}>Rebuild with this outline</button>
        </span>
      </footer>
    </div>}
  </section>;
}
