import type { ContentsItemRef, ContentsMoveInput, ContentsSection, ContentsSource, LibraryContents } from "@gunther/contracts";

/** A section with what is placed in it, or a source row, in the user's order. */
export type ContentsNode =
  | { type: "section"; id: string; section: ContentsSection; children: ContentsNode[] }
  | { type: "source"; id: string; source: ContentsSource };

export interface ContentsTree {
  /** Top-level sections, in order. */
  roots: ContentsNode[];
  /** Sources placed nowhere, newest first. */
  unplaced: ContentsSource[];
  sections: Map<string, Extract<ContentsNode, { type: "section" }>>;
  /** Parent of every section and placed source (null = top level). */
  parents: Map<string, string | null>;
}

export interface ContentsRow {
  /** `section:<id>`, `source:<id>` or `unplaced`: also the drag and drop id. */
  key: string;
  type: "section" | "source" | "unplaced";
  id: string;
  depth: number;
  /** Where a drop beside this row lands (null for top level and for Not placed yet). */
  parentId: string | null;
  collapsed: boolean;
  /** "8 sources" for a section, the count for Not placed yet. */
  count: string;
  node?: ContentsNode;
  source?: ContentsSource;
  /** A source row that is under Not placed yet. */
  loose: boolean;
}

export const sectionKey = (id: string) => `section:${id}`;
export const sourceKey = (id: string) => `source:${id}`;
export const UNPLACED_KEY = "unplaced";
export const refKey = (ref: ContentsItemRef) => ref.kind === "section" ? sectionKey(ref.id) : sourceKey(ref.id);

