import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useDraggable, useDroppable } from "@dnd-kit/core";
import { AudioLines, BookOpenText, Camera, ChevronRight, FileText, GripVertical, Inbox, Link2, NotebookText, Table2, type LucideIcon } from "lucide-react";
import type { SourceKind } from "@gunther/contracts";
import { rowDetail, type ContentsRow as Row } from "./contentsTree";
import { SectionMenu, SourceMenu, type SectionActions } from "./SectionMenu";

export const kindIcon: Record<SourceKind, LucideIcon> = {
  note: NotebookText, paper: BookOpenText, link: Link2, file: FileText, image: Camera, table: Table2, recording: AudioLines, course: AudioLines,
};
export const INDENT = 14;

export interface DropMark { key: string; position: "before" | "on" | "after"; allowed: boolean }

interface CommonProps {
  row: Row;
  dragDisabled: boolean;
  /** The drop being previewed, when it is on this row. */
  drop: DropMark | null;
}

/** The row is the drop target. Its handle is the only drag activator. */
function useRowDnd(row: Row, dragDisabled: boolean) {
  const drag = useDraggable({ id: row.key, disabled: dragDisabled || row.type === "unplaced" || row.type === "empty" });
  const over = useDroppable({ id: row.key });
  const ref = (node: HTMLElement | null) => { drag.setNodeRef(node); over.setNodeRef(node); };
  const handle = { ref: drag.setActivatorNodeRef, disabled: dragDisabled, ...drag.attributes, ...drag.listeners };
  return { ref, handle, dragging: drag.isDragging };
}

const markClass = (drop: DropMark | null) => drop ? ` is-drop-${drop.position}${drop.allowed ? "" : " is-drop-denied"}` : "";

/** One faint line under each open section above this row, so the levels read at a glance. */
function Guides({ depth }: { depth: number }) {
  return <span className="contents-guides" aria-hidden="true" style={{ width: depth * INDENT }} />;
}

/** Edits a name in place: Enter or leaving keeps it, Esc puts the old one back. */
export function TitleEditor({ value, label, onSave, onCancel }: { value: string; label: string; onSave: (title: string) => void; onCancel: () => void }) {
  const [name, setName] = useState(value);
  const field = useRef<HTMLInputElement>(null);
  const done = useRef(false);
  useEffect(() => { field.current?.focus(); field.current?.select(); }, []);
  const finish = (save: boolean) => {
    if (done.current) return;
    done.current = true;
    if (save && name.trim()) onSave(name.trim()); else onCancel();
  };
  const keys = (event: KeyboardEvent) => {
    event.stopPropagation();
    if (event.key === "Enter") { event.preventDefault(); finish(true); }
    if (event.key === "Escape") { event.preventDefault(); finish(false); }
  };
  return <input ref={field} className="contents-title-input" aria-label={label} maxLength={160} value={name}
    onChange={(event) => setName(event.target.value)} onKeyDown={keys} onBlur={() => finish(true)}
    onClick={(event) => event.stopPropagation()} onDoubleClick={(event) => event.stopPropagation()} />;
}

export function ScopeTick({ label, checked, mixed = false, disabled, onCheck }: {
  label: string; checked: boolean; mixed?: boolean; disabled?: boolean; onCheck: (checked: boolean) => void;
}) {
  const box = useRef<HTMLInputElement>(null);
  useEffect(() => { if (box.current) box.current.indeterminate = mixed; }, [mixed]);
  return <input ref={box} type="checkbox" className="contents-check" aria-label={label}
    checked={checked} disabled={disabled} onClick={(event) => event.stopPropagation()}
    onDoubleClick={(event) => event.stopPropagation()} onChange={(event) => onCheck(event.target.checked)} />;
}

/** A section heading with its own handle and actions. */
export function SectionRow({ row, dragDisabled, drop, editing, sourceCount, scope, actions, onToggle, onSave, onCancel }: CommonProps & {
  editing: boolean; sourceCount: number; scope: { checked: boolean; mixed: boolean; disabled: boolean; onCheck: (checked: boolean) => void };
  actions: SectionActions; onToggle: () => void; onSave: (title: string) => void; onCancel: () => void;
}) {
  const { ref, handle, dragging } = useRowDnd(row, dragDisabled || editing);
  if (row.node?.type !== "section") return null;
  const section = row.node.section;
  const level = Math.min(row.depth + 1, 3);
  const keys = (event: KeyboardEvent) => {
    if (event.key === "F2") { event.preventDefault(); actions.onRename(); }
  };
  return <div ref={ref} role="presentation"
    className={`contents-row contents-section is-level-${level}${dragging ? " is-dragging" : ""}${markClass(drop)}`}
    data-row={row.key}>
    <Guides depth={row.depth} />
    <button {...handle} type="button" className="contents-drag-handle" aria-label={`Drag ${section.title}`}><GripVertical size={14} /></button>
    <button type="button" className={`contents-toggle${row.collapsed ? "" : " is-open"}`} aria-expanded={!row.collapsed} aria-label={`${row.collapsed ? "Expand" : "Collapse"} ${section.title}`}
      onClick={(event) => { event.stopPropagation(); onToggle(); }} onDoubleClick={(event) => event.stopPropagation()}><ChevronRight size={14} /></button>
    <ScopeTick label={`Select sources in ${section.title}`} {...scope} />
    {editing
      ? <TitleEditor value={section.title} label="Section name" onSave={onSave} onCancel={onCancel} />
      : <span className="contents-line">
        <span role="heading" aria-level={level + 1} className="contents-heading">
          <button type="button" className="contents-main-btn" data-focus={row.key} onClick={onToggle} onKeyDown={keys}>{section.title}</button>
        </span>
        {section.description && <span className="contents-note">{section.description}</span>}
      </span>}
    {!editing && (row.collapsed || level === 1) && <small className="contents-count" title={row.count}>{level === 1 ? sourceCount : row.count}</small>}
    {!editing && <span className="contents-actions"><SectionMenu title={section.title} actions={actions} /></span>}
  </div>;
}

