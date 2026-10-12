import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useDraggable, useDroppable } from "@dnd-kit/core";
import { AudioLines, Camera, ChevronRight, FileText, GripVertical, Link2, NotebookText, Table2, type LucideIcon } from "lucide-react";
import type { SourceKind } from "@gunther/contracts";
import { rowDetail, type ContentsRow as Row } from "./contentsTree";
import { SectionMenu, SourceMenu, type SectionActions } from "./SectionMenu";

const kindIcon: Record<SourceKind, LucideIcon> = {
  note: NotebookText, paper: FileText, link: Link2, file: FileText, image: Camera, table: Table2, recording: AudioLines, course: AudioLines,
};
const INDENT = 16;

export interface DropMark { key: string; position: "before" | "on" | "after"; allowed: boolean }

interface CommonProps {
  row: Row;
  dragDisabled: boolean;
  /** The drop being previewed, when it is on this row. */
  drop: DropMark | null;
}

/** Wires a row to dnd-kit: it can be picked up by its handle and dropped onto. */
function useRowDnd(row: Row, dragDisabled: boolean) {
  const drag = useDraggable({ id: row.key, disabled: dragDisabled || row.type === "unplaced" });
  const over = useDroppable({ id: row.key });
  const ref = (node: HTMLElement | null) => { drag.setNodeRef(node); over.setNodeRef(node); };
  return { ref, drag };
}

const markClass = (drop: DropMark | null) => drop ? ` is-drop-${drop.position}${drop.allowed ? "" : " is-drop-denied"}` : "";

/** A section heading: toggle, title (editable), count when collapsed, ⋯ menu. */
export function SectionRow({ row, dragDisabled, drop, editing, fresh, canAsk, actions, onToggle, onSave, onCancel }: CommonProps & {
  editing: boolean; fresh: boolean; canAsk: boolean; actions: SectionActions;
  onToggle: () => void; onSave: (title: string, description: string) => void; onCancel: () => void;
}) {
  const { ref, drag } = useRowDnd(row, dragDisabled);
  if (row.node?.type !== "section") return null;
  const section = row.node.section;
  const level = Math.min(row.depth + 1, 3);
  return <div ref={ref} className={`contents-row contents-section is-level-${level}${drag.isDragging ? " is-dragging" : ""}${markClass(drop)}`} style={{ paddingLeft: row.depth * INDENT }} data-row={row.key}>
    <span className="contents-handle" aria-label={`Move ${section.title}`} {...drag.attributes} {...(dragDisabled ? {} : drag.listeners)}><GripVertical size={14} /></span>
    <button type="button" className={`contents-toggle${row.collapsed ? "" : " is-open"}`} aria-expanded={!row.collapsed} aria-label={`${row.collapsed ? "Expand" : "Collapse"} ${section.title}`} onClick={onToggle}><ChevronRight size={14} /></button>
    {editing
      ? <SectionEditor title={section.title} description={section.description} fresh={fresh} onSave={onSave} onCancel={onCancel} />
      : <div className="contents-heading-block">
        <div role="heading" aria-level={level + 1} className="contents-heading" onDoubleClick={actions.onRename}>{section.title}</div>
        {section.description && <p className="contents-note">{section.description}</p>}
      </div>}
    {!editing && row.collapsed && <small className="contents-count">{row.count}</small>}
    {!editing && <SectionMenu title={section.title} actions={actions} canAsk={canAsk} />}
  </div>;
}

function SectionEditor({ title, description, fresh, onSave, onCancel }: { title: string; description: string; fresh: boolean; onSave: (title: string, description: string) => void; onCancel: () => void }) {
  const [name, setName] = useState(title);
  const [note, setNote] = useState(description);
  const field = useRef<HTMLInputElement>(null);
  const done = useRef(false);
  useEffect(() => { field.current?.focus(); field.current?.select(); }, []);
  const finish = (save: boolean) => {
    if (done.current) return;
    done.current = true;
    if (save && name.trim()) onSave(name.trim(), note.trim()); else onCancel();
  };
  const keys = (event: KeyboardEvent) => {
    if (event.key === "Enter") { event.preventDefault(); finish(true); }
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); finish(false); }
  };
  return <div className="contents-editor" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) finish(true); }}>
    <input ref={field} className="contents-title-input" aria-label="Section name" maxLength={160} value={name} onChange={(event) => setName(event.target.value)} onKeyDown={keys} />
    <input className="contents-note-input" aria-label="Section note" placeholder={fresh ? "A one-line note (optional)" : "Add a one-line note"} maxLength={400} value={note} onChange={(event) => setNote(event.target.value)} onKeyDown={keys} />
  </div>;
}

/** The Not placed yet heading: where a drop takes sources out of their section. */
export function UnplacedRow({ row, drop, onSuggest }: { row: Row; drop: DropMark | null; onSuggest: (() => void) | null }) {
  const { ref } = useRowDnd(row, true);
  return <div ref={ref} className={`contents-row contents-unplaced${markClass(drop)}`} data-row={row.key}>
    <div role="heading" aria-level={2} className="contents-heading is-unplaced">Not placed yet<span className="contents-count">{row.count}</span></div>
    {onSuggest && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={onSuggest}>Suggest sections</button>}
  </div>;
}

/** A source: handle, checkbox, kind icon, title and one grey detail. Clicking opens it. */
export function SourceRow({ row, dragDisabled, drop, selected, selecting, onSelect, onOpen, onMove, onTakeOut }: CommonProps & {
  selected: boolean; selecting: boolean;
  onSelect: (shift: boolean, toggle: boolean) => void; onOpen: () => void; onMove: () => void; onTakeOut: () => void;
}) {
  const { ref, drag } = useRowDnd(row, dragDisabled);
  const source = row.source;
  if (!source) return null;
  const Icon = kindIcon[source.kind];
  return <div ref={ref} className={`contents-row contents-source-row${selected ? " is-selected" : ""}${selecting ? " is-selecting" : ""}${drag.isDragging ? " is-dragging" : ""}${markClass(drop)}`} style={{ paddingLeft: row.depth * INDENT }} data-row={row.key}>
    <span className="contents-handle" aria-label={`Move ${source.title}`} {...drag.attributes} {...(dragDisabled ? {} : drag.listeners)}><GripVertical size={14} /></span>
    <input type="checkbox" className="contents-check" aria-label={`Select ${source.title}`} checked={selected}
      onClick={(event) => { event.stopPropagation(); onSelect(event.shiftKey, event.metaKey || event.ctrlKey); }} onChange={() => undefined} />
    <button type="button" className="contents-source" onClick={(event) => {
      if (event.shiftKey || event.metaKey || event.ctrlKey) { onSelect(event.shiftKey, event.metaKey || event.ctrlKey); return; }
      onOpen();
    }}>
      <span className={`gx-kind-icon kind-${source.kind}`}><Icon size={14} /></span>
      <strong>{source.title}</strong>
      <span className={`contents-detail${source.processing === "failed" ? " is-failed" : ""}`}>{rowDetail(source)}</span>
    </button>
    <SourceMenu title={source.title} placed={!row.loose} onMove={onMove} onTakeOut={onTakeOut} />
  </div>;
}
