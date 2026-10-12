import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { DndContext, KeyboardSensor, PointerSensor, closestCenter, pointerWithin, useSensor, useSensors, type CollisionDetection, type DragEndEvent, type DragMoveEvent, type DragStartEvent } from "@dnd-kit/core";
import { Plus, Search, X } from "lucide-react";
import type { ContentsItemRef, LibraryContents as Contents } from "@gunther/contracts";
import { persistentStorage } from "../persistentStorage";
import { knowledgeApi } from "../api";
import { applyMove, buildTree, dropTarget, naturalOrder, nodeRef, sourcesIn, subtreeStats, visibleRows, type ContentsNode, type DropPosition } from "./contentsTree";
import { SectionRow, SourceRow, UnplacedRow, type DropMark } from "./ContentsRow";
import { MovePicker } from "./MovePicker";
import { SectionOverview, SectionSuggestions } from "./SectionPanels";
import "./contents.css";

const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;
const plural = (count: number, word: string) => `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;
const SUGGEST_FROM = 6;
const POLL_MS = 2500;

type Picker = { mode: "move"; moving: ContentsItemRef[] } | { mode: "add"; section: { id: string; title: string } };

// Rows are stacked without gaps, so the pointer is always over one; fall back to the
// nearest centre for the keyboard, which has no pointer.
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

/**
 * The Contents tab of a library: its sections and sources as the user arranged them, then
 * what is not placed yet. Drag by the handle, or use the menus and the Move to… picker.
 */
export function LibraryContents({ baseId, toolbarEnd, onOpenSource, onAsk, onAddSource, onChanged, onLoaded }: {
  baseId: string;
  /** Buttons at the end of the toolbar (import, add source). */
  toolbarEnd?: ReactNode;
  onOpenSource: (id: string, queue: string[]) => void;
  onAsk: (sectionId: string) => void;
  onAddSource: () => void;
  /** The sections changed (so lists of sections elsewhere can reload). */
  onChanged?: () => void;
  /** How many sources the library has, once known. */
  onLoaded?: (sourceCount: number) => void;
}) {
  const [contents, setContents] = useState<Contents | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [error, setError] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(() => readCollapsed(baseId));
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [editing, setEditing] = useState<{ id: string; fresh: boolean } | null>(null);
  const [picker, setPicker] = useState<Picker | null>(null);
  const [overviews, setOverviews] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState<string | null>(null);
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
  const anchor = useRef<string | null>(null);
  contentsRef.current = contents;

  const tree = useMemo(() => buildTree(contents ?? { sections: [], placements: [], sources: [] }), [contents]);
  const filtering = filter.trim() !== "";
  const rows = useMemo(() => visibleRows(tree, collapsed, filter), [tree, collapsed, filter]);
  const sourceIds = useMemo(() => rows.filter((row) => row.type === "source").map((row) => row.id), [rows]);

  const load = useCallback(async () => {
    try {
      const next = await knowledgeApi.contents(baseId);
      setContents(next); setUnavailable(false);
    } catch { setUnavailable(true); }
  }, [baseId]);

  useEffect(() => {
    setContents(null); setSelected([]); setEditing(null); setFilter(""); setError(""); setCollapsed(readCollapsed(baseId));
    void load();
    const refresh = () => void load();
    window.addEventListener("gunther:sources-updated", refresh);
    window.addEventListener("gunther:topics-updated", refresh);
    return () => { window.removeEventListener("gunther:sources-updated", refresh); window.removeEventListener("gunther:topics-updated", refresh); };
  }, [baseId, load]);

  useEffect(() => { if (contents) onLoaded?.(contents.sources.length); }, [contents, onLoaded]);

  // Sources still being read change on their own: look again until they are done.
  const reading = contents?.sources.some((source) => source.processing === "reading") ?? false;
  useEffect(() => {
    if (!reading) return;
    const timer = window.setTimeout(() => void load(), POLL_MS);
    return () => window.clearTimeout(timer);
  }, [reading, contents, load]);

  // A source chosen elsewhere (Home search) opens as soon as this tab shows.
  useEffect(() => {
    const key = `gunther:open-source:${baseId}`;
    const id = persistentStorage.getItem(key);
    if (!id) return;
    persistentStorage.removeItem(key);
    onOpenSource(id, [id]);
  }, [baseId, onOpenSource]);

  const remember = (next: Set<string>) => {
    setCollapsed(next);
    persistentStorage.setItem(`gunther:contents-collapsed:${baseId}`, JSON.stringify([...next]));
  };
  const toggle = (id: string) => { const next = new Set(collapsed); if (!next.delete(id)) next.add(id); remember(next); };
  const expand = (id: string) => { if (collapsed.has(id)) { const next = new Set(collapsed); next.delete(id); remember(next); } };

  /** Show the result at once, ask the server, then take the server's word; on failure put the rows back. */
  const change = async (optimistic: Contents | null, call: () => Promise<Contents>) => {
    const before = contentsRef.current;
    if (optimistic) setContents(optimistic);
    setError("");
    try {
      setContents(await call());
      onChanged?.();
    } catch (reason) {
      if (before) setContents(before);
      setError(message(reason, "That change could not be saved."));
    }
  };
  const move = (items: ContentsItemRef[], parentId: string | null, index: number | null) => {
    const current = contentsRef.current;
    if (!current || items.length === 0) return Promise.resolve();
    if (parentId) expand(parentId);
    return change(applyMove(current, { items, parentId, index }), () => knowledgeApi.moveContents(baseId, { items, parentId, index }));
  };

  const addSection = async (parentId: string | null) => {
    setError("");
    try {
      const created = await knowledgeApi.saveTopic(baseId, { title: "New section", description: "", parentId });
      if (parentId) expand(parentId);
      await load(); onChanged?.();
      setEditing({ id: created.id, fresh: true });
    } catch (reason) { setError(message(reason, "The section could not be added.")); }
  };
  const saveSection = async (id: string, title: string, description: string) => {
    const section = contentsRef.current?.sections.find((item) => item.id === id);
    setEditing(null);
    if (!section) return;
    if (section.title === title && section.description === description) return;
    try {
      await knowledgeApi.saveTopic(baseId, { title, description, parentId: section.parentId, version: section.version }, id);
      onChanged?.();
    } catch (reason) { setError(message(reason, "The section could not be renamed.")); }
    await load();
  };
  const cancelEdit = async () => {
    const current = editing;
    setEditing(null);
    // A section that was only just added and never named goes away again.
    if (current?.fresh) await change(null, () => knowledgeApi.deleteSection(baseId, current.id));
  };
  const deleteSection = async (id: string) => {
    setConfirming(null);
    await change(null, () => knowledgeApi.deleteSection(baseId, id));
  };
  const sortSection = (node: Extract<ContentsNode, { type: "section" }>) => {
    const current = contentsRef.current;
    if (!current || node.children.length < 2) return;
    const children = naturalOrder(node.children).map(nodeRef);
    void change(applyMove(current, { items: children, parentId: node.id, index: 0 }), () => knowledgeApi.orderContents(baseId, { parentId: node.id, children }));
  };

  // Selection: click toggles, Shift-click takes the range from the last click.
  const select = (id: string, shift: boolean) => {
    if (shift && anchor.current && anchor.current !== id) {
      const from = sourceIds.indexOf(anchor.current);
      const to = sourceIds.indexOf(id);
      if (from >= 0 && to >= 0) {
        const range = sourceIds.slice(Math.min(from, to), Math.max(from, to) + 1);
        setSelected((previous) => [...new Set([...previous, ...range])]);
        return;
      }
    }
    anchor.current = id;
    setSelected((previous) => previous.includes(id) ? previous.filter((item) => item !== id) : [...previous, id]);
  };
  const clearSelection = () => { setSelected([]); anchor.current = null; };
  const selectedSources = sourceIds.filter((id) => selected.includes(id));
  const selectedPlaced = selectedSources.filter((id) => tree.parents.has(id));

  // Drag and drop.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor),
  );
  const carried = (key: string): ContentsItemRef[] => {
    const row = rows.find((item) => item.key === key);
    if (!row || (row.type !== "section" && row.type !== "source")) return [];
    if (row.type === "source" && selected.includes(row.id)) return selectedSources.map((id) => ({ kind: "source" as const, id }));
    return [{ kind: row.type, id: row.id }];
  };
  const place = (event: DragMoveEvent | DragEndEvent, items: ContentsItemRef[]) => {
    const over = event.over;
    const row = over && rows.find((item) => item.key === over.id);
    if (!over || !row) return null;
    // The row as it is on screen now, against the pointer; the keyboard has no pointer, so
    // it uses the middle of the moved row.
    const element = document.querySelector(`[data-row="${CSS.escape(String(over.id))}"]`);
    const rect = element?.getBoundingClientRect() ?? over.rect;
    const box = event.active.rect.current.translated;
    const y = pointerRef.current?.y ?? (box ? box.top + box.height / 2 : rect.top + rect.height / 2);
    const fraction = rect.height > 0 ? (y - rect.top) / rect.height : 0.5;
    const position: DropPosition = row.type === "unplaced" ? "on" : row.type === "section" ? (fraction < 0.25 ? "before" : "on") : (fraction < 0.5 ? "before" : "after");
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
  const sectionNode = (id: string) => tree.sections.get(id);
  const confirmText = (node: Extract<ContentsNode, { type: "section" }>) => {
    const stats = subtreeStats(node);
    const rest = stats.sections ? ` and its ${plural(stats.sections, "sub-section")}` : "";
    const sources = stats.sources ? `${stats.sections ? "Their" : "Its"} ${plural(stats.sources, "source")} go${stats.sources === 1 && !stats.sections ? "es" : ""} back to Not placed yet; no source is deleted.` : "No source is deleted.";
    return `Delete “${node.section.title}”${rest}? ${sources}`;
  };

  const empty = contents !== null && contents.sources.length === 0 && contents.sections.length === 0;
  return <section className="contents" aria-label="Contents">
    <div className="contents-toolbar">
      <label className="contents-filter"><Search size={14} />
        <input value={filter} placeholder="Filter" aria-label="Filter contents" onChange={(event) => setFilter(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape" && filter) { event.stopPropagation(); setFilter(""); } }} />
        {filter && <button type="button" aria-label="Clear filter" onClick={() => setFilter("")}><X size={13} /></button>}
      </label>
      <span className="contents-toolbar-end">
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" aria-label="Add section" onClick={() => void addSection(null)}><Plus size={13} />Section</button>
        {toolbarEnd}
      </span>
    </div>
    {error && <p role="alert" className="contents-error">{error}</p>}
    {selected.length > 0 && <div className="contents-selection" role="toolbar" aria-label="Selected sources">
      <strong>{selected.length} selected</strong>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => setPicker({ mode: "move", moving: selectedSources.map((id) => ({ kind: "source", id })) })}>Move to…</button>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={selectedPlaced.length === 0} onClick={() => { void move(selectedPlaced.map((id) => ({ kind: "source", id })), null, null); clearSelection(); }}>Take out of section</button>
      <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={clearSelection}>Clear</button>
    </div>}

    {contents === null && !unavailable && <p className="contents-loading">Opening this library’s contents…</p>}
    {unavailable && contents === null && <p className="contents-loading">The contents could not be loaded. They will appear when the knowledge service reconnects.</p>}
    {empty && <div className="contents-empty"><h2>This library is ready for its first source.</h2><p>Start with whichever source you already have, then arrange it under your own headings.</p><button type="button" className="gx-btn gx-btn-primary" onClick={onAddSource}>Choose a source</button></div>}
    {contents !== null && !empty && contents.sections.length === 0 && !filtering && <p className="contents-hint">Add a section to start arranging these sources under your own headings.</p>}
    {contents !== null && filtering && rows.length === 0 && <p className="contents-hint">Nothing matches “{filter.trim()}”.</p>}

    <DndContext sensors={sensors} collisionDetection={collide} onDragStart={onDragStart} onDragMove={onDragMove} onDragEnd={onDragEnd} onDragCancel={stopDrag}>
      <div className="contents-list">
        {rows.map((row) => {
          const mark = drop?.key === row.key ? drop : null;
          if (row.type === "unplaced") {
            return <Fragment key={row.key}>
              <UnplacedRow row={row} drop={mark} onSuggest={unplacedPapers >= SUGGEST_FROM && !filtering ? () => setSuggesting((value) => !value) : null} />
              {suggesting && <SectionSuggestions baseId={baseId} onCreated={() => { void load(); onChanged?.(); }} onClose={() => setSuggesting(false)} />}
            </Fragment>;
          }
          if (row.type === "source") {
            return <SourceRow key={row.key} row={row} dragDisabled={filtering} drop={mark} selected={selected.includes(row.id)} selecting={selected.length > 0}
              onSelect={(shift) => select(row.id, shift)}
              onOpen={() => onOpenSource(row.id, sourceIds)}
              onMove={() => setPicker({ mode: "move", moving: [{ kind: "source", id: row.id }] })}
              onTakeOut={() => void move([{ kind: "source", id: row.id }], null, null)} />;
          }
          const node = sectionNode(row.id);
          if (!node) return null;
          return <Fragment key={row.key}>
            <SectionRow row={row} dragDisabled={filtering} drop={mark} editing={editing?.id === row.id} fresh={editing?.id === row.id && editing.fresh}
              canAsk={node.children.length > 0 || node.section.passageCount > 0}
              actions={{
                onAddSubsection: () => void addSection(row.id),
                onRename: () => setEditing({ id: row.id, fresh: false }),
                onAddSources: () => setPicker({ mode: "add", section: { id: row.id, title: node.section.title } }),
                onSort: () => sortSection(node),
                onAsk: () => onAsk(row.id),
                onOverview: () => setOverviews((previous) => { const next = new Set(previous); if (!next.delete(row.id)) next.add(row.id); return next; }),
                onDelete: () => setConfirming(row.id),
              }}
              onToggle={() => toggle(row.id)}
              onSave={(title, description) => void saveSection(row.id, title, description)}
              onCancel={() => void cancelEdit()} />
            {confirming === row.id && <div className="contents-confirm" role="alertdialog" aria-label={`Delete ${node.section.title}`} style={{ marginLeft: row.depth * 16 + 40 }}>
              <p>{confirmText(node)}</p>
              <span><button type="button" className="gx-btn gx-btn-quiet gx-btn-sm is-danger" onClick={() => void deleteSection(row.id)}>Delete section</button><button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => setConfirming(null)}>Cancel</button></span>
            </div>}
            {overviews.has(row.id) && <div style={{ marginLeft: row.depth * 16 + 40 }}>
              <SectionOverview baseId={baseId} section={{ id: row.id, title: node.section.title }} paperCount={sourcesIn(node).filter((source) => source.kind === "paper").length} onOpenSource={(id) => onOpenSource(id, [id])} />
            </div>}
          </Fragment>;
        })}
      </div>
      {dragging && dragging.length > 0 && pointer && createPortal(<div className="contents-drag-chip" style={{ left: pointer.x + 14, top: pointer.y + 10 }}>{dragging.length === 1 ? dragTitle(tree, dragging[0]!) : `${dragging.length} items`}</div>, document.body)}
    </DndContext>

    {picker && <MovePicker tree={tree} onClose={() => setPicker(null)} {...(picker.mode === "move"
      ? { mode: "move" as const, moving: picker.moving, onChoose: (parentId: string | null) => { setPicker(null); void move(picker.moving, parentId, null); clearSelection(); } }
      : { mode: "add" as const, section: picker.section, onAdd: (ids: string[]) => { setPicker(null); void move(ids.map((id) => ({ kind: "source" as const, id })), picker.section.id, null); } })} />}
  </section>;
}

function dragTitle(tree: ReturnType<typeof buildTree>, ref: ContentsItemRef) {
  if (ref.kind === "section") return tree.sections.get(ref.id)?.section.title ?? "Section";
  const loose = tree.unplaced.find((source) => source.id === ref.id);
  if (loose) return loose.title;
  const parent = tree.parents.get(ref.id);
  const node = parent ? tree.sections.get(parent)?.children.find((child) => child.id === ref.id) : undefined;
  return node?.type === "source" ? node.source.title : "Source";
}
