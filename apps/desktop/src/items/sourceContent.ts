import type { InboxItem, SourceKind } from "@gunther/contracts";
import { markdownToPlainText } from "../components/markdown/markdownEditing";

/**
 * Read the plain-text envelopes the capture pipeline stores for each kind of
 * source (recordings, web snapshots, uploaded files, web research) back into
 * structured fields, so each kind can be presented on its own terms. Every
 * parser degrades to "just text" when the envelope is not recognised.
 */

export type SourceView = "recording" | "web" | "research" | "document" | "image" | "table" | "text";

export interface TranscriptSegment {
  startSeconds: number | null;
  text: string;
}

export interface RecordingContent {
  title: string | null;
  durationSeconds: number | null;
  capturedLabel: string | null;
  recordingId: string | null;
  moments: Array<{ seconds: number; label: string }>;
  summary: string | null;
  keyPoints: string[];
  actions: string[];
  openQuestions: string[];
  terms: string[];
  transcript: TranscriptSegment[];
  transcriptText: string;
}

export interface WebContent {
  originalUrl: string | null;
  finalUrl: string | null;
  capturedAt: string | null;
  httpStatus: string | null;
  contentType: string | null;
  sha256: string | null;
  context: string | null;
  body: string;
}

export interface FileContent {
  fileName: string | null;
  mediaType: string | null;
  sizeBytes: number | null;
  sha256: string | null;
  context: string | null;
  ocrStatus: string | null;
  ocrProvider: string | null;
  processingNote: string | null;
  extracted: string;
}

export interface ResearchContent {
  query: string;
  answer: string;
  references: Array<{ title: string; url: string | null; snippet: string | null }>;
}

const RECORDING_META = /^Duration:\s*([\d:]+)\s*·\s*Captured:\s*(.+?)(?:\s*·\s*Local recording:\s*(rec_[a-f0-9]{24}))?\s*$/m;

/** "01:02:03" or "12:34" → seconds. */
export function clockToSeconds(value: string): number | null {
  const parts = value.trim().split(":").map((part) => Number.parseInt(part, 10));
  if (!parts.length || parts.some((part) => !Number.isFinite(part))) return null;
  return parts.reduce((total, part) => total * 60 + part, 0);
}