/** The Not placed yet heading: where new sources wait, and where a drop takes them out of a section. */
export function UnplacedRow({ row, drop, onSuggest }: { row: Row; drop: DropMark | null; onSuggest: (() => void) | null }) {
  const { ref } = useRowDnd(row, true);
  return <div ref={ref} className={`contents-row contents-unplaced${markClass(drop)}`} data-row={row.key}>
    <span className="contents-unplaced-icon" aria-hidden="true"><Inbox size={14} /></span>
    <span className="contents-line"><span role="heading" aria-level={2} className="contents-heading">Not placed yet</span><span className="contents-note">New sources wait here</span></span>
    <small className="contents-count">{row.count}</small>
    {onSuggest && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={onSuggest}>Suggest sections</button>}
  </div>;
}

/** Stands in an open, empty section: somewhere to drop, and the two ways to fill it. */
export function EmptyRow({ row, drop, onAddSources, onAddSubsection }: { row: Row; drop: DropMark | null; onAddSources: () => void; onAddSubsection: () => void }) {
  const { ref } = useRowDnd(row, true);
  return <div ref={ref} className={`contents-row contents-empty-row${markClass(drop)}`} data-row={row.key}>
    <Guides depth={row.depth} />
    <span className="contents-empty-text">Empty. Drag sources here, or
      <button type="button" className="contents-inline-btn" onClick={onAddSources}>add sources</button>·
      <button type="button" className="contents-inline-btn" onClick={onAddSubsection}>add a sub-section</button>
    </span>
  </div>;
}

export function SourceRow({ row, dragDisabled, drop, current, checked, editing, onSelect, onCheck, onRename, onSave, onCancel, onMove, onTakeOut, onTrash }: CommonProps & {
  current: boolean; checked: boolean; editing: boolean;
  onSelect: () => void; onCheck: (checked: boolean) => void;
  onRename: () => void; onSave: (title: string) => void; onCancel: () => void; onMove: () => void; onTakeOut: () => void; onTrash: () => void;
}) {
  const { ref, handle, dragging } = useRowDnd(row, dragDisabled || editing);
  const source = row.source;
  if (!source) return null;
  const Icon = kindIcon[source.kind];
  return <div ref={ref} role="presentation"
    className={`contents-row contents-source-row${current ? " is-current" : ""}${dragging ? " is-dragging" : ""}${markClass(drop)}`}
    data-row={row.key}>
    <Guides depth={row.depth} />
    <button {...handle} type="button" className="contents-drag-handle" aria-label={`Drag ${source.title}`}><GripVertical size={14} /></button>
    <ScopeTick label={`Select ${source.title}`} checked={checked} onCheck={onCheck} />
    {editing
      ? <span className="contents-source is-editing"><span className={`gx-kind-icon kind-${source.kind}`}><Icon size={13} /></span><TitleEditor value={source.title} label="Source name" onSave={onSave} onCancel={onCancel} /></span>
      : <button type="button" className="contents-source" data-focus={row.key} aria-current={current || undefined} onClick={onSelect} onKeyDown={(event) => { if (event.key === "F2") { event.preventDefault(); onRename(); } }}>
        <span className={`gx-kind-icon kind-${source.kind}`}><Icon size={13} /></span>
        <strong title={source.title}>{source.title}</strong>
        <span className={`contents-detail${source.processing === "failed" ? " is-failed" : ""}${source.processing === "reading" ? " is-reading" : ""}`}>{rowDetail(source)}</span>
      </button>}
    {!editing && <span className="contents-actions">
      <SourceMenu title={source.title} placed={!row.loose} onRename={onRename} onMove={onMove} onTakeOut={onTakeOut} onTrash={onTrash} />
    </span>}
  </div>;
}
