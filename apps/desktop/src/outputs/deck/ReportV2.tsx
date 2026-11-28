import { Children, isValidElement, useLayoutEffect, useMemo, useRef, useState, type ComponentProps, type ReactElement, type ReactNode } from "react";
import { AnswerBody } from "../../pages/AnswerBody";
import type { OutputPresentation } from "../OutputParts";
import { Chart } from "./Charts";
import type { ExhibitTable, ReportBlock } from "./parseReport";
import { visible } from "./units";
import "./deck.css";
import "./report.css";

type References = Pick<ComponentProps<typeof AnswerBody>, "numbers" | "onCitation">;
type Grid = Extract<ExhibitTable, { layout: "table" }>;
type ChartData = Extract<ExhibitTable, { layout: "chart" }>;

// Give each column room for several characters and the gap between column labels.
const COLUMN_MEASURE = 6;

function tableElements(children: ReactNode, ...tags: string[]) {
  return Children.toArray(children).filter((child): child is ReactElement<{ children?: ReactNode }, string> =>
    isValidElement(child) && typeof child.type === "string" && tags.includes(child.type));
}

function ReportTable({ children, label = "Table", paper }: {
  children: ReactNode; label?: string; paper: boolean;
}) {
  const region = useRef<HTMLDivElement>(null);
  const table = useRef<HTMLTableElement>(null);
  const [layout, setLayout] = useState({ width: 0, overflow: false });
  const [thead] = tableElements(children, "thead");
  const [headingRow] = tableElements(thead?.props.children, "tr");
  const headers = tableElements(headingRow?.props.children, "th");
  const minWidth = headers.length * COLUMN_MEASURE;
  const stack = paper && layout.overflow;
  useLayoutEffect(() => {
    const box = region.current!;
    const grid = table.current;
    const measure = () => {
      const width = box.clientWidth;
      if (!width) return;
      const overflow = grid !== null && (box.scrollWidth > width || grid.scrollWidth > width
        || Array.from(grid.rows).some((row) => Array.from(row.cells).some((cell) => cell.scrollWidth > cell.clientWidth)));
      setLayout((current) => {
        // Paper removes the measured grid when it reflows. Only retry it at a new width.
        if (current.width === width && (!grid || current.overflow === overflow)) return current;
        return { width, overflow };
      });
    };
    measure();
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    resize?.observe(box);
    const content = new MutationObserver(measure);
    if (grid) {
      resize?.observe(grid);
      content.observe(grid, { childList: true, characterData: true, subtree: true });
    }
    document.fonts?.addEventListener("loadingdone", measure);
    return () => {
      resize?.disconnect();
      content.disconnect();
      document.fonts?.removeEventListener("loadingdone", measure);
    };
  }, [minWidth, stack]);

  const rows = stack ? tableElements(children, "tbody").flatMap((group) => tableElements(group.props.children, "tr")) : [];
  const scrollable = layout.overflow && !paper;
  return <div ref={region} className="rp-table" role={scrollable ? "region" : undefined}
    aria-label={scrollable ? label : undefined} tabIndex={scrollable ? 0 : undefined}>
    {stack ? <div className="rp-paper-rows">{(rows.length ? rows : [null]).map((row, index) => {
      const cells = tableElements(row?.props.children, "th", "td");
      return <dl key={index}>{headers.map((heading, column) => <div key={column}>
        <dt>{heading.props.children}</dt>
        <dd>{cells[column]?.props.children}</dd>
      </div>)}</dl>;
    })}</div> : <table ref={table} style={{ minInlineSize: `${minWidth}em` }}>{children}</table>}
  </div>;
}

function ReportMarkdown({ content, paper, ...references }: { content: string; paper: boolean } & References) {
  const table = useMemo(() => function MarkdownTable({ children }: { children?: ReactNode }) {
    return <ReportTable paper={paper}>{children}</ReportTable>;
  }, [paper]);
  return <AnswerBody content={content} table={table} {...references} />;
}

function ExhibitGrid({ grid, label, paper, references }: {
  grid: Grid; label: string; paper: boolean; references: References;
}) {
  return <ReportTable label={label} paper={paper}>
      <thead><tr>{grid.head.map((cell, column) => <th scope="col" key={column} data-align={grid.align[column]}>
        <AnswerBody content={cell} {...references} />
      </th>)}</tr></thead>
      <tbody>{grid.rows.map((row, index) => <tr key={index} className={row.highlight ? "is-highlighted" : undefined}>
        {row.cells.map((cell, column) => {
          const Cell = column === 0 ? "th" : "td";
          return <Cell key={column} scope={column === 0 ? "row" : undefined} data-align={grid.align[column]}>
            <AnswerBody content={cell} {...references} />
          </Cell>;
        })}
      </tr>)}</tbody>
  </ReportTable>;
}

