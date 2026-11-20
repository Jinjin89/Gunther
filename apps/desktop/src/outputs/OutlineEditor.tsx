import type { OutlineItem, SkillRun } from "@gunther/contracts";
import { ArrowDown, ArrowUp, ChevronRight, Plus, X } from "lucide-react";
import { useEffect, useState } from "react";

/** Labels used when displaying saved legacy slide layouts. */
export const SLIDE_LAYOUTS: Record<string, string> = {
  title: "Title",
  composed: "Composed elements",
  big_number: "Big number",
  compare: "Comparison table",
  two_column: "Two columns",
  flow: "Steps",
  timeline: "Timeline",
  bar_chart: "Bar chart",
  line_chart: "Line chart",
  cards: "Cards",
  quote: "Quote",
  figure: "Figure",
  diagram: "Relationship diagram",
  text: "Text",
  takeaways: "Takeaways",
};

const newOutlineId = () => `p_${Array.from(crypto.getRandomValues(new Uint8Array(12)), byte => byte.toString(16).padStart(2, "0")).join("")}`;

/**
 * Show the saved plan and let Studio rebuild it from edited headings, goals and order.
 */
export function OutlineEditor({ outline, label, max, busy, onRebuild, review }: {
  outline: OutlineItem[];
  /** "sections" or "slides". */
  label: string;
  max: number;
  busy: boolean;
  onRebuild: (outline: OutlineItem[]) => void;
  review?: SkillRun["outlineReview"];
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(outline);
  useEffect(() => {
    setDraft(outline);
    setEditing(false);
  }, [outline]);

  const change = (index: number, item: Partial<OutlineItem>) =>
    setDraft((current) => current.map((entry, at) => at === index ? { ...entry, ...item } : entry));
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
      {open && !editing && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => { setDraft(outline.map(item => label === "slides" && !item.id ? { ...item, id: newOutlineId() } : item)); setEditing(true); }}>Edit outline</button>}
    </header>
    {open && !editing && <ol className="outputs-outline-list">{outline.map((item, index) => <li key={index}>
      <strong>{item.heading}</strong>{item.layout && <em>{SLIDE_LAYOUTS[item.layout] ?? item.layout}</em>}{item.goal && <small>{item.goal}</small>}{item.link && <small className="outputs-outline-link">From previous: {item.link}</small>}
      {(item.message || item.question || item.requires?.length) && <div className="outputs-outline-purpose">{item.message && <span><b>Message:</b> {item.message}</span>}{item.question && <span><b>Audience question:</b> {item.question}</span>}{!!item.requires?.length && <span>Builds on: {item.requires.map(id => outline.findIndex(page => page.id === id) + 1).join(", ")}</span>}</div>}
      {item.design && <details className="outputs-outline-design"><summary>Page design</summary><small><b>Intent:</b> {item.design.intent}</small><small><b>Visual:</b> {item.design.visual}</small><small><b>Composition:</b> {item.design.composition}</small>{item.design.emphasis && <small><b>Emphasis:</b> {item.design.emphasis}</small>}{item.design.notes && <small><b>Speaker notes:</b> {item.design.notes}</small>}</details>}
      {item.designAttempted && !item.design && <small className="outputs-outline-design-failed">Page design did not complete; the original plan was kept.</small>}
    </li>)}</ol>}
    {open && !editing && review && <details className="outputs-outline-purpose">
      <summary>Planning coverage</summary>
      {review.requirements?.map((item,index) => <p key={index}><b>{item.requirement}</b> — {item.slides.length ? `slides ${item.slides.join(", ")}` : item.cards.length ? "not yet mapped" : "context or format requirement"}{item.basis === "inferred" ? " (inferred priority)" : ""}</p>)}
      <p>{review.coverage.filter(item => item.slides.length > 0).length} of {review.coverage.length} evidence cards assigned to pages.</p>
      {review.issues.length > 0 && <details><summary>{review.repaired ? "Initial review notes used for replanning" : "Review notes"}</summary>{review.issues.map((item,index) => <p key={index}>{item.problem} {item.fix}</p>)}</details>}
    </details>}
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
        <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={draft.length >= max} onClick={() => setDraft((current) => [...current, { ...(label === "slides" ? { id: newOutlineId() } : {}), heading: "", goal: "" }])}><Plus size={13} />Add</button>
        <span>
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => { setDraft(outline); setEditing(false); }}>Cancel</button>
          <button type="button" className="gx-btn gx-btn-primary gx-btn-sm" disabled={busy || usable.length === 0} onClick={() => onRebuild(usable.map(({ id, heading, goal }) => ({
            ...(id ? { id } : {}), heading: heading.trim(), goal: goal.trim(),
          })))}>Rebuild with this outline</button>
        </span>
      </footer>
    </div>}
  </section>;
}
