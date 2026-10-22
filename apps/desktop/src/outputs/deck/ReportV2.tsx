import { useRef, type CSSProperties } from "react";
import { SlideContext } from "../slides/Rich";
import { Chart, useBox } from "./Charts";
import { Inline, Prose } from "./Inline";
import { readBlocks, type ExhibitTable, type ReportBlock } from "./parseReport";
import { deckStyle } from "./theme";
import "./deck.css";
import "./report.css";

const SUMMARY_MARK = /^\s*<!--\s*block:\s*summary\s*-->\s*$/m;

function Table({ table }: { table: ExhibitTable }) {
  if (table.layout === "chart") {
    const head = ["", ...table.series.map((s) => s.name)];
    return <table><thead><tr>{head.map((cell, at) => <th key={at} className={at > 0 ? "is-num" : ""}><Inline text={cell} /></th>)}</tr></thead>
      <tbody>{table.categories.map((c, row) => <tr key={row} className={c.highlight ? "is-hi" : ""}><th scope="row"><Inline text={c.label} /></th>{table.series.map((s, k) => <td key={k} className="is-num">{s.values[row]!.label}</td>)}</tr>)}</tbody></table>;
  }
  return <table><thead><tr>{table.head.map((cell, at) => <th key={at} className={table.align[at] === "right" ? "is-num" : ""}><Inline text={cell} /></th>)}</tr></thead>
    <tbody>{table.rows.map((row, r) => <tr key={r} className={row.highlight ? "is-hi" : ""}>{row.cells.map((cell, c) => c === 0 ? <th scope="row" key={c}><Inline text={cell} /></th> : <td key={c} className={table.align[c] === "right" ? "is-num" : ""}><Inline text={cell} /></td>)}</tr>)}</tbody></table>;
}

/** A chart drawn at its design size (1000 px wide) and scaled to the column it sits in. */
function ScaledChart({ table }: { table: Extract<ExhibitTable, { layout: "chart" }> }) {
  const outer = useRef<HTMLDivElement>(null);
  const { w } = useBox(outer, { w: 640, h: 0 });
  const scale = Math.min(0.8, Math.max(0.4, w / 1000));
  const n = table.categories.length;
  const m = table.series.length;
  // A few rows with long labels read better as horizontal bars; and a chart is never taller than its data needs.
  const kind = table.kind === "column" && n <= 3 && Math.max(...table.categories.map((c) => c.label.length)) > 12 ? "bar" : table.kind;
  const drawn = kind === table.kind ? table : { ...table, kind };
  const height = kind === "bar" ? Math.min(n <= 3 ? 320 : 420, Math.max(180, n * (m > 1 ? 60 : 50) + 70)) : kind === "column" ? (n <= 3 ? 300 : 380) : 360;
  return <div ref={outer} className="rp-chart" style={{ height: height * scale }}>
    <div style={{ width: 1000, height, transform: `scale(${scale})`, display: "flex", flexDirection: "column" }}><Chart data={drawn} /></div>
  </div>;
}

function Block({ block, numbers, onCite }: { block: ReportBlock; numbers: number[]; onCite?: ((index: number) => void) | undefined }) {
  if (block.t === "md") return <Prose markdown={block.text} paragraphs />;
  if (block.t === "callout") return <aside className="rp-callout"><strong>{block.title}</strong><Prose markdown={block.text} paragraphs /></aside>;
  return <figure className="rp-exhibit">
    <figcaption><Inline text={block.caption} /></figcaption>
    {block.table?.layout === "chart" ? <><ScaledChart table={block.table} /><details className="rp-data"><summary>Data</summary><Table table={block.table} /></details></> : block.table ? <Table table={block.table} /> : null}
    {block.source && <p className="rp-source"><Inline text={`Source: ${block.source}`} /></p>}
  </figure>;
}

/**
 * One section of a studio report: its claim heading and its blocks. The executive summary is a
 * box; exhibits are full column width with a caption and a source line; callouts are boxes.
 */
export function ReportV2Section({ text, numbers, onCite }: { text: string; numbers: number[]; onCite?: ((index: number) => void) | undefined }) {
  const [first = "", ...rest] = text.split("\n");
  const summary = SUMMARY_MARK.test(rest.find((line) => line.trim()) ?? "");
  const body = summary ? rest.join("\n").replace(SUMMARY_MARK, "") : rest.join("\n");
  const context = { numbers, sources: [], onCite };
  return <SlideContext.Provider value={context}>
    <div className={`rp${summary ? " is-summary" : ""}`} lang={/[\u3400-\u9fff]/.test(text) ? "zh-CN" : undefined} style={deckStyle("paper", null)}>
      <h2 className="rp-heading"><Inline text={first.replace(/^##\s*/, "")} /></h2>
      <div className={summary ? "rp-summary" : "rp-section"}>
        {readBlocks(body).map((block, at) => <Block key={at} block={block} numbers={numbers} onCite={onCite} />)}
      </div>
    </div>
  </SlideContext.Provider>;
}

export const reportStyle = (): CSSProperties => deckStyle("paper", null);
