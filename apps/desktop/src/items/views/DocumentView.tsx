import type { ContentBlock, SourceDetail, SourceStructure } from "@gunther/contracts";
import { CircleAlert, Download, FileText, ImageIcon, LoaderCircle, RotateCcw, Sigma } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { knowledgeApi, sourceAssetUrl } from "../../api";
import { MarkdownView } from "../../components/markdown/MarkdownView";
import { extensionOf, formatBytes, mediaTypeLabel, parseDelimitedTable, readableOriginal, withoutRepeatedTitle, type DelimitedTable, type FileContent } from "../sourceContent";
import { PaperCard } from "./PaperCard";
import { ContextNote, PlainText } from "./ReaderViews";
import { DataTable } from "./TableView";

const STRUCTURE_PAGE = 500;
const ORIGINAL_TEXT_LIMIT = 2 * 1024 * 1024;
const WORKING = ["pending", "queued", "running"];

/** Structured blocks for a source, polling while the service is still reading it. */
export function useStructure(sourceId: string, enabled = true) {
  const [data, setData] = useState<SourceStructure | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [reload, setReload] = useState(0);
  const generation = useRef(0);
  useEffect(() => {
    if (!enabled) return undefined;
    const run = ++generation.current;
    let timer: number | undefined;
    setData(null);
    setError(null);
    const load = async () => {
      try {
        const result = await knowledgeApi.sourceStructure(sourceId, 0, null, null, STRUCTURE_PAGE);
        if (generation.current !== run) return;
        setData(result);
        setError(null);
        if (WORKING.includes(result.processing.state)) timer = window.setTimeout(() => void load(), 2_500);
      } catch (reason) {
        if (generation.current === run) setError(reason instanceof Error ? reason.message : "The document could not be read.");
      }
    };
    void load();
    return () => {
      generation.current += 1;
      window.clearTimeout(timer);
    };
  }, [enabled, reload, sourceId]);
  const loadMore = useCallback(async () => {
    if (!data || data.nextOffset === null) return;
    const run = generation.current;
    setLoadingMore(true);
    try {
      const next = await knowledgeApi.sourceStructure(sourceId, data.nextOffset, data.revisionId, null, STRUCTURE_PAGE);
      if (generation.current === run) setData({ ...next, blocks: [...data.blocks, ...next.blocks] });
    } catch (reason) {
      if (generation.current === run) setError(reason instanceof Error ? reason.message : "More of the document could not be read.");
    } finally {
      if (generation.current === run) setLoadingMore(false);
    }
  }, [data, sourceId]);
  const reprocess = useCallback(async () => {
    await knowledgeApi.reprocessSource(sourceId);
    window.dispatchEvent(new CustomEvent("gunther:sources-updated"));
    setReload((value) => value + 1);
  }, [sourceId]);
  return { data, error, loadingMore, loadMore, reprocess };
}

export type ReaderBlock =
  | { kind: "page"; page: number }
  | { kind: "heading"; depth: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "lead"; text: string }
  | { kind: "table"; table: DelimitedTable | null; text: string }
  | { kind: "figure"; text: string }
  | { kind: "equation"; text: string };

type TableCell = { text?: string; start_row_offset_idx?: number; start_col_offset_idx?: number };

function tableFromPayload(payload: Record<string, unknown> | undefined): DelimitedTable | null {
  const data = payload?.table as { num_rows?: number; num_cols?: number; table_cells?: TableCell[] } | undefined;
  if (!data?.table_cells?.length || !data.num_rows || !data.num_cols || data.num_rows < 2) return null;
  const grid = Array.from({ length: data.num_rows }, () => Array.from({ length: data.num_cols! }, () => ""));
  for (const cell of data.table_cells) {
    const row = cell.start_row_offset_idx ?? -1;
    const column = cell.start_col_offset_idx ?? -1;
    if (grid[row] && column >= 0 && column < data.num_cols) grid[row]![column] = String(cell.text ?? "").trim();
  }
  const [header, ...rows] = grid;
  return { header: header!, rows, delimiter: "|", numericColumns: header!.map((_, index) => rows.length > 0 && rows.every((cells) => !cells[index] || /^[-+]?[\d.,]+%?$/.test(cells[index]!))) };
}

/**
 * Rebuild a readable document from indexed blocks: pages become dividers,
 * sentence-sized pieces rejoin into paragraphs, and table lines become grids.
 * The user's own context is shown separately, so it is skipped here.
 */