function chartLabelsFit(svg: SVGSVGElement, chart: ChartData) {
  const plot = svg.getBoundingClientRect();
  const categories = new Set(chart.categories.map((_, row) => String(row)));
  const values = new Set(chart.series.flatMap((series, column) => series.values.map((_, row) => `${row}:${column}`)));
  const labels: DOMRect[] = [];
  for (const text of svg.querySelectorAll<SVGTextElement>("text[data-category]")) {
    const { category, series } = text.dataset;
    if (series === undefined) {
      if (!categories.delete(category!)) return false;
      if (text.lastElementChild?.textContent?.endsWith("…")
        && [...text.children].map((line) => line.textContent).join(" ") !== visible(chart.categories[Number(category)]!.label)) return false;
    } else if (!values.delete(`${category}:${series}`)) return false;
    const label = text.getBoundingClientRect();
    if (label.left < plot.left || label.right > plot.right || label.top < plot.top || label.bottom > plot.bottom) return false;
    if (labels.some((other) => label.left < other.right && other.left < label.right
      && label.top < other.bottom && other.top < label.bottom)) return false;
    labels.push(label);
  }
  return categories.size === 0 && values.size === 0;
}

function ExhibitChart({ chart, paper, table }: { chart: ChartData; paper: boolean; table: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState(true);
  // A bar group needs label height and separation for each series plus space between groups.
  const height = chart.kind === "bar" ? Math.max(176, chart.categories.length * (chart.series.length * 25 + 12)) : 272;
  useLayoutEffect(() => {
    const element = box.current!;
    const measure = () => {
      const svg = element.querySelector("svg");
      if (svg?.getBoundingClientRect().width) setFit(chartLabelsFit(svg, chart));
    };
    measure();
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    resize?.observe(element);
    const redraw = new MutationObserver(measure);
    redraw.observe(element, { childList: true, attributes: true, subtree: true });
    document.fonts?.addEventListener("loadingdone", measure);
    return () => {
      resize?.disconnect();
      redraw.disconnect();
      document.fonts?.removeEventListener("loadingdone", measure);
    };
  }, [chart]);

  return <>
    <div ref={box} className={`rp-chart${fit ? "" : " is-measuring"}`} aria-hidden={fit ? undefined : true}
      style={{ blockSize: height }}><Chart data={chart} /></div>
    {fit && !paper ? <details className="rp-data"><summary>Data</summary>{table}</details> : table}
  </>;
}

function Exhibit({ block, paper, references }: {
  block: Extract<ReportBlock, { t: "exhibit" }>; paper: boolean; references: References;
}) {
  if (!block.grid) return <div className="rp-exhibit">
    <p className="rp-exhibit-note">Exhibit data could not be read.</p>
    <ReportMarkdown content={block.raw} paper={paper} {...references} />
  </div>;
  const table = <ExhibitGrid grid={block.grid} label={visible(block.caption) || "Exhibit data"} paper={paper} references={references} />;
  return <figure className="rp-exhibit" data-layout={block.table?.layout}>
    {block.caption && <figcaption><AnswerBody content={block.caption} {...references} /></figcaption>}
    {block.table?.layout === "chart" ? <ExhibitChart chart={block.table} paper={paper} table={table} /> : table}
    {block.source && <div className="rp-source"><AnswerBody content={`Source: ${block.source}`} {...references} /></div>}
  </figure>;
}

export function ReportBlocks({ blocks, presentation, ...references }: {
  blocks: ReportBlock[]; presentation: OutputPresentation;
} & References) {
  const paper = presentation === "print";
  return blocks.map((block, index) => {
    if (block.t === "exhibit") return <Exhibit key={index} block={block} paper={paper} references={references} />;
    if (block.t === "md") return <ReportMarkdown key={index} content={block.text} paper={paper} {...references} />;
    return <aside className="rp-callout" key={index}>
      {block.title && <div className="rp-callout-title"><AnswerBody content={block.title} {...references} /></div>}
      <ReportMarkdown content={block.text} paper={paper} {...references} />
    </aside>;
  });
}
