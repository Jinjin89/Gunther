import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { DndContext, PointerSensor, closestCenter, pointerWithin, useSensor, useSensors, type CollisionDetection, type DragEndEvent, type DragMoveEvent, type DragStartEvent } from "@dnd-kit/core";
import { ChevronsDownUp, ChevronsUpDown, Plus, Search, X } from "lucide-react";
import type { ContentsItemRef, LibraryContents as Contents } from "@gunther/contracts";
import { persistentStorage } from "../persistentStorage";
import { knowledgeApi } from "../api";
import { applyMove, buildTree, dropTarget, findSource, naturalOrder, nodeRef, sectionPath, sourceKey, subtreeStats, visibleRows, type ContentsNode, type ContentsRow, type DropPosition } from "./contentsTree";
import { EmptyRow, SectionRow, SourceRow, UnplacedRow, type DropMark } from "./ContentsRow";
import { LibraryPanel, SectionPanel, SelectionPanel, SourcePanel, type LibraryInfo } from "./ContentsPanel";
import { MovePicker } from "./MovePicker";
import { SectionSuggestions } from "./SectionPanels";
import "./contents.css";

const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;
const plural = (count: number, word: string) => `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;
const SUGGEST_FROM = 6;
const POLL_MS = 2500;

type Picker = { mode: "move"; moving: ContentsItemRef[] } | { mode: "add"; section: { id: string; title: string } };
type Current = { kind: "section" | "source"; id: string } | null;
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

/**
 * The Contents tab of a library. On the left, its sections and sources as the user arranged
 * them, then what is not placed yet; on the right, a panel about what is chosen there: the
 * library, a section (named and noted in place) or a source. Drag rows, or use Move to….
 */
export function LibraryContents({ baseId, library, toolbarEnd, afterList, onOpenSource, onAsk, onAskLibrary, onEditLibrary, onAddSource, onChanged, onLoaded }: {
  baseId: string;
  /** The library's name and note, for the side panel. */
  library?: LibraryInfo;
  /** Buttons at the end of the toolbar (import, add source). */
  toolbarEnd?: ReactNode;
  /** Shown under the list (a demo library's references). */
  afterList?: ReactNode;
  onOpenSource: (id: string, queue: string[]) => void;
  onAsk: (sectionId: string) => void;
  onAskLibrary?: () => void;
  onEditLibrary?: () => void;
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
  const [current, setCurrent] = useState<Current>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [editing, setEditing] = useState<{ id: string; kind: "section" | "source"; fresh: boolean } | null>(null);
  const [picker, setPicker] = useState<Picker | null>(null);
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
  // A row to focus (and bring into view) once it has been drawn.
  const pendingFocus = useRef<string | null>(null);
  contentsRef.current = contents;

  const tree = useMemo(() => buildTree(contents ?? { sections: [], placements: [], sources: [] }), [contents]);
  const filtering = filter.trim() !== "";
  const rows = useMemo(() => visibleRows(tree, collapsed, filter), [tree, collapsed, filter]);
  const sourceIds = useMemo(() => rows.filter((row) => row.type === "source").map((row) => row.id), [rows]);
  const libraryInfo: LibraryInfo = library ?? { title: "This library" };

  const load = useCallback(async () => {
    try {
      const next = await knowledgeApi.contents(baseId);
      setContents(next); setUnavailable(false);
    } catch { setUnavailable(true); }
  }, [baseId]);

  useEffect(() => {
    setContents(null); setSelected([]); setCurrent(null); setEditing(null); setFilter(""); setError(""); setConfirming(null); setCollapsed(readCollapsed(baseId));
    void load();
    const refresh = () => void load();
    window.addEventListener("gunther:sources-updated", refresh);
    window.addEventListener("gunther:topics-updated", refresh);
    return () => { window.removeEventListener("gunther:sources-updated", refresh); window.removeEventListener("gunther:topics-updated", refresh); };
  }, [baseId, load]);

  useEffect(() => { if (contents) onLoaded?.(contents.sources.length); }, [contents, onLoaded]);

  // Sources still being read change on their own: look again until they are done.
  const reading = contents?.sources.some((source) => source.processing === "reading" || source.durationPending) ?? false;
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

  useEffect(() => {
    const key = pendingFocus.current;
    if (!key) return;
    const element = document.querySelector<HTMLElement>(`[data-focus="${CSS.escape(key)}"]`);
    if (!element) return;
    pendingFocus.current = null;
    element.focus({ preventScroll: true });
    element.scrollIntoView?.({ block: "nearest" });
  });

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
    const now = contentsRef.current;
    if (!now || items.length === 0) return Promise.resolve();
    if (parentId) expand([parentId]);
    return change(applyMove(now, { items, parentId, index }), () => knowledgeApi.moveContents(baseId, { items, parentId, index }));
  };

  /** Choose a row: the panel shows it, and the list brings it into view (opening its sections). */
  const choose = (next: Current, focus = false) => {
    setCurrent(next); setSelected([]); setConfirming(null);
    if (!next) return;
    anchor.current = next.kind === "source" ? next.id : null;
    const above = sectionPath(tree, next.id).map((step) => step.id);
    if (above.length) expand(above);
    if (focus) pendingFocus.current = next.kind === "source" ? sourceKey(next.id) : `section:${next.id}`;
  };

  const addSection = async (parentId: string | null) => {
    setError("");
    try {
      const created = await knowledgeApi.saveTopic(baseId, { title: "New section", description: "", parentId });
      if (parentId) expand([parentId]);
      await load(); onChanged?.();
      setCurrent({ kind: "section", id: created.id }); setSelected([]);
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
    setContents({ ...now, sections: now.sections.map((item) => item.id === id ? { ...item, title, description } : item) });
    try {
      await knowledgeApi.saveTopic(baseId, { title, description, parentId: section.parentId, version: section.version }, id);
      onChanged?.();
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
    // A section that was only just added and never named goes away again.
    if (was?.fresh) { setCurrent(null); await change(null, () => knowledgeApi.deleteSection(baseId, was.id)); }
  };
  const deleteSection = async (id: string) => {
    setConfirming(null);
    await change(null, () => knowledgeApi.deleteSection(baseId, id));
  };
  const sortSection = (node: SectionNode) => {
    const now = contentsRef.current;
    if (!now || node.children.length < 2) return;
    const children = naturalOrder(node.children).map(nodeRef);
    void change(applyMove(now, { items: children, parentId: node.id, index: 0 }), () => knowledgeApi.orderContents(baseId, { parentId: node.id, children }));
  };
  const askDelete = (id: string) => { choose({ kind: "section", id }); setConfirming(id); };

  // Selection: a click chooses one row; ⌘/Ctrl-click and the box add or remove one;
  // Shift-click takes the run of rows from the last one chosen.
  const range = (id: string) => {
    const start = anchor.current ?? (current?.kind === "source" ? current.id : null);
    const from = start ? sourceIds.indexOf(start) : -1;
    const to = sourceIds.indexOf(id);
    if (from < 0 || to < 0) return null;
    return sourceIds.slice(Math.min(from, to), Math.max(from, to) + 1);
  };
  const toggleSelected = (id: string) => {
    setSelected((previous) => {
      const seed = previous.length === 0 && current?.kind === "source" && current.id !== id ? [current.id] : previous;
      return seed.includes(id) ? seed.filter((item) => item !== id) : [...seed, id];
    });
    anchor.current = id;
  };
  const clickSource = (id: string, event: MouseEvent) => {
    if (event.shiftKey) {
      const run = range(id);
      if (run) { setSelected((previous) => [...new Set([...previous, ...run])]); return; }
    }
    if (event.metaKey || event.ctrlKey) { toggleSelected(id); return; }
    choose({ kind: "source", id });
  };
  const checkSource = (id: string, shift: boolean) => {
    const run = shift ? range(id) : null;
    if (run) { setSelected((previous) => [...new Set([...previous, ...run])]); return; }
    setSelected((previous) => previous.includes(id) ? previous.filter((item) => item !== id) : [...previous, id]);
    anchor.current = id;
  };
  const clearSelection = () => { setSelected([]); anchor.current = null; };
  const selectedSources = sourceIds.filter((id) => selected.includes(id));
  const selectedPlaced = selectedSources.filter((id) => tree.parents.has(id));
  const open = (id: string) => onOpenSource(id, sourceIds);

  // The arrow keys walk the rows; Left and Right close and open sections; Esc lets go.
  const navigable = rows.filter((row) => row.type === "section" || row.type === "source");
  const focusRow = (row: ContentsRow | undefined) => {
    if (!row) return;
    setCurrent({ kind: row.type as "section" | "source", id: row.id }); setSelected([]); setConfirming(null);
    if (row.type === "source") anchor.current = row.id;
    pendingFocus.current = row.key;
    document.querySelector<HTMLElement>(`[data-focus="${CSS.escape(row.key)}"]`)?.focus();
  };
  const keys = (event: KeyboardEvent) => {
    const key = (event.target as HTMLElement).dataset?.focus;
    if (event.key === "Escape") {
      if (selected.length) { clearSelection(); event.preventDefault(); return; }
      if (current) { setCurrent(null); event.preventDefault(); }
      return;
    }
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
    if (row.type === "source" && selected.includes(row.id)) return selectedSources.map((id) => ({ kind: "source" as const, id }));
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
  const confirmText = (node: SectionNode) => {
    const stats = subtreeStats(node);
    const rest = stats.sections ? ` and its ${plural(stats.sections, "sub-section")}` : "";
    const sources = stats.sources ? `${stats.sections ? "Their" : "Its"} ${plural(stats.sources, "source")} go${stats.sources === 1 && !stats.sections ? "es" : ""} back to Not placed yet; no source is deleted.` : "No source is deleted.";
    return `Delete “${node.section.title}”${rest}? ${sources}`;
  };
  const sectionActions = (node: SectionNode) => ({
    onAddSubsection: () => void addSection(node.id),
    onRename: () => { choose({ kind: "section", id: node.id }); setEditing({ id: node.id, kind: "section", fresh: false }); },
    onAddSources: () => setPicker({ mode: "add", section: { id: node.id, title: node.section.title } }),
    onSort: () => sortSection(node),
    onMove: () => setPicker({ mode: "move", moving: [{ kind: "section", id: node.id }] }),
    onAsk: () => onAsk(node.id),
    onDelete: () => askDelete(node.id),
  });

  // What the panel shows: several chosen sources, one section or source, or the library.
  const panel = () => {
    if (!contents) return null;
    const toLibrary = () => { setCurrent(null); clearSelection(); setConfirming(null); };
    const toSection = (id: string) => choose({ kind: "section", id }, true);
    if (selectedSources.length > 1) {
      return <SelectionPanel sources={selectedSources.map((id) => findSource(tree, id)!).filter(Boolean)} placedCount={selectedPlaced.length}
        onMove={() => setPicker({ mode: "move", moving: selectedSources.map((id) => ({ kind: "source", id })) })}
        onTakeOut={() => { void move(selectedPlaced.map((id) => ({ kind: "source", id })), null, null); clearSelection(); }}
        onClear={toLibrary} />;
    }
    const sourceId = selectedSources.length === 1 ? selectedSources[0]! : current?.kind === "source" ? current.id : null;
    const source = sourceId ? findSource(tree, sourceId) : undefined;
    if (source) {
      const placed = tree.parents.has(source.id);
      return <SourcePanel tree={tree} source={source} placed={placed}
        onRename={(title) => void renameSource(source.id, title)} onOpen={() => open(source.id)}
        onMove={() => setPicker({ mode: "move", moving: [{ kind: "source", id: source.id }] })}
        onTakeOut={() => void move([{ kind: "source", id: source.id }], null, null)}
        onLibrary={toLibrary} onSection={toSection} />;
    }
    const node = current?.kind === "section" ? tree.sections.get(current.id) : undefined;
    if (node) {
      const actions = sectionActions(node);
      return <SectionPanel baseId={baseId} tree={tree} node={node} confirming={confirming === node.id} confirmText={confirmText(node)}
        actions={{
          ...actions,
          onRename: (title) => void saveSection(node.id, { title }),
          onNote: (description) => void saveSection(node.id, { description }),
          onDelete: () => setConfirming(node.id),
          onConfirmDelete: () => void deleteSection(node.id),
          onCancelDelete: () => setConfirming(null),
        }}
        onLibrary={toLibrary} onSection={toSection} onOpenSource={(id) => onOpenSource(id, [id])} />;
    }
    return <LibraryPanel library={libraryInfo} contents={contents} tree={tree} onAsk={onAskLibrary} onEdit={onEditLibrary}
      onReveal={(id) => { setFilter(""); choose({ kind: "source", id }, true); }}
      onShowUnplaced={() => { setFilter(""); requestAnimationFrame(() => document.querySelector('[data-row="unplaced"]')?.scrollIntoView?.({ block: "start", behavior: "smooth" })); }} />;
  };

  const empty = contents !== null && contents.sources.length === 0 && contents.sections.length === 0;
  const chosen = current !== null || selected.length > 0;
  return <section className={`contents${chosen ? " has-choice" : ""}`} aria-label="Contents">
    <div className="contents-main" onClick={(event) => { if (event.target === event.currentTarget) { setCurrent(null); clearSelection(); } }}>
      <div className="contents-top">
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
          {toolbarEnd}
        </span>
      </div>
      {error && <p role="alert" className="contents-error">{error}<button type="button" aria-label="Dismiss" onClick={() => setError("")}><X size={13} /></button></p>}
      </div>

      {contents === null && !unavailable && <p className="contents-loading">Opening this library’s contents…</p>}
      {unavailable && contents === null && <p className="contents-loading">The contents could not be loaded. They will appear when the knowledge service reconnects.</p>}
      {empty && <div className="contents-empty"><h2>This library is ready for its first source.</h2><p>Start with whichever source you already have, then arrange it under your own headings.</p><button type="button" className="gx-btn gx-btn-primary" onClick={onAddSource}>Choose a source</button></div>}
      {contents !== null && !empty && contents.sections.length === 0 && !filtering && <div className="contents-hint">
        <p>Arrange these sources under your own headings: a part of a course, a lecture, a topic of papers.</p>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => void addSection(null)}><Plus size={13} />Add the first section</button>
      </div>}
      {contents !== null && filtering && rows.length === 0 && <p className="contents-loading">Nothing matches “{filter.trim()}”.</p>}

      <DndContext sensors={sensors} collisionDetection={collide} onDragStart={onDragStart} onDragMove={onDragMove} onDragEnd={onDragEnd} onDragCancel={stopDrag}>
        <div className="contents-list" onKeyDown={keys}>
          {rows.map((row) => {
            const mark = drop?.key === row.key ? drop : null;
            if (row.type === "unplaced") {
              return <Fragment key={row.key}>
                <UnplacedRow row={row} drop={mark} onSuggest={unplacedPapers >= SUGGEST_FROM && !filtering ? () => setSuggesting((value) => !value) : null} />
                {suggesting && <SectionSuggestions baseId={baseId} onCreated={() => { void load(); onChanged?.(); }} onClose={() => setSuggesting(false)} />}
              </Fragment>;
            }
            if (row.type === "empty") {
              const node = tree.sections.get(row.id);
              return <EmptyRow key={row.key} row={row} drop={mark} onAddSubsection={() => void addSection(row.id)}
                onAddSources={() => setPicker({ mode: "add", section: { id: row.id, title: node?.section.title ?? "" } })} />;
            }
            if (row.type === "source") {
              return <SourceRow key={row.key} row={row} dragDisabled={filtering} drop={mark}
                current={selected.length === 0 && current?.kind === "source" && current.id === row.id}
                selected={selected.includes(row.id)} selecting={selected.length > 0}
                editing={editing?.kind === "source" && editing.id === row.id}
                onClick={(event) => clickSource(row.id, event)} onCheck={(shift) => checkSource(row.id, shift)}
                onOpen={() => open(row.id)}
                onRename={() => { choose({ kind: "source", id: row.id }); setEditing({ id: row.id, kind: "source", fresh: false }); }}
                onSave={(title) => void renameSource(row.id, title)} onCancel={() => setEditing(null)}
                onMove={() => setPicker({ mode: "move", moving: [{ kind: "source", id: row.id }] })}
                onTakeOut={() => void move([{ kind: "source", id: row.id }], null, null)} />;
            }
            const node = tree.sections.get(row.id);
            if (!node) return null;
            return <SectionRow key={row.key} row={row} dragDisabled={filtering} drop={mark}
              current={selected.length === 0 && current?.kind === "section" && current.id === row.id}
              editing={editing?.kind === "section" && editing.id === row.id}
              canAsk={node.children.length > 0 || node.section.passageCount > 0}
              actions={sectionActions(node)}
              onFocus={() => choose({ kind: "section", id: row.id })}
              onToggle={() => toggle(row.id)}
              onSave={(title) => void saveSection(row.id, { title })}
              onCancel={() => void cancelEdit()} />;
          })}
        </div>
        {dragging && dragging.length > 0 && pointer && createPortal(<div className="contents-drag-chip" style={{ left: pointer.x + 14, top: pointer.y + 10 }}>{dragging.length === 1 ? dragTitle(tree, dragging[0]!) : `${dragging.length} items`}</div>, document.body)}
      </DndContext>
      {afterList}
    </div>

    <aside className="contents-panel" aria-label="Details">{panel()}</aside>

    {picker && <MovePicker tree={tree} onClose={() => setPicker(null)} {...(picker.mode === "move"
      ? { mode: "move" as const, moving: picker.moving, onChoose: (parentId: string | null) => { setPicker(null); void move(picker.moving, parentId, null); clearSelection(); } }
      : { mode: "add" as const, section: picker.section, onAdd: (ids: string[]) => { setPicker(null); void move(ids.map((id) => ({ kind: "source" as const, id })), picker.section.id, null); } })} />}
  </section>;
}

function dragTitle(tree: ReturnType<typeof buildTree>, ref: ContentsItemRef) {
  if (ref.kind === "section") return tree.sections.get(ref.id)?.section.title ?? "Section";
  return findSource(tree, ref.id)?.title ?? "Source";
}
