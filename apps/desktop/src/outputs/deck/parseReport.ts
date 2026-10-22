/**
 * A studio report (design v2, docs/STUDIO_FORMAT.md section 2) read into blocks: ordinary
 * Markdown, the executive summary box, numbered exhibits (a chart or a table with a source line)
 * and callouts. Old reports have none of these and are not read here.
 */
import { splitDocument } from "../sections";
import { parseDeckSlide, type ChartKind, type DeckData } from "./parseDeck";

export type ExhibitTable = Extract<DeckData, { layout: "chart" | "table" }>;

export type ReportBlock =
  | { t: "md"; text: string }
  | { t: "exhibit"; caption: string; source: string | null; table: ExhibitTable | null; raw: string }
  | { t: "callout"; title: string; text: string };

export interface ReportSection { heading: string; summary: boolean; blocks: ReportBlock[]; text: string }
export interface ReportDoc { title: string | null; subtitle: string | null; sections: ReportSection[] }

const SUMMARY_MARK = /^\s*<!--\s*block:\s*summary\s*-->\s*$/;

/** The blocks of a stretch of Markdown that holds `:::exhibit` and `:::callout` fences. */
export function readBlocks(text: string): ReportBlock[] {
  const blocks: ReportBlock[] = [];
  let plain: string[] = [];
  const flush = () => { const joined = plain.join("\n").trim(); if (joined) blocks.push({ t: "md", text: joined }); plain = []; };
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  for (let at = 0; at < lines.length; at += 1) {
    const line = lines[at] ?? "";
    const open = /^:::(exhibit|callout)\b(.*)$/.exec(line.trim());
    if (!open) { plain.push(line); continue; }
    let end = lines.findIndex((candidate, index) => index > at && candidate.trim() === ":::");
    const closed = end >= 0;
    if (!closed) end = lines.length;
    flush();
    const inside = lines.slice(at + 1, end);
    if (open[1] === "callout") blocks.push({ t: "callout", title: (open[2] ?? "").trim(), text: inside.join("\n").trim() });
    else blocks.push(exhibit(open[2] ?? "", inside));
    at = closed ? end : lines.length;
  }
  flush();
  return blocks;
}

function exhibit(options: string, inside: string[]): ReportBlock {
  const kind = /\bchart\s*=\s*(bar|column|line)\b/.exec(options)?.[1] as ChartKind | undefined;
  const rows = inside.filter((line) => line.trim().startsWith("|"));
  const rest = inside.filter((line) => !line.trim().startsWith("|") && line.trim());
  let sourceAt = -1;
  rest.forEach((line, index) => { if (/^Source:/i.test(line.trim())) sourceAt = index; });
  const source = sourceAt >= 0 ? rest[sourceAt]!.trim().replace(/^Source:\s*/i, "") : null;
  const caption = rest.find((_, index) => index !== sourceAt) ?? "";
  const body = rows.join("\n");
  const parsed = body ? parseDeckSlide(kind ? "chart" : "table", `## x\n${body}`, kind ? { chart: kind } : {}) : null;
  const table = parsed && (parsed.layout === "chart" || parsed.layout === "table") ? parsed : null;
  return { t: "exhibit", caption: caption.trim(), source, table, raw: inside.join("\n") };
}

export function parseReport(content: string): ReportDoc {
  const { preamble, sections } = splitDocument(content, "report");
  const lines = preamble.split("\n").map((line) => line.trim()).filter((line) => line && !/^<!--\s*report:\s*v2\s*-->$/.test(line));
  const title = lines.find((line) => line.startsWith("# "))?.slice(2).trim() ?? null;
  const subtitle = lines.find((line) => !line.startsWith("#"))?.replace(/^[*_](.*)[*_]$/, "$1").trim() ?? null;
  return {
    title, subtitle,
    sections: sections.map((text) => {
      const [first = "", ...rest] = text.split("\n");
      const summary = SUMMARY_MARK.test(rest.find((line) => line.trim()) ?? "");
      const body = (summary ? rest.slice(rest.findIndex((line) => line.trim()) + 1) : rest).join("\n");
      return { heading: first.replace(/^##\s*/, "").trim(), summary, blocks: readBlocks(body), text };
    }),
  };
}
