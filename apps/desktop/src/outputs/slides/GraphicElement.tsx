import { type CSSProperties, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Rich } from "./Rich";
import { parseGraphicBlock, plainText, type ChartShape, type DiagramShape, type FlowShape, type GraphicShape, type GraphicLayout } from "./parseSlide";
import type { SceneLeaf, SceneEnvelope, SlideBlock } from "./scene";

interface GraphicElementProps {
  node: SceneLeaf;
  block: SlideBlock;
  scene: SceneEnvelope;
  graphicsValid: boolean;
  numbers: number[];
  onIssue: (blockId: string, issue: string | null) => void;
}
interface Size { width: number; height: number }
type Point = { x: number; y: number };
interface DiagramLayout { width: number; height: number; nodeWidth: number; nodeHeight: number; positions: Map<string, Point>; edges: Array<{ from: Point; to: Point; label: Point; path: string; labelWidth: number; labelHeight: number }> }
type FlowOrientation = "flow-row" | "column";

function chartCitationsIssue(shape: GraphicShape, numbers: number[]): string | null {
  if (shape.layout === "bar_chart" || shape.layout === "line_chart") {
    if (shape.points.some((point) => point.cites.length === 0)) return "every plotted value needs a source citation";
    if (shape.points.some((point) => point.cites.some((ref) => !numbers.includes(ref)))) return "a plotted value cites an unavailable source";
  }
  if (shape.layout === "diagram" && shape.edges.some((edge) => edge.refs.length === 0 || edge.refs.some((ref) => !numbers.includes(ref)))) return "every relationship needs an available source citation";
  return null;
}
function chartLabelWidth(raw: string, font = 8) { return [...plainText(raw)].reduce((sum, char) => sum + (/\p{Script=Han}/u.test(char) ? font : font * 0.58), 0); }
function graphicTextWidth(raw: string, font: number) {
  const marks = [...raw.matchAll(/\[(?:(i|p|d):)?(\d+(?:\s*[,，、]\s*\d+)*)\]|\[\?\]/g)];
  const words = chartLabelWidth(raw.replace(/\[(?:(?:i|p|d):)?\d+(?:\s*[,，、]\s*\d+)*\]|\[\?\]/g, ""), font);
  const markWidth = marks.reduce((sum, match) => {
    if (match[0] === "[?]") return sum + chartLabelWidth("unverified", font);
    const numbers = (match[2] ?? "").match(/\d+/g) ?? [];
    const citations = numbers.reduce((width, number) => width + chartLabelWidth(`[${number}]`, font), 0);
    if (match[1] === "i") return sum + chartLabelWidth("inference · from", font) + citations;
    if (match[1] === "d") return sum + chartLabelWidth("disputed · by", font) + citations;
    return sum + citations;
  }, 0);
  return words + markWidth + marks.length * font * 0.3;
}

