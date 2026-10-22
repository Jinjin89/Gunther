/**
 * A studio report as a Word file (docx): title, the summary box as a shaded table cell, claim
 * headings, paragraphs with superscript citation numbers, exhibits as real tables (a chart is its
 * table plus its caption: Word charts are out of scope), callouts as shaded boxes, and a Sources
 * list at the end.
 */
import { AlignmentType, BorderStyle, Document, HeadingLevel, Packer, Paragraph, ShadingType, Table, TableCell, TableRow, TextRun, WidthType, type ParagraphChild } from "docx";
import { joinNumbers, tokens, type Token } from "./inlineTokens";
import { scan } from "./parseDeck";
import { parseReport, readBlocks, type ExhibitTable, type ReportBlock } from "./parseReport";
import { visible } from "./units";

export interface DocxOptions {
  /** What each citation names, in citation-number order ("Harlow 2024 · p. 3"). */
  sources?: string[];
  title?: string;
}

const FONT = "Georgia";
const SANS = "Arial";
const ACCENT = "2668B6";
const SOFT = "F3F4F6";
const WIDTH = 9026; // A4 with 1" margins, in twentieths of a point

/** An options object without its undefined members (the docx types do not take them). */
const clean = <T extends object>(options: T): never => Object.fromEntries(Object.entries(options).filter(([, value]) => value !== undefined)) as never;

interface Base { size?: number | undefined; bold?: boolean | undefined; italics?: boolean | undefined; color?: string | undefined; font?: string | undefined }

function runs(raw: string, base: Base = {}): ParagraphChild[] {
  const out: ParagraphChild[] = [];
  const walk = (parts: Token[], inherit: { bold?: boolean; italics?: boolean }) => {
    for (const token of parts) {
      const style = { font: base.font ?? FONT, size: base.size ?? 22, color: base.color, ...inherit, bold: inherit.bold ?? base.bold, italics: inherit.italics ?? base.italics };
      if (token.t === "text") out.push(new TextRun(clean({ ...style, text: joinNumbers(token.v) })));
      else if (token.t === "bold") walk(tokens(token.v), { ...inherit, bold: true });
      else if (token.t === "em") walk(tokens(token.v), { ...inherit, italics: true });
      else if (token.t === "code") out.push(new TextRun(clean({ ...style, text: token.v, font: "Courier New" })));
      else out.push(new TextRun(clean({ ...style, text: token.unverified ? "?" : token.n.join(","), superScript: true, color: ACCENT, bold: true })));
    }
  };
  walk(tokens(raw), {});
  return out;
}

const para = (raw: string, extra: Record<string, unknown> = {}, base: Base = {}) =>
  new Paragraph({ children: runs(raw, base), spacing: { after: 140, line: 330 }, ...extra });

const none = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
const line = (color: string, size = 4) => ({ style: BorderStyle.SINGLE, size, color });

function box(children: Paragraph[], edge: "top" | "left"): Table {
  return new Table({
    width: { size: WIDTH, type: WidthType.DXA }, columnWidths: [WIDTH],
    rows: [new TableRow({ children: [new TableCell({
      children, width: { size: WIDTH, type: WidthType.DXA }, shading: { type: ShadingType.CLEAR, fill: SOFT, color: "auto" },
      margins: { top: 140, bottom: 80, left: 220, right: 220 },
      borders: { top: edge === "top" ? line(ACCENT, 24) : none, left: edge === "left" ? line(ACCENT, 24) : none, bottom: none, right: none },
    })] })],
  });
}

function markdown(text: string): Array<Paragraph | Table> {
  const out: Array<Paragraph | Table> = [];
  for (const block of scan(text)) {
    if (block.t === "line") out.push(para(block.text));
    else if (block.t === "h3") out.push(new Paragraph({ heading: HeadingLevel.HEADING_3, children: runs(block.text, { font: SANS, size: 24, bold: true, color: "111317" }), spacing: { before: 240, after: 100 } }));
    else if (block.t === "items") block.items.forEach((item, at) => out.push(new Paragraph(block.ordered ? { children: [...runs(`${at + 1}. `), ...runs(item)], indent: { left: 360, hanging: 360 }, spacing: { after: 80, line: 330 } } : { children: runs(item), bullet: { level: 0 }, spacing: { after: 80, line: 330 } })));
    else if (block.t === "quote") out.push(para(block.text, { indent: { left: 360 } }, { italics: true }));
    else if (block.t === "table") out.push(rawTable(block.rows));
    // An image from the sources is not carried into Word: its caption (alt text) stands in for it.
    else if (block.t === "image") out.push(para(block.alt, {}, { italics: true }));
  }
  return out;
}

function cell(raw: string, width: number, opts: { head?: boolean; hi?: boolean; num?: boolean } = {}): TableCell {
  return new TableCell(clean({
    width: { size: width, type: WidthType.DXA },
    shading: opts.hi ? { type: ShadingType.CLEAR, fill: SOFT, color: "auto" } : undefined,
    margins: { top: 60, bottom: 60, left: 0, right: 100 },
    borders: { top: none, left: none, right: none, bottom: opts.head ? line("111317", 12) : line(opts.hi ? "111317" : "DCDFE3", opts.hi ? 8 : 4) },
    children: [new Paragraph({ alignment: opts.num ? AlignmentType.RIGHT : AlignmentType.LEFT, spacing: { after: 0 }, children: runs(raw, { font: SANS, size: 19, bold: opts.head || opts.hi, color: opts.head ? "545A63" : undefined }) })],
  }));
}