export function readerBlocks(blocks: ContentBlock[]): ReaderBlock[] {
  const result: ReaderBlock[] = [];
  let page: number | undefined;
  let paragraph: { text: string; end: number | undefined; ref: unknown; headings: string } | null = null;
  let tableLines: string[] = [];
  let lastTableRef: unknown;
  const flushParagraph = () => {
    if (paragraph) result.push({ kind: "paragraph", text: paragraph.text });
    paragraph = null;
  };
  const flushTable = () => {
    if (tableLines.length) {
      const text = tableLines.join("\n");
      result.push({ kind: "table", table: parseDelimitedTable(text), text });
    }
    tableLines = [];
  };
  for (const block of blocks) {
    if (block.headings[0] === "Your context") continue;
    const blockPage = block.anchor.page;
    if (blockPage && blockPage !== page) {
      flushParagraph();
      flushTable();
      if (page !== undefined || blockPage > 1) result.push({ kind: "page", page: blockPage });
      page = blockPage;
    }
    const ref = (block.anchor as { documentRef?: unknown }).documentRef;
    if (block.kind === "heading") {
      flushParagraph();
      flushTable();
      result.push({ kind: "heading", depth: Math.max(1, block.headings.length), text: block.content });
    } else if (block.kind === "table") {
      flushParagraph();
      // Long tables are stored in slices; the first slice already carries the whole grid.
      if (ref !== undefined && ref === lastTableRef) continue;
      lastTableRef = ref;
      const structured = tableFromPayload(block.payload);
      if (structured || ref) {
        flushTable();
        result.push({ kind: "table", table: structured, text: block.content });
      } else {
        tableLines.push(block.content);
      }
    } else if (block.kind === "figure" || block.kind === "equation") {
      flushParagraph();
      flushTable();
      result.push({ kind: block.kind, text: block.content });
    } else {
      flushTable();
      const start = block.anchor.charStart;
      const headings = block.headings.join("/");
      // Extracted text cannot tell a wrapped line from a new one. A short line
      // without closing punctuation, followed by a capitalised line, reads as
      // a run-in heading (a PDF section title) rather than the start of a sentence.
      const previous: string = paragraph?.text ?? "";
      const endsLine = ref === undefined && previous.length > 0 && previous.length < 64 && !/[.!?:;,)\]"”’]$/.test(previous) && /^[A-Z0-9“"(]/.test(block.content);
      const continues = paragraph !== null && !endsLine && (
        (ref !== undefined && ref === paragraph.ref)
        || (start !== undefined && paragraph.end !== undefined && start - paragraph.end <= 1 && headings === paragraph.headings)
      );
      if (endsLine && paragraph && start !== undefined && paragraph.end !== undefined && start - paragraph.end <= 1) {
        result.push({ kind: "lead", text: previous });
        paragraph = null;
      }
      if (continues && paragraph) {
        paragraph.text += ref !== undefined && ref === paragraph.ref ? block.content : ` ${block.content}`;
        paragraph.end = block.anchor.charEnd;
      } else {
        flushParagraph();
        paragraph = { text: block.content, end: block.anchor.charEnd, ref, headings };
      }
    }
  }
  flushParagraph();
  flushTable();
  return result;
}

function DocumentReader({ blocks }: { blocks: ReaderBlock[] }) {
  return (
    <div className="gx-prose gx-document">
      {blocks.map((block, index) => {
        switch (block.kind) {
          case "page":
            return <div key={index} className="gx-page-break" role="separator" aria-label={`Page ${block.page}`}><span>Page {block.page}</span></div>;
          case "heading": {
            const Tag = block.depth <= 1 ? "h2" : block.depth === 2 ? "h3" : "h4";
            return <Tag key={index} className={`gx-md-h${Math.min(3, block.depth + 1)}`}>{block.text}</Tag>;
          }
          case "table":
            return block.table ? <DataTable key={index} table={block.table} /> : <pre key={index} className="gx-document-table">{block.text}</pre>;
          case "figure":
            return <p key={index} className="gx-document-figure"><ImageIcon size={14} />{block.text === "Figure" ? "Figure" : block.text}</p>;
          case "lead":
            return <p key={index} className="gx-document-lead">{block.text}</p>;
          case "equation":
            return <p key={index} className="gx-document-equation"><Sigma size={14} /><code>{block.text}</code></p>;
          default:
            return <p key={index}>{block.text}</p>;
        }
      })}
    </div>
  );
}

function OriginalText({ assetId, mode, title }: { assetId: string; mode: NonNullable<ReturnType<typeof readableOriginal>>; title: string }) {
  const [text, setText] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    setText(null);
    setFailed(false);
    void fetch(sourceAssetUrl(assetId))
      .then((response) => response.ok ? response.text() : Promise.reject(new Error(String(response.status))))
      .then((value) => { if (active) setText(value); })
      .catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [assetId]);
  const table = useMemo(() => (mode === "delimited" && text ? parseDelimitedTable(text) : null), [mode, text]);
  if (failed) return null;
  if (text === null) return <div className="gx-reading-skeleton" aria-label="Opening the original">{[0, 1, 2, 3].map((index) => <b key={index} />)}</div>;
  if (mode === "markdown") return <MarkdownView source={withoutRepeatedTitle(text, title)} />;
  if (mode === "delimited" && table) return <DataTable table={table} />;
  if (mode === "json") {
    let pretty = text;
    try {
      pretty = JSON.stringify(JSON.parse(text), null, 2);
    } catch {
      // Show the original bytes when they are not valid JSON.
    }
    return <MarkdownView source={`\`\`\`json\n${pretty.replaceAll("```", "``​`")}\n\`\`\``} />;
  }
  return <PlainText text={text} />;
}

