import type { DocumentElement, DocumentReference, SourceDocument } from "@gunther/contracts";
import { Fragment, useState, type ReactNode } from "react";
import { sourceAssetUrl } from "../../api";

// Page-furniture kinds preserved by the backend's document_structure policy.
// They stay stored with their provenance and on the page view, not in the reading text.
const PAGE_FURNITURE = new Set(["page_header", "page_footer", "footnote", "page_number", "sidebar", "margin_note"]);
// Never printed inline: an older document's bibliography has its own list at the end (a
// structured one prints its entries in place), page images are design,
// and the text read out of a figure belongs to the figure's picture.
const NOT_INLINE = new Set(["reference", "embedded_image", "figure_panel", "figure_text"]);
const WORKING = ["pending", "queued", "running"];

type ContentsEntry = { id: string; content: string; level: number };

const roleOf = (element: DocumentElement): string => typeof element.payload?.role === "string" ? element.payload.role : "";
const isRole = (element: DocumentElement | undefined, kind: string, role: string): boolean => element?.kind === kind && roleOf(element) === role;

/**
 * The caption to print with a figure or table. The bare word "Figure" or "Table" is a placeholder,
 * not a caption. A figure's text is its caption; a table's text is its cells, so that never is one.
 */
function captionOf(element: DocumentElement, useContent: boolean): string {
  const stored = element.payload?.caption;
  const text = (typeof stored === "string" ? stored : useContent ? element.content : "").trim();
  return /^(?:figure|fig\.?|table)$/i.test(text) ? "" : text;
}

/** One byline: names stored one per block, or split by ";" or line breaks, joined with ", ". */
function authorLine(elements: DocumentElement[]): string {
  return elements.flatMap((element) => element.content.split(/[;\n]/)).map((name) => name.trim()).filter(Boolean).join(", ");
}

// A printed "Keywords:" in the stored text would repeat the label the reader adds.
const KEYWORDS_LABEL = /^\s*(?:key\s?words?|index terms|关键[词字])\s*[:：—–-]\s*/i;

type InlineSpan = { start: number; end: number; script?: "super" | "sub" | "fraction"; numeratorEnd?: number; denominatorStart?: number; mark?: string; from?: string; checked?: boolean };

const GLYPH_NOTES: Record<string, (from: string) => string> = {
  decoded: (from) => `Read from the printed shape; the PDF's text layer names it “${from}”`,
  uncertain: (from) => `Unreadable in the PDF's text layer (it names “${from}”); see the page for the printed character`,
};

/**
 * An element's text, or its first ``end`` characters, with the formatting recorded for it:
 * raised and lowered type, fractions, characters the glyph check decoded or could not read,
 * and links from citations to bibliography entries.
 */
function passage(element: DocumentElement, references: Map<string, DocumentReference>, end?: number): ReactNode {
  const chars = Array.from(element.content);
  const stop = Math.min(end ?? chars.length, chars.length);
  const spans = (Array.isArray(element.payload?.inlineSpans) ? element.payload.inlineSpans : []) as InlineSpan[];
  const scripts = spans.filter((span) => span.script === "sub" || span.script === "super" || span.script === "fraction");
  const marks = spans.filter((span) => typeof span.mark === "string" && GLYPH_NOTES[span.mark]).sort((a, b) => a.start - b.start);
  const marked = (start: number, end: number): ReactNode[] => {
    const pieces: ReactNode[] = [];
    let offset = start;
    for (const mark of marks) {
      const left = Math.max(start, mark.start), right = Math.min(end, mark.end);
      if (left < offset || right <= left) continue;
      pieces.push(chars.slice(offset, left).join(""));
      pieces.push(<span key={`g${left}`} className={`gx-glyph-${mark.mark}`} title={GLYPH_NOTES[mark.mark!]!(mark.from ?? "")}>{chars.slice(left, right).join("")}</span>);
      offset = right;
    }
    pieces.push(chars.slice(offset, end).join(""));
    return pieces;
  };
  const formatted = (start: number, end: number): ReactNode => {
    const pieces: ReactNode[] = [];
    let offset = start;
    for (const span of scripts) {
      const left = Math.max(start, span.start), right = Math.min(end, span.end);
      if (left < offset || right <= left) continue;
      pieces.push(marked(offset, left));
      if (span.script === "fraction" && span.numeratorEnd !== undefined && span.denominatorStart !== undefined) {
        pieces.push(<span className="gx-inline-fraction" key={`${left}-${right}`}><span>{marked(left, span.numeratorEnd)}</span><span>{marked(span.denominatorStart, right)}</span></span>);
      } else {
        const Tag = span.script === "super" ? "sup" : "sub";
        pieces.push(<Tag key={`${left}-${right}`}>{marked(left, right)}</Tag>);
      }
      offset = right;
    }
    pieces.push(marked(offset, end));
    return pieces;
  };
  const nodes: ReactNode[] = [];
  let at = 0;
  for (const mention of [...element.mentions].sort((a, b) => a.start - b.start)) {
    if (mention.start < at || mention.end > stop) continue;
    nodes.push(formatted(at, mention.start));
    const target = mention.status === "resolved" ? references.get(mention.targets[0] ?? "") : undefined;
    nodes.push(target
      ? <a key={mention.id} id={mention.id} href={`#${target.id}`} title={target.raw}>{formatted(mention.start, mention.end)}</a>
      : <span key={mention.id} title={`Reference ${mention.status}`}>{formatted(mention.start, mention.end)}</span>);
    at = mention.end;
  }
  nodes.push(formatted(at, stop));
  return nodes;
}

