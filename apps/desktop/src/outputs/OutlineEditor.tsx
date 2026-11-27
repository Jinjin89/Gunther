import type { ArtifactPart, OutlineItem, PlanPart, PlanPartEdit, SkillRun } from "@gunther/contracts";
import { ArrowDown, ArrowUp, Plus, X } from "lucide-react";
import { useId, useLayoutEffect, useRef } from "react";
import { finishLineEdit, newPlanRowId } from "./PreferenceFields";
import { sectionHash } from "./sections";

export interface OutlineEditorProps {
  value: PlanPart[]; onChange: (next: PlanPart[]) => void;
  unitLabel: string; max: number; layouts: readonly string[];
  firstPartRole?: string | null | undefined;
  readOnly: boolean; disabled: boolean; review?: SkillRun["outlineReview"];
}

const LAYOUT_LABELS: Record<string, string> = {
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

const headingIssue = (heading: string) => !heading.trim() ? "Enter a heading." :
  /[\n\r\v\f\u001c-\u001e\u0085\u2028\u2029]/u.test(heading) ? "Use a single line for the heading." :
    heading.trim().length > 160 ? "Use at most 160 characters." : "";

export async function normalizeSavedOutline(versionId: string, outline: OutlineItem[], parts: ArtifactPart[] = []): Promise<PlanPart[]> {
  // Current reads already carry the version's canonical outline IDs.
  const used = new Set<string>();
  const reserved = new Set(outline.map((row) => row.id));
  const positions = new Map(parts.map((part) => [part.position - 1, part.itemId]));
  const validId = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
  const rows: PlanPart[] = [];
  for (const [position, row] of outline.entries()) {
    let id = row.id;
    if (!id || !validId.test(id) || used.has(id)) {
      // Older service responses may only have the saved row's position.
      const stored = positions.get(position);
      if (stored && validId.test(stored) && !reserved.has(stored) && !used.has(stored)) id = stored;
      else {
        let nonce = 0;
        do {
          const hash = await sectionHash(JSON.stringify([versionId, position, nonce++]));
          id = `p_${hash.slice(0, 62)}`;
        } while (reserved.has(id) || used.has(id));
      }
    }
    used.add(id);
    rows.push({ ...row, id, notes: row.notes ?? "", layout: row.layout ?? null });
  }
  return rows;
}

export function partEdits(value: PlanPart[]): PlanPartEdit[] {
  return value.map(({ id, heading, goal, layout, notes }) => ({
    id, heading: heading.trim(), goal: goal.trim(), layout, notes: notes?.trim() ?? "",
  }));
}

export function outlineIssue(value: PlanPart[], max: number): string {
  if (!value.length || value.length > max) return `Keep between 1 and ${max} outline items.`;
  if (value.some((row) => headingIssue(row.heading) || row.goal.trim().length > 400 || (row.notes?.trim().length ?? 0) > 400)) {
    return "Complete each outline heading and check the field lengths.";
  }
  return "";
}

function OutlineReview({ review }: { review: NonNullable<OutlineEditorProps["review"]> }) {
  return <details className="outputs-outline-purpose">
    <summary>Planning coverage</summary>
    {review.requirements?.map((item, index) => <p key={index}><b>{item.requirement}</b> · {
      item.slides.length ? `Slides ${item.slides.join(", ")}` : item.cards.length ? "Unmapped" : "Context or format requirement"
    }{item.basis === "inferred" ? " (inferred priority)" : ""}</p>)}
    <p>{review.coverage.filter((item) => item.slides.length > 0).length} of {review.coverage.length} evidence cards assigned to pages.</p>
    {review.issues.length > 0 && <details><summary>{review.repaired ? "Initial review notes used for replanning" : "Review notes"}</summary>
      {review.issues.map((item, index) => <p key={index}>{item.problem} {item.fix}</p>)}
    </details>}
  </details>;
}

export function OutlineEditor({ value, onChange, unitLabel, max, layouts, firstPartRole, readOnly, disabled, review }: OutlineEditorProps) {
  const hasTitle = layouts.includes("title");
  const firstMovable = firstPartRole || hasTitle ? 1 : 0;
  const uid = useId();
  const headings = useRef(new Map<string, HTMLInputElement>());
  const addButton = useRef<HTMLButtonElement>(null);
  const focus = useRef<string | null | undefined>(undefined);
  useLayoutEffect(() => {
    const id = focus.current;
    if (id === undefined) return;
    focus.current = undefined;
    (id === null ? addButton.current : headings.current.get(id))?.focus();
  }, [value]);

  const change = (id: string, edit: Partial<PlanPartEdit>) => onChange(value.map((row) => row.id === id ? { ...row, ...edit } : row));
  const move = (index: number, to: number) => {
    if (index < firstMovable || to < firstMovable || to >= value.length) return;
    const next = value.slice();
    const [row] = next.splice(index, 1);
    if (!row) return;
    next.splice(to, 0, row);
    focus.current = row.id;
    onChange(next);
  };
  const remove = (index: number) => {
    if (value.length <= 1 || index < firstMovable) return;
    focus.current = value[index + 1]?.id ?? value[index - 1]?.id ?? null;
    onChange(value.filter((_, position) => position !== index));
  };
  const add = () => {
    if (value.length >= max) return;
    const row: PlanPart = { id: newPlanRowId(), heading: "", goal: "", layout: null, notes: "" };
    focus.current = row.id;
    onChange([...value, row]);
  };

  return <section className="outputs-outline" aria-labelledby={uid}>
    <header>
      <h2 id={uid}>Outline</h2>
      <small>{value.length} {unitLabel}{value.length === 1 ? "" : "s"}</small>
    </header>
    {readOnly ? <ol className="outputs-outline-list">{value.map((row, index) => <li key={row.id}>
      <strong>{row.heading}</strong>
      {index === 0 && firstPartRole ? <em>{firstPartRole}</em> : row.layout && <em>{LAYOUT_LABELS[row.layout] ?? row.layout}</em>}
      {row.goal && <small>{row.goal}</small>}
      {row.notes && <small className="outputs-outline-notes"><b>Writer notes</b> {row.notes}</small>}
      {row.link && <small>From previous · {row.link}</small>}
      {(row.message || row.question || row.requires?.length) ? <div className="outputs-outline-purpose">
        {row.message && <span><b>Message</b> {row.message}</span>}
        {row.question && <span><b>Audience question</b> {row.question}</span>}
        {!!row.requires?.length && <span>Builds on {row.requires.map((id) => {
          const position = value.findIndex((part) => part.id === id);
          return position === -1 ? id : `${unitLabel} ${position + 1}`;
        }).join(", ")}</span>}
      </div> : null}
      {row.design && <details className="outputs-outline-design"><summary>Page design</summary>
        <small><b>Intent</b> {row.design.intent}</small><small><b>Visual</b> {row.design.visual}</small>
        <small><b>Composition</b> {row.design.composition}</small>
        {row.design.emphasis && <small><b>Emphasis</b> {row.design.emphasis}</small>}
        {row.design.notes && <small><b>Speaker notes</b> {row.design.notes}</small>}
      </details>}
      {row.designAttempted && !row.design && <small className="outputs-outline-design-failed">Page design did not complete. The original plan was kept.</small>}
    </li>)}</ol> : <div className="outputs-outline-edit">
      <ol>{value.map((row, index) => {
        const position = index + 1;
        const error = headingIssue(row.heading);
        const errorId = `${uid}-${row.id}-heading`;
        const choices = layouts.filter((layout) => !hasTitle || (layout === "title") === (index === 0));
        return <li key={row.id}>
          <div className="outputs-outline-fields">
            {index === 0 && firstPartRole && <small className="outputs-outline-role">{firstPartRole}</small>}
            <label>Heading<input ref={(element) => {
              if (element) headings.current.set(row.id, element); else headings.current.delete(row.id);
            }} value={row.heading} aria-label={`Heading ${position}`} aria-invalid={Boolean(error) || undefined}
              aria-describedby={error ? errorId : undefined} maxLength={160} disabled={disabled}
              onKeyDown={finishLineEdit} onChange={(event) => change(row.id, { heading: event.currentTarget.value })} /></label>
            {error && <small className="plan-field-error" id={errorId}>{error}</small>}
            <label>Goal<input value={row.goal} aria-label={`Goal ${position}`} maxLength={400} disabled={disabled}
              onKeyDown={finishLineEdit} onChange={(event) => change(row.id, { goal: event.currentTarget.value })} /></label>
            <label>Writer notes<textarea value={row.notes ?? ""} aria-label={`Writer notes ${position}`} rows={2} maxLength={400} disabled={disabled}
              onChange={(event) => change(row.id, { notes: event.currentTarget.value })} /></label>
            {layouts.length > 0 && <label>Layout<select value={row.layout ?? ""} aria-label={`Layout ${position}`} disabled={disabled}
              onChange={(event) => change(row.id, { layout: event.currentTarget.value || null })}>
              <option value="">Auto</option>
              {row.layout && !choices.includes(row.layout) && <option value={row.layout}>{LAYOUT_LABELS[row.layout] ?? row.layout} (saved)</option>}
              {choices.map((layout) => <option key={layout} value={layout}>{LAYOUT_LABELS[layout] ?? layout}</option>)}
            </select></label>}
          </div>
          <div className="plan-row-actions">
            <button type="button" className="gx-icon-button" aria-label={`Move up ${unitLabel} ${position}`} title="Move up"
              disabled={disabled || index <= firstMovable} onClick={() => move(index, index - 1)}><ArrowUp size={14} aria-hidden="true" /></button>
            <button type="button" className="gx-icon-button" aria-label={`Move down ${unitLabel} ${position}`} title="Move down"
              disabled={disabled || index < firstMovable || index === value.length - 1} onClick={() => move(index, index + 1)}><ArrowDown size={14} aria-hidden="true" /></button>
            <button type="button" className="gx-icon-button" aria-label={`Remove ${unitLabel} ${position}`} title="Remove"
              disabled={disabled || value.length <= 1 || index < firstMovable} onClick={() => remove(index)}><X size={14} aria-hidden="true" /></button>
          </div>
        </li>;
      })}</ol>
      <button type="button" ref={addButton} className="gx-btn gx-btn-quiet gx-btn-sm" disabled={disabled || value.length >= max}
        onClick={add}><Plus size={14} aria-hidden="true" />Add {unitLabel}</button>
    </div>}
    {readOnly && review && <OutlineReview review={review} />}
  </section>;
}
