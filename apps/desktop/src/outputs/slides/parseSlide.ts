/**
 * A slide's body (its marker already taken off, see sections.ts) read for the layout it is to be
 * drawn in. The result says what each part of the layout holds; `null` says the body does not
 * take that layout's form, and the slide is drawn as plain text, as every slide was before layouts.
 *
 * The rules are those of the service's `layout_shape` check (skill_checks.py, `_misfit`); the same
 * cases are in parseSlide.test.ts and tests/test_skillbook.py. Where this differs it is to draw
 * more, never to draw something the text does not say:
 * - a table that will not draw as a chart (a value that is not a number, a unit that changes from
 *   row to row, fewer or more rows than a chart takes) is drawn as the `compare` table it is;
 * - a `two_column` table with other than two columns is drawn as `compare`;
 * - words around the part a layout draws (between the heading and a table, under a list) are kept
 *   as `lead` and `after`.
 *
 * Every piece of text is the Markdown slice of the slide it came from, cut by the parser's offsets,
 * so a citation like `[3]` survives and AnswerBody draws it as a button.
 */
import { fromMarkdown } from "mdast-util-from-markdown";
import { gfmFromMarkdown } from "mdast-util-gfm";
import { gfm } from "micromark-extension-gfm";
import type { List, ListItem, Root, RootContent, Table, TableCell, TableRow } from "mdast";

/** Text as the slide has it, Markdown and citations included. */
export type Raw = string;

/** The words around what a layout draws. */
interface Frame {
  /** The slide's heading, which is its conclusion; none when the slide has no heading. */
  title: Raw | null;
  /** Words between the heading and what the layout draws. */
  lead: Raw | null;
  /** Words after it: the sentence under a table or a chart. */
  after: Raw | null;
}

export type Align = "left" | "right" | "center" | null;

export interface TitleShape {
  layout: "title";
  title: Raw;
  /** The line under the title, and anything else the slide says. */
  message: Raw | null;
}

export interface BigNumberShape {
  layout: "big_number";
  title: Raw | null;
  /** The number, or the short phrase, inside its bold marks. */
  figure: Raw;
  /** What it is compared with and where it comes from. */
  caption: Raw;
}

export interface TableShape extends Frame {
  layout: "compare" | "two_column";
  head: Raw[];
  rows: Raw[][];
  align: Align[];
}

/** One row of a chart. */
export interface ChartPoint {
  /** The row's name (a time, for a line), as written. */
  label: Raw;
  /** Where it is on a line chart's time axis when every label is a number; otherwise null. */
  x: number | null;
  value: number;
  /** The value as written, with the header's unit when the cell had none; no citation. */
  text: Raw;
  /** The citation marks of the cell as written: "[3]", "[i:3][4]", "[?]". */
  marks: Raw;
  /** The numbers of the sources the cell cites. */
  cites: number[];
  /** The row's label is bold: the one to point at. */
  highlight: boolean;
}

export interface ChartShape extends Frame {
  layout: "bar_chart" | "line_chart";
  /** The first column's header as written: what the rows are (a year, an item). */
  axis: Raw;
  /** The value column's header as written, unit and all. */
  measure: Raw;
  points: ChartPoint[];
}

export interface FlowShape extends Frame {
  layout: "flow";
  steps: Array<{ number: number; title: Raw | null; detail: Raw }>;
}

export interface TimelineShape extends Frame {
  layout: "timeline";
  events: Array<{ when: Raw; what: Raw }>;
}

export interface DiagramShape extends Frame {
  layout: "diagram";
  nodes: Array<{ key: string; label: Raw }>;
  edges: Array<{ fromKey: string; from: Raw; relationship: Raw; toKey: string; to: Raw; refs: number[] }>;
  layers: string[][];
}

export interface CardsShape extends Frame {
  layout: "cards";
  cards: Array<{ name: Raw; text: Raw }>;
}

export interface QuoteShape extends Frame {
  layout: "quote";
  quote: Raw;
  /** Who said it, from the line that starts with an em dash. */
  by: Raw;
}

