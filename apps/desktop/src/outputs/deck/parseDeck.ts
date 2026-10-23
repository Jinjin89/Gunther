/**
 * A studio deck (design v2, docs/STUDIO_FORMAT.md) read into one typed shape per layout.
 *
 * Every piece of text stays the Markdown slice it was written as, so a citation like `[3]` and
 * **bold** survive. A body that does not take its layout's grammar degrades, and never loses text:
 * a chart whose numbers are not numbers becomes a table, anything else becomes `text` (the
 * slide's Markdown, drawn as ordinary points). Words that belong to no part of the grammar are
 * kept in `after`.
 */
import { attrsOf, layoutOf, parseSlides, splitDocument, splitDesign, type DeckStyle, type DeckTheme } from "../sections";
import { plainText, unitOf, valueOf } from "../slides/parseSlide";
import { BUDGETS, READ_FACTOR, countUnits, isDeckLayout, type DeckLayout, type Range } from "./units";

export type Raw = string;
export type ChartKind = "bar" | "column" | "line";
export type Align = "left" | "right" | "center" | null;

export interface Pair { lead: Raw | null; detail: Raw }
export interface ChartCategory { label: Raw; highlight: boolean }
export interface ChartValue { n: number; text: Raw; label: string }
export interface ChartSeries { name: Raw; unit: string | null; values: ChartValue[] }
export interface TableRow { cells: Raw[]; highlight: boolean }

export type DeckData =
  | { layout: "title"; title: Raw; subtitle: Raw | null; meta: Raw | null }
  | { layout: "section"; title: Raw; subtitle: Raw | null }
  | { layout: "summary"; title: Raw; items: Pair[]; after: Raw | null }
  | { layout: "stat"; title: Raw; value: Raw; label: Raw; context: Raw | null }
  | { layout: "stats"; title: Raw; items: Array<{ value: Raw; label: Raw }>; after: Raw | null }
  | { layout: "chart"; title: Raw; kind: ChartKind; unit: string | null; categories: ChartCategory[]; series: ChartSeries[]; after: Raw | null }
  | { layout: "table"; title: Raw; head: Raw[]; rows: TableRow[]; align: Align[]; after: Raw | null }
  | { layout: "compare"; title: Raw; sides: Array<{ name: Raw; items: Raw[] }>; after: Raw | null }
  | { layout: "process"; title: Raw; items: Pair[]; after: Raw | null }
  | { layout: "timeline"; title: Raw; items: Array<{ when: Raw; event: Raw }>; after: Raw | null }
  | { layout: "quote"; title: Raw; quote: Raw; attribution: Raw | null; after: Raw | null }
  | { layout: "figure"; title: Raw; alt: string; assetId: string; takeaway: Raw | null }
  | { layout: "statement"; title: Raw; statement: Raw; items: Raw[]; after: Raw | null }
  | { layout: "points"; title: Raw; items: Pair[]; after: Raw | null }
  | { layout: "actions"; title: Raw; items: Pair[]; after: Raw | null }
  | { layout: "text"; title: Raw; markdown: Raw; from: string | null };

// --- reading lines ---------------------------------------------------------------------------

type Block =
  | { t: "items"; ordered: boolean; items: string[] }
  | { t: "table"; rows: string[][]; aligns: Align[] }
  | { t: "quote"; text: string }
  | { t: "h3"; text: string }
  | { t: "image"; alt: string; id: string }
  | { t: "line"; text: string };

const ITEM = /^\s*(?:([-*+•])|(\d{1,2}[.)]))\s+(.*)$/;
const IMAGE = /^!\[([^\]]*)\]\(gunther-asset:([A-Za-z0-9_-]+)\)\s*$/;
const DELIMITER = /^:?-+:?$/;

/** The cells of a table row: leading and trailing pipes are dropped, `\|` is a pipe in a cell. */
function cellsOf(line: string): string[] {
  let text = line.trim();
  if (text.startsWith("|")) text = text.slice(1);
  if (text.endsWith("|") && !text.endsWith("\\|")) text = text.slice(0, -1);
  const cells: string[] = [];
  let current = "";
  for (let at = 0; at < text.length; at += 1) {
    if (text[at] === "\\" && text[at + 1] === "|") { current += "|"; at += 1; } else if (text[at] === "|") { cells.push(current.trim()); current = ""; } else current += text[at];
  }
  cells.push(current.trim());
  return cells;
}