const plural = (count: number, word: string) => `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;

export function buildTree(contents: LibraryContents): ContentsTree {
  const sources = new Map(contents.sources.map((source) => [source.id, source]));
  const sections = new Map<string, Extract<ContentsNode, { type: "section" }>>();
  for (const section of contents.sections) sections.set(section.id, { type: "section", id: section.id, section, children: [] });
  const order = new Map<string, number>();
  const parents = new Map<string, string | null>();
  const roots: ContentsNode[] = [];
  const slot = (parentId: string | null) => parentId === null ? roots : sections.get(parentId)!.children;
  for (const node of sections.values()) {
    // A section whose parent is missing is shown at the top rather than lost.
    const parentId = node.section.parentId !== null && sections.has(node.section.parentId) ? node.section.parentId : null;
    parents.set(node.id, parentId);
    order.set(node.id, node.section.position);
    slot(parentId).push(node);
  }
  const placed = new Set<string>();
  for (const placement of contents.placements) {
    const source = sources.get(placement.sourceId);
    if (!source || !sections.has(placement.sectionId) || placed.has(source.id)) continue;
    placed.add(source.id);
    parents.set(source.id, placement.sectionId);
    order.set(source.id, placement.position);
    sections.get(placement.sectionId)!.children.push({ type: "source", id: source.id, source });
  }
  // Sections and sources share one order inside a parent; ties show sections first.
  const rank = (node: ContentsNode) => (order.get(node.id) ?? 0) * 2 + (node.type === "section" ? 0 : 1);
  const sort = (nodes: ContentsNode[]) => {
    nodes.sort((a, b) => rank(a) - rank(b) || a.id.localeCompare(b.id));
    for (const node of nodes) if (node.type === "section") sort(node.children);
  };
  sort(roots);
  const unplaced = contents.sources
    .filter((source) => !placed.has(source.id))
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt) || a.id.localeCompare(b.id));
  return { roots, unplaced, sections, parents };
}

/** Every source in a section and below it. */
export function sourcesIn(node: ContentsNode): ContentsSource[] {
  if (node.type === "source") return [node.source];
  return node.children.flatMap(sourcesIn);
}

/** "8 sources", or "12 papers" when all of them are papers. */
export function sectionCount(node: ContentsNode): string {
  const found = sourcesIn(node);
  if (found.length === 0) return "Empty";
  return found.every((source) => source.kind === "paper") ? plural(found.length, "paper") : plural(found.length, "source");
}

/** True when `id` is inside the section `ancestorId` (not the section itself). */
export function isDescendant(tree: ContentsTree, id: string, ancestorId: string): boolean {
  const seen = new Set<string>();
  let cursor = tree.parents.get(id) ?? null;
  while (cursor && !seen.has(cursor)) {
    if (cursor === ancestorId) return true;
    seen.add(cursor);
    cursor = tree.parents.get(cursor) ?? null;
  }
  return false;
}

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
const titleOf = (node: ContentsNode) => node.type === "section" ? node.section.title : node.source.title;

/** Children sorted by title, numbers by value (Lecture 2 before Lecture 10). Ties keep their order. */
export function naturalOrder(children: ContentsNode[]): ContentsNode[] {
  return children.map((node, index) => ({ node, index }))
    .sort((a, b) => collator.compare(titleOf(a.node), titleOf(b.node)) || a.index - b.index)
    .map((entry) => entry.node);
}

export const nodeRef = (node: ContentsNode): ContentsItemRef => ({ kind: node.type, id: node.id });

function formatDuration(seconds: number) {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const two = (value: number) => String(value).padStart(2, "0");
  return h > 0 ? `${h}:${two(m)}:${two(s)}` : `${m}:${two(s)}`;
}

function shortDate(iso: string) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }) });
}

const FILE_TYPES: Record<string, string> = {
  "application/pdf": "PDF",
  "text/markdown": "Markdown",
  "text/plain": "Text",
  "text/csv": "CSV",
  "application/msword": "Word",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "Slides",
  "application/vnd.ms-powerpoint": "Slides",
};

function fileType(mediaType: string | null) {
  if (!mediaType) return "";
  if (FILE_TYPES[mediaType]) return FILE_TYPES[mediaType];
  if (mediaType.startsWith("image/")) return "Photo";
  return "";
}

/** The one grey detail at the right of a row. */
export function rowDetail(source: ContentsSource): string {
  if (source.processing === "reading") return "Reading…";
  if (source.processing === "failed") return "Couldn’t read";
  const date = shortDate(source.createdAt);
  const join = (...parts: Array<string | null | undefined>) => parts.filter(Boolean).join(" · ");
  switch (source.kind) {
    case "recording":
    case "course":
      return join(date, source.durationSeconds ? formatDuration(source.durationSeconds) : "");
    case "paper":
      return join(source.authors, source.year ? String(source.year) : "") || date;
    case "link":
      return join(source.host, date);
    case "note":
      return date;
    default:
      return join(fileType(source.mediaType), source.pages ? plural(source.pages, "page") : "") || date;
  }
}

const matches = (needle: string, ...texts: string[]) => texts.some((text) => text.toLowerCase().includes(needle));

/**
 * The rows to draw, top to bottom: sections and sources in order, then Not placed yet.
 * Collapsed sections hide their children; a filter ignores collapse, keeps the headings of
 * matching rows, and shows everything inside a section whose own title matches.
 */
export function visibleRows(tree: ContentsTree, collapsed: ReadonlySet<string>, filter = ""): ContentsRow[] {
  const needle = filter.trim().toLowerCase();
  const rows: ContentsRow[] = [];
  const sourceRow = (source: ContentsSource, depth: number, parentId: string | null, loose: boolean): ContentsRow => ({
    key: sourceKey(source.id), type: "source", id: source.id, depth, parentId, collapsed: false, count: "", source, loose,
    node: { type: "source", id: source.id, source },
  });
  const hit = (node: ContentsNode, all: boolean): boolean => {
    if (!needle || all) return true;
    if (node.type === "source") return matches(needle, node.source.title, rowDetail(node.source));
    return matches(needle, node.section.title) || node.children.some((child) => hit(child, false));
  };
  const walk = (nodes: ContentsNode[], depth: number, parentId: string | null, all: boolean) => {
    for (const node of nodes) {
      if (!hit(node, all)) continue;
      if (node.type === "source") { rows.push(sourceRow(node.source, depth, parentId, false)); continue; }
      const open = needle ? true : !collapsed.has(node.id);
      rows.push({ key: sectionKey(node.id), type: "section", id: node.id, depth, parentId, collapsed: !open, count: sectionCount(node), node, loose: false });
      if (open) walk(node.children, depth + 1, node.id, all || (needle !== "" && matches(needle, node.section.title)));
    }
  };
  walk(tree.roots, 0, null, false);
  const loose = needle ? tree.unplaced.filter((source) => matches(needle, source.title, rowDetail(source))) : tree.unplaced;
  if (loose.length > 0) {
    rows.push({ key: UNPLACED_KEY, type: "unplaced", id: UNPLACED_KEY, depth: 0, parentId: null, collapsed: false, count: String(tree.unplaced.length), loose: false });
    for (const source of loose) rows.push(sourceRow(source, 1, null, true));
  }
  return rows;
}

export type DropPosition = "before" | "on" | "after";
export interface DropTarget { parentId: string | null; index: number | null }

/**
 * Where a drop lands. Onto a section heading: the end of that section. Beside a row: that
 * row's parent, at that place among the children that stay. Onto Not placed yet: out of its
 * section. Null when the drop is not allowed (a section into itself or below itself, a
 * section into Not placed yet, or onto something being dragged).
 */
export function dropTarget(tree: ContentsTree, rows: ContentsRow[], overKey: string, position: DropPosition, dragged: ContentsItemRef[]): DropTarget | null {
  const over = rows.find((row) => row.key === overKey);
  if (!over || dragged.length === 0) return null;
  const draggedKeys = new Set(dragged.map(refKey));
  const sectionsDragged = dragged.filter((ref) => ref.kind === "section");
  const allowedInto = (parentId: string | null) => parentId === null || !sectionsDragged.some((ref) => ref.id === parentId || isDescendant(tree, parentId, ref.id));
  if (over.type === "unplaced" || over.loose) return sectionsDragged.length ? null : { parentId: null, index: null };
  if (draggedKeys.has(over.key)) return null;
  if (over.type === "section" && position === "on") return allowedInto(over.id) ? { parentId: over.id, index: null } : null;
  const parentId = over.parentId;
  // A source cannot sit at the top level: that is Not placed yet.
  if (parentId === null && dragged.some((ref) => ref.kind === "source")) return null;
  if (!allowedInto(parentId)) return null;
  const siblings = parentId === null ? tree.roots : tree.sections.get(parentId)?.children ?? [];
  const remaining = siblings.filter((node) => !draggedKeys.has(refKey(nodeRef(node))));
  const at = remaining.findIndex((node) => node.id === over.id && node.type === over.type);
  if (at < 0) return null;
  return { parentId, index: at + (position === "after" ? 1 : 0) };
}

/** Apply a move to local contents, the way the server will: used before it answers. */
export function applyMove(contents: LibraryContents, input: Pick<ContentsMoveInput, "items" | "parentId" | "index">): LibraryContents {
  const tree = buildTree(contents);
  const moving = new Set(input.items.map(refKey));
  const target = input.parentId;
  const siblings = (target === null ? tree.roots : tree.sections.get(target)?.children ?? [])
    .filter((node) => !moving.has(refKey(nodeRef(node))))
    .map(nodeRef);
  const incoming = target === null ? input.items.filter((item) => item.kind === "section") : input.items;
  const at = input.index === null ? siblings.length : Math.min(input.index, siblings.length);
  const next = [...siblings.slice(0, at), ...incoming, ...siblings.slice(at)];
  const position = new Map(next.map((ref, index) => [refKey(ref), index]));
  const movedSources = new Set(input.items.filter((item) => item.kind === "source").map((item) => item.id));
  const sections = contents.sections.map((section) => {
    if (moving.has(sectionKey(section.id)) && incoming.some((ref) => ref.kind === "section" && ref.id === section.id)) {
      return { ...section, parentId: target, position: position.get(sectionKey(section.id))!, version: section.version + 1 };
    }
    if ((section.parentId ?? null) === target && position.has(sectionKey(section.id))) return { ...section, position: position.get(sectionKey(section.id))! };
    return section;
  });
  const placements = contents.placements
    .filter((placement) => !movedSources.has(placement.sourceId))
    .map((placement) => placement.sectionId === target && position.has(sourceKey(placement.sourceId)) ? { ...placement, position: position.get(sourceKey(placement.sourceId))! } : placement);
  if (target !== null) {
    for (const item of incoming) if (item.kind === "source") placements.push({ sourceId: item.id, sectionId: target, position: position.get(sourceKey(item.id))! });
  }
  return { ...contents, sections, placements };
}

/** Sections below a node (not counting itself) and the sources inside it, for the delete question. */
export function subtreeStats(node: ContentsNode): { sections: number; sources: number } {
  if (node.type === "source") return { sections: 0, sources: 1 };
  let sections = 0;
  let sources = 0;
  for (const child of node.children) {
    const inner = subtreeStats(child);
    sections += inner.sections + (child.type === "section" ? 1 : 0);
    sources += inner.sources;
  }
  return { sections, sources };
}

/** Sections as an indented list, for the Move to… picker. */
export function sectionList(tree: ContentsTree): Array<{ id: string; title: string; depth: number; path: string }> {
  const out: Array<{ id: string; title: string; depth: number; path: string }> = [];
  const walk = (nodes: ContentsNode[], depth: number, path: string[]) => {
    for (const node of nodes) {
      if (node.type !== "section") continue;
      const here = [...path, node.section.title];
      out.push({ id: node.id, title: node.section.title, depth, path: here.join(" / ") });
      walk(node.children, depth + 1, here);
    }
  };
  walk(tree.roots, 0, []);
  return out;
}