export interface FigureShape {
  layout: "figure";
  title: Raw | null;
  /** The managed asset the image line names (`gunther-asset:ID`). */
  assetId: string;
  /** The image's own caption, which is what shows if the asset is missing. */
  alt: string;
  /** What to look at, with its citation. */
  line: Raw;
}

export interface TextShape {
  layout: "text";
  title: Raw | null;
  body: Raw;
}

export interface TakeawaysShape extends Frame {
  layout: "takeaways";
  /** The list items; none when the slide holds no list (then its words are in `after`). */
  items: Raw[];
}

export type SlideShape =
  | TitleShape | BigNumberShape | TableShape | ChartShape | FlowShape | TimelineShape
  | DiagramShape | CardsShape | QuoteShape | FigureShape | TextShape | TakeawaysShape;
export type GraphicShape = FlowShape | DiagramShape | ChartShape;
export type GraphicLayout = GraphicShape["layout"];

/** The layouts a slide can be drawn in: the names a marker carries. */
export const SLIDE_LAYOUTS = [
  "title", "big_number", "compare", "two_column", "flow", "timeline", "bar_chart", "line_chart",
  "cards", "quote", "figure", "diagram", "text", "takeaways",
] as const;

// What counts as a citation in a table cell: [3], [1, 2], [?], and the marks the Checker adds,
// [i:3] (an inference), [p:3] (partly supported) and [d:3] (disputed). Each with the space before it.
const MARK = /\s*\[(?:[ipd]:)?(?:\d+(?:\s*[,，、]\s*\d+)*|\?)\]/g;
// A number with at most a sign and a currency sign before it and a unit after it: VALUE in skill_checks.py.
const VALUE = /^([~≈<>≤≥±+\-−–]?)([$€£¥￥]?)(\d+(?:\.\d+)?)(\D{0,12})$/;
// "1,200" and "1 200" are 1200: a separator followed by exactly three digits. Written without a
// look-behind, which the oldest Safari the app is built for does not have.
const THOUSANDS = /(\d)[,，\u2009\u202f ](?=\d{3}(?!\d))/g;
const NUMBER = /^[+\-−–]?\d+(?:\.\d+)?$/;
const BOLD_ONLY = /^\*\*(.+?)\*\*$/;
const IMAGE = /^!\[([^\]]*)\]\(gunther-asset:([A-Za-z0-9_-]+)\)$/;
const CJK = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]/g;
const WORD = /[A-Za-z0-9][\p{L}\p{N}_'’.%-]*/gu;
const LEAD_SEPARATOR = /^[\s:：、,，.\-–—·]+/;
const NEGATIVE = new Set(["-", "−", "–"]);
// A unit that goes straight after its number: 62%, 25°, 12万.
const ATTACHED_UNIT = /^[%‰°\u3000-\u9fff\uff00-\uffef]/;

/** How long a piece of text reads, as `units` in skill_checks.py: a CJK character is 1, a Latin word 1.7. */
export function units(text: string): number {
  const plain = text.replace(MARK, "").replaceAll("**", "").replaceAll("__", "");
  return (plain.match(CJK)?.length ?? 0) + 1.7 * (plain.replace(CJK, " ").match(WORD)?.length ?? 0);
}

/** The words of a Markdown fragment without its marks, citations and escapes: for a tooltip or a label. */
export function plainText(raw: string): string {
  return raw.replace(MARK, "").replace(/\*\*|__|`/g, "").replace(/\\(.)/g, "$1").replace(/\s+/g, " ").trim();
}

/** The unit a table header names in brackets: "Share (%)" gives "%", "用户数（万）" gives "万". */
export function unitOf(header: string): string | null {
  const groups = [...plainText(header).matchAll(/[（(]([^（）()]{1,16})[）)]/g)];
  return groups.at(-1)?.[1]?.trim() || null;
}

interface Value {
  number: number;
  /** What follows the digits in the cell, without spaces: the cell's own unit. */
  tail: string;
  /** The cell without its citations: the number and its unit as written. */
  text: string;
  marks: string;
  cites: number[];
}

/** A table cell read as a number, with its citations taken off, or null when it is not one. */
export function valueOf(cell: string): Value | null {
  const marks = cell.match(MARK)?.map((mark) => mark.trim()) ?? [];
  const text = cell.replace(MARK, "").trim();
  const found = VALUE.exec(text.replaceAll("**", "").replaceAll("__", "").trim().replace(THOUSANDS, "$1"));
  const number = Number(found?.[3]);
  if (!found || !Number.isFinite(number)) return null;
  return {
    number: NEGATIVE.has(found[1] ?? "") ? -number : number,
    tail: (found[4] ?? "").replace(/\s+/g, ""),
    text,
    marks: marks.join(""),
    cites: marks.flatMap((mark) => (mark.match(/\d+/g) ?? []).map(Number)),
  };
}

// --- reading the Markdown -----------------------------------------------------------------

const markdown = (text: string): Root => fromMarkdown(text, { extensions: [gfm()], mdastExtensions: [gfmFromMarkdown()] });

interface Located { position?: Root["position"] | undefined }
const startOf = (node: Located) => node.position?.start.offset ?? 0;
const endOf = (node: Located) => node.position?.end.offset ?? 0;

/** The slice of the slide a node came from. */
const rawOf = (body: string, node: Located): Raw => body.slice(startOf(node), endOf(node));

/** What is inside a node: from its first child to its last (a heading without its "#", a cell without its pipes). */
function innerOf(body: string, node: { children: Located[] }): Raw {
  const first = node.children[0];
  const last = node.children.at(-1);
  return first && last ? body.slice(startOf(first), endOf(last)).trim() : "";
}

const cut = (body: string, from: number, to = body.length): Raw | null => body.slice(from, to).trim() || null;

/** What a reader knows about the slide it reads. */
interface Slide {
  body: string;
  blocks: RootContent[];
  title: Raw | null;
  /** Where the heading ends (0 for a slide with none), and the index of the first block after it. */
  from: number;
  first: number;
}

/** The words around a part of the slide: those before it and those after it. */
function around(slide: Slide, start: number, end: number): Frame {
  return { title: slide.title, lead: cut(slide.body, slide.from, start), after: cut(slide.body, end) };
}

interface TableRead {
  head: Raw[];
  rows: Raw[][];
  /** For each row, whether its label (its first cell) is all bold. */
  bold: boolean[];
  align: Align[];
  start: number;
  end: number;
}

const wholeBold = (cell: TableCell) => cell.children.length === 1 && cell.children[0]?.type === "strong";

/**
 * The table of the slide, as rows of cell text. GFM takes a sentence on the line right under a
 * table for one more row; a row of the table has a pipe in it, so the table stops where they stop.
 */
function readTable(slide: Slide): TableRead | null {
  const table = slide.blocks.find((block): block is Table => block.type === "table");
  if (!table) return null;
  const lines: TableRow[] = [];
  for (const row of table.children) {
    if (!rawOf(slide.body, row).includes("|")) break;
    lines.push(row);
  }
  const [head, ...rows] = lines;
  if (!head || rows.length === 0) return null;
  const cells = (row: TableRow) => row.children.map((cell) => ({ raw: innerOf(slide.body, cell), bold: wholeBold(cell) }));
  const header = cells(head);
  const body = rows.map(cells);
  // A cell beyond the header's columns would be lost.
  if (header.length === 0 || body.some((row) => row.length > header.length)) return null;
  return {
    head: header.map((cell) => cell.raw),
    rows: body.map((row) => header.map((_, at) => row[at]?.raw ?? "")),
    bold: body.map((row) => row[0]?.bold ?? false),
    align: header.map((_, at) => table.align?.[at] ?? null),
    start: startOf(table),
    end: endOf(lines[lines.length - 1] ?? table),
  };
}

function tableShape(slide: Slide, found: TableRead, layout: "compare" | "two_column"): TableShape {
  return { layout, ...around(slide, found.start, found.end), head: found.head, rows: found.rows, align: found.align };
}

const DIAGRAM_CITE = /\[(?:[ipd]:)?(\d+(?:\s*[,，、]\s*\d+)*)\]/g;
const diagramWidth = (text: string) => [...plainText(text)].reduce(
  (width, char) => width + (/^[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uac00-\ud7af]$/.test(char) ? 1 : /\s/.test(char) ? 0.28 : /[A-Za-z0-9]/.test(char) ? 0.58 : 0.4),
  0,
);

function diagramShape(slide: Slide): DiagramShape | TableShape | null {
  const found = readTable(slide);
  if (!found) return null;
  const fallback = () => tableShape(slide, found, "compare");
  const headers = found.head.map((cell) => plainText(cell).replace(/\s+/g, " ").toLocaleLowerCase());
  if (headers.length !== 3 || headers.some((header) => !header) || new Set(headers).size !== 3) return fallback();
  if (!found.rows.length || found.rows.length > 8 || found.rows.some((row) => row.length !== 3)) return fallback();

  const nodes: string[] = [];
  const keys: string[] = [];
  const adjacency = new Map<string, Set<string>>();
  const indegree = new Map<string, number>();
  const edges: DiagramShape["edges"] = [];
  const uniqueEdges = new Set<string>();
  const nodeKey = (raw: string) => plainText(raw).replace(/\s+/g, " ").toLocaleLowerCase();
  const addNode = (raw: string, key: string) => {
    MARK.lastIndex = 0;
    const cited = MARK.test(raw);
    MARK.lastIndex = 0;
    if (!key || units(raw) > 24 || diagramWidth(raw) > 15 || cited) return false;
    if (!indegree.has(key)) {
      nodes.push(raw.trim());
      keys.push(key);
      adjacency.set(key, new Set());
      indegree.set(key, 0);
    }
    return true;
  };
  for (const [fromRaw = "", relationship, toRaw = ""] of found.rows) {
    const rel = relationship ?? "";
    const from = nodeKey(fromRaw);
    const to = nodeKey(toRaw);
    if (!addNode(fromRaw, from) || !addNode(toRaw, to)) return fallback();
    const refs = [...rel.matchAll(DIAGRAM_CITE)].flatMap((match) => (match[1]?.match(/\d+/g) ?? []).map(Number));
    if (!refs.length || !plainText(rel) || units(rel) > 18 || diagramWidth(rel) > 13.5 || from === to) return fallback();
    const edgeKey = `${from}\u0000${to}`;
    if (uniqueEdges.has(edgeKey)) return fallback();
    uniqueEdges.add(edgeKey);
    adjacency.get(from)?.add(to);
    indegree.set(to, (indegree.get(to) ?? 0) + 1);
    edges.push({ fromKey: from, from: fromRaw.trim(), relationship: rel.trim(), toKey: to, to: toRaw.trim(), refs });
  }
  if (nodes.length < 2 || nodes.length > 6) return fallback();
  const ready = keys.filter((key) => indegree.get(key) === 0);
  const ranks = new Map(ready.map((key) => [key, 0]));
  let visited = 0;
  while (ready.length) {
    const node = ready.shift() as string;
    visited += 1;
    for (const target of adjacency.get(node) ?? []) {
      ranks.set(target, Math.max(ranks.get(target) ?? 0, (ranks.get(node) ?? 0) + 1));
      indegree.set(target, (indegree.get(target) ?? 0) - 1);
      if (indegree.get(target) === 0) ready.push(target);
    }
  }
  if (visited !== nodes.length) return fallback();
  const layerCount = Math.max(...ranks.values()) + 1;
  const layers = Array.from({ length: layerCount }, (_, rank) => keys.filter((key) => ranks.get(key) === rank));
  if (layers.length > 3 || layers.some((layer) => layer.length > 3)) return fallback();
  const y = new Map<string, number>();
  for (const layer of layers) layer.forEach((node, index) => y.set(node, 210 + (index - (layer.length - 1) / 2) * 140));
  const lanes = new Map<number, number[]>();
  for (const edge of edges) {
    const from = edge.fromKey;
    const to = edge.toKey;
    const rank = ranks.get(from) ?? 0;
    if ((ranks.get(to) ?? 0) - rank !== 1) return fallback();
    const center = ((y.get(from) ?? 210) + (y.get(to) ?? 210)) / 2;
    const previous = lanes.get(rank) ?? [];
    if (previous.some((at) => Math.abs(at - center) < 64)) return fallback();
    previous.push(center);
    lanes.set(rank, previous);
  }
  return {
    layout: "diagram",
    ...around(slide, found.start, found.end),
    nodes: nodes.map((label, index) => ({ label, key: keys[index] ?? nodeKey(label) })),
    edges,
    layers,
  };
}

/** A unit the header names that can go after a number; "10k" and "$M" say what the number counts and stay in the header. */
function attachable(unit: string | null): string | null {
  return unit && !/\d|[$€£¥￥]/.test(unit) ? unit : null;
}

/** A chart's rows, or null when one value is not a number, or the rows count in different units. */
function pointsOf(found: TableRead): ChartPoint[] | null {
  const read = found.rows.map((row) => valueOf(row[1] ?? ""));
  if (read.some((value) => value === null)) return null;
  const values = read as Value[];
  // Bars of 12 and of 3.5 万 are not on one scale.
  if (new Set(values.map((value) => value.tail)).size > 1) return null;
  const unit = attachable(unitOf(found.head[1] ?? ""));
  const everyBold = found.bold.every(Boolean);
  const points = values.map((value, at): ChartPoint => ({
    label: found.rows[at]?.[0] ?? "",
    x: null,
    value: value.number,
    // The header's unit goes on a number that has none of its own.
    text: !value.tail && unit ? `${value.text}${ATTACHED_UNIT.test(unit) ? "" : " "}${unit}` : value.text,
    marks: value.marks,
    cites: value.cites,
    highlight: !everyBold && (found.bold[at] ?? false),
  }));
  // When every label is a number (years), the time axis is true to them.
  const labels = points.map((point) => plainText(point.label));
  const xs = labels.map((label) => Number(label.replace(/^[−–]/, "-")));
  if (labels.every((label) => NUMBER.test(label)) && new Set(xs).size === xs.length) {
    points.forEach((point, at) => { point.x = xs[at] ?? null; });
    points.sort((a, b) => (a.x ?? 0) - (b.x ?? 0));
  }
  return points;
}

function chartShape(slide: Slide, layout: "bar_chart" | "line_chart"): ChartShape | TableShape | null {
  const found = readTable(slide);
  if (!found) return null;
  const points = found.head.length >= 2 && found.rows.length >= 2 && found.rows.length <= 6 ? pointsOf(found) : null;
  // Not drawn as a chart: the table it is.
  if (!points) return tableShape(slide, found, "compare");
  return { layout, ...around(slide, found.start, found.end), axis: found.head[0] ?? "", measure: found.head[1] ?? "", points };
}

/** The part of a list item after its bold start, without the colon or dash that joins them. */
function boldStart(body: string, item: ListItem): { bold: Raw; text: Raw } | null {
  const paragraph = item.children[0];
  if (paragraph?.type !== "paragraph") return null;
  const strong = paragraph.children[0];
  // "**" and not "__": the service looks for the same.
  if (strong?.type !== "strong" || startOf(strong) !== startOf(paragraph) || !body.startsWith("**", startOf(strong))) return null;
  const last = item.children.at(-1) ?? paragraph;
  return { bold: innerOf(body, strong), text: body.slice(endOf(strong), endOf(last)).replace(LEAD_SEPARATOR, "").trim() };
}

/** Split a flow item's existing bold start from its explanation using Markdown offsets. */
function flowStep(body: string, item: ListItem): { title: Raw | null; detail: Raw } {
  const raw = innerOf(body, item);
  const paragraph = item.children[0];
  const strong = paragraph?.type === "paragraph" ? paragraph.children[0] : null;
  if (
    paragraph?.type !== "paragraph"
    || strong?.type !== "strong"
    || startOf(strong) !== startOf(paragraph)
    || !body.startsWith("**", startOf(strong))
  ) return { title: null, detail: raw };

  const titleEnd = endOf(strong);
  const itemEnd = endOf(item.children.at(-1) ?? paragraph);
  const tail = body.slice(titleEnd, itemEnd);
  const punctuation = /^([ \t]*(?::|：|,|，)[ \t]*)([\s\S]*)$/.exec(tail);
  const dash = /^([ \t]+(?:—|–|-)[ \t]+)([\s\S]*)$/.exec(tail);
  const dashTail = dash?.[2] ?? "";
  const valuePrefix = /^[\p{Sc}\p{N}.．+\-−–—]/u.test(dashTail.trimStart());
  const separator = punctuation ?? (dash && !valuePrefix ? dash : null);
  const between = separator?.[1] ?? /^[ \t]*/.exec(tail)?.[0] ?? "";
  const detail = separator?.[2] ?? tail.slice(between.length);
  const citationOnly = detail
    .replace(/\[(?:(?:[ipd]:)?\d+(?:\s*[,，、]\s*\d+)*|\?)\]/g, "")
    .trim() === "";
  return {
    title: body.slice(startOf(strong), titleEnd) + (citationOnly ? tail : between.trimEnd()),
    detail: citationOnly ? "" : detail,
  };
}

/** The first list of the slide (an ordered one, if asked) when it has between `least` and `most` items. */
function listOf(slide: Slide, ordered: boolean | null, least: number, most: number): { items: ListItem[]; frame: Frame } | null {
  const list = slide.blocks.find((block): block is List => block.type === "list" && (ordered === null || block.ordered === ordered));
  if (!list || list.children.length < least || list.children.length > most) return null;
  return { items: list.children, frame: around(slide, startOf(list), endOf(list)) };
}

/** The first line of the paragraph that opens the slide's body, and where it ends. */
function firstLine(slide: Slide): { line: string; end: number } | null {
  const paragraph = slide.blocks[slide.first];
  if (paragraph?.type !== "paragraph") return null;
  const text = rawOf(slide.body, paragraph);
  const newline = text.indexOf("\n");
  return { line: (newline < 0 ? text : text.slice(0, newline)).trim(), end: startOf(paragraph) + (newline < 0 ? text.length : newline) };
}

/** Every item opens with bold text (a time, a name), or the list is not one the layout draws. */
function boldStarts(slide: Slide, items: ListItem[]): Array<{ bold: Raw; text: Raw }> | null {
  const starts = items.map((item) => boldStart(slide.body, item));
  return starts.every((start) => start !== null) ? (starts as Array<{ bold: Raw; text: Raw }>) : null;
}

// --- one reader for each layout -----------------------------------------------------------

type Reader = (slide: Slide) => SlideShape | null;

const READERS: Record<(typeof SLIDE_LAYOUTS)[number], Reader> = {
  title(slide) {
    const heading = slide.blocks[0];
    // A title slide is a "# " title; the line under it is the message, and it may be missing.
    if (heading?.type !== "heading" || heading.depth !== 1) return null;
    return { layout: "title", title: slide.title ?? "", message: cut(slide.body, slide.from) };
  },

  big_number(slide) {
    const number = firstLine(slide);
    const found = number && BOLD_ONLY.exec(number.line);
    if (!number || !found || units(found[1] ?? "") > 24) return null;
    const caption = cut(slide.body, number.end);
    return caption ? { layout: "big_number", title: slide.title, figure: found[1] ?? "", caption } : null;
  },

  compare(slide) {
    const found = readTable(slide);
    return found && tableShape(slide, found, "compare");
  },

  two_column(slide) {
    const found = readTable(slide);
    return found && tableShape(slide, found, found.head.length === 2 ? "two_column" : "compare");
  },

  bar_chart: (slide) => chartShape(slide, "bar_chart"),
  line_chart: (slide) => chartShape(slide, "line_chart"),

  flow(slide) {
    const found = listOf(slide, true, 2, 6);
    return found && { layout: "flow", ...found.frame, steps: found.items.map((item, index) => {
      const marker = /^\s*(\d+)[.)][ \t]+/.exec(rawOf(slide.body, item));
      return { number: Number(marker?.[1] ?? index + 1), ...flowStep(slide.body, item) };
    }) };
  },

  timeline(slide) {
    const found = listOf(slide, null, 2, 7);
    const events = found && boldStarts(slide, found.items);
    return found && events && { layout: "timeline", ...found.frame, events: events.map((event) => ({ when: event.bold, what: event.text })) };
  },

  cards(slide) {
    const found = listOf(slide, null, 2, 6);
    const cards = found && boldStarts(slide, found.items);
    return found && cards && { layout: "cards", ...found.frame, cards: cards.map((card) => ({ name: card.bold, text: card.text })) };
  },

  quote(slide) {
    const quoted = slide.blocks.find((block) => block.type === "blockquote");
    if (!quoted) return null;
    // The words are the lines marked with ">"; who said them is the first line that starts with a
    // dash. That line is not marked, so GFM reads it as the last line of the quote.
    const lines = slide.body.slice(startOf(quoted)).split("\n");
    const byAt = lines.findIndex((line) => line.trim().startsWith("—"));
    if (!lines[0]?.trim().startsWith("> ") || byAt < 0) return null;
    let byEnd = byAt + 1;
    while (byEnd < lines.length && lines[byEnd]?.trim()) byEnd += 1;
    const words = lines.slice(0, byAt).map((line) => line.replace(/^\s*>\s?/, "")).join("\n").trim();
    if (!words) return null;
    return {
      layout: "quote",
      title: slide.title,
      lead: cut(slide.body, slide.from, startOf(quoted)),
      quote: words,
      by: lines.slice(byAt, byEnd).join("\n").trim(),
      after: lines.slice(byEnd).join("\n").trim() || null,
    };
  },

  diagram(slide) {
    return diagramShape(slide);
  },

  figure(slide) {
    const image = firstLine(slide);
    const found = image && IMAGE.exec(image.line);
    if (!image || !found) return null;
    const line = cut(slide.body, image.end);
    // One figure, and one line about it.
    return line && !line.includes("![") ? { layout: "figure", title: slide.title, assetId: found[2] ?? "", alt: found[1] ?? "", line } : null;
  },

  text: (slide) => ({ layout: "text", title: slide.title, body: cut(slide.body, slide.from) ?? "" }),

  takeaways(slide) {
    const found = listOf(slide, null, 0, Number.POSITIVE_INFINITY);
    if (!found) return { layout: "takeaways", title: slide.title, lead: null, after: cut(slide.body, slide.from), items: [] };
    return { layout: "takeaways", ...found.frame, items: found.items.map((item) => innerOf(slide.body, item)) };
  },
};

/**
 * What a slide body holds for a layout, or null when it does not take that form (or the layout is
 * none of the fourteen). Every slide a skill makes was checked by the service for its form, but a
 * slide typed by hand has not been.
 */
export function parseSlide(layout: string | null | undefined, body: string): SlideShape | null {
  const name = SLIDE_LAYOUTS.find((known) => known === layout);
  if (!name) return null;
  const blocks = markdown(body).children;
  const heading = blocks[0]?.type === "heading" ? blocks[0] : null;
  return READERS[name]({
    body,
    blocks,
    title: heading ? innerOf(body, heading) || null : null,
    from: heading ? endOf(heading) : 0,
    first: heading ? 1 : 0,
  });
}

/** Parse one list/table block as an element graphic, with strict source shape requirements. */
export function parseGraphicBlock(layout: GraphicLayout, body: string): GraphicShape | null {
  const blocks = markdown(body).children;
  const slide: Slide = { body, blocks, title: null, from: 0, first: 0 };
  if (layout === "flow") {
    if (blocks.length !== 1) return null;
    const list = blocks.find((block): block is List => block.type === "list" && block.ordered === true);
    if (!list || list.children.length < 2 || list.children.length > 6) return null;
    const shape = READERS.flow(slide);
    return shape?.layout === "flow" && !shape.lead && !shape.after ? shape : null;
  }
  if (blocks.length !== 1 || blocks[0]?.type !== "table") return null;
  if (body.replace(/\r\n?/g, "\n").split("\n").some((line) => line.trim() && !line.includes("|"))) return null;
  const table = readTable(slide);
  if (!table) return null;
  if (layout === "diagram") {
    if (table.head.length !== 3 || table.rows.some((row) => row.length !== 3) || table.rows.length < 1 || table.rows.length > 8) return null;
    const shape = READERS.diagram(slide);
    return shape?.layout === "diagram" && !shape.lead && !shape.after ? shape : null;
  }
  if (table.head.length !== 2 || table.rows.some((row) => row.length !== 2) || table.rows.length < 2 || table.rows.length > 6) return null;
  const shape = READERS[layout](slide);
  return shape?.layout === layout && !shape.lead && !shape.after ? shape : null;
}
