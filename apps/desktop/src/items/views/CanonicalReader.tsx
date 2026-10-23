import type { DocumentElement, DocumentReference, SourceDocument } from "@gunther/contracts";
import { Fragment, type ReactNode } from "react";
import { sourceAssetUrl } from "../../api";

// Page-furniture kinds preserved by the backend's document_structure policy.
const PAGE_FURNITURE = new Set(["page_header", "page_footer", "footnote", "page_number", "sidebar", "margin_note"]);

function passage(element: DocumentElement, references: Map<string, DocumentReference>): ReactNode {
  const chars = Array.from(element.content);
  const nodes: ReactNode[] = [];
  let at = 0;
  for (const mention of [...element.mentions].sort((a, b) => a.start - b.start)) {
    if (mention.start < at || mention.end > chars.length) continue;
    nodes.push(chars.slice(at, mention.start).join(""));
    const target = mention.status === "resolved" ? references.get(mention.targets[0] ?? "") : undefined;
    nodes.push(target
      ? <a key={mention.id} id={mention.id} href={`#${target.id}`} title={target.raw}>{mention.raw}</a>
      : <span key={mention.id} title={`Reference ${mention.status}`}>{mention.raw}</span>);
    at = mention.end;
  }
  nodes.push(chars.slice(at).join(""));
  return nodes;
}

function Table({ element }: { element: DocumentElement }) {
  type Cell = { text?: string; start_row_offset_idx?: number; start_col_offset_idx?: number; end_row_offset_idx?: number; end_col_offset_idx?: number; column_header?: boolean };
  const data = element.payload?.table as { num_rows?: number; table_cells?: Cell[] } | undefined;
  if (!data?.table_cells?.length) return <pre>{element.content}</pre>;
  const rows = new Map<number, Cell[]>();
  for (const cell of data.table_cells) {
    const row = cell.start_row_offset_idx ?? 0;
    rows.set(row, [...(rows.get(row) ?? []), cell]);
  }
  return <div className="gx-document-table"><table><tbody>{[...rows].sort(([a], [b]) => a - b).map(([row, cells]) =>
    <tr key={row}>{cells.sort((a, b) => (a.start_col_offset_idx ?? 0) - (b.start_col_offset_idx ?? 0)).map((cell, index) => {
      const Tag = cell.column_header ? "th" : "td";
      return <Tag key={index} rowSpan={Math.max(1, (cell.end_row_offset_idx ?? row + 1) - row)} colSpan={Math.max(1, (cell.end_col_offset_idx ?? (cell.start_col_offset_idx ?? 0) + 1) - (cell.start_col_offset_idx ?? 0))}>{cell.text}</Tag>;
    })}</tr>)}</tbody></table></div>;
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
      const unique = [...new Set(values.map((v) => v.value))];
      return <Fragment key={name}><dt>{labels[name]}</dt><dd title={values.map((v) => `${v.origin}: ${v.value}`).join("\n")}>{unique.join(" · ")}</dd></Fragment>;
    })}</dl>
    {notes.filter((e) => e.kind === "metadata").map((e) => <p key={e.id} id={e.id}>{e.payload?.status === "unresolved" && <small>Uncertain source text: </small>}{e.content}</p>)}
    {notes.filter((e) => e.kind === "embedded_image").map((e) => typeof e.payload?.imageAssetId === "string" && <img key={e.id} src={sourceAssetUrl(e.payload.imageAssetId)} alt={e.content} loading="lazy" style={{ maxWidth: "100%", height: "auto" }} />)}
  </details>;
}

export function CanonicalReader({ document }: { document: SourceDocument }) {
  const references = new Map(document.references.map((reference) => [reference.id, reference]));
  const back = new Map<string, string>();
  for (const element of document.elements) for (const mention of element.mentions) {
    if (mention.status === "resolved") for (const target of mention.targets) if (!back.has(target)) back.set(target, mention.id);
  }
  const visualChildren = new Map<string, DocumentElement[]>();
  const figureRefs = new Set(document.elements.filter((e) => e.kind === "figure").map((e) => e.payload?.documentRef));
  for (const element of document.elements) {
    const owner = element.payload?.visualOwner;
    if (typeof owner === "string" && figureRefs.has(owner)) visualChildren.set(owner, [...(visualChildren.get(owner) ?? []), element]);
  }
  const headings = document.elements.filter((e) => e.kind === "heading" && e.payload?.role !== "title");
  const furniture = document.elements.filter((e) => PAGE_FURNITURE.has(e.kind));
  return <div className="gx-prose gx-document">
    <DocumentDetails document={document} />
    {furniture.length > 0 && <details className="gx-document-details"><summary>Show page furniture</summary>{furniture.map((element) =>
      <p key={element.id} id={element.id}><small>{element.locator} · {element.kind.replaceAll("_", " ")}: </small>{passage(element, references)}</p>)}</details>}
    {headings.length > 1 && <details className="gx-document-outline"><summary>Contents</summary><nav aria-label="Document contents"><ul>{headings.map((e) => <li key={e.id} style={{ marginInlineStart: Math.max(0, (Number(e.payload?.level) || 2) - 2) * 12 }}><a href={`#${e.id}`}>{e.content}</a></li>)}</ul></nav></details>}
    {document.elements.map((element) => {
      if (PAGE_FURNITURE.has(element.kind) || ["reference", "metadata", "embedded_image", "figure_panel"].includes(element.kind)) return null;
      if (typeof element.payload?.visualOwner === "string" && figureRefs.has(element.payload.visualOwner)) return null;
      if (element.kind === "heading") {
        const level = Math.max(1, Math.min(6, Number(element.payload?.level) || (element.headings.length > 1 ? 3 : 2)));
        const Tag = `h${level}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
        return <Tag key={element.id} id={element.id}>{element.content}</Tag>;
      }
      if (element.kind === "table") return <Table key={element.id} element={element} />;
      if (element.kind === "figure") {
        const asset = element.payload?.imageAssetId;
        const caption = String(element.payload?.caption || element.content);
        const children = visualChildren.get(String(element.payload?.documentRef)) ?? [];
        return <figure key={element.id} id={element.id}>{typeof asset === "string"
          ? <img src={sourceAssetUrl(asset)} alt={caption} loading="lazy" style={{ maxWidth: "100%", height: "auto" }} />
          : <p>Image unavailable · {element.locator}</p>}<figcaption>{caption}</figcaption>
          {children.some((e) => e.kind === "figure_text" || e.kind === "table") && <details><summary>Extracted figure text and tables</summary>{children.filter((e) => e.kind === "figure_text" || e.kind === "table").map((e) => e.kind === "table" ? <Table key={e.id} element={e} /> : <p key={e.id} id={e.id}>{e.content}</p>)}</details>}
        </figure>;
      }
      if (element.kind === "figure_text") return <details key={element.id}><summary>Visual annotation · {element.locator}</summary><p id={element.id}>{element.content}</p></details>;
      if (element.kind === "equation") return <pre key={element.id} id={element.id}>{element.content}</pre>;
      const marker = element.kind === "list_item" ? String(element.payload?.marker || "") : "";
      return <p key={element.id} id={element.id}>{marker && !element.content.trimStart().startsWith(marker) && `${marker} `}{passage(element, references)}</p>;
    })}
    {document.references.length > 0 && <section aria-label="References"><h2>References</h2>{document.references.map((reference) =>
      <p key={reference.id} id={reference.id}>{reference.raw}{back.has(reference.id) && <Fragment> <a href={`#${back.get(reference.id)}`}>Return to citation</a></Fragment>}</p>)}</section>}
  </div>;
}
