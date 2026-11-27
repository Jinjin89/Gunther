import type { ArtifactMark, PlanLine } from "@gunther/contracts";
import { ArrowDown, ArrowUp, Check, Plus, X } from "lucide-react";
import { useId, useLayoutEffect, useRef, type KeyboardEvent } from "react";
import { artifactRequestId } from "./artifactConversations";

export const LINE_LISTS = {
  emphasize: { label: "What to emphasize", minRows: 0 },
  avoid: { label: "What to avoid", minRows: 0 },
  coverage: { label: "Coverage topics", minRows: 1 },
} as const;
export type LineList = keyof typeof LINE_LISTS;
export type LineEntries = Partial<Record<LineList, PlanLine | undefined>>;
export type ChangeLineList = (field: LineList, rows: PlanLine[], entry: PlanLine | undefined) => void;
type LineLists = Partial<Record<LineList, PlanLine[] | null>>;

const MAX_ROWS = 8;
const MAX_LENGTH = 300;
const normalizedText = (text: string) => text.normalize("NFKC").trim().replace(/\s+/gu, " ").toLowerCase();

function entryProblem(value: PlanLine[], text: string) {
  if (!text.trim()) return "";
  if (value.length >= MAX_ROWS) return `Keep at most ${MAX_ROWS} items.`;
  if (text.trim().length > MAX_LENGTH) return `Use at most ${MAX_LENGTH} characters.`;
  if (value.some((row) => normalizedText(row.text) === normalizedText(text))) return "This item is already in the list.";
  return "";
}

export const hasLineEntries = (entries: LineEntries) => Object.values(entries).some((entry) => entry?.text.trim());

export function collectLineEntries(lists: LineLists, entries: LineEntries): Partial<Record<LineList, PlanLine[]>> | null {
  const changes: Partial<Record<LineList, PlanLine[]>> = {};
  for (const field of Object.keys(LINE_LISTS) as LineList[]) {
    const entry = entries[field];
    if (!entry?.text.trim()) continue;
    const rows = lists[field] ?? [];
    if (entryProblem(rows, entry.text)) return null;
    changes[field] = [...rows, { ...entry, text: entry.text.trim() }];
  }
  return changes;
}

interface EditableLineListProps {
  field: LineList; value: PlanLine[]; entry: PlanLine | undefined; onChange: ChangeLineList; disabled: boolean;
  marks?: Readonly<Record<string, ArtifactMark>>;
}

export interface PreferenceFieldsProps {
  value: { emphasize: PlanLine[]; avoid: PlanLine[] };
  entries: LineEntries; marks: Readonly<Record<string, ArtifactMark>>; disabled: boolean;
  onChange: ChangeLineList;
}

export const newPlanRowId = () => `p_${artifactRequestId()}`;

export function finishLineEdit(event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) {
  if (event.key === "Enter" && !event.nativeEvent.isComposing) {
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.blur();
  }
}

