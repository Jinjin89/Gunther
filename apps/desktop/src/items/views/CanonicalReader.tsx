import type { DocumentElement, DocumentReference, SourceDocument } from "@gunther/contracts";
import { Fragment, type ReactNode } from "react";
import { sourceAssetUrl } from "../../api";

// Page-furniture kinds preserved by the backend's document_structure policy.
const PAGE_FURNITURE = new Set(["page_header", "page_footer", "footnote", "page_number", "sidebar", "margin_note"]);

function passage(element: DocumentElement, references: Map<string, DocumentReference>): ReactNode {
  const chars = Array.from(element.content);
  type ScriptSpan = { start: number; end: number; script: "super" | "sub" | "fraction"; numeratorEnd?: number; denominatorStart?: number };
  const scripts = (Array.isArray(element.payload?.inlineSpans) ? element.payload.inlineSpans : []) as ScriptSpan[];
  const formatted = (start: number, end: number): ReactNode => {
    const pieces: ReactNode[] = [];
    let offset = start;
    for (const span of scripts) {
      const left = Math.max(start, span.start), right = Math.min(end, span.end);
      if (left < offset || right <= left || !["sub", "super", "fraction"].includes(span.script)) continue;
      pieces.push(chars.slice(offset, left).join(""));
      if (span.script === "fraction" && span.numeratorEnd !== undefined && span.denominatorStart !== undefined) {
        pieces.push(<span className="gx-inline-fraction" key={`${left}-${right}`}><span>{chars.slice(left, span.numeratorEnd).join("")}</span><span>{chars.slice(span.denominatorStart, right).join("")}</span></span>);
      } else {
        const Tag = span.script === "super" ? "sup" : "sub";
        pieces.push(<Tag key={`${left}-${right}`}>{chars.slice(left, right).join("")}</Tag>);
      }
      offset = right;
    }
    pieces.push(chars.slice(offset, end).join(""));
    return pieces;
  };
  const nodes: ReactNode[] = [];
  let at = 0;
  for (const mention of [...element.mentions].sort((a, b) => a.start - b.start)) {
    if (mention.start < at || mention.end > chars.length) continue;
    nodes.push(formatted(at, mention.start));
    const target = mention.status === "resolved" ? references.get(mention.targets[0] ?? "") : undefined;
    nodes.push(target
      ? <a key={mention.id} id={mention.id} href={`#${target.id}`} title={target.raw}>{formatted(mention.start, mention.end)}</a>
      : <span key={mention.id} title={`Reference ${mention.status}`}>{formatted(mention.start, mention.end)}</span>);
    at = mention.end;
  }
  nodes.push(formatted(at, chars.length));
  return nodes;
}

function Table({ element }: { element: DocumentElement }) {
  type Cell = { text?: string; start_row_offset_idx?: number; start_col_offset_idx?: number; end_row_offset_idx?: number; end_col_offset_idx?: number; column_header?: boolean };
  const data = element.payload?.table as { num_rows?: number; table_cells?: Cell[] } | undefined;
  if (!data?.table_cells?.length) return <pre id={element.id} className="gx-document-raw">{element.content}</pre>;
  const rows = new Map<number, Cell[]>();
  for (const cell of data.table_cells) {
    const row = cell.start_row_offset_idx ?? 0;
    rows.set(row, [...(rows.get(row) ?? []), cell]);
  }
  return <div id={element.id} className="gx-document-table" tabIndex={0} role="region" aria-label={`Table · ${element.locator}`}><table><tbody>{[...rows].sort(([a], [b]) => a - b).map(([row, cells]) =>
    <tr key={row}>{cells.sort((a, b) => (a.start_col_offset_idx ?? 0) - (b.start_col_offset_idx ?? 0)).map((cell, index) => {
      const Tag = cell.column_header ? "th" : "td";
      return <Tag key={index} rowSpan={Math.max(1, (cell.end_row_offset_idx ?? row + 1) - row)} colSpan={Math.max(1, (cell.end_col_offset_idx ?? (cell.start_col_offset_idx ?? 0) + 1) - (cell.start_col_offset_idx ?? 0))}>{cell.text}</Tag>;
    })}</tr>)}</tbody></table></div>;
}

