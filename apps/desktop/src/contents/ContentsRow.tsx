import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { useDraggable, useDroppable } from "@dnd-kit/core";
import { ArrowUpRight, AudioLines, BookOpenText, Camera, ChevronRight, FileText, FolderPlus, Inbox, Link2, NotebookText, Plus, Table2, type LucideIcon } from "lucide-react";
import type { SourceKind } from "@gunther/contracts";
import { rowDetail, type ContentsRow as Row } from "./contentsTree";
import { PopMenu, SectionMenu, SourceMenu, type SectionActions } from "./SectionMenu";

export const kindIcon: Record<SourceKind, LucideIcon> = {
  note: NotebookText, paper: BookOpenText, link: Link2, file: FileText, image: Camera, table: Table2, recording: AudioLines, course: AudioLines,
};
export const INDENT = 20;

export interface DropMark { key: string; position: "before" | "on" | "after"; allowed: boolean }

interface CommonProps {
  row: Row;
  dragDisabled: boolean;
  /** The drop being previewed, when it is on this row. */
  drop: DropMark | null;
}

/** Wires a row to dnd-kit: the whole row can be picked up (after a 6 px move) and dropped onto. */
function useRowDnd(row: Row, dragDisabled: boolean) {
  const drag = useDraggable({ id: row.key, disabled: dragDisabled || row.type === "unplaced" || row.type === "empty" });
  const over = useDroppable({ id: row.key });
  const ref = (node: HTMLElement | null) => { drag.setNodeRef(node); over.setNodeRef(node); };
  return { ref, listeners: dragDisabled ? {} : drag.listeners, dragging: drag.isDragging };
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

/** A section heading: toggle, title, its note, a count when closed, then + and ⋯ on hover. */
export function SectionRow({ row, dragDisabled, drop, current, editing, canAsk, actions, onFocus, onToggle, onSave, onCancel }: CommonProps & {
  current: boolean; editing: boolean; canAsk: boolean; actions: SectionActions;
  onFocus: () => void; onToggle: () => void; onSave: (title: string) => void; onCancel: () => void;
}) {
  const { ref, listeners, dragging } = useRowDnd(row, dragDisabled || editing);
  if (row.node?.type !== "section") return null;
  const section = row.node.section;
  const level = Math.min(row.depth + 1, 3);
  const keys = (event: KeyboardEvent) => {
    if (event.key === "F2") { event.preventDefault(); actions.onRename(); }
  };
  return <div ref={ref} {...listeners} role="presentation"
    className={`contents-row contents-section is-level-${level}${current ? " is-current" : ""}${dragging ? " is-dragging" : ""}${markClass(drop)}`}
    data-row={row.key} onClick={onFocus} onDoubleClick={editing ? undefined : actions.onRename}>
    <Guides depth={row.depth} />
    <button type="button" className={`contents-toggle${row.collapsed ? "" : " is-open"}`} aria-expanded={!row.collapsed} aria-label={`${row.collapsed ? "Expand" : "Collapse"} ${section.title}`}
      onClick={(event) => { event.stopPropagation(); onToggle(); }} onDoubleClick={(event) => event.stopPropagation()}><ChevronRight size={14} /></button>
    {editing
      ? <TitleEditor value={section.title} label="Section name" onSave={onSave} onCancel={onCancel} />
      : <span className="contents-line">
        <span role="heading" aria-level={level + 1} className="contents-heading">
          <button type="button" className="contents-main" data-focus={row.key} aria-current={current || undefined} onKeyDown={keys}>{section.title}</button>
        </span>
        {section.description && <span className="contents-note">{section.description}</span>}
      </span>}
    {!editing && row.collapsed && <small className="contents-count">{row.count}</small>}
    {!editing && <span className="contents-actions">
      <PopMenu label={`Add to ${section.title}`} triggerLabel={`Add to ${section.title}`} triggerClass="contents-icon-btn" trigger={<Plus size={15} />} items={[
        { label: "Sub-section", icon: FolderPlus, onSelect: actions.onAddSubsection },
        { label: "Sources from this library…", icon: FileText, onSelect: actions.onAddSources },
      ]} />
      <SectionMenu title={section.title} actions={actions} canAsk={canAsk} />
    </span>}
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

/**
 * A source: checkbox, kind icon, title and one grey detail. A click shows it in the side
 * panel; a double click, Enter or the ↗ button opens it.
 */
export function SourceRow({ row, dragDisabled, drop, current, selected, selecting, editing, onClick, onCheck, onOpen, onRename, onSave, onCancel, onMove, onTakeOut }: CommonProps & {
  current: boolean; selected: boolean; selecting: boolean; editing: boolean;
  onClick: (event: MouseEvent) => void; onCheck: (shift: boolean) => void; onOpen: () => void;
  onRename: () => void; onSave: (title: string) => void; onCancel: () => void; onMove: () => void; onTakeOut: () => void;
}) {
  const { ref, listeners, dragging } = useRowDnd(row, dragDisabled || editing);
  const source = row.source;
  if (!source) return null;
  const Icon = kindIcon[source.kind];
  const keys = (event: KeyboardEvent) => {
    if (event.key === "Enter") { event.preventDefault(); onOpen(); }
    if (event.key === "F2") { event.preventDefault(); onRename(); }
  };
  return <div ref={ref} {...listeners} role="presentation"
    className={`contents-row contents-source-row${current ? " is-current" : ""}${selected ? " is-selected" : ""}${selecting ? " is-selecting" : ""}${dragging ? " is-dragging" : ""}${markClass(drop)}`}
    data-row={row.key} onClick={editing ? undefined : onClick} onDoubleClick={editing ? undefined : onOpen}>
    <Guides depth={row.depth} />
    <input type="checkbox" className="contents-check" aria-label={`Select ${source.title}`} checked={selected}
      onClick={(event) => { event.stopPropagation(); onCheck(event.shiftKey); }} onDoubleClick={(event) => event.stopPropagation()} onChange={() => undefined} />
    {editing
      ? <span className="contents-source is-editing"><span className={`gx-kind-icon kind-${source.kind}`}><Icon size={13} /></span><TitleEditor value={source.title} label="Source name" onSave={onSave} onCancel={onCancel} /></span>
      : <button type="button" className="contents-source" data-focus={row.key} aria-current={current || undefined} onKeyDown={keys}>
        <span className={`gx-kind-icon kind-${source.kind}`}><Icon size={13} /></span>
        <strong>{source.title}</strong>
        <span className={`contents-detail${source.processing === "failed" ? " is-failed" : ""}${source.processing === "reading" ? " is-reading" : ""}`}>{rowDetail(source)}</span>
      </button>}
    {!editing && <span className="contents-actions">
      <button type="button" className="contents-icon-btn" aria-label={`Open ${source.title}`} title="Open" onClick={(event) => { event.stopPropagation(); onOpen(); }} onDoubleClick={(event) => event.stopPropagation()}><ArrowUpRight size={15} /></button>
      <SourceMenu title={source.title} placed={!row.loose} onOpen={onOpen} onRename={onRename} onMove={onMove} onTakeOut={onTakeOut} />
    </span>}
  </div>;
}