export function scan(body: string, options: { paragraphs?: boolean } = {}): Block[] {
  const blocks: Block[] = [];
  const lines = body.replace(/\r\n?/g, "\n").split("\n");
  let blank = true;
  for (let at = 0; at < lines.length; at += 1) {
    const line = lines[at] ?? "";
    const trimmed = line.trim();
    if (!trimmed) { blank = true; continue; }
    const last = blocks.at(-1);
    const wasBlank = blank;
    blank = false;
    const item = ITEM.exec(line);
    if (trimmed.startsWith("|")) {
      const cells = cellsOf(trimmed);
      if (last?.t === "table") {
        if (last.rows.length === 1 && last.aligns.length === 0 && cells.every((cell) => DELIMITER.test(cell))) last.aligns = cells.map((cell) => cell.startsWith(":") && cell.endsWith(":") ? "center" : cell.endsWith(":") ? "right" : cell.startsWith(":") ? "left" : null);
        else last.rows.push(cells);
      } else blocks.push({ t: "table", rows: [cells], aligns: [] });
    } else if (/^#{3,6}\s+/.test(trimmed)) {
      blocks.push({ t: "h3", text: trimmed.replace(/^#{3,6}\s+/, "").trim() });
    } else if (trimmed.startsWith(">")) {
      const text = trimmed.replace(/^>\s?/, "");
      if (last?.t === "quote") last.text += `\n${text}`; else blocks.push({ t: "quote", text });
    } else if (IMAGE.test(trimmed)) {
      const found = IMAGE.exec(trimmed)!;
      blocks.push({ t: "image", alt: found[1] ?? "", id: found[2] ?? "" });
    } else if (item) {
      const ordered = item[2] !== undefined;
      if (last?.t === "items" && last.ordered === ordered) last.items.push(item[3]!.trim()); else blocks.push({ t: "items", ordered, items: [item[3]!.trim()] });
    } else if (last?.t === "items" && /^\s{2,}\S/.test(line)) {
      last.items[last.items.length - 1] += ` ${trimmed}`;
    } else if (options.paragraphs && last?.t === "line" && !wasBlank) last.text += ` ${trimmed}`;
    else blocks.push({ t: "line", text: trimmed });
  }
  return blocks;
}

const join = (...parts: Array<string | null | undefined>): Raw | null => parts.filter((part): part is string => !!part).join("\n") || null;
const lineText = (blocks: Block[]): Raw | null => join(...blocks.map((block) => block.t === "line" ? block.text : null));

/** `**lead**: detail` (the colon may be inside the bold marks). */
export function splitLead(raw: string): Pair {
  const found = /^(?:\*\*|__)(.+?)(?:\*\*|__)\s*[:：]?\s*([\s\S]*)$/.exec(raw.trim());
  if (!found) return { lead: null, detail: raw.trim() };
  const lead = (found[1] ?? "").replace(/\s*[:：]\s*$/, "").trim();
  return lead ? { lead, detail: (found[2] ?? "").trim() } : { lead: null, detail: raw.trim() };
}

const stripHeading = (body: string, depths: RegExp): { title: Raw | null; rest: string } => {
  const lines = body.replace(/\r\n?/g, "\n").split("\n");
  const first = lines.findIndex((line) => line.trim());
  const found = first >= 0 ? depths.exec(lines[first]!.trim()) : null;
  if (!found) return { title: null, rest: body };
  return { title: found[1]!.trim(), rest: lines.slice(first + 1).join("\n") };
};

const withLead = (lead: Raw | null, after: Raw | null) => join(lead, after);

function text(title: Raw, rest: string, from: string | null): DeckData {
  return { layout: "text", title, markdown: rest.trim(), from };
}

function pairs(blocks: Block[]): { items: Pair[]; before: Raw | null; after: Raw | null } | null {
  const at = blocks.findIndex((block) => block.t === "items");
  if (at < 0) return null;
  const list = blocks[at] as Extract<Block, { t: "items" }>;
  const others = [...blocks.slice(0, at), ...blocks.slice(at + 1)];
  if (others.some((block) => block.t !== "line")) return null;
  return { items: list.items.map(splitLead), before: lineText(blocks.slice(0, at)), after: lineText(blocks.slice(at + 1)) };
}

function toTable(title: Raw, found: Extract<Block, { t: "table" }>, before: Raw | null, after: Raw | null): DeckData | null {
  const [head, ...body] = found.rows;
  if (!head || body.length === 0) return null;
  const wide = Math.max(head.length, ...body.map((row) => row.length));
  const fill = (row: string[]) => Array.from({ length: wide }, (_, at) => row[at] ?? "");
  return {
    layout: "table", title, head: fill(head),
    rows: body.map((row) => ({ cells: fill(row), highlight: /^(\*\*|__).+(\*\*|__)$/.test(row[0] ?? "") && (row[0] ?? "").replace(/^(\*\*|__)|(\*\*|__)$/g, "").length > 0 && !(row[0] ?? "").slice(2, -2).includes("**") })),
    align: Array.from({ length: wide }, (_, at) => found.aligns[at] ?? null), after: withLead(before, after),
  };
}

const bareHeader = (header: string) => plainText(header).replace(/\s*[（(][^()（）]*[）)]\s*$/, "").trim();

function toChart(title: Raw, found: Extract<Block, { t: "table" }>, kind: ChartKind, unitOption: string | null, before: Raw | null, after: Raw | null): DeckData | null {
  const [head, ...body] = found.rows;
  if (!head || head.length < 2 || head.length > 4 || body.length < 2 || body.length > 12) return null;
  const grid = body.map((row) => head.map((_, at) => row[at] ?? ""));
  const read = grid.map((row) => row.slice(1).map(valueOf));
  if (read.some((row) => row.some((value) => value === null))) return null;
  const series: ChartSeries[] = head.slice(1).map((name, column) => {
    const unit = unitOption ?? unitOf(name);
    return {
      name: bareHeader(name) || name, unit,
      values: read.map((row) => {
        const value = row[column]!;
        const attached = !value.tail && unit && !/[\p{L}\p{N}]/u.test(unit) && unit.length <= 2 ? unit : "";
        // Bold marks on a value only point at its row (see `bold` below); they are not drawn.
        const shown = value.text.replace(/^(\*\*|__)(.*)\1/, "$2").trim();
        return { n: value.number, text: shown, label: `${shown}${attached}` };
      }),
    };
  });
  const bold = grid.map((row) => row.some((cell) => /^(\*\*|__).+(\*\*|__)(?:\s*\[[^\]]*\])*$/.test(cell.trim())));
  const first = bold.indexOf(true);
  return {
    layout: "chart", title, kind, unit: unitOption, after: withLead(before, after), series,
    categories: grid.map((row, at) => ({ label: row[0]!.replace(/^(\*\*|__)(.*)(\*\*|__)$/, "$2").trim(), highlight: at === first })),
  };
}

/** One slide's body (marker and notes already taken off) as the data its layout draws. */
export function parseDeckSlide(layout: string | null | undefined, body: string, attrs: Record<string, string> = {}): DeckData {
  const name: DeckLayout | null = isDeckLayout(layout) ? layout : null;
  const { title: heading, rest } = stripHeading(body, /^#{1,2}\s+(.*)$/);
  const title = heading ?? "";
  const blocks = scan(rest);
  const fallback = () => text(title, rest, name);
  if (!name) return fallback();

  switch (name) {
    case "title":
    case "section": {
      const lines = blocks.filter((block): block is Extract<Block, { t: "line" }> => block.t === "line").map((block) => block.text);
      if (blocks.length !== lines.length) return fallback();
      if (heading === null) return lines.length ? (name === "title" ? { layout: name, title: lines[0]!, subtitle: lines[1] ?? null, meta: join(...lines.slice(2)) } : { layout: name, title: lines[0]!, subtitle: join(...lines.slice(1)) }) : fallback();
      return name === "title" ? { layout: name, title, subtitle: lines[0] ?? null, meta: join(...lines.slice(1)) } : { layout: name, title, subtitle: join(...lines) };
    }
    case "summary":
    case "points":
    case "process":
    case "actions": {
      const found = pairs(blocks);
      if (!found || found.items.length < (name === "process" ? 2 : 1)) return fallback();
      return { layout: name, title, items: found.items, after: withLead(found.before, found.after) };
    }
    case "stat": {
      const lines = blocks.every((block) => block.t === "line") ? lineText(blocks)?.split("\n") ?? [] : [];
      // A citation mark after the bold value is allowed (the format checker allows it); the label cites too.
      const value = /^(?:\*\*|__)(.+?)(?:\*\*|__)\s*(?:\[[^\]]+\]\s*)*$/.exec(lines[0] ?? "");
      if (!value || lines.length < 2) return fallback();
      return { layout: "stat", title, value: value[1]!.trim(), label: lines[1]!, context: join(...lines.slice(2)) };
    }
    case "stats": {
      const found = pairs(blocks);
      if (!found || found.items.length < 2 || found.items.some((item) => !item.lead)) return fallback();
      return { layout: "stats", title, items: found.items.map((item) => ({ value: item.lead!, label: item.detail })), after: withLead(found.before, found.after) };
    }
    case "timeline": {
      const found = pairs(blocks);
      if (!found || found.items.length < 2 || found.items.some((item) => !item.lead)) return fallback();
      return { layout: "timeline", title, items: found.items.map((item) => ({ when: item.lead!, event: item.detail })), after: withLead(found.before, found.after) };
    }
    case "chart":
    case "table": {
      const at = blocks.findIndex((block) => block.t === "table");
      const found = blocks[at];
      const others = blocks.filter((_, index) => index !== at);
      if (!found || found.t !== "table" || others.some((block) => block.t !== "line")) return fallback();
      const before = lineText(blocks.slice(0, at));
      const after = lineText(blocks.slice(at + 1));
      const kind = attrs.chart === "column" || attrs.chart === "line" ? attrs.chart : "bar";
      if (name === "chart") {
        const chart = toChart(title, found, kind, attrs.unit?.trim() || null, before, after);
        if (chart) return chart;
      }
      return toTable(title, found, before, after) ?? fallback();
    }
    case "compare": {
      const sides: Array<{ name: Raw; items: Raw[] }> = [];
      const trailing: Block[] = [];
      let lead: Raw | null = null;
      for (const block of blocks) {
        if (block.t === "h3") sides.push({ name: block.text, items: [] });
        else if (block.t === "items" && sides.length) sides.at(-1)!.items.push(...block.items);
        else if (block.t === "line") { if (sides.length) trailing.push(block); else lead = join(lead, block.text); } else return fallback();
      }
      if (sides.length !== 2 || sides.some((side) => side.items.length === 0)) return fallback();
      return { layout: "compare", title, sides, after: withLead(lead, lineText(trailing)) };
    }
    case "quote": {
      const quote = blocks.find((block): block is Extract<Block, { t: "quote" }> => block.t === "quote");
      if (!quote) return fallback();
      const others = blocks.filter((block) => block !== quote);
      if (others.some((block) => block.t !== "line")) return fallback();
      const lines = quote.text.split("\n");
      const dash = /^\s*(?:—|―|–|--|-)\s*/;
      let attribution: Raw | null = null;
      let said = lines;
      if (lines.length > 1 && dash.test(lines.at(-1)!)) { attribution = lines.at(-1)!.replace(dash, "").trim(); said = lines.slice(0, -1); }
      const plain = others.filter((block): block is Extract<Block, { t: "line" }> => block.t === "line").map((block) => block.text);
      const after = plain.filter((line) => !(attribution === null && dash.test(line)));
      if (attribution === null) {
        const named = plain.find((line) => dash.test(line));
        if (named) attribution = named.replace(dash, "").trim();
      }
      return { layout: "quote", title, quote: said.join(" ").trim(), attribution, after: join(...after) };
    }
    case "figure": {
      const image = blocks.find((block): block is Extract<Block, { t: "image" }> => block.t === "image");
      if (!image || blocks.some((block) => block.t !== "image" && block.t !== "line")) return fallback();
      return { layout: "figure", title, alt: image.alt, assetId: image.id, takeaway: lineText(blocks) };
    }
    case "statement": {
      const first = blocks.findIndex((block) => block.t === "line");
      const line = blocks[first];
      if (!line || line.t !== "line") return fallback();
      const list = blocks.find((block): block is Extract<Block, { t: "items" }> => block.t === "items");
      const rest = blocks.filter((block, index) => index !== first && block !== list);
      if (rest.some((block) => block.t !== "line")) return fallback();
      return { layout: "statement", title, statement: line.text, items: list?.items ?? [], after: lineText(rest) };
    }
  }
}

// --- whole decks -----------------------------------------------------------------------------

/** `design` is the slide's HTML (its `:::design` block), null when the template draws it; it is never part of `body` or `notes`. */
export interface DeckSlide { layout: string | null; attrs: Record<string, string>; data: DeckData; notes: string; body: string; design: string | null }
/** `style` is set for a designed deck (the title marker's `style:`); the slides with a `design` are drawn from it. */
export interface Deck { version: 2 | 0; theme: DeckTheme; accent: string | null; style: DeckStyle | null; slides: DeckSlide[] }

/** A deck's slides read for the studio design. `version` is 2 when the title slide's marker says `design: v2`. */
export function parseDeck(content: string): Deck {
  const sections = splitDocument(content, "slides").sections.map((section) => splitDesign(section).text);
  const first = sections[0] ? layoutOf(sections[0]) : null;
  const slides = parseSlides(content).map((slide, at) => {
    const attrs = attrsOf(sections[at] ?? "");
    return { layout: slide.layout, attrs, body: slide.body, notes: slide.notes, design: slide.design, data: parseDeckSlide(slide.layout, slide.body, attrs) };
  });
  return { version: first?.designVersion === 2 ? 2 : 0, theme: first?.deckTheme ?? "paper", accent: first?.accent ?? null, style: first?.style ?? null, slides };
}

// --- budgets ---------------------------------------------------------------------------------

const within = (value: number, [min, max]: Range, factor = 1) => value >= min && value <= Math.floor(max * factor);

/** What is over (or under) the budget of STUDIO_FORMAT, as short messages. Read decks may use 1.5x the body budgets. */
export function budgetIssues(data: DeckData, read = false): string[] {
  const issues: string[] = [];
  const f = read ? READ_FACTOR : 1;
  const check = (what: string, raw: string | null | undefined, range: Range, factor = f) => {
    const units = countUnits(raw ?? "");
    if (raw === null || raw === undefined) { if (range[0] > 0) issues.push(`${what} is missing`); return; }
    if (!within(units, range, factor)) issues.push(`${what} has ${units} units (allowed ${range[0]}-${Math.floor(range[1] * factor)})`);
  };
  const count = (what: string, n: number, range: Range) => { if (n < range[0] || n > range[1]) issues.push(`${what}: ${n} (allowed ${range[0]}-${range[1]})`); };
  if (data.layout === "title" || data.layout === "section") check("title", data.title, BUDGETS[data.layout].title, 1);
  else if (data.layout !== "text") check("title", data.title, BUDGETS.all.title, 1);
  switch (data.layout) {
    case "title": check("subtitle", data.subtitle, BUDGETS.title.subtitle, 1); check("meta", data.meta, BUDGETS.title.meta, 1); break;
    case "section": check("subtitle", data.subtitle, BUDGETS.section.subtitle, 1); break;
    case "summary": count("items", data.items.length, BUDGETS.summary.items); data.items.forEach((item) => { check("lead", item.lead ?? "", BUDGETS.summary.lead); check("detail", item.detail, BUDGETS.summary.detail); }); break;
    case "stat": check("value", data.value, BUDGETS.stat.value, 1); check("label", data.label, BUDGETS.stat.label); check("context", data.context ?? "", BUDGETS.stat.context); break;
    case "stats": count("items", data.items.length, BUDGETS.stats.items); data.items.forEach((item) => { check("value", item.value, BUDGETS.stats.value, 1); check("label", item.label, BUDGETS.stats.label); }); check("closing", data.after ?? "", BUDGETS.stats.closing); break;
    case "chart": count("rows", data.categories.length, BUDGETS.chart.rows); count("series", data.series.length, BUDGETS.chart.series); check("closing", data.after ?? "", BUDGETS.chart.closing); break;
    case "table": count("columns", data.head.length, BUDGETS.table.cols); count("rows", data.rows.length, BUDGETS.table.rows); data.rows.forEach((row) => row.cells.forEach((cell) => check("cell", cell, BUDGETS.table.cell))); check("closing", data.after ?? "", BUDGETS.table.closing); break;
    case "compare": data.sides.forEach((side) => { check("side name", side.name, BUDGETS.compare.side, 1); count("items", side.items.length, BUDGETS.compare.items); side.items.forEach((item) => check("item", item, BUDGETS.compare.item)); }); check("verdict", data.after ?? "", BUDGETS.compare.verdict); break;
    case "process": count("steps", data.items.length, BUDGETS.process.items); data.items.forEach((item) => { check("step", item.lead ?? "", BUDGETS.process.step, 1); check("detail", item.detail, BUDGETS.process.detail); }); break;
    case "timeline": count("events", data.items.length, BUDGETS.timeline.items); data.items.forEach((item) => { check("when", item.when, BUDGETS.timeline.when, 1); check("event", item.event, BUDGETS.timeline.event); }); break;
    case "quote": check("quote", data.quote, BUDGETS.quote.quote); check("attribution", data.attribution ?? "", BUDGETS.quote.attribution); break;
    case "figure": check("takeaway", data.takeaway ?? "", BUDGETS.figure.takeaway); break;
    case "statement": check("statement", data.statement, BUDGETS.statement.statement); count("items", data.items.length, BUDGETS.statement.items); data.items.forEach((item) => check("item", item, BUDGETS.statement.item)); break;
    case "points": count("items", data.items.length, BUDGETS.points.items); data.items.forEach((item) => { check("lead", item.lead ?? "", BUDGETS.points.lead); check("detail", item.detail, BUDGETS.points.detail); }); break;
    case "actions": count("items", data.items.length, BUDGETS.actions.items); data.items.forEach((item) => { check("lead", item.lead ?? "", BUDGETS.actions.lead, 1); check("detail", item.detail, BUDGETS.actions.detail); }); break;
    case "text": break;
  }
  return issues;
}