function grid(head: string[], rows: Array<{ cells: string[]; hi?: boolean }>, numeric: boolean[]): Table {
  const cols = head.length;
  const first = cols > 1 ? Math.round(WIDTH * 0.34) : WIDTH;
  const rest = cols > 1 ? Math.floor((WIDTH - first) / (cols - 1)) : 0;
  const widths = head.map((_, at) => at === 0 ? first : rest);
  return new Table({
    width: { size: WIDTH, type: WidthType.DXA }, columnWidths: widths,
    rows: [new TableRow({ tableHeader: true, children: head.map((h, at) => cell(h, widths[at]!, { head: true, num: numeric[at] ?? false })) }),
      ...rows.map((row) => new TableRow({ cantSplit: true, children: row.cells.map((c, at) => cell(c, widths[at]!, { hi: row.hi ?? false, num: numeric[at] ?? false })) }))],
  });
}

function rawTable(rows: string[][]): Table {
  const [head = [], ...body] = rows;
  return grid(head, body.map((cells) => ({ cells: head.map((_, at) => cells[at] ?? "") })), head.map(() => false));
}

function exhibitTable(table: ExhibitTable): Table {
  if (table.layout === "chart") {
    return grid(["", ...table.series.map((s) => s.name)], table.categories.map((c, row) => ({ cells: [c.label, ...table.series.map((s) => s.values[row]!.label)], hi: c.highlight })), ["", ...table.series].map((_, at) => at > 0));
  }
  return grid(table.head, table.rows.map((row) => ({ cells: row.cells, hi: row.highlight })), table.align.map((a) => a === "right"));
}

function blocks(list: ReportBlock[]): Array<Paragraph | Table> {
  const out: Array<Paragraph | Table> = [];
  for (const block of list) {
    if (block.t === "md") out.push(...markdown(block.text));
    else if (block.t === "callout") {
      out.push(box([new Paragraph({ spacing: { after: 60 }, children: runs(visible(block.title).toUpperCase(), { font: SANS, size: 18, bold: true }) }), ...markdown(block.text).filter((p): p is Paragraph => p instanceof Paragraph)], "left"), new Paragraph({ spacing: { after: 120 }, children: [] }));
    } else {
      out.push(new Paragraph({ keepNext: true, spacing: { before: 200, after: 100 }, children: runs(block.caption, { font: SANS, size: 20, bold: true }) }));
      if (block.table) out.push(exhibitTable(block.table));
      else if (block.raw) out.push(...markdown(block.raw));
      if (block.source) out.push(new Paragraph({ spacing: { before: 80, after: 200 }, children: runs(`Source: ${block.source}`, { font: SANS, size: 16, color: "545A63" }) }));
    }
  }
  return out;
}

export function buildDocx(content: string, options: DocxOptions = {}): Document {
  const doc = parseReport(content);
  const body: Array<Paragraph | Table> = [];
  const title = doc.title ?? options.title ?? "Report";
  body.push(new Paragraph({ heading: HeadingLevel.TITLE, spacing: { after: 120 }, children: runs(title, { font: SANS, size: 52, bold: true, color: "111317" }) }));
  if (doc.subtitle) body.push(new Paragraph({ spacing: { after: 320 }, children: runs(doc.subtitle, { size: 26, italics: true, color: "545A63" }) }));
  for (const section of doc.sections) {
    if (section.summary) {
      body.push(new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 200, after: 120 }, keepNext: true, children: runs(section.heading, { font: SANS, size: 32, bold: true, color: "111317" }) }));
      body.push(box(blocks(section.blocks).filter((p): p is Paragraph => p instanceof Paragraph), "top"), new Paragraph({ spacing: { after: 160 }, children: [] }));
    } else {
      body.push(new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 360, after: 120 }, keepNext: true, children: runs(section.heading, { font: SANS, size: 32, bold: true, color: "111317" }) }));
      body.push(...blocks(section.blocks));
    }
  }
  const sources = options.sources ?? [];
  if (sources.length) {
    body.push(new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 480, after: 120 }, keepNext: true, children: runs("Sources", { font: SANS, size: 32, bold: true, color: "111317" }) }));
    sources.forEach((source, at) => body.push(new Paragraph({ spacing: { after: 80 }, indent: { left: 480, hanging: 480 }, children: [new TextRun({ text: `${at + 1}.\t`, font: SANS, size: 19, bold: true, color: ACCENT }), new TextRun({ text: source, font: SANS, size: 19 })] })));
  }
  return new Document({
    creator: "Gunther", title,
    styles: { default: { document: { run: { font: FONT, size: 22 } } } },
    sections: [{ properties: { page: { margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 } } }, children: body }],
  });
}

/** The report as a Blob of a .docx file (in the app). */
export async function docxBlob(content: string, options: DocxOptions = {}): Promise<Blob> {
  return Packer.toBlob(buildDocx(content, options));
}

/** The report as the bytes of a .docx file (in Node, for tests and scripts). */
export async function docxBytes(content: string, options: DocxOptions = {}): Promise<Uint8Array> {
  return new Uint8Array(await Packer.toBuffer(buildDocx(content, options)));
}

export { readBlocks };