const PROCESSING_COPY: Record<string, string> = {
  pending: "Waiting to be read",
  queued: "Waiting to be read",
  running: "Reading the document…",
  ready: "Read and indexed",
  partial: "Partly readable",
  failed: "Couldn’t be read",
  cancelled: "Reading was cancelled",
};

export function FileCard({ source, file, pages, state, onReprocess }: { source: SourceDetail; file: FileContent; pages: number | null; state: string | undefined; onReprocess?: () => Promise<void> }) {
  const asset = source.asset;
  const name = asset?.originalName ?? file.fileName ?? source.title;
  const type = mediaTypeLabel(asset?.mediaType ?? file.mediaType, name);
  const size = asset?.sizeBytes ?? file.sizeBytes;
  const [retrying, setRetrying] = useState(false);
  const working = state ? WORKING.includes(state) : false;
  return (
    <div className="gx-file-card">
      <span className="gx-file-mark" aria-hidden="true"><FileText size={20} /><em>{(extensionOf(name) || type.split(" ")[0]!).slice(0, 4)}</em></span>
      <span className="gx-file-body">
        <strong title={name}>{name}</strong>
        <small>{[type, size ? formatBytes(size) : null, pages ? `${pages} ${pages === 1 ? "page" : "pages"}` : null].filter(Boolean).join(" · ")}</small>
        {state && (
          <em className={`gx-file-state is-${state}`}>
            {working ? <LoaderCircle className="spin" size={12} /> : state === "failed" || state === "partial" ? <CircleAlert size={12} /> : <i />}
            {PROCESSING_COPY[state] ?? state}
            {(state === "failed" || state === "partial" || state === "cancelled") && onReprocess && (
              <button type="button" className="gx-link" disabled={retrying} onClick={() => { setRetrying(true); void onReprocess().finally(() => setRetrying(false)); }}>
                <RotateCcw size={11} />Try again
              </button>
            )}
          </em>
        )}
      </span>
      {asset && <a className="gx-btn gx-btn-quiet gx-btn-sm" href={sourceAssetUrl(asset.id)} download={asset.originalName}><Download size={13} />Download</a>}
    </div>
  );
}

export function DocumentBody({ source, file }: { source: SourceDetail; file: FileContent }) {
  const asset = source.asset;
  const original = asset && asset.sizeBytes <= ORIGINAL_TEXT_LIMIT ? readableOriginal(asset.mediaType, asset.originalName) : null;
  const structure = useStructure(source.id);
  const blocks = useMemo(() => readerBlocks(structure.data?.blocks ?? []), [structure.data]);
  const pages = useMemo(() => {
    const numbers = (structure.data?.blocks ?? []).map((block) => block.anchor.page ?? 0);
    return numbers.length ? Math.max(...numbers) || null : null;
  }, [structure.data]);
  const state = structure.data?.processing.state ?? source.processing?.state;
  const working = state ? WORKING.includes(state) : false;
  let content;
  if (original && asset) content = <OriginalText assetId={asset.id} mode={original} title={source.title} />;
  else if (blocks.length) content = <DocumentReader blocks={blocks} />;
  else if (file.extracted.trim()) content = <PlainText text={file.extracted} />;
  else if (working || !structure.data) content = <div className="gx-reading-skeleton is-working" aria-label="Reading the document">{[0, 1, 2, 3, 4].map((index) => <b key={index} />)}<p><LoaderCircle className="spin" size={13} />Gunther is reading this document. The original is already safe.</p></div>;
  else content = <p className="gx-item-empty-line">{structure.data.processing.warning ?? "No readable text was found. The original file is preserved and can be downloaded."}</p>;
  return (
    <div className="gx-reader">
      <FileCard source={source} file={file} pages={pages} state={state} onReprocess={structure.reprocess} />
      {state === "ready" || state === "partial" ? <PaperCard sourceId={source.id} revisionId={structure.data?.revisionId} /> : null}
      <ContextNote text={file.context} />
      {structure.data?.processing.warning && blocks.length > 0 && <p className="gx-reader-warning"><CircleAlert size={13} />{structure.data.processing.warning}</p>}
      {content}
      {!original && structure.data?.nextOffset != null && (
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm gx-continue" disabled={structure.loadingMore} onClick={() => void structure.loadMore()}>
          {structure.loadingMore ? <LoaderCircle className="spin" size={13} /> : null}Continue reading
        </button>
      )}
    </div>
  );
}