function metadataValues(name: string, values: string[]): string[] {
  const unique = [...new Set(values.flatMap((value) => name === "authors" ? value.split(/;\s*/) : [value]).map((value) => value.trim()).filter(Boolean))];
  if (name !== "fileCreated" && name !== "fileModified") return unique;
  return unique.map((value) => {
    const date = /^D:(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(Z|[+-]\d{2}'?\d{2}'?)?$/.exec(value);
    if (!date) return value;
    const [, year, month, day, hour, minute, second, zone] = date;
    const offset = zone === "Z" ? " UTC" : zone ? ` UTC${zone.slice(0, 3)}:${zone.replaceAll("'", "").slice(3)}` : "";
    return `${year}-${month}-${day} ${hour}:${minute}:${second}${offset}`;
  });
}

function DocumentDetails({ document }: { document: SourceDocument }) {
  type Field = { value: string; origin: string; status: string };
  const metadata = document.manifest.metadata as Record<string, Field[]> | undefined;
  const notes = document.elements.filter((e) => ["metadata", "embedded_image"].includes(e.kind));
  const labels: Record<string, string> = { title: "Title", authors: "Authors", publication: "Publication", publisher: "Publisher", source: "Source", doi: "DOI", identifier: "Identifier", published: "Published", received: "Received", accepted: "Accepted", issued: "Issued", updated: "Updated", coverDate: "Cover date", subject: "Subject", keywords: "Keywords", rights: "Rights and publication notices", issn: "ISSN", isbn: "ISBN", fileCreated: "File created", fileModified: "File modified", fileCreator: "File creator", fileProducer: "File producer" };
  const fields = Object.entries(metadata ?? {}).filter(([name, values]) => labels[name] && values.length);
  if (!fields.length && !notes.length) return null;
  return <details className="gx-document-details"><summary>Document details</summary>
    <dl>{fields.map(([name, values]) => {
      // Keep conflicts inspectable; identical declarations need only one visible value.
      const unique = metadataValues(name, values.map((v) => v.value));
      return <Fragment key={name}><dt>{labels[name]}</dt><dd title={values.map((v) => `${v.origin}: ${v.value}`).join("\n")}>{unique.join(" · ")}</dd></Fragment>;
    })}</dl>
    {notes.filter((e) => e.kind === "metadata").map((e) => <p key={e.id} id={e.id}>{e.payload?.status === "unresolved" && <small>Uncertain source text: </small>}{e.content}</p>)}
    {notes.filter((e) => e.kind === "embedded_image").map((e) => typeof e.payload?.imageAssetId === "string" && <img key={e.id} src={sourceAssetUrl(e.payload.imageAssetId)} alt={e.content} loading="lazy" style={{ maxWidth: "100%", height: "auto" }} />)}
  </details>;
}

export function CanonicalReader({ document, continuation, onOpenPage }: { document: SourceDocument; continuation?: ReactNode; onOpenPage?: ((page: number) => void) | undefined }) {
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
  const headings = document.elements.filter((e) => e.kind === "heading" && e.payload?.role !== "title");
  const furniture = document.elements.filter((e) => PAGE_FURNITURE.has(e.kind));
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
  return <div className="gx-prose gx-document">
    <DocumentDetails document={document} />
    {furniture.length > 0 && <details className="gx-document-details"><summary>Show page furniture</summary>{furniture.map((element) =>
      <p key={element.id} id={element.id}><small>{element.locator} · {element.kind.replaceAll("_", " ")}: </small>{passage(element, references)}</p>)}</details>}
    {headings.length > 1 && <details className="gx-document-outline"><summary>Contents</summary><nav aria-label="Document contents"><ul>{headings.map((e) => <li key={e.id} style={{ marginInlineStart: Math.max(0, (Number(e.payload?.level) || 2) - 2) * 12 }}><a href={`#${e.id}`}>{e.content}</a></li>)}</ul></nav></details>}
    {document.elements.map((element) => {
      if (continued.has(element.id)) return null;
      if (PAGE_FURNITURE.has(element.kind) || ["reference", "metadata", "embedded_image", "figure_panel"].includes(element.kind)) return null;
      if (typeof element.payload?.visualOwner === "string" && figureRefs.has(element.payload.visualOwner)) return null;
      if (element.kind === "heading") {
        const level = Math.max(1, Math.min(6, Number(element.payload?.level) || (element.headings.length > 1 ? 3 : 2)));
        const Tag = `h${level}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
        return <Tag key={element.id} id={element.id} className={`gx-md-h${level}`}>{element.content}</Tag>;
      }
      if (element.kind === "table") return <Table key={element.id} element={element} />;
      if (element.kind === "figure") {
        const asset = element.payload?.imageAssetId;
        const caption = String(element.payload?.caption || element.content);
        return <figure key={element.id} id={element.id} className="gx-document-visual">{typeof asset === "string"
          ? <img src={sourceAssetUrl(asset)} alt={caption} loading="lazy" style={printed(element)} />
          : <p>Image unavailable · {element.locator}</p>}<figcaption>{caption}</figcaption>
          {onOpenPage && element.anchor.page && <button type="button" className="gx-link" onClick={() => onOpenPage(element.anchor.page!)}>View on page {element.anchor.page}</button>}
        </figure>;
      }
      if (element.kind === "figure_text") return null;
      if (element.kind === "equation") {
        const asset = element.payload?.imageAssetId;
        const page = onOpenPage && element.anchor.page;
        return <figure className="gx-document-math" key={element.id} id={element.id}>
          {typeof asset === "string" ? <img src={sourceAssetUrl(asset)} alt={`Formula from ${element.locator}`} loading="lazy" style={printed(element)} /> : <pre className="gx-document-raw">{element.content}</pre>}
          {typeof asset === "string" && (element.content || page) && <figcaption className="gx-document-math-meta">
            {element.content && <details><summary>Formula text</summary><pre className="gx-document-raw">{element.content}</pre></details>}
            {page && <button type="button" className="gx-link" onClick={() => onOpenPage(page)}>Page {page}</button>}
          </figcaption>}
        </figure>;
      }
      const marker = element.kind === "list_item" ? String(element.payload?.marker || "") : "";
      const parts = paragraphs.get(String(element.payload?.documentRef));
      if (parts && parts.length > 1) return <p key={element.id} id={element.id}>{parts.map((part, index) => <Fragment key={part.id}>{index > 0 && " "}<span id={index ? part.id : undefined}>{passage(part, references)}</span></Fragment>)}</p>;
      return <p key={element.id} id={element.id}>{marker && !element.content.trimStart().startsWith(marker) && `${marker} `}{passage(element, references)}</p>;
    })}
    {continuation}
    {document.references.length > 0 && <section className="gx-document-references" aria-label="References"><h2 className="gx-md-h2">References</h2>{document.references.map((reference) =>
      <p key={reference.id} id={reference.id}>{reference.raw}{back.has(reference.id) && <Fragment> <a href={`#${back.get(reference.id)}`}>Return to citation</a></Fragment>}</p>)}</section>}
  </div>;
}
