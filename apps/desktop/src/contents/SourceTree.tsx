import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { DndContext, PointerSensor, closestCenter, pointerWithin, useSensor, useSensors, type CollisionDetection, type DragEndEvent, type DragMoveEvent, type DragStartEvent } from "@dnd-kit/core";
import { ChevronsDownUp, ChevronsUpDown, Plus, Search, X } from "lucide-react";
import type { ContentsItemRef, LibraryContents as Contents } from "@gunther/contracts";
import { persistentStorage } from "../persistentStorage";
import { knowledgeApi } from "../api";
import { applyMove, buildTree, dropTarget, findSource, naturalOrder, nodeRef, sourcesIn, subtreeStats, visibleRows, type ContentsNode, type ContentsRow, type DropPosition } from "./contentsTree";
import { EmptyRow, ScopeTick, SectionRow, SourceRow, UnplacedRow, type DropMark } from "./ContentsRow";
import { allSources, type SourceSelection } from "./sourceScope";
import { MovePicker } from "./MovePicker";
import { SectionSuggestions } from "./SectionPanels";
import "./contents.css";

const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;
const plural = (count: number, word: string) => `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;
const SUGGEST_FROM = 6;
const POLL_MS = 2500;

type Picker = { mode: "move"; moving: ContentsItemRef[] } | { mode: "add"; section: { id: string; title: string } };
type SectionNode = Extract<ContentsNode, { type: "section" }>;

// Rows are stacked without gaps, so the pointer is always over one; fall back to the
// nearest centre when it is not.
const collide: CollisionDetection = (args) => {
  const hits = pointerWithin(args);
  return hits.length > 0 ? hits : closestCenter(args);
};

const readCollapsed = (baseId: string): Set<string> => {
  try {
    const raw = JSON.parse(persistentStorage.getItem(`gunther:contents-collapsed:${baseId}`) ?? "[]") as unknown;
    return new Set(Array.isArray(raw) ? raw.filter((item): item is string => typeof item === "string") : []);
  } catch { return new Set(); }
};

export function SourceTree({ baseId, contents, onContents, selection, selectedIds, onScope, activeSourceId, onSource, onOpenSource, addSource }: {
  baseId: string;
  contents: Contents | null;
  onContents: (contents: Contents | null) => void;
  selection: SourceSelection;
  selectedIds: string[];
  onScope: (selection: SourceSelection) => void;
  activeSourceId: string | null;
  onSource: (id: string) => void;
  onOpenSource: (id: string, queue: string[]) => void;
  addSource: ReactNode;
}) {
  const [unavailable, setUnavailable] = useState(false);
  const [error, setError] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(() => readCollapsed(baseId));
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState<{ id: string; kind: "section" | "source"; fresh: boolean } | null>(null);
  const [picker, setPicker] = useState<Picker | null>(null);
  const [noteSection, setNoteSection] = useState<string | null>(null);
  const [suggesting, setSuggesting] = useState(false);
  const [dragging, setDragging] = useState<ContentsItemRef[] | null>(null);
  const [drop, setDrop] = useState<DropMark | null>(null);
  // Where the pointer is while dragging: the chip follows it, and it decides above/below.
  const [pointer, setPointer] = useState<{ x: number; y: number } | null>(null);
  const pointerRef = useRef<{ x: number; y: number } | null>(null);
  useEffect(() => {
    if (!dragging) return;
    const follow = (event: PointerEvent) => {
      pointerRef.current = { x: event.clientX, y: event.clientY };
      setPointer(pointerRef.current);
    };
    window.addEventListener("pointermove", follow);
    return () => window.removeEventListener("pointermove", follow);
  }, [dragging]);
  const contentsRef = useRef<Contents | null>(null);
  contentsRef.current = contents;

  const tree = useMemo(() => buildTree(contents ?? { sections: [], placements: [], sources: [] }), [contents]);
  const filtering = filter.trim() !== "";
  const rows = useMemo(() => visibleRows(tree, collapsed, filter), [tree, collapsed, filter]);
  const sourceIds = useMemo(() => rows.filter((row) => row.type === "source").map((row) => row.id), [rows]);
  const selected = new Set(selectedIds);
  const check = (ids: string[], checked: boolean) => {
    const next = new Set(selectedIds);
    for (const id of ids) {
      if (checked) next.add(id); else next.delete(id);
    }
    onScope({ mode: "selected", sourceIds: [...next] });
  };

  const load = useCallback(async () => {
    try {
      const next = await knowledgeApi.contents(baseId);
      onContents(next); setUnavailable(false);
    } catch { setUnavailable(true); }
  }, [baseId, onContents]);

  useEffect(() => {
    void load();
    const refresh = () => void load();
    window.addEventListener("gunther:sources-updated", refresh);
    window.addEventListener("gunther:topics-updated", refresh);
    return () => { window.removeEventListener("gunther:sources-updated", refresh); window.removeEventListener("gunther:topics-updated", refresh); };
  }, [baseId, load]);

  // Sources still being read change on their own: look again until they are done.
  const reading = contents?.sources.some((source) => source.processing === "reading" || source.durationPending) ?? false;
  useEffect(() => {
    if (!reading) return;
    const timer = window.setTimeout(() => void load(), POLL_MS);
    return () => window.clearTimeout(timer);
  }, [reading, contents, load]);

  const remember = (next: Set<string>) => {
    setCollapsed(next);
    persistentStorage.setItem(`gunther:contents-collapsed:${baseId}`, JSON.stringify([...next]));
  };
  const toggle = (id: string) => { const next = new Set(collapsed); if (!next.delete(id)) next.add(id); remember(next); };
  const expand = (ids: string[]) => {
    if (!ids.some((id) => collapsed.has(id))) return;
    const next = new Set(collapsed);
    for (const id of ids) next.delete(id);
    remember(next);
  };
  const withChildren = [...tree.sections.values()].filter((node) => node.children.some((child) => child.type === "section" || child.type === "source"));
  const anyOpen = withChildren.some((node) => !collapsed.has(node.id));
  const toggleAll = () => remember(anyOpen ? new Set(withChildren.map((node) => node.id)) : new Set());

  /** Show the result at once, ask the server, then take the server's word; on failure put the rows back. */
  const change = async (optimistic: Contents | null, call: () => Promise<Contents>) => {
    const before = contentsRef.current;
    if (optimistic) onContents(optimistic);
    setError("");
    try {
      onContents(await call());
    } catch (reason) {
      if (before) onContents(before);
      setError(message(reason, "That change could not be saved."));
    }
  };
  const move = (items: ContentsItemRef[], parentId: string | null, index: number | null) => {
    const now = contentsRef.current;
    if (!now || items.length === 0) return Promise.resolve();
    if (parentId) expand([parentId]);
    return change(applyMove(now, { items, parentId, index }), () => knowledgeApi.moveContents(baseId, { items, parentId, index }));
  };

  const addSection = async (parentId: string | null) => {
    setError("");
    try {
      const created = await knowledgeApi.saveTopic(baseId, { title: "New section", description: "", parentId });
      if (parentId) expand([parentId]);
      await load();
      setEditing({ id: created.id, kind: "section", fresh: true });
    } catch (reason) { setError(message(reason, "The section could not be added.")); }
  };
  const saveSection = async (id: string, patch: { title?: string; description?: string }) => {
    const section = contentsRef.current?.sections.find((item) => item.id === id);
    setEditing(null);
    if (!section) return;
    const title = patch.title ?? section.title;
    const description = patch.description ?? section.description;
    if (section.title === title && section.description === description) return;
    const now = contentsRef.current!;
    onContents({ ...now, sections: now.sections.map((item) => item.id === id ? { ...item, title, description } : item) });
    try {
      await knowledgeApi.saveTopic(baseId, { title, description, parentId: section.parentId, version: section.version }, id);
    } catch (reason) { setError(message(reason, "The section could not be saved.")); }
    await load();
  };
  const renameSource = async (id: string, title: string) => {
    setEditing(null);
    const now = contentsRef.current;
    const source = now?.sources.find((item) => item.id === id);
    if (!now || !source || !title || source.title === title) return;
    await change({ ...now, sources: now.sources.map((item) => item.id === id ? { ...item, title } : item) }, async () => {
      await knowledgeApi.renameSource(id, title);
      return knowledgeApi.contents(baseId);
    });
  };
  const cancelEdit = async () => {
    const was = editing;
    setEditing(null);
    if (was?.fresh) await change(null, () => knowledgeApi.deleteSection(baseId, was.id));
  };
  const sortSection = (node: SectionNode) => {
    const now = contentsRef.current;
    if (!now || node.children.length < 2) return;
    const children = naturalOrder(node.children).map(nodeRef);
    void change(applyMove(now, { items: children, parentId: node.id, index: 0 }), () => knowledgeApi.orderContents(baseId, { parentId: node.id, children }));
  };
  const open = (id: string) => onOpenSource(id, sourceIds);

  const navigable = rows.filter((row) => row.type === "section" || row.type === "source");
  const focusRow = (row: ContentsRow | undefined) => {
    if (row) document.querySelector<HTMLElement>(`[data-focus="${CSS.escape(row.key)}"]`)?.focus();
  };
  const keys = (event: KeyboardEvent) => {
    const key = (event.target as HTMLElement).dataset?.focus;
    if (!key) return;
    const at = navigable.findIndex((row) => row.key === key);
    const row = navigable[at];
    if (!row) return;
    if (event.key === "ArrowDown") { event.preventDefault(); focusRow(navigable[at + 1]); }
    else if (event.key === "ArrowUp") { event.preventDefault(); focusRow(navigable[at - 1]); }
    else if (event.key === "ArrowRight" && row.type === "section") {
      event.preventDefault();
      if (row.collapsed) toggle(row.id); else focusRow(navigable[at + 1]?.parentId === row.id ? navigable[at + 1] : undefined);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      if (row.type === "section" && !row.collapsed) toggle(row.id);
      else if (row.parentId) focusRow(navigable.find((item) => item.type === "section" && item.id === row.parentId));
    }
  };

  // Drag and drop: the whole row can be picked up once the pointer has moved 6 px.
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
  const carried = (key: string): ContentsItemRef[] => {
    const row = rows.find((item) => item.key === key);
    if (!row || (row.type !== "section" && row.type !== "source")) return [];
    return [{ kind: row.type, id: row.id }];
  };
  const place = (event: DragMoveEvent | DragEndEvent, items: ContentsItemRef[]) => {
    const over = event.over;
    const row = over && rows.find((item) => item.key === over.id);
    if (!over || !row) return null;
    // The row as it is on screen now, against the pointer.
    const element = document.querySelector(`[data-row="${CSS.escape(String(over.id))}"]`);
    const rect = element?.getBoundingClientRect() ?? over.rect;
    const box = event.active.rect.current.translated;
    const y = pointerRef.current?.y ?? (box ? box.top + box.height / 2 : rect.top + rect.height / 2);
    const fraction = rect.height > 0 ? (y - rect.top) / rect.height : 0.5;
    const position: DropPosition = row.type === "unplaced" || row.type === "empty" ? "on" : row.type === "section" ? (fraction < 0.25 ? "before" : "on") : (fraction < 0.5 ? "before" : "after");
    return { row, position, target: dropTarget(tree, rows, row.key, position, items) };
  };
  const onDragStart = (event: DragStartEvent) => {
    const start = event.activatorEvent;
    pointerRef.current = start instanceof MouseEvent ? { x: start.clientX, y: start.clientY } : null;
    setPointer(pointerRef.current);
    setDragging(carried(String(event.active.id)));
  };
  const onDragMove = (event: DragMoveEvent) => {
    const found = dragging ? place(event, dragging) : null;
    setDrop(found ? { key: found.row.key, position: found.position, allowed: found.target !== null } : null);
  };
  const onDragEnd = (event: DragEndEvent) => {
    const items = dragging;
    const found = items ? place(event, items) : null;
    setDragging(null); setDrop(null); setPointer(null); pointerRef.current = null;
    if (items && found?.target) void move(items, found.target.parentId, found.target.index);
  };
  const stopDrag = () => { setDragging(null); setDrop(null); setPointer(null); pointerRef.current = null; };

  const unplacedPapers = tree.unplaced.filter((source) => source.kind === "paper").length;
  const sectionActions = (node: SectionNode) => ({
    onAddSubsection: () => void addSection(node.id),
    onRename: () => setEditing({ id: node.id, kind: "section", fresh: false }),
    onAddSources: () => setPicker({ mode: "add", section: { id: node.id, title: node.section.title } }),
    onSort: () => sortSection(node),
    onMove: () => setPicker({ mode: "move", moving: [{ kind: "section", id: node.id }] }),
    onEditNote: () => setNoteSection(node.id),
    onDelete: () => {
      const { sections, sources } = subtreeStats(node);
      const children = sections ? ` and its ${plural(sections, "sub-section")}` : "";
      const unplaced = sources ? ` Move ${plural(sources, "source")} to Not placed yet.` : "";
      if (window.confirm(`Delete “${node.section.title}”${children}?${unplaced}`)) {
        void change(null, () => knowledgeApi.deleteSection(baseId, node.id));
      }
    },
  });

  return <section className="contents" aria-label="Sources">
    <div className="contents-top">
      <h2>Sources</h2>
      <div className="contents-toolbar">
        <label className="contents-filter"><Search size={14} />
          <input value={filter} placeholder="Filter by name" aria-label="Filter contents" onChange={(event) => setFilter(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape" && filter) { event.stopPropagation(); setFilter(""); } }} />
          {filter && <button type="button" aria-label="Clear filter" onClick={() => setFilter("")}><X size={13} /></button>}
        </label>
        {withChildren.length > 0 && !filtering && <button type="button" className="contents-icon-btn is-visible is-bordered" aria-label={anyOpen ? "Collapse all" : "Expand all"} title={anyOpen ? "Collapse all" : "Expand all"} onClick={toggleAll}>
          {anyOpen ? <ChevronsDownUp size={15} /> : <ChevronsUpDown size={15} />}
        </button>}
        <span className="contents-toolbar-end">
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" aria-label="Add section" onClick={() => void addSection(null)}><Plus size={13} />Section</button>
        </span>
      </div>
      {error && <p role="alert" className="contents-error">{error}<button type="button" aria-label="Dismiss" onClick={() => setError("")}><X size={13} /></button></p>}
      <label className="contents-scope">
        <ScopeTick label="All sources" checked={selection.mode === "all"} mixed={selection.mode === "selected" && selected.size > 0}
          onCheck={(checked) => onScope(checked ? allSources : { mode: "selected", sourceIds: [] })} />
        <span>All sources</span><small>{contents?.sources.length ?? ""}</small>
      </label>
      {contents && selection.mode === "selected" && selected.size === 0 && <p id="empty-source-scope" className="contents-loading" role="status">Select at least one source</p>}
    </div>
    <div className="contents-scroll">
      {!contents && <p className="contents-loading">{unavailable ? "Sources could not be loaded." : "Loading sources…"}</p>}
      {contents?.sources.length === 0 && <p className="contents-loading">No sources yet.</p>}
      {contents && filtering && rows.length === 0 && <p className="contents-loading">Nothing matches “{filter.trim()}”.</p>}
      <DndContext sensors={sensors} collisionDetection={collide} onDragStart={onDragStart} onDragMove={onDragMove} onDragEnd={onDragEnd} onDragCancel={stopDrag}>
        <div className="contents-list" onKeyDown={keys}>
          {rows.map((row) => {
            const mark = drop?.key === row.key ? drop : null;
            if (row.type === "unplaced") {
              return <Fragment key={row.key}>
                <UnplacedRow row={row} drop={mark} onSuggest={unplacedPapers >= SUGGEST_FROM && !filtering ? () => setSuggesting((value) => !value) : null} />
                {suggesting && <SectionSuggestions baseId={baseId} onCreated={() => void load()} onClose={() => setSuggesting(false)} />}
              </Fragment>;
            }
            if (row.type === "empty") {
              const node = tree.sections.get(row.id);
              return <EmptyRow key={row.key} row={row} drop={mark} onAddSubsection={() => void addSection(row.id)}
                onAddSources={() => setPicker({ mode: "add", section: { id: row.id, title: node?.section.title ?? "" } })} />;
            }
            if (row.type === "source") {
              return <SourceRow key={row.key} row={row} dragDisabled={filtering} drop={mark}
                current={activeSourceId === row.id} checked={selected.has(row.id)}
                editing={editing?.kind === "source" && editing.id === row.id}
                onSelect={() => onSource(row.id)} onCheck={(checked) => check([row.id], checked)}
                onOpen={() => open(row.id)}
                onRename={() => setEditing({ id: row.id, kind: "source", fresh: false })}
                onSave={(title) => void renameSource(row.id, title)} onCancel={() => setEditing(null)}
                onMove={() => setPicker({ mode: "move", moving: [{ kind: "source", id: row.id }] })}
                onTakeOut={() => void move([{ kind: "source", id: row.id }], null, null)} />;
            }
            const node = tree.sections.get(row.id);
            if (!node) return null;
            const ids = sourcesIn(node).map((item) => item.id);
            const checked = ids.length > 0 && ids.every((id) => selected.has(id));
            return <Fragment key={row.key}>
              <SectionRow row={row} dragDisabled={filtering} drop={mark}
                editing={editing?.kind === "section" && editing.id === row.id}
                scope={{ checked, mixed: !checked && ids.some((id) => selected.has(id)), disabled: !ids.length, onCheck: (value) => check(ids, value) }}
                actions={sectionActions(node)}
                onToggle={() => toggle(row.id)}
                onSave={(title) => void saveSection(row.id, { title })}
                onCancel={() => void cancelEdit()} />
              {noteSection === row.id && <form className="contents-note-edit" onSubmit={(event) => {
                event.preventDefault();
                const description = String(new FormData(event.currentTarget).get("note") ?? "").trim();
                setNoteSection(null);
                void saveSection(row.id, { description });
              }}>
                <textarea aria-label="Section note" name="note" defaultValue={node.section.description} autoFocus />
                <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => setNoteSection(null)}>Cancel</button>
                <button type="submit" className="gx-btn gx-btn-primary gx-btn-sm">Save note</button>
              </form>}
            </Fragment>;
          })}
        </div>
        {dragging && dragging.length > 0 && pointer && createPortal(<div className="contents-drag-chip" style={{ left: pointer.x + 14, top: pointer.y + 10 }}>{dragTitle(tree, dragging[0]!)}</div>, document.body)}
      </DndContext>
    </div>

    <footer className="contents-add">{addSource}</footer>

    {picker && <MovePicker tree={tree} onClose={() => setPicker(null)} {...(picker.mode === "move"
      ? { mode: "move" as const, moving: picker.moving, onChoose: (parentId: string | null) => { setPicker(null); void move(picker.moving, parentId, null); } }
      : { mode: "add" as const, section: picker.section, onAdd: (ids: string[]) => { setPicker(null); void move(ids.map((id) => ({ kind: "source" as const, id })), picker.section.id, null); } })} />}
  </section>;
}

function dragTitle(tree: ReturnType<typeof buildTree>, ref: ContentsItemRef) {
  if (ref.kind === "section") return tree.sections.get(ref.id)?.section.title ?? "Section";
  return findSource(tree, ref.id)?.title ?? "Source";
}