/** Seconds → "12:34" or "1:02:03". */
export function formatClock(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const rest = safe % 60;
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}` : `${minutes}:${String(rest).padStart(2, "0")}`;
}

/** Seconds → "42 min", "1 h 5 min", "38 sec". */
export function formatDuration(seconds: number): string {
  const safe = Math.max(0, Math.round(seconds));
  if (safe < 60) return `${safe} sec`;
  const hours = Math.floor(safe / 3600);
  const minutes = Math.round((safe % 3600) / 60);
  if (!hours) return `${minutes} min`;
  return minutes ? `${hours} h ${minutes} min` : `${hours} h`;
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1_048_576) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  if (bytes < 1_073_741_824) return `${(bytes / 1_048_576).toFixed(bytes < 10_485_760 ? 1 : 0)} MB`;
  return `${(bytes / 1_073_741_824).toFixed(1)} GB`;
}

const MEDIA_LABELS: Array<[RegExp, string]> = [
  [/pdf/, "PDF"],
  [/wordprocessingml|msword/, "Word document"],
  [/presentationml|powerpoint/, "Slides"],
  [/spreadsheetml|ms-excel/, "Spreadsheet"],
  [/markdown/, "Markdown"],
  [/csv/, "CSV"],
  [/tab-separated/, "TSV"],
  [/json/, "JSON"],
  [/html/, "Web page"],
  [/^text\//, "Plain text"],
  [/png/, "PNG image"],
  [/jpe?g/, "JPEG image"],
  [/heic|heif/, "HEIC image"],
  [/webp/, "WebP image"],
  [/gif/, "GIF image"],
  [/^image\//, "Image"],
  [/^audio\//, "Audio"],
];

/** A file name's extension ("pdf"), or "" when it has none. */
export function extensionOf(fileName: string | null | undefined): string {
  const match = fileName?.match(/\.([a-z0-9]{1,5})$/i);
  return match ? match[1]!.toLowerCase() : "";
}

export function mediaTypeLabel(mediaType: string | null | undefined, fileName?: string | null): string {
  const type = (mediaType ?? "").toLowerCase();
  const extension = extensionOf(fileName);
  if (extension === "md" || extension === "markdown") return "Markdown";
  for (const [pattern, label] of MEDIA_LABELS) if (pattern.test(type)) return label;
  return extension ? extension.toUpperCase() : "File";
}

/** Text-like originals Gunther can show verbatim. */
export function readableOriginal(mediaType: string | null | undefined, fileName?: string | null): "markdown" | "delimited" | "json" | "text" | null {
  const type = (mediaType ?? "").toLowerCase();
  const extension = extensionOf(fileName);
  if (type.includes("markdown") || extension === "md" || extension === "markdown") return "markdown";
  if (type.includes("csv") || type.includes("tab-separated") || extension === "csv" || extension === "tsv") return "delimited";
  if (type.includes("json") || extension === "json") return "json";
  if (type.startsWith("text/plain") || extension === "txt") return "text";
  return null;
}

interface Section {
  heading: string;
  body: string;
}

/** Split a Markdown envelope on `##` headings (outside code fences). */
function splitSections(markdown: string): { preamble: string; title: string | null; sections: Section[] } {
  const lines = markdown.split("\n");
  let title: string | null = null;
  const preamble: string[] = [];
  const sections: Section[] = [];
  let current: { heading: string; lines: string[] } | null = null;
  let fence = false;
  for (const line of lines) {
    if (/^\s*(```|~~~)/.test(line)) fence = !fence;
    const h1: RegExpMatchArray | null = !fence && !current && title === null ? line.match(/^#\s+(.+?)\s*$/) : null;
    if (h1) {
      title = h1[1]!;
      continue;
    }
    const h2 = fence ? null : line.match(/^##\s+(.+?)\s*$/);
    if (h2) {
      if (current) sections.push({ heading: current.heading, body: current.lines.join("\n").trim() });
      current = { heading: h2[1]!, lines: [] };
      continue;
    }
    (current ? current.lines : preamble).push(line);
  }
  if (current) sections.push({ heading: current.heading, body: current.lines.join("\n").trim() });
  return { preamble: preamble.join("\n").trim(), title, sections };
}

/** Everything after the first `## heading` from `terminal`, verbatim (it may contain its own headings). */
function tailAfter(markdown: string, terminal: string[]): { head: string; tail: string | null } {
  const pattern = new RegExp(`^##\\s+(${terminal.map((item) => item.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})\\s*$`, "m");
  const match = pattern.exec(markdown);
  if (!match) return { head: markdown, tail: null };
  return { head: markdown.slice(0, match.index), tail: markdown.slice(match.index + match[0].length).replace(/^\n+/, "").trimEnd() };
}

const metadataValue = (block: string, label: string) => {
  const match = new RegExp(`^${label}:\\s*(.+?)\\s*$`, "m").exec(block);
  return match ? match[1]! : null;
};

const listItems = (body: string | undefined) => (body ?? "")
  .split("\n")
  .map((line) => line.match(/^\s*(?:[-*+]|\d+[.)])\s+(.+)$/)?.[1]?.trim())
  .filter((item): item is string => Boolean(item));

export function isRecordingContent(content: string): boolean {
  return RECORDING_META.test(content.slice(0, 2_000));
}

export function parseTranscript(text: string): TranscriptSegment[] {
  const segments: TranscriptSegment[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    if (paragraph.length) segments.push({ startSeconds: null, text: paragraph.join(" ").trim() });
    paragraph = [];
  };
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) {
      flush();
      continue;
    }
    const stamp = line.match(/^\[?((?:\d+:)?\d{1,2}:\d{2})(?:\.\d+)?\]?\s+(.*)$/);
    if (stamp) {
      flush();
      segments.push({ startSeconds: clockToSeconds(stamp[1]!), text: stamp[2]!.trim() });
    } else {
      paragraph.push(line);
    }
  }
  flush();
  return segments.filter((segment) => segment.text);
}

export function parseRecordingContent(content: string): RecordingContent {
  const { head, tail } = tailAfter(content, ["Full transcript", "Transcript"]);
  const { title, preamble, sections } = splitSections(head);
  const meta = RECORDING_META.exec(preamble) ?? RECORDING_META.exec(head);
  const section = (name: string) => sections.find((item) => item.heading.toLowerCase() === name.toLowerCase())?.body;
  const terms: string[] = [];
  const stripTerms = (body: string | undefined) => (body ?? "").split("\n").filter((line) => {
    const match = line.match(/^Terms:\s*(.+)$/);
    if (match) terms.push(...match[1]!.split("·").map((term) => term.trim()).filter(Boolean));
    return !match;
  }).join("\n").trim();
  const summary = stripTerms(section("Summary"));
  const keyPoints = listItems(stripTerms(section("Key points")));
  const actions = listItems(stripTerms(section("Actions")));
  const openQuestions = listItems(stripTerms(section("Open questions")));
  const moments = listItems(section("Marked moments")).map((item) => {
    const match = item.match(/^((?:\d+:)?\d{1,2}:\d{2})\s*·\s*(.*)$/);
    const seconds = match ? clockToSeconds(match[1]!) : null;
    return seconds === null ? null : { seconds, label: match![2]!.trim() || "Marked moment" };
  }).filter((moment): moment is { seconds: number; label: string } => moment !== null);
  const transcriptText = (tail ?? "").trim();
  return {
    title,
    durationSeconds: meta ? clockToSeconds(meta[1]!) : null,
    capturedLabel: meta?.[2]?.trim() ?? null,
    recordingId: meta?.[3] ?? null,
    moments,
    summary: summary || null,
    keyPoints,
    actions,
    openQuestions,
    terms,
    transcript: parseTranscript(transcriptText),
    transcriptText,
  };
}

export function isWebSnapshotContent(content: string): boolean {
  return content.startsWith("# Web snapshot\n");
}

export function parseWebContent(content: string): WebContent {
  const { head, tail } = tailAfter(content, ["Captured content"]);
  const { preamble, sections } = splitSections(head);
  return {
    originalUrl: metadataValue(preamble, "Original URL"),
    finalUrl: metadataValue(preamble, "Final URL"),
    capturedAt: metadataValue(preamble, "Captured at"),
    httpStatus: metadataValue(preamble, "HTTP status"),
    contentType: metadataValue(preamble, "Content type"),
    sha256: metadataValue(preamble, "SHA-256"),
    context: sections.find((item) => item.heading === "Your context")?.body || null,
    body: tail ?? "",
  };
}

export function isFileContent(content: string): boolean {
  return content.startsWith("# Original file\n");
}

export function parseFileContent(content: string): FileContent {
  const { head, tail } = tailAfter(content, ["Extracted content"]);
  const { preamble, sections } = splitSections(head);
  const section = (name: string) => sections.find((item) => item.heading === name)?.body ?? null;
  const ocr = section("OCR provenance") ?? "";
  const size = metadataValue(preamble, "Size")?.match(/^(\d+)/)?.[1];
  return {
    fileName: metadataValue(preamble, "File"),
    mediaType: metadataValue(preamble, "Media type"),
    sizeBytes: size ? Number.parseInt(size, 10) : null,
    sha256: metadataValue(preamble, "SHA-256"),
    context: section("Your context") || null,
    ocrStatus: metadataValue(ocr, "OCR status"),
    ocrProvider: metadataValue(ocr, "OCR provider"),
    processingNote: section("Processing note") || null,
    extracted: tail ?? "",
  };
}

export function isResearchContent(content: string): boolean {
  return /^Search query:/.test(content) && content.includes("Answer captured from web research:");
}

export function parseResearchContent(content: string): ResearchContent {
  const query = content.match(/^Search query:\s*(.*)$/m)?.[1]?.trim() ?? "";
  const afterAnswer = content.split("Answer captured from web research:")[1] ?? "";
  const [answerPart = "", referencePart = ""] = afterAnswer.split(/\n\s*Referenced pages:\s*\n/);
  const references = referencePart.split(/\n\s*\n/).map((block) => {
    const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
    const title = lines[0]?.replace(/^\d+\.\s*/, "") ?? "";
    const url = lines.find((line) => /^https?:\/\//i.test(line)) ?? null;
    const snippet = lines.filter((line, index) => index > 0 && line !== url).join(" ") || null;
    return title ? { title, url, snippet } : null;
  }).filter((reference): reference is ResearchContent["references"][number] => reference !== null);
  return { query, answer: answerPart.trim(), references };
}

/** Choose how a source is presented. Pasted text stays text, whatever its kind. */
export function sourceView(kind: SourceKind, content: string, mediaType?: string | null, hasFile = false): SourceView {
  if (kind === "recording" || kind === "course" || isRecordingContent(content)) return "recording";
  if (isWebSnapshotContent(content)) return "web";
  if (isResearchContent(content)) return "research";
  const uploaded = hasFile || isFileContent(content);
  if (uploaded && (kind === "image" || mediaType?.startsWith("image/"))) return "image";
  if (kind === "table") return "table";
  if (uploaded) return "document";
  return "text";
}

/** Drop a leading `# Title` when it repeats the source title shown above it. */
export function withoutRepeatedTitle(content: string, title: string): string {
  const match = content.match(/^#\s+(.+?)\s*\n/);
  if (!match) return content;
  const normalize = (value: string) => value.trim().toLowerCase().replace(/\s+/g, " ");
  return normalize(match[1]!) === normalize(title) ? content.slice(match[0].length).replace(/^\s+/, "") : content;
}

export function hostOf(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return null;
  }
}

export function pathOf(url: string | null | undefined): string {
  if (!url) return "";
  try {
    const parsed = new URL(url);
    return `${parsed.pathname === "/" ? "" : decodeURI(parsed.pathname)}${parsed.search}`;
  } catch {
    return "";
  }
}

export interface InboxPreview {
  /** A short factual line such as "PDF · 2.4 MB" or "example.com". */
  detail: string | null;
  /** Readable text for the two-line preview. */
  text: string;
}

const between = (value: string, start: string, ends: string[]) => {
  const at = value.indexOf(start);
  if (at < 0) return null;
  const rest = value.slice(at + start.length);
  const stop = Math.min(...ends.map((end) => {
    const index = rest.indexOf(end);
    return index < 0 ? rest.length : index;
  }));
  return rest.slice(0, stop).trim() || null;
};

/**
 * Inbox previews arrive whitespace-collapsed and truncated by the service.
 * Turn the storage envelope into something a person would write.
 */
export function inboxPreview(item: Pick<InboxItem, "preview" | "sourceKind" | "itemType">): InboxPreview {
  const preview = item.preview.trim();
  if (preview.startsWith("# Original file")) {
    const fileName = between(preview, "File:", [" Media type:"]);
    const mediaType = between(preview, "Media type:", [" Size:"]);
    const size = preview.match(/Size:\s*(\d+)\s*bytes/)?.[1];
    const context = between(preview, "## Your context", [" ## "]);
    const extracted = between(preview, "## Extracted content", []);
    return {
      detail: [mediaTypeLabel(mediaType, fileName), size ? formatBytes(Number(size)) : null, fileName].filter(Boolean).join(" · ") || null,
      text: markdownToPlainText(context ?? extracted ?? ""),
    };
  }
  if (preview.startsWith("# Web snapshot")) {
    const url = between(preview, "Final URL:", [" Captured at:"]) ?? between(preview, "Original URL:", [" Final URL:"]);
    const context = between(preview, "## Your context", [" ## "]);
    const body = between(preview, "## Captured content", []);
    return { detail: [hostOf(url), pathOf(url)].filter(Boolean).join("") || null, text: markdownToPlainText(context ?? body ?? "") };
  }
  const recording = preview.match(/Duration:\s*([\d:]+)\s*·\s*Captured:/);
  if (recording || item.sourceKind === "recording" || item.sourceKind === "course") {
    const seconds = recording ? clockToSeconds(recording[1]!) : null;
    const summary = between(preview, "## Summary", [" ## "]) ?? between(preview, "## Full transcript", []) ?? between(preview, "## Transcript", []);
    const fallback = preview.replace(/^#\s+[^#]*?(?=Duration:)/, "").replace(/Duration:.*?(rec_[a-f0-9]{24}|$)/, "").replace(/##\s+Marked moments/, "");
    return {
      detail: seconds ? formatDuration(seconds) : null,
      text: markdownToPlainText(summary ?? fallback).replace(/^\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*/, ""),
    };
  }
  const table = item.sourceKind === "table" ? preview.match(/^(\d+) rows? · Columns: (.+)$/) : null;
  if (table) {
    const columns = table[2]!.split(", ").length;
    return { detail: `${table[1]} ${table[1] === "1" ? "row" : "rows"} · ${columns} ${columns === 1 ? "column" : "columns"}`, text: table[2]! };
  }
  if (preview.startsWith("Search query:")) {
    const query = between(preview, "Search query:", [" Answer captured"]);
    const answer = between(preview, "Answer captured from web research:", [" Referenced pages:"]);
    return { detail: query ? `Web research · “${query}”` : "Web research", text: markdownToPlainText(answer ?? "") };
  }
  return { detail: null, text: collapsedToPlainText(preview) };
}

/** Previews arrive on one line, so line-anchored Markdown markers appear mid-text. */
function collapsedToPlainText(preview: string): string {
  return markdownToPlainText(preview
    .replace(/\s?\[\d{1,3}\]/g, "")
    .replace(/(^|\s)#{1,6}\s+/g, "$1")
    .replace(/(^|\s)(?:[-*+]|\d{1,9}[.)])\s+\[[ xX]\]\s+/g, "$1")
    .replace(/(^|\s)>\s+/g, "$1"));
}

export interface DelimitedTable {
  header: string[];
  rows: string[][];
  delimiter: "," | "\t" | ";" | "|";
  numericColumns: boolean[];
}

function splitDelimited(text: string, delimiter: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index]!;
    if (quoted) {
      if (char === "\"" && text[index + 1] === "\"") {
        field += "\"";
        index += 1;
      } else if (char === "\"") {
        quoted = false;
      } else {
        field += char;
      }
      continue;
    }
    if (char === "\"" && field === "") quoted = true;
    else if (char === delimiter) {
      row.push(field);
      field = "";
    } else if (char === "\n" || char === "\r") {
      if (char === "\r" && text[index + 1] === "\n") index += 1;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += char;
    }
  }
  if (field !== "" || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows.map((cells) => cells.map((cell) => cell.trim())).filter((cells) => cells.some(Boolean));
}

const NUMERIC = /^[-+(]?[$€£¥]?\s?\d[\d,_ ]*(\.\d+)?\)?\s?(%|[kKmMbB])?$/;

/** Parse pasted spreadsheet rows (TSV, CSV, semicolons or a pipe table). */
export function parseDelimitedTable(content: string): DelimitedTable | null {
  // Keep leading tabs: an empty first header cell is common in pasted spreadsheets.
  const text = content.replace(/^\uFEFF/, "").replace(/^(?:[ \t]*\r?\n)+/, "").trimEnd();
  if (!text) return null;
  const lines = text.split(/\r?\n/).filter((line) => line.trim());
  if (lines.length < 2) return null;
  let delimiter: DelimitedTable["delimiter"] | null = null;
  let rows: string[][] = [];
  if (lines.every((line) => line.trim().startsWith("|"))) {
    delimiter = "|";
    rows = lines
      .filter((line) => !/^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(line))
      .map((line) => line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim()));
  } else {
    for (const candidate of ["\t", ",", ";"] as const) {
      const parsed = splitDelimited(text, candidate);
      const widths = parsed.slice(0, 30).map((cells) => cells.length);
      const width = widths[0] ?? 0;
      if (width >= 2 && widths.filter((value) => value === width).length >= Math.ceil(widths.length * 0.8)) {
        delimiter = candidate;
        rows = parsed;
        break;
      }
    }
  }
  if (!delimiter || rows.length < 2) return null;
  const width = Math.max(...rows.map((cells) => cells.length));
  const normalized = rows.map((cells) => Array.from({ length: width }, (_, index) => cells[index] ?? ""));
  const [header, ...body] = normalized;
  const numericColumns = header!.map((_, column) => {
    const values = body.map((cells) => cells[column]!).filter(Boolean);
    return values.length > 0 && values.filter((value) => NUMERIC.test(value)).length / values.length >= 0.8;
  });
  return { header: header!, rows: body, delimiter, numericColumns };
}