export function EditableLineList({ field, value, entry, onChange, disabled, marks }: EditableLineListProps) {
  const { label, minRows } = LINE_LISTS[field];
  const headingId = useId();
  const entryErrorId = `${headingId}-entry-error`;
  const error = entryProblem(value, entry?.text ?? "");
  const addInput = useRef<HTMLInputElement>(null);
  const rows = useRef(new Map<string, HTMLTextAreaElement>());
  const focusRow = useRef<string | null | undefined>(undefined);
  useLayoutEffect(() => {
    const id = focusRow.current;
    if (id === undefined) return;
    focusRow.current = undefined;
    (id === null ? addInput.current : rows.current.get(id))?.focus();
  }, [value]);

  const move = (index: number, destination: number) => {
    const next = value.slice();
    const [row] = next.splice(index, 1);
    if (!row || destination < 0 || destination >= value.length) return;
    next.splice(destination, 0, row);
    focusRow.current = row.id;
    onChange(field, next, entry);
  };
  const remove = (index: number) => {
    if (value.length <= minRows) return;
    focusRow.current = value[index + 1]?.id ?? value[index - 1]?.id ?? null;
    onChange(field, value.filter((_, position) => position !== index), entry);
  };
  const add = () => {
    if (disabled || error || !entry?.text.trim()) return;
    // Keep the entry field ready for the next item unless this one fills the list.
    focusRow.current = value.length + 1 === MAX_ROWS ? entry.id : null;
    onChange(field, [...value, { ...entry, text: entry.text.trim() }], undefined);
  };

  return <section className="plan-lines" aria-labelledby={headingId}>
    <h3 id={headingId}>{label}</h3>
    {value.length > 0 && <ol>
      {value.map((row, index) => {
        const rowLabel = `${label}, item ${index + 1}`;
        const invalid = !row.text.trim();
        const errorId = `${headingId}-${row.id}-error`;
        return <li key={row.id}>
          <div className="plan-line-field">
            <div className="plan-line-text" data-value={row.text}>
              <textarea ref={(element) => {
                if (element) rows.current.set(row.id, element); else rows.current.delete(row.id);
              }} aria-label={rowLabel} aria-invalid={invalid || undefined} aria-describedby={invalid ? errorId : undefined}
                rows={2} value={row.text} maxLength={MAX_LENGTH} disabled={disabled} onKeyDown={finishLineEdit}
                onChange={(event) => {
                  const text = event.currentTarget.value;
                  onChange(field, value.map((item) => item.id === row.id ? { ...item, text } : item), entry);
                }} />
            </div>
            {invalid && <small id={errorId} className="plan-field-error">Enter text for this item.</small>}
            {marks?.[row.id] === "guessed" && <span className="plan-suggestion">
              Suggested
              <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" aria-label={`Accept suggestion for ${rowLabel}`}
                disabled={disabled || invalid} onClick={() => {
                  // Accepting replaces the suggestion with an authored entry at the same position.
                  const accepted = { id: newPlanRowId(), text: row.text };
                  focusRow.current = accepted.id;
                  onChange(field, value.map((item) => item.id === row.id ? accepted : item), entry);
                }}><Check size={13} aria-hidden="true" />Accept</button>
            </span>}
          </div>
          <div className="plan-row-actions">
            <button type="button" className="gx-icon-button" aria-label={`Move up ${rowLabel}`} title="Move up"
              disabled={disabled || index === 0} onClick={() => move(index, index - 1)}><ArrowUp size={14} aria-hidden="true" /></button>
            <button type="button" className="gx-icon-button" aria-label={`Move down ${rowLabel}`} title="Move down"
              disabled={disabled || index === value.length - 1} onClick={() => move(index, index + 1)}><ArrowDown size={14} aria-hidden="true" /></button>
            <button type="button" className="gx-icon-button" aria-label={`Remove ${rowLabel}`} title="Remove"
              disabled={disabled || value.length <= minRows} onClick={() => remove(index)}><X size={14} aria-hidden="true" /></button>
          </div>
        </li>;
      })}
    </ol>}
    {(value.length < MAX_ROWS || entry?.text.trim()) && <form className="plan-line-add" aria-label={`Add to ${label}`}
      onSubmit={(event) => { event.preventDefault(); event.stopPropagation(); add(); }}>
      <input ref={addInput} aria-label={`New item for ${label}`} placeholder="Add an item" value={entry?.text ?? ""}
        aria-invalid={Boolean(error) || undefined} aria-describedby={error ? entryErrorId : undefined}
        disabled={disabled} onChange={(event) => {
          const text = event.currentTarget.value;
          onChange(field, value, text ? { id: entry?.id ?? newPlanRowId(), text } : undefined);
        }} />
      <button type="submit" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={disabled || Boolean(error) || !entry?.text.trim()}>
        <Plus size={14} aria-hidden="true" />Add
      </button>
      {error && <small id={entryErrorId} className="plan-field-error">{error}</small>}
    </form>}
  </section>;
}

export function PreferenceFields({ value, entries, marks, disabled, onChange }: PreferenceFieldsProps) {
  const avoid = new Set(value.avoid.map((row) => normalizedText(row.text)).filter(Boolean));
  const overlap = value.emphasize.some((row) => avoid.has(normalizedText(row.text)));
  return <div className="plan-preferences">
    <EditableLineList field="emphasize" value={value.emphasize} entry={entries.emphasize} marks={marks} disabled={disabled} onChange={onChange} />
    <EditableLineList field="avoid" value={value.avoid} entry={entries.avoid} marks={marks} disabled={disabled} onChange={onChange} />
    {overlap && <p className="plan-warning" role="status">The same instruction appears in both preference lists.</p>}
  </div>;
}
