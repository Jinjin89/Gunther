/**
 * A report's original Markdown, split into its preamble, headed sections and optional
 * Studio blocks (docs/STUDIO_FORMAT.md section 2).
 */
import { markdownLines, splitDocument } from "../sections";
import { parseDeckSlide, type ChartKind, type DeckData } from "./parseDeck";

export type ExhibitTable = Extract<DeckData, { layout: "chart" | "table" }>;
type ExhibitGrid = Extract<ExhibitTable, { layout: "table" }>;

export type ReportBlock =
  | { t: "md"; text: string }
  | { t: "exhibit"; caption: string; source: string | null; table: ExhibitTable | null; grid: ExhibitGrid | null; raw: string }
  | { t: "callout"; title: string; text: string };

export interface ReportSection { heading: string; summary: boolean; blocks: ReportBlock[]; text: string }
export interface ReportDoc { title: string | null; subtitle: string | null; preamble: ReportBlock[]; sections: ReportSection[] }

const REPORT_MARK = /^\s*<!--\s*report:\s*v2\s*-->[ \t]*(?:\n|$)/;
const SUMMARY_MARK = /^\s*<!--\s*block:\s*summary\s*-->\s*$/;

/** The blocks of a stretch of Markdown that holds `:::exhibit` and `:::callout` fences. */
export function readBlocks(text: string): ReportBlock[] {
  if (!text.trim()) return [];
  if (!text.includes(":::")) return [{ t: "md", text }];
  const blocks: ReportBlock[] = [];
  let plain: string[] = [];
  const flush = () => {
    const joined = plain.join("\n");
    if (joined.trim()) blocks.push({ t: "md", text: joined });
    plain = [];
  };
  const lines = markdownLines(text);
  for (let at = 0; at < lines.length; at += 1) {
    const { line, fenced } = lines[at]!;
    const open = !fenced && /^:::(exhibit|callout)(?=\s|$)(.*)$/.exec(line.trim());
    if (!open) { plain.push(line); continue; }
    let end = at + 1;
    while (end < lines.length && (lines[end]!.fenced || lines[end]!.line.trim() !== ":::")) end += 1;
    flush();
    const inside = lines.slice(at + 1, end).map(({ line }) => line);
    if (open[1] === "callout") blocks.push({ t: "callout", title: (open[2] ?? "").trim(), text: inside.join("\n").trim() });
    else blocks.push(exhibit(open[2] ?? "", inside));
    at = end;
  }
  flush();
  return blocks;
}

function exhibit(options: string, inside: string[]): ReportBlock {
  const kind = /\bchart\s*=\s*(bar|column|line)\b/.exec(options)?.[1] as ChartKind | undefined;
  const lines = inside.map((line) => line.trim()).filter(Boolean);
  const source = /^Source:\s*(.*)$/i.exec(lines.at(-1) ?? "")?.[1] ?? null;
  if (source !== null) lines.pop();
  const caption = lines[0]?.startsWith("|") ? "" : lines.shift() ?? "";
  const body = lines.length && lines.every((line) => line.startsWith("|")) ? `## Exhibit\n${lines.join("\n")}` : "";
  const parsed = body ? parseDeckSlide(kind ? "chart" : "table", body, kind ? { chart: kind } : {}) : null;
  const table = parsed && (parsed.layout === "chart" || parsed.layout === "table") ? parsed : null;
  // Chart data formats values and series labels. Keep the original table cells for reading.
  const original = table?.layout === "chart" ? parseDeckSlide("table", body) : table;
  const grid = original?.layout === "table" ? original : null;
  return { t: "exhibit", caption, source, table, grid, raw: inside.join("\n") };
}

export function parseReport(content: string): ReportDoc {
  const { preamble, sections } = splitDocument(content, "report");
  const lines = markdownLines(preamble.replace(REPORT_MARK, ""));
  const titleAt = lines.findIndex(({ line, fenced }) => !fenced && line.startsWith("# "));
  const title = lines[titleAt]?.line.slice(2).trim() ?? null;
  const subtitle = lines.find(({ line, fenced }) => !fenced && line.trim() && !line.startsWith("#"))?.line.trim()
    .replace(/^[*_](.*)[*_]$/, "$1").trim() ?? null;
  return {
    title, subtitle, preamble: readBlocks(lines.filter((_, index) => index !== titleAt).map(({ line }) => line).join("\n")),
    sections: sections.map((text) => {
      const [first = "", ...rest] = text.split("\n");
      const firstBodyLine = rest.findIndex((line) => line.trim());
      const summary = SUMMARY_MARK.test(rest[firstBodyLine] ?? "");
      const body = (summary ? rest.slice(firstBodyLine + 1) : rest).join("\n");
      return { heading: first.replace(/^##\s*/, "").trim(), summary, blocks: readBlocks(body), text };
    }),
  };
}