function Table({ element, caption, references }: { element: DocumentElement; caption?: string | undefined; references?: Map<string, DocumentReference> }) {
  type Cell = { text?: string; start_row_offset_idx?: number; start_col_offset_idx?: number; end_row_offset_idx?: number; end_col_offset_idx?: number; column_header?: boolean };
  const data = element.payload?.table as { num_rows?: number; table_cells?: Cell[] } | undefined;
  // A captioned table carries its id on the figure that wraps caption and grid.
  const id = caption ? undefined : element.id;
  let grid: ReactNode;
  if (!data?.table_cells?.length) {
    grid = <pre id={id} className="gx-document-raw">{element.content}</pre>;
  } else {
    const rows = new Map<number, Cell[]>();
    for (const cell of data.table_cells) {
      const row = cell.start_row_offset_idx ?? 0;
      rows.set(row, [...(rows.get(row) ?? []), cell]);
    }
    grid = <div id={id} className="gx-document-table" tabIndex={0} role="region" aria-label={`Table · ${element.locator}`}><table><tbody>{[...rows].sort(([a], [b]) => a - b).map(([row, cells]) =>
      <tr key={row}>{cells.sort((a, b) => (a.start_col_offset_idx ?? 0) - (b.start_col_offset_idx ?? 0)).map((cell, index) => {
        const Tag = cell.column_header ? "th" : "td";
        return <Tag key={index} rowSpan={Math.max(1, (cell.end_row_offset_idx ?? row + 1) - row)} colSpan={Math.max(1, (cell.end_col_offset_idx ?? (cell.start_col_offset_idx ?? 0) + 1) - (cell.start_col_offset_idx ?? 0))}>{cell.text}</Tag>;
      })}</tr>)}</tbody></table></div>;
  }
  // The stored text starts with the caption, so its inline formatting applies as recorded.
  const printed = caption && references && element.content.startsWith(caption) ? passage(element, references, Array.from(caption).length) : caption;
  return caption ? <figure id={element.id} className="gx-document-captioned"><figcaption>{printed}</figcaption>{grid}</figure> : grid;
}

/** Names in one byline: "A. Smith, B. Lee and C. Wu" or "Smith, A.; Lee, B.". */
function splitNames(value: string): string[] {
  const parts = value.includes(";") ? value.split(";") : value.split(/,|\band\b|&|\n/).reduce<string[]>((names, part) => {
    // "Smith, J." without semicolons: a part of initials only belongs to the surname before it.
    if (names.length && /^\s*(?:\p{L}\.\s*-?)+$/u.test(part)) names[names.length - 1] = `${names[names.length - 1]}, ${part.trim()}`;
    else names.push(part);
    return names;
  }, []);
  return parts.map((part) => part.replace(/\s+/g, " ").trim()).filter((part) => /\p{L}/u.test(part));
}

/** (surname, first initial) with accents folded, so "Smith, J." and "John Smith" are one author. */
function nameKey(name: string): [string, string] {
  const plain = name.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  if (plain.includes(",")) {
    const [surname = "", given = ""] = plain.split(/,(.*)/s);
    return [(surname.match(/[a-z]+/g) ?? []).join(" "), (given.match(/[a-z]/) ?? [""])[0]];
  }
  const words = plain.match(/[a-z]+\.?/g) ?? [];
  const full = words.filter((word) => !word.endsWith(".") && word.length > 1);
  const surname = full[full.length - 1] ?? plain.trim();
  const other = words.find((word) => word.replace(".", "") !== surname);
  return [surname, other ? other[0]! : ""];
}

