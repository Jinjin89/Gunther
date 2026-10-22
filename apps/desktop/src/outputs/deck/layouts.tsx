import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { sourceAssetUrl } from "../../api";
import { Chart } from "./Charts";
import { Inline, Prose } from "./Inline";
import type { DeckData } from "./parseDeck";
import { countUnits } from "./units";

type Of<L extends DeckData["layout"]> = Extract<DeckData, { layout: L }>;
const After = ({ text, className = "" }: { text: string | null | undefined; className?: string }) => text ? <p className={`dk-after ${className}`}><Inline text={text} /></p> : null;
const size = (value: number): CSSProperties => ({ "--dk-size": `${value}px` }) as CSSProperties;

export function Cover({ data }: { data: Of<"title"> | Of<"section"> }) {
  const meta = data.layout === "title" ? data.meta : null;
  const long = countUnits(data.title) > 60;
  return <div className="dk-cover">
    <div className="dk-cover-main">
      <span className="dk-cover-rule" />
      <h1 className={long ? "is-long" : ""}><Inline text={data.title} /></h1>
      {data.subtitle && <p className="dk-cover-sub"><Inline text={data.subtitle} /></p>}
    </div>
    {meta && <p className="dk-cover-meta"><Inline text={meta} /></p>}
  </div>;
}

export function Summary({ data }: { data: Of<"summary"> }) {
  return <div className={`dk-summary n${data.items.length}`}>
    <ol>{data.items.map((item, at) => <li key={at}>
      <span className="dk-num">{String(at + 1).padStart(2, "0")}</span>
      <div>{item.lead && <strong><Inline text={item.lead} /></strong>}<p><Inline text={item.detail} /></p></div>
    </li>)}</ol>
    <After text={data.after} />
  </div>;
}

/** A value fitted to the width it has, between 112 and the size it would like. */
const valueSize = (value: string, room: number, want: number) => Math.max(40, Math.min(want, Math.floor(room / (Math.max(1, countUnits(value)) * 0.6))));

/** A value short enough to stand in the left 45% of the slide, beside its label. */
export const statBeside = (value: string) => countUnits(value) <= 7;

export function Stat({ data }: { data: Of<"stat"> }) {
  const beside = statBeside(data.value);
  return <div className={`dk-stat ${beside ? "is-beside" : ""}`}>
    <div className="dk-stat-figure">
      <p className="dk-stat-value" style={size(valueSize(data.value, beside ? 470 : 1100, 160))}><Inline text={data.value} /></p>
      <span className="dk-stat-rule" />
    </div>
    <div className="dk-stat-words">
      <p className="dk-stat-label"><Inline text={data.label} /></p>
      {data.context && <p className="dk-stat-context"><Inline text={data.context} /></p>}
    </div>
  </div>;
}

export function Stats({ data }: { data: Of<"stats"> }) {
  const n = data.items.length;
  const widest = Math.max(...data.items.map((item) => countUnits(item.value)));
  const mode = n === 1 || widest > 10 || (n === 3 && widest > 7) ? "rows" : n === 4 && widest > 5 ? "grid" : "cols";
  const room = mode === "cols" ? (1120 - (n - 1) * 48) / n : mode === "grid" ? 520 : 440;
  const want = mode === "rows" ? (n >= 4 ? 56 : 80) : mode === "grid" ? 88 : n === 2 ? 144 : n === 3 ? 120 : 104;
  return <div className="dk-stats">
    <ul className={`is-${mode} n${n}`}>{data.items.map((item, at) => <li key={at}>
      <p className="dk-stat-value" style={size(valueSize(item.value, room, want))}><Inline text={item.value} /></p>
      <p className="dk-stat-label"><Inline text={item.label} /></p>
    </li>)}</ul>
    <After text={data.after} />
  </div>;
}

export function ChartLayout({ data }: { data: Of<"chart"> }) {
  return <div className="dk-chartslide"><Chart data={data} /><After text={data.after} className="is-takeaway" /></div>;
}

export function TableLayout({ data }: { data: Of<"table"> }) {
  const cols = data.head.length;
  const cells = data.rows.flatMap((row) => row.cells);
  const longest = Math.max(...cells.map((cell) => countUnits(cell)), ...data.head.map((cell) => countUnits(cell)));
  const dense = data.rows.length * cols;
  const font = dense > 20 || longest > 36 ? 20 : dense > 12 || longest > 24 ? 22 : dense <= 9 && longest <= 18 ? 28 : 24;
  return <div className={`dk-tableslide ${data.rows.length <= 5 ? "is-fill" : "is-dense"}`}>
    <table style={{ "--dk-cell": `${font}px`, "--dk-fill": `${data.rows.length * 92 + 64}px` } as CSSProperties}>
      <thead><tr>{data.head.map((cell, at) => <th key={at} style={{ textAlign: data.align[at] ?? "left" }}><Inline text={cell} /></th>)}</tr></thead>
      <tbody>{data.rows.map((row, r) => <tr key={r} className={row.highlight ? "is-hi" : ""}>{row.cells.map((cell, at) => <td key={at} style={{ textAlign: data.align[at] ?? "left" }}><Inline text={cell} /></td>)}</tr>)}</tbody>
    </table>
    <After text={data.after} className="is-takeaway" />
  </div>;
}