export function diagramLayout(shape: DiagramShape, width: number, height: number): DiagramLayout | null {
  const maxLayer = Math.max(...shape.layers.map((layer) => layer.length));
  const labelTextWidths = shape.edges.map((edge) => graphicTextWidth(edge.relationship, 13));
  const labelWidths = labelTextWidths.map((textWidth) => Math.min(116, Math.max(44, textWidth + 12)));
  const labelHeights = labelTextWidths.map((textWidth, index) => Math.ceil(textWidth / Math.max(1, (labelWidths[index] ?? 44) - 6)) * 17 + 6);
  const widestLabel = Math.max(44, ...labelWidths);
  const tallestLabel = Math.max(36, ...labelHeights);
  for (const horizontal of [true, false]) {
    const rankCount = shape.layers.length;
    const rankExtent = horizontal ? width : height;
    const withinExtent = horizontal ? height : width;
    const nodeWidth = horizontal
      ? Math.min(154, (width - 16 - (rankCount - 1) * (widestLabel + 16)) / rankCount)
      : Math.min(176, width / maxLayer - 18);
    const nodeHeight = horizontal
      ? Math.min(64, (height - 16) / maxLayer - 12)
      : Math.min(62, (height - 16 - (rankCount - 1) * (tallestLabel + 16)) / rankCount);
    if (nodeWidth < 96 || nodeHeight < 46) continue;
    if (shape.nodes.some((node) => chartLabelWidth(node.label, 16) / (nodeWidth - 14) * 20 > nodeHeight - 10)) continue;
    if (shape.edges.some((edge, index) => (labelTextWidths[index] ?? 0) > 132 || (labelHeights[index] ?? 0) > 54)) continue;
    const positions = new Map<string, Point>();
    shape.layers.forEach((layer, rank) => layer.forEach((key, index) => {
      const nodeWidthForLayer = layer.length === 1 ? 0 : (width - nodeWidth - 16) / (layer.length - 1);
      const nodeHeightForLayer = layer.length === 1 ? 0 : (height - nodeHeight - 16) / (layer.length - 1);
      positions.set(key, horizontal
        ? { x: 8 + nodeWidth / 2 + rank * ((width - 16 - nodeWidth) / Math.max(1, rankCount - 1)), y: layer.length === 1 ? height / 2 : 8 + nodeHeight / 2 + index * nodeHeightForLayer }
        : { x: layer.length === 1 ? width / 2 : 8 + nodeWidth / 2 + index * nodeWidthForLayer, y: 8 + nodeHeight / 2 + rank * ((height - 16 - nodeHeight) / Math.max(1, rankCount - 1)) });
    }));
    const edges = shape.edges.map((edge) => {
      const from = positions.get(edge.fromKey); const to = positions.get(edge.toKey);
      if (!from || !to) return null;
      const label = horizontal ? { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 } : { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 };
      const path = horizontal
        ? `M ${from.x + nodeWidth / 2} ${from.y} H ${label.x} V ${to.y} H ${to.x - nodeWidth / 2}`
        : `M ${from.x} ${from.y + nodeHeight / 2} V ${label.y} H ${to.x} V ${to.y - nodeHeight / 2}`;
      const labelWidth = labelWidths[shape.edges.indexOf(edge)] ?? 44;
      return { from, to, label, path, labelWidth, labelHeight: labelHeights[shape.edges.indexOf(edge)] ?? 36 };
    });
    if (edges.some((edge) => !edge)) continue;
    const boxes = [...positions.values()].map((at) => ({ x: at.x - nodeWidth / 2 - 8, y: at.y - nodeHeight / 2 - 8, w: nodeWidth + 16, h: nodeHeight + 16 }));
    const labelBoxes = (edges as NonNullable<typeof edges[number]>[]).map(({ label, labelWidth, labelHeight }) => ({ x: label.x - labelWidth / 2, y: label.y - labelHeight / 2, w: labelWidth, h: labelHeight }));
    const overlaps = (a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
    if (labelBoxes.some((label) => boxes.some((node) => overlaps(label, node)))) continue;
    if (labelBoxes.some((label, index) => labelBoxes.slice(index + 1).some((other) => overlaps(label, other)))) continue;
    return { width, height, nodeWidth, nodeHeight, positions, edges: edges as NonNullable<typeof edges[number]>[] };
  }
  return null;
}

function FlowGraphic({ shape, layout }: { shape: FlowShape; layout: "row" | "column" }) {
  return <div className={`outputs-graphic-flow is-${layout}`}>
    {shape.steps.map((step, index) => <div className="outputs-graphic-flow-step" key={index}>
      <span className="outputs-graphic-flow-number">{step.number}</span>
      <div className="outputs-graphic-flow-copy">{step.title && <div className="outputs-graphic-flow-title"><Rich text={step.title} /></div>}{step.detail && <div className="outputs-graphic-flow-detail"><Rich text={step.detail} /></div>}</div>
    </div>)}
  </div>;
}
function DiagramGraphic({ shape, layout }: { shape: DiagramShape; layout: DiagramLayout }) {
  const id = `graphic-arrow-${useId().replace(/:/g, "")}`;
  const incoming = new Set(shape.edges.map((edge) => edge.toKey));
  return <div className="outputs-graphic-diagram" style={{ "--graphic-width": `${layout.width}px`, "--graphic-height": `${layout.height}px`, "--graphic-node-width": `${layout.nodeWidth}px`, "--graphic-node-height": `${layout.nodeHeight}px` } as CSSProperties}>
    <svg viewBox={`0 0 ${layout.width} ${layout.height}`} preserveAspectRatio="none" aria-hidden="true">
      <defs><marker id={id} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" /></marker></defs>
      {layout.edges.map((edge, index) => <path key={index} d={edge.path} markerEnd={`url(#${id})`} />)}
    </svg>
    {shape.nodes.map((node) => { const at = layout.positions.get(node.key)!; return <div key={node.key} className={`outputs-graphic-diagram-node${incoming.has(node.key) ? "" : " is-origin"}`} style={{ left: `${at.x / layout.width * 100}%`, top: `${at.y / layout.height * 100}%` }}><Rich text={node.label} /></div>; })}
    {shape.edges.map((edge, index) => { const placed = layout.edges[index]!; const at = placed.label; return <div key={index} className="outputs-graphic-diagram-edge" style={{ left: `${at.x / layout.width * 100}%`, top: `${at.y / layout.height * 100}%`, width: `${placed.labelWidth}px`, height: `${placed.labelHeight}px` }}><Rich text={edge.relationship} /></div>; })}
    <ol className="outputs-sr-only" aria-label="Diagram relationships">{shape.edges.map((edge, index) => <li key={index}>{edge.from} — {plainText(edge.relationship)} — {edge.to}; sources {edge.refs.join(", ")}</li>)}</ol>
  </div>;
}

function BarGraphic({ shape }: { shape: ChartShape }) {
  const values = shape.points.map((point) => point.value);
  const low = Math.min(0, ...values); const high = Math.max(0, ...values);
  const extent = high - low || 1; const zero = (0 - low) / extent * 100;
  return <div className="outputs-graphic-chart">
    <div className="outputs-graphic-chart-heading"><Rich text={shape.axis} /><Rich text={shape.measure} /></div>
    <div className="outputs-graphic-bars" style={{ "--graphic-zero": `${zero}%` } as CSSProperties}>
      {shape.points.map((point, index) => {
        const position = (point.value - low) / extent * 100;
        return <div className="outputs-graphic-bar-row" key={index}>
          <div className="outputs-graphic-bar-label"><Rich text={point.label} /></div>
          <div className="outputs-graphic-bar-track"><i className="outputs-graphic-bar-zero" /><span className={`outputs-graphic-bar${point.value < 0 ? " is-negative" : ""}`} style={{ left: `${Math.min(zero, position)}%`, width: `${Math.abs(position - zero)}%` }} /></div>
          <div className="outputs-graphic-bar-value"><Rich text={`${point.text} ${point.marks}`.trim()} /></div>
        </div>;
      })}
    </div>
  </div>;
}
function LineGraphic({ shape, size }: { shape: ChartShape; size: Size }) {
  const points = shape.points; const margin = { left: 24, right: 24, top: 14, bottom: 30 };
  const headingHeight = 34; const rowsHeight = points.length * 25 + 8;
  const svgHeight = Math.max(120, size.height - headingHeight - rowsHeight);
  const width = size.width - margin.left - margin.right; const plotHeight = svgHeight - margin.top - margin.bottom;
  const values = points.map((point) => point.value); const low = Math.min(0, ...values); const high = Math.max(0, ...values);
  const xValues = points.map((point, index) => point.x ?? index);
  const xLow = Math.min(...xValues); const xHigh = Math.max(...xValues);
  const x = (value: number) => margin.left + (xHigh === xLow ? width / 2 : (value - xLow) / (xHigh - xLow) * width);
  const y = (value: number) => margin.top + (high === low ? plotHeight / 2 : (high - value) / (high - low) * plotHeight);
  const path = points.map((point, index) => `${index ? "L" : "M"} ${x(xValues[index] ?? index)} ${y(point.value)}`).join(" ");
  const zeroY = y(0);
  return <div className="outputs-graphic-chart">
    <div className="outputs-graphic-chart-heading"><Rich text={shape.axis} /><Rich text={shape.measure} /></div>
    <svg className="outputs-graphic-line" viewBox={`0 0 ${size.width} ${svgHeight}`} preserveAspectRatio="none" aria-hidden="true">
      <line className="is-zero" x1={margin.left} x2={size.width - margin.right} y1={zeroY} y2={zeroY} />
      <path d={path} />
      {points.map((point, index) => <circle key={index} cx={x(xValues[index] ?? index)} cy={y(point.value)} r="5" />)}
      {points.map((point, index) => <text key={index} x={x(xValues[index] ?? index)} y={svgHeight - 7} textAnchor="middle">{plainText(point.label)}</text>)}
    </svg>
    <div className="outputs-graphic-line-values">{points.map((point, index) => <div key={index}><span>{plainText(point.label)}</span><Rich text={`${point.text} ${point.marks}`.trim()} /></div>)}</div>
  </div>;
}
function ContentShape({ shape, layout, size }: { shape: GraphicShape; layout: GraphicLayout | FlowOrientation | DiagramLayout; size: Size }) {
  if (shape.layout === "flow") return <FlowGraphic shape={shape} layout={layout === "flow-row" ? "row" : "column"} />;
  if (shape.layout === "diagram" && typeof layout === "object") return <DiagramGraphic shape={shape} layout={layout} />;
  if (shape.layout === "bar_chart") return <BarGraphic shape={shape} />;
  if (shape.layout === "line_chart") return <LineGraphic shape={shape} size={size} />;
  return null;
}
function chooseLayout(shape: GraphicShape, size: Size): GraphicLayout | FlowOrientation | DiagramLayout | null {
  const { width, height } = size;
  if (shape.layout === "flow") {
    const horizontalWidths = width / shape.steps.length - 62;
    const horizontalHeights = shape.steps.map((step) => {
      const lines = Math.ceil(Math.max(chartLabelWidth(step.title ?? "", 17), chartLabelWidth(step.detail, 17)) / Math.max(50, horizontalWidths));
      return 54 + lines * 22;
    });
    const verticalLines = shape.steps.map((step) => Math.ceil((chartLabelWidth(step.title ?? "", 17) + chartLabelWidth(step.detail, 17)) / Math.max(70, width - 72)));
    const verticalHeight = verticalLines.reduce((sum, lines) => sum + 24 + lines * 22, 0) + (shape.steps.length - 1) * 12;
    const horizontal = width / shape.steps.length >= 118 && height >= Math.max(118, ...horizontalHeights);
    const vertical = width >= 150 && height >= Math.max(shape.steps.length * 62 + 12 * (shape.steps.length - 1), verticalHeight);
    if (!horizontal && !vertical) return null;
    if (vertical && (!horizontal || verticalHeight <= Math.max(118, ...horizontalHeights) || width < height * 1.15)) return "column";
    return "flow-row";
  }
  if (shape.layout === "diagram") return diagramLayout(shape, width, height);
  if (shape.layout === "bar_chart") {
    const labelWidth = width * 0.24; const valueWidth = width * 0.2;
    const rowHeights = shape.points.map((point) => Math.max(
      Math.ceil(chartLabelWidth(point.label, 16) / labelWidth),
      Math.ceil(chartLabelWidth(`${point.text} ${point.marks}`, 14) / valueWidth),
      1,
    ) * 23);
    const requiredHeight = 42 + rowHeights.reduce((sum, row) => sum + row, 0) + (shape.points.length - 1) * 7;
    return width >= 270 && height >= Math.max(shape.points.length * 45 + 46, requiredHeight) ? shape.layout : null;
  }
  const xValues = shape.points.map((point, index) => point.x ?? index);
  const span = Math.max(...xValues) - Math.min(...xValues) || 1;
  const plotWidth = width - 48;
  const labelsFit = shape.points.every((point, index) => {
    const adjacent = [xValues[index - 1], xValues[index + 1]].filter((value): value is number => value !== undefined)
      .map((value) => Math.abs(value - (xValues[index] ?? 0)) / span * plotWidth);
    return chartLabelWidth(point.label, 12) <= Math.min(...adjacent, plotWidth) * 0.82;
  });
  return width >= Math.max(270, shape.points.length * 56) && height >= 162 + shape.points.length * 25 + 42 && labelsFit ? shape.layout : null;
}

export function GraphicElement({ node, block, scene, graphicsValid, numbers, onIssue }: GraphicElementProps) {
  const host = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<Size>({ width: 0, height: 0 });
  useLayoutEffect(() => {
    const element = host.current;
    if (!element) return undefined;
    const update = (width: number, height: number) => setSize((old) => old.width === width && old.height === height ? old : { width, height });
    update(element.clientWidth, element.clientHeight);
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver((entries) => { const rect = entries[0]?.contentRect; if (rect) update(rect.width, rect.height); });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const shape = useMemo(() => parseGraphicBlock(node.view as GraphicLayout, block.markdown), [node.view, block.markdown]);
  const permission = scene.version === 2 && graphicsValid && scene.graphics[block.id] === node.view;
  const shapeIssue = shape ? chartCitationsIssue(shape, numbers) : "the source block does not meet this graphic's structure requirements";
  const chosenLayout = shape && !shapeIssue && permission && size.width > 0 && size.height > 0 ? chooseLayout(shape, size) : null;
  const issue = !permission ? "its verified graphic permission is unavailable" : shapeIssue ?? (size.width > 0 && size.height > 0 && !chosenLayout ? "the available area is too small for complete labels" : null);
  useLayoutEffect(() => { onIssue(block.id, issue); }, [block.id, issue, onIssue]);
  return <div ref={host} className={`outputs-composed-leaf is-${node.view} is-${node.size} role-${node.role} align-${node.align}`} data-block-id={node.blockId} data-graphic-view={node.view} data-graphic-rendered={chosenLayout ? node.view : "source"} data-graphic-fallback={chosenLayout ? undefined : issue ? "true" : "pending"}>
    {chosenLayout && shape
      ? <ContentShape shape={shape} layout={chosenLayout} size={size} />
      : <Rich text={block.markdown} />}
  </div>;
}