function sameAuthor(a: [string, string], b: [string, string]): boolean {
  return a[0] === b[0] && (!a[1] || !b[1] || a[1] === b[1]);
}

// A PDF date ("D:20200301120000+01'00'") or an ISO one, shown as a date and time people read.
function readableDate(value: string): string {
  const pdf = /^D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(Z|[+-]\d{2}'?\d{2}'?)?/.exec(value.trim());
  const iso = /^(\d{4})-(\d{2})(?:-(\d{2}))?(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$/.exec(value.trim());
  const found = pdf ?? iso;
  if (!found) return value;
  const [, year, month, day, hour, minute, second, zone] = found;
  const date = [year, month, day].filter(Boolean).join("-");
  if (!hour) return date;
  const clean = zone?.replaceAll("'", "").replace(":", "");
  const offset = !clean ? "" : clean === "Z" ? " UTC" : ` UTC${clean.slice(0, 3)}:${clean.slice(3) || "00"}`;
  return `${date} ${hour}:${minute ?? "00"}${second ? `:${second}` : ""}${offset}`;
}

const DATE_FIELDS = new Set(["published", "received", "revised", "accepted", "issued", "updated", "coverDate", "fileCreated", "fileModified"]);

function metadataValues(name: string, values: string[]): string[] {
  if (name === "authors") {
    // Every declaration of the authors, each author once however it is spelled.
    const names: string[] = [];
    const keys: [string, string][] = [];
    for (const author of values.flatMap(splitNames)) {
      const key = nameKey(author);
      if (keys.some((seen) => sameAuthor(seen, key))) continue;
      keys.push(key);
      names.push(author);
    }
    return names;
  }
  const unique = [...new Set(values.map((value) => value.trim()).filter(Boolean))];
  return DATE_FIELDS.has(name) ? [...new Set(unique.map(readableDate))] : unique;
}

// The document's own declarations, and facts about the file, are told apart.
const PUBLICATION_FIELDS: Record<string, string> = { title: "Title", authors: "Authors", publication: "Publication", publisher: "Publisher", source: "Source", doi: "DOI", identifier: "Identifier", published: "Published", received: "Received", revised: "Revised", accepted: "Accepted", issued: "Issued", updated: "Updated", coverDate: "Cover date", subject: "Subject", keywords: "Keywords", rights: "Rights and publication notices", issn: "ISSN", isbn: "ISBN" };
const FILE_FIELDS: Record<string, string> = { fileCreated: "File created", fileModified: "File modified", fileCreator: "Made with (software)", fileProducer: "PDF written by (software)" };
const ORIGINS: Record<string, string> = { document: "printed in the document" };

function originName(origin: string): string {
  return ORIGINS[origin] ?? (origin.startsWith("xmp:") ? "file metadata (XMP)" : origin.startsWith("pdf-info:") ? "file metadata (Info)" : origin);
}

function DocumentDetails({ document, structured }: { document: SourceDocument; structured: boolean }) {
  type Field = { value: string; origin: string; status: string };
  const metadata = document.manifest.metadata as Record<string, Field[]> | undefined;
  // Logos and badges (role "decoration") are page design, not document details. A structured
  // document prints its authors and keywords in the text, so they are not listed twice.
  const printedInText = (e: DocumentElement) => structured && ["authors", "keywords"].includes(roleOf(e));
  const notes = document.elements.filter((e) => (e.kind === "metadata" && !printedInText(e)) || (e.kind === "embedded_image" && e.payload?.role !== "decoration"));
  const fields = (labels: Record<string, string>) => Object.entries(metadata ?? {})
    .map(([name, values]): [string, Field[]] => [name, Array.isArray(values) ? values.filter((v) => typeof v?.value === "string") : []])
    .filter(([name, values]) => labels[name] && values.length)
    .sort(([a], [b]) => Object.keys(labels).indexOf(a) - Object.keys(labels).indexOf(b));
  const publication = fields(PUBLICATION_FIELDS), file = fields(FILE_FIELDS);
  if (!publication.length && !file.length && !notes.length) return null;
  const list = (entries: [string, Field[]][], labels: Record<string, string>) => <dl>{entries.map(([name, values]) => {
    // Keep conflicts inspectable; equivalent declarations need only one visible value, and the
    // document's own printed form leads ("Ana Kovač", not the file's "Kovač, A.").
    const rank = (origin: string) => origin === "document" ? 0 : origin.startsWith("xmp:") ? 1 : 2;
    const ordered = [...values].sort((a, b) => rank(a.origin) - rank(b.origin));
    const unique = metadataValues(name, ordered.map((v) => v.value));
    const sources = [...new Set(values.map((v) => originName(v.origin)))];
    return <Fragment key={name}><dt>{labels[name]}</dt><dd title={values.map((v) => `${originName(v.origin)}: ${v.value}`).join("\n")}>{unique.join(" · ")}
      {unique.length > 1 && name !== "authors" && <small className="gx-document-conflict"> (declared differently: {sources.join(", ")})</small>}
    </dd></Fragment>;
  })}</dl>;
  return <details className="gx-document-details"><summary>Document details</summary>
    {publication.length > 0 && list(publication, PUBLICATION_FIELDS)}
    {file.length > 0 && <><p className="gx-document-details-group">About this file (not publication dates)</p>{list(file, FILE_FIELDS)}</>}
    {notes.filter((e) => e.kind === "metadata").map((e) => <p key={e.id} id={e.id}>{e.payload?.status === "unresolved" && <small>Uncertain source text: </small>}{e.content}</p>)}
    {notes.filter((e) => e.kind === "embedded_image").map((e) => typeof e.payload?.imageAssetId === "string" && <img key={e.id} src={sourceAssetUrl(e.payload.imageAssetId)} alt={e.content} loading="lazy" style={{ maxWidth: "100%", height: "auto" }} />)}
  </details>;
}

type ExtractionWarning = { kind?: string; message?: string; count?: number; pages?: number[] };
type Omission = { block?: string; reason?: string; page?: number; text?: string; label?: string };

/**
 * What the extraction could not vouch for, apart from how the structure was decided: glyphs
 * read from their shapes or unreadable, equations kept as images, figures that may be cut,
 * and every source block left out, with its reason.
 */
function ExtractionStatus({ document }: { document: SourceDocument }) {
  const extraction = document.manifest.extraction as { state?: string; warnings?: ExtractionWarning[]; omitted?: Record<string, number> } | undefined;
  if (!extraction || typeof extraction !== "object" || WORKING.includes(document.processing.state)) return null;
  const warnings = Array.isArray(extraction.warnings) ? extraction.warnings : [];
  const omissions = (Array.isArray(document.manifest.omissions) ? document.manifest.omissions : []) as Omission[];
  const pages = (list?: number[]) => list?.length ? ` (page${list.length > 1 ? "s" : ""} ${list.slice(0, 12).join(", ")}${list.length > 12 ? ", …" : ""})` : "";
  const line = warnings.length ? `Extraction incomplete: ${warnings.length} ${warnings.length === 1 ? "problem" : "problems"} to check` : "Extraction checked: no problems found";
  return <details className={`gx-extraction-status ${warnings.length ? "is-partial" : ""}`}><summary>{line}{omissions.length > 0 && ` · ${omissions.length} source ${omissions.length === 1 ? "block" : "blocks"} left out`}</summary>
    {warnings.length > 0 && <ul>{warnings.map((warning, index) => <li key={`${warning.kind}-${index}`}>{warning.message}{warning.count ? ` ${warning.count}×` : ""}{pages(warning.pages)}</li>)}</ul>}
    {omissions.length > 0 && <table className="gx-extraction-omissions"><thead><tr><th>Page</th><th>Left out because</th><th>Text</th></tr></thead><tbody>
      {omissions.map((omission, index) => <tr key={omission.block ?? index}><td>{omission.page ?? ""}</td><td>{omission.reason}</td><td>{omission.text || `(${omission.label ?? "block"})`}</td></tr>)}
    </tbody></table>}
  </details>;
}

/** The model name in "provider:model", e.g. "deepseek:deepseek-flash" -> "deepseek-flash". */
function modelName(by: unknown): string {
  const text = typeof by === "string" ? by.trim() : "";
  return text.slice(text.indexOf(":") + 1).trim();
}

/**
 * One quiet line saying how the structure of a structured (schema 2) document was decided.
 * Old documents have no such line. While the document is being read again the line would
 * describe the version that is being replaced, so it waits.
 */
function StructureStatus({ document, onReprocess }: { document: SourceDocument; onReprocess?: (() => Promise<void> | void) | undefined }) {
  const [retrying, setRetrying] = useState(false);
  const structure = document.manifest.structure as { state?: unknown; by?: unknown; error?: unknown } | null | undefined;
  if (Number(document.manifest.schema) !== 2 || !structure || typeof structure !== "object" || WORKING.includes(document.processing.state)) return null;
  const error = typeof structure.error === "string" ? structure.error.trim() : "";
  const model = modelName(structure.by);
  let line: string;
  if (structure.state === "checked") line = model ? `Structure checked by ${model}` : "Structure checked by a model";
  else if (structure.state === "draft" || structure.state === "needs-key") line = "Structure not checked by a model. Add one in Settings → Models, then Read again.";
  else if (structure.state === "failed") line = error ? `Structure check failed: ${error}` : "Structure check failed";
  else if (structure.state === "too-long") line = "Too long for a structure check; showing the layout version.";
  else return null;
  return <p className="gx-structure-status">{line}
    {structure.state === "failed" && onReprocess && (
      <button type="button" className="gx-link" disabled={retrying} onClick={() => { setRetrying(true); void Promise.resolve(onReprocess()).finally(() => setRetrying(false)); }}>Try again</button>
    )}
  </p>;
}

export function CanonicalReader({ document, continuation, onOpenPage, onReprocess }: { document: SourceDocument; continuation?: ReactNode; onOpenPage?: ((page: number) => void) | undefined; onReprocess?: (() => Promise<void> | void) | undefined }) {
  // Schema 2 documents were put in order by the structure plan: front, body, notes, back.
  const structured = Number(document.manifest.schema) === 2;
  const references = new Map(document.references.map((reference) => [reference.id, reference]));
  const back = new Map<string, string>();
  for (const element of document.elements) for (const mention of element.mentions) {
    if (mention.status === "resolved") for (const target of mention.targets) if (!back.has(target)) back.set(target, mention.id);
  }
  // Images keep their printed size relative to the body text, so a formula's
  // glyphs match the reader's own type; wide visuals still fit the column.
  const bodyPt = Number(document.manifest.bodyFontSize) || 10;
  const printed = (element: DocumentElement) => {
    const width = Number(element.payload?.widthPt);
    return width > 0 ? { width: `min(100%, ${(width / bodyPt).toFixed(2)}em)` } : undefined;
  };
  const figureRefs = new Set(document.elements.filter((e) => e.kind === "figure").map((e) => e.payload?.documentRef));
  // A structured document prints each bibliography entry where it stands, under its own
  // heading. Entries not loaded yet are listed at the end, so every citation has its target.
  const entryOf = new Map(document.references.map((reference) => [reference.elementId, reference]));
  const loaded = new Set(document.elements.map((e) => e.id));
  const pending = structured ? document.references.filter((reference) => !loaded.has(reference.elementId)) : document.references;
  const entry = (reference: DocumentReference): ReactNode => <p key={reference.id} id={reference.id} className="gx-document-reference">{reference.raw}{back.has(reference.id) && <Fragment> <a href={`#${back.get(reference.id)}`}>Return to citation</a></Fragment>}</p>;
  const paragraphs = new Map<string, DocumentElement[]>();
  const continued = new Set<string>();
  for (const element of document.elements) {
    if (element.kind !== "paragraph") continue;
    const previous = element.payload?.continuesDocumentRef;
    const group = typeof previous === "string" ? paragraphs.get(previous) : undefined;
    const parts = group ?? [];
    parts.push(element);
    if (group) continued.add(element.id);
    const ref = element.payload?.documentRef;
    if (typeof ref === "string") paragraphs.set(ref, parts);
  }
  const heading = (element: DocumentElement, level: number): ReactNode => {
    const Tag = `h${level}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
    return <Tag key={element.id} id={element.id} className={`gx-md-h${level}`}>{element.content}</Tag>;
  };
  const headingLevel = (element: DocumentElement) => Math.max(1, Math.min(6, Number(element.payload?.level) || (element.headings.length > 1 ? 3 : 2)));
  const figure = (element: DocumentElement, noun = "Figure"): ReactNode => {
    const asset = element.payload?.imageAssetId;
    const caption = captionOf(element, structured);
    return <figure key={element.id} id={element.id} className="gx-document-visual">{typeof asset === "string"
      ? <img src={sourceAssetUrl(asset)} alt={caption || `${noun} on ${element.locator}`} loading="lazy" style={printed(element)} />
      : <p>Image unavailable · {element.locator}</p>}{caption && <figcaption>{caption === element.content.trim() ? passage(element, references) : caption}</figcaption>}
      {onOpenPage && element.anchor.page && <button type="button" className="gx-link" onClick={() => onOpenPage(element.anchor.page!)}>View on page {element.anchor.page}</button>}
    </figure>;
  };
  const equation = (element: DocumentElement): ReactNode => {
    const asset = element.payload?.imageAssetId;
    const page = onOpenPage && element.anchor.page;
    const number = typeof element.payload?.equationNumber === "string" ? element.payload.equationNumber : "";
    const issue = typeof element.payload?.transcriptionIssue === "string" ? element.payload.transcriptionIssue : "";
    // Only a transcription checked glyph by glyph is offered as the formula's text.
    const unread = element.payload?.transcription === "unreliable" || element.payload?.transcription === "unavailable";
    return <figure className="gx-document-math" key={element.id} id={element.id}>
      {typeof asset === "string" ? <img src={sourceAssetUrl(asset)} alt={element.content ? `Formula${number ? ` ${number}` : ""}: ${element.content}` : `Formula${number ? ` ${number}` : ""} from ${element.locator}`} loading="lazy" style={printed(element)} /> : element.content ? <pre className="gx-document-raw">{element.content}</pre> : <p>Formula image unavailable · {element.locator}</p>}
      {(element.content || page || number || unread) && <figcaption className="gx-document-math-meta">
        {/* The number is read from inside the formula's region, so its picture already prints it. */}
        {number && typeof asset !== "string" && <span className="gx-document-math-number">{number}</span>}
        {element.content && typeof asset === "string" && <details><summary>Formula text</summary><pre className="gx-document-raw">{passage(element, references)}</pre></details>}
        {unread && <small className="gx-document-math-note">Shown as printed; no text transcription{issue ? ` (${issue})` : ""}.</small>}
        {page && <button type="button" className="gx-link" onClick={() => onOpenPage(page)}>Page {page}</button>}
      </figcaption>}
    </figure>;
  };
  const text = (element: DocumentElement): ReactNode => {
    const marker = element.kind === "list_item" ? String(element.payload?.marker || "") : "";
    const parts = paragraphs.get(String(element.payload?.documentRef));
    if (parts && parts.length > 1) return <p key={element.id} id={element.id}>{parts.map((part, index) => <Fragment key={part.id}>{index > 0 && " "}<span id={index ? part.id : undefined}>{passage(part, references)}</span></Fragment>)}</p>;
    return <p key={element.id} id={element.id}>{marker && !element.content.trimStart().startsWith(marker) && `${marker} `}{passage(element, references)}</p>;
  };

  const nodes: ReactNode[] = [];
  const contents: ContentsEntry[] = [];
  if (!structured) {
    // Old documents: every kind is guessed in place, so furniture and visual labels are filtered out here.
    for (const element of document.elements) {
      if (element.kind === "heading" && element.payload?.role !== "title") contents.push({ id: element.id, content: element.content, level: Number(element.payload?.level) || 2 });
      if (continued.has(element.id)) continue;
      if (PAGE_FURNITURE.has(element.kind) || ["reference", "metadata", "embedded_image", "figure_panel"].includes(element.kind)) continue;
      if (typeof element.payload?.visualOwner === "string" && figureRefs.has(element.payload.visualOwner)) continue;
      if (element.kind === "heading") nodes.push(heading(element, headingLevel(element)));
      else if (element.kind === "table") nodes.push(<Table key={element.id} element={element} />);
      else if (element.kind === "figure") nodes.push(figure(element));
      else if (element.kind === "figure_text") continue;
      else if (element.kind === "equation") nodes.push(equation(element));
      else nodes.push(text(element));
    }
  } else {
    const elements = document.elements;
    // The title is the document's one h1: sections stored from level 1 are shown one level lower.
    const lower = elements.some((e) => isRole(e, "heading", "title"))
      && elements.some((e) => e.kind === "heading" && !["title", "subtitle"].includes(roleOf(e)) && headingLevel(e) === 1) ? 1 : 0;
    const merged = new Set<string>();
    let headed = false;
    let labelled = false;
    for (const [index, element] of elements.entries()) {
      if (merged.has(element.id) || continued.has(element.id)) continue;
      if (element.kind === "reference") {
        const reference = entryOf.get(element.id);
        nodes.push(reference ? entry(reference) : <p key={element.id} id={element.id} className="gx-document-reference">{passage(element, references)}</p>);
        continue;
      }
      if (PAGE_FURNITURE.has(element.kind) || NOT_INLINE.has(element.kind)) continue;
      const role = roleOf(element);
      if (element.kind === "heading") {
        if (role === "title") {
          nodes.push(heading(element, 1));
        } else if (role === "subtitle") {
          nodes.push(<p key={element.id} id={element.id} className="gx-document-subtitle">{element.content}</p>);
        } else {
          if (role === "abstract") headed = true;
          const level = Math.min(6, headingLevel(element) + lower);
          contents.push({ id: element.id, content: element.content, level });
          nodes.push(heading(element, level));
        }
      } else if (element.kind === "metadata") {
        if (role === "authors") {
          // Authors stored as several blocks in a row make one line.
          let end = index;
          while (isRole(elements[end + 1], "metadata", "authors")) end += 1;
          const run = elements.slice(index, end + 1);
          for (const author of run) merged.add(author.id);
          const names = authorLine(run);
          if (names) nodes.push(<p key={element.id} id={element.id} className="gx-document-authors">{names}</p>);
        } else if (role === "keywords") {
          const words = element.content.replace(KEYWORDS_LABEL, "").trim();
          if (words) nodes.push(<p key={element.id} id={element.id} className="gx-document-keywords"><span>Keywords:</span> {words}</p>);
        }
        // Other metadata is not part of the reading; it is listed under "Document details".
      } else if (element.kind === "paragraph" && role === "abstract" && !headed && !labelled) {
        // The paper prints no heading for its abstract: the reader names it, once.
        labelled = true;
        contents.push({ id: `${element.id}-label`, content: "Abstract", level: 2 });
        nodes.push(<Fragment key={element.id}><h2 id={`${element.id}-label`} className="gx-md-h2">Abstract</h2>{text(element)}</Fragment>);
      } else if (element.kind === "note") {
        // Notes sit together at the end of the body under one heading.
        const before = elements[index - 1];
        const marker = String(element.payload?.marker || "");
        nodes.push(<Fragment key={element.id}>
          {before?.kind !== "note" && !isRole(before, "heading", "notes") && <h2 id={`${element.id}-label`} className="gx-md-h2">Notes</h2>}
          <p id={element.id} className="gx-document-note">{marker && !element.content.trimStart().startsWith(marker) && <><span className="gx-document-note-marker">{marker}</span>{" "}</>}{passage(element, references)}</p>
        </Fragment>);
      } else if (element.kind === "code") {
        nodes.push(<pre key={element.id} id={element.id} className="gx-document-raw"><code>{element.content}</code></pre>);
      } else if (element.kind === "table") {
        const cells = (element.payload?.table as { table_cells?: unknown[] } | undefined)?.table_cells;
        // A table that was printed as a picture is shown as that picture.
        if (!cells?.length && typeof element.payload?.imageAssetId === "string") nodes.push(figure(element, "Table"));
        else nodes.push(<Table key={element.id} element={element} caption={captionOf(element, false) || undefined} references={references} />);
      } else if (element.kind === "figure") {
        nodes.push(figure(element));
      } else if (element.kind === "equation") {
        nodes.push(equation(element));
      } else {
        nodes.push(text(element));
      }
    }
  }
  return <div className="gx-prose gx-document">
    <StructureStatus document={document} onReprocess={onReprocess} />
    <ExtractionStatus document={document} />
    <DocumentDetails document={document} structured={structured} />
    {contents.length > 1 && <details className="gx-document-outline"><summary>Contents</summary><nav aria-label="Document contents"><ul>{contents.map((e) => <li key={e.id} style={{ marginInlineStart: Math.max(0, e.level - 2) * 12 }}><a href={`#${e.id}`}>{e.content}</a></li>)}</ul></nav></details>}
    {nodes}
    {continuation}
    {pending.length > 0 && <section className="gx-document-references" aria-label="References"><h2 className="gx-md-h2">{structured ? "References not yet shown" : "References"}</h2>{pending.map(entry)}</section>}
  </div>;
}