export function Compare({ data }: { data: Of<"compare"> }) {
  const most = Math.max(...data.sides.map((side) => side.items.length));
  const total = data.sides.reduce((sum, side) => sum + side.items.reduce((inner, item) => inner + countUnits(item), 0), 0);
  return <div className={`dk-compare ${most >= 4 ? "is-dense" : most <= 2 && total <= 400 ? (total > 200 ? "is-roomy is-md" : "is-roomy") : ""}`}>
    <div className="dk-sides">{data.sides.map((side, at) => <section key={at}>
      <h3><Inline text={side.name} /></h3>
      <ul>{side.items.map((item, i) => <li key={i}><span><Inline text={item} /></span></li>)}</ul>
    </section>)}</div>
    <After text={data.after} className="is-takeaway" />
  </div>;
}

const totalUnits = (...parts: Array<string | null | undefined>) => parts.reduce((sum, part) => sum + countUnits(part ?? ""), 0);

export function Process({ data }: { data: Of<"process"> }) {
  const n = data.items.length;
  const roomy = n <= 4 && totalUnits(...data.items.flatMap((item) => [item.lead, item.detail])) <= 220;
  return <div className={`dk-process ${roomy ? "is-roomy" : ""}`}>
    <ol className={`n${n}`}>{data.items.map((item, at) => <li key={at}>
      <span className="dk-step"><b>{at + 1}</b></span>
      <div className="dk-step-text">{item.lead && <strong><Inline text={item.lead} /></strong>}
      <p><Inline text={item.detail} /></p></div>
    </li>)}</ol>
    <After text={data.after} />
  </div>;
}

export function Timeline({ data }: { data: Of<"timeline"> }) {
  const n = data.items.length;
  const roomy = n <= 4 && totalUnits(...data.items.map((item) => item.event)) <= 160;
  return <div className={`dk-timeline ${roomy ? "is-roomy" : ""}`}>
    <ol className={`n${n}`}>{data.items.map((item, at) => <li key={at}>
      <strong><Inline text={item.when} /></strong>
      <span className="dk-dot" />
      <p><Inline text={item.event} /></p>
    </li>)}</ol>
    <After text={data.after} />
  </div>;
}

export function Quote({ data }: { data: Of<"quote"> }) {
  const units = countUnits(data.quote);
  const px = units <= 90 ? 54 : units <= 150 ? 44 : 36;
  return <div className="dk-quote">
    <span className="dk-mark" aria-hidden="true">“</span>
    <blockquote style={size(px)}><Inline text={data.quote} /></blockquote>
    {data.attribution && <p className="dk-attrib"><Inline text={data.attribution} /></p>}
    <After text={data.after} />
  </div>;
}

export function Figure({ data }: { data: Of<"figure"> }) {
  const [missing, setMissing] = useState(false);
  useEffect(() => setMissing(false), [data.assetId]);
  return <div className="dk-figure">
    <figure>
      <div className="dk-picture">{missing
        ? <span className="dk-missing" role="img" aria-label={data.alt}>{data.alt || "The figure could not be loaded."}</span>
        : <img src={sourceAssetUrl(data.assetId)} alt={data.alt} onError={() => setMissing(true)} />}</div>
      {data.alt && <figcaption>{data.alt}</figcaption>}
    </figure>
    <After text={data.takeaway} className="is-takeaway" />
  </div>;
}

export function Statement({ data }: { data: Of<"statement"> }) {
  const units = countUnits(data.statement);
  const px = units <= 70 ? 56 : units <= 120 ? 50 : 46;
  return <div className="dk-statement">
    <p className="dk-big" style={size(px)}><Inline text={data.statement} /></p>
    {data.items.length > 0 && <ul className={`n${data.items.length}`}>{data.items.map((item, at) => <li key={at}><Inline text={item} /></li>)}</ul>}
    <After text={data.after} />
  </div>;
}

export function Points({ data }: { data: Of<"points"> }) {
  const n = data.items.length;
  const roomy = totalUnits(...data.items.flatMap((item) => [item.lead, item.detail])) <= 240;
  return <div className={`dk-points ${roomy ? "is-roomy" : ""}`}>
    <ul className={`n${n}`}>{data.items.map((item, at) => <li key={at}>
      {item.lead && <strong><Inline text={item.lead} /></strong>}
      <p><Inline text={item.detail} /></p>
    </li>)}</ul>
    <After text={data.after} />
  </div>;
}

export function Actions({ data }: { data: Of<"actions"> }) {
  const roomy = totalUnits(...data.items.flatMap((item) => [item.lead, item.detail])) <= 200;
  return <div className={`dk-actions n${data.items.length} ${roomy ? "is-roomy" : ""}`}>
    <ol>{data.items.map((item, at) => <li key={at}>
      <span className="dk-badge">{at + 1}</span>
      <div>{item.lead && <strong><Inline text={item.lead} /></strong>}{item.detail && <p><Inline text={item.detail} /></p>}</div>
    </li>)}</ol>
    <After text={data.after} />
  </div>;
}

export function TextFallback({ data }: { data: Of<"text"> }) {
  return <div className="dk-text"><Prose markdown={data.markdown} /></div>;
}

export function Layout({ data }: { data: DeckData }): ReactNode {
  switch (data.layout) {
    case "title": case "section": return <Cover data={data} />;
    case "summary": return <Summary data={data} />;
    case "stat": return <Stat data={data} />;
    case "stats": return <Stats data={data} />;
    case "chart": return <ChartLayout data={data} />;
    case "table": return <TableLayout data={data} />;
    case "compare": return <Compare data={data} />;
    case "process": return <Process data={data} />;
    case "timeline": return <Timeline data={data} />;
    case "quote": return <Quote data={data} />;
    case "figure": return <Figure data={data} />;
    case "statement": return <Statement data={data} />;
    case "points": return <Points data={data} />;
    case "actions": return <Actions data={data} />;
    case "text": return <TextFallback data={data} />;
  }
}
