import { Check, Copy } from "lucide-react";
import { useMemo, useState } from "react";
import { MarkdownView } from "../../components/markdown/MarkdownView";
import { parseDelimitedTable, type DelimitedTable } from "../sourceContent";

const PAGE_ROWS = 500;

const DELIMITER_LABEL: Record<DelimitedTable["delimiter"], string> = {
  "\t": "Tab-separated",
  ",": "Comma-separated",
  ";": "Semicolon-separated",
  "|": "Markdown table",
};

/** A calm data grid: sticky header, row numbers, tabular figures, numbers aligned right. */
export function DataTable({ table, caption }: { table: DelimitedTable; caption?: string }) {
  const [showAll, setShowAll] = useState(false);
  const [copied, setCopied] = useState(false);
  const rows = showAll ? table.rows : table.rows.slice(0, PAGE_ROWS);
  const copy = async () => {
    const tsv = [table.header, ...table.rows].map((cells) => cells.map((cell) => cell.replace(/[\t\n]/g, " ")).join("\t")).join("\n");
    try {
      await navigator.clipboard.writeText(tsv);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_400);
    } catch {
      // Clipboard can be unavailable; the grid remains selectable.
    }
  };
  return (
    <figure className="gx-data">
      <figcaption>
        <span><strong>{table.rows.length.toLocaleString()}</strong> {table.rows.length === 1 ? "row" : "rows"} · <strong>{table.header.length}</strong> {table.header.length === 1 ? "column" : "columns"}{caption ? ` · ${caption}` : ""}</span>
        <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => void copy()} title="Copy as tab-separated values, ready to paste into a spreadsheet">
          {copied ? <Check size={13} /> : <Copy size={13} />}{copied ? "Copied" : "Copy table"}
        </button>
      </figcaption>
      <div className="gx-data-scroll" role="region" aria-label="Table" tabIndex={0}>
        <table>
          <thead>
            <tr>
              <th className="gx-data-index" aria-label="Row" />
              {table.header.map((cell, index) => <th key={index} className={table.numericColumns[index] ? "is-number" : undefined} scope="col">{cell || <span className="gx-data-blank">Column {index + 1}</span>}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((cells, rowIndex) => (
              <tr key={rowIndex}>
                <td className="gx-data-index">{rowIndex + 1}</td>
                {cells.map((cell, index) => <td key={index} className={table.numericColumns[index] ? "is-number" : undefined}>{cell}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.rows.length > PAGE_ROWS && (
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm gx-continue" onClick={() => setShowAll((value) => !value)}>
          {showAll ? "Show fewer rows" : `Show all ${table.rows.length.toLocaleString()} rows`}
        </button>
      )}
    </figure>
  );
}

export function TableBody({ content }: { content: string }) {
  const table = useMemo(() => parseDelimitedTable(content), [content]);
  if (!table) {
    return (
      <div className="gx-reader">
        <p className="gx-item-empty-line">These rows didn’t form a regular table, so they are shown as captured.</p>
        <MarkdownView source={content} />
      </div>
    );
  }
  return <DataTable table={table} caption={DELIMITER_LABEL[table.delimiter]} />;
}
