import { flextree } from "d3-flextree";
import { select } from "d3-selection";
import { zoom, zoomIdentity, type ZoomTransform } from "d3-zoom";
import { Minus, Plus } from "lucide-react";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import type { MapNode } from "./MindMapView";

type Size = { width: number; height: number };
type Box = Size & { x: number; y: number };
type Point = [number, number];
type Metrics = { size: Size; rtl: boolean; nodes: Map<number, Size> };

const COLUMN_GAP = 32;
const GUTTER = 24;
const READING_SCALE = 12 / 14; // The reference size and the map's regular label size scale together.
const MIN_ZOOM = 1 / 8;
const MAX_ZOOM = 4;
const ZOOM_STEP = Math.pow(2, 1 / 4);

function layoutMap(roots: MapNode[], visible: MapNode[], metrics: Metrics) {
  if (visible.some((node) => !metrics.nodes.has(node.position))) return null;
  const positions = new Set(visible.map(({ position }) => position));
  const columnWidths = new Map<number, number>();
  for (const { depth, position } of visible) {
    columnWidths.set(depth, Math.max(columnWidths.get(depth) ?? 0, metrics.nodes.get(position)!.width));
  }
  const layout = flextree<MapNode | null>({
    nodeSize: ({ data }) => data
      ? [metrics.nodes.get(data.position)!.height, columnWidths.get(data.depth)! + COLUMN_GAP]
      : [0, 0],
    spacing: (left, right) => left.parent === right.parent ? 12 : 24,
  });
  // A virtual parent lets older documents with several depth-zero rows use the same layout.
  const tree = layout(layout.hierarchy(null, (node) => node
    ? node.children.filter((child) => positions.has(child.position))
    : roots));
  const boxes = new Map<number, Box>();
  for (const { data, x, y } of tree.descendants()) {
    if (!data) continue;
    const size = metrics.nodes.get(data.position)!;
    boxes.set(data.position, { ...size, x: metrics.rtl ? -y - size.width : y, y: x - size.height / 2 });
  }
  return boxes;
}

function fitMap(boxes: Map<number, Box>, size: Size) {
  const values = [...boxes.values()];
  const left = Math.min(...values.map(({ x }) => x));
  const top = Math.min(...values.map(({ y }) => y));
  const width = Math.max(...values.map(({ x, width }) => x + width)) - left;
  const height = Math.max(...values.map(({ y, height }) => y + height)) - top;
  const margin = Math.min(GUTTER, size.width / 4, size.height / 4);
  const scale = Math.min(1, (size.width - 2 * margin) / Math.max(1, width), (size.height - 2 * margin) / Math.max(1, height));
  return zoomIdentity.translate((size.width - width * scale) / 2 - left * scale, (size.height - height * scale) / 2 - top * scale)
    .scale(scale);
}

function leadingEdge(box: Box, rtl: boolean): Point {
  return [rtl ? box.x + box.width : box.x, box.y + box.height / 2];
}

function moveTo(point: Point, destination: Point, scale: number) {
  return zoomIdentity.translate(destination[0] - scale * point[0], destination[1] - scale * point[1]).scale(scale);
}

function revealBox(view: ZoomTransform, box: Box, size: Size, rtl: boolean) {
  const margin = Math.min(GUTTER / 2, size.width / 4, size.height / 4);
  const shift = (start: number, size: number, extent: number, reversed = false) => {
    if (size > extent - 2 * margin) return (reversed ? extent - margin - size : margin) - start;
    const visibleStart = Math.max(margin, Math.min(start, extent - margin - size));
    return visibleStart - start;
  };
  return zoomIdentity.translate(view.x + shift(view.applyX(box.x), box.width * view.k, size.width, rtl),
    view.y + shift(view.applyY(box.y), box.height * view.k, size.height)).scale(view.k);
}

export default function MapCanvas({ roots, visible, selected, focus, onInitialView, renderNode }: {
  roots: MapNode[];
  visible: MapNode[];
  selected: number | undefined;
  focus: { position: number } | null;
  onInitialView: (narrow: boolean) => void;
  renderNode: (node: MapNode, style: CSSProperties) => ReactNode;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const scene = useRef<HTMLDivElement>(null);
  const previous = useRef<{ boxes: Map<number, Box>; rtl: boolean } | null>(null);
  const current = useRef(zoomIdentity);
  const lastFocus = useRef<typeof focus>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [scale, setScale] = useState(1);

  const behavior = useMemo(() => zoom<HTMLDivElement, unknown>()
    .extent(function () { return [[0, 0], [this.clientWidth, this.clientHeight]]; })
    .filter((event) => {
      if (event.type === "wheel") return event.ctrlKey;
      return !event.button && !(event.target instanceof Element && event.target.closest(".mind-map-node"));
    })
    .on("zoom", (event) => {
      const transform: ZoomTransform = event.transform;
      current.current = transform;
      if (scene.current) scene.current.style.transform = `translate(${transform.x}px, ${transform.y}px) scale(${transform.k})`;
      setScale(transform.k);
    }), []);
  useLayoutEffect(() => {
    const element = select(viewport.current!);
    element.call(behavior).on("dblclick.zoom", null);
    return () => { element.interrupt().on(".zoom", null); };
  }, [behavior]);

  useLayoutEffect(() => {
    const view = viewport.current!;
    const elements = [...scene.current!.querySelectorAll<HTMLElement>(".mind-map-node")];
    const page = view.closest<HTMLElement>(".library-middle-page");
    const reader = view.closest(".output-reader");
    const detailBar = view.parentElement?.querySelector<HTMLElement>(".mind-map-selection-bar");
    const measure = () => {
      if (page?.clientHeight) {
        const top = view.getBoundingClientRect().top - page.getBoundingClientRect().top + page.scrollTop;
        const available = page.clientHeight - top - GUTTER - (detailBar?.offsetHeight ?? 0) - 8;
        view.style.setProperty("--map-available-height", `${available}px`);
      }
      const size = { width: view.clientWidth, height: view.clientHeight };
      const rtl = getComputedStyle(view).direction === "rtl";
      // Offset dimensions are border-box measurements in CSS pixels, independent of pan and zoom.
      const nodes = new Map(elements.map((element) => [Number(element.dataset.nodePosition),
        { width: element.offsetWidth, height: element.offsetHeight }]));
      setMetrics((old) => old?.size.width === size.width && old.size.height === size.height && old.rtl === rtl
        && old.nodes.size === nodes.size && [...nodes].every(([id, box]) =>
          old.nodes.get(id)?.width === box.width && old.nodes.get(id)?.height === box.height)
        ? old : { size, rtl, nodes });
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      if (frame) return;
      frame = requestAnimationFrame(() => { frame = 0; measure(); });
    });
    for (const element of [view, ...elements, page, reader, detailBar]) {
      if (element) observer.observe(element, { box: "border-box" });
    }
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [visible]);

  const boxes = useMemo(() => metrics && layoutMap(roots, visible, metrics), [roots, visible, metrics]);
  const size = metrics?.size;
  const rtl = metrics?.rtl ?? false;
  const fit = useMemo(() => boxes && size?.width && size.height ? fitMap(boxes, size) : null, [boxes, size]);
  const minimum = Math.min(MIN_ZOOM, fit?.k ?? 1);
  const selectedBox = selected === undefined ? undefined : boxes?.get(selected);

  useLayoutEffect(() => {
    if (!boxes) return;
    const pendingFocus = focus && focus !== lastFocus.current ? boxes.get(focus.position) : undefined;
    if (fit && size) {
      const element = select(viewport.current!);
      const prior = previous.current;
      let transform = current.current;
      behavior.scaleExtent([minimum, MAX_ZOOM]);
      if (!prior) {
        const main = roots[0]!;
        const root = boxes.get(main.position)!;
        const branches = main.children.map(({ position }) => boxes.get(position)?.width ?? Infinity);
        const narrow = branches.length > 0 &&
          (root.width + COLUMN_GAP + Math.min(...branches)) * READING_SCALE + 2 * GUTTER > size.width;
        onInitialView(narrow);
        transform = fit.k >= READING_SCALE ? fit : moveTo(leadingEdge(root, rtl),
          [rtl ? size.width - GUTTER : GUTTER, size.height / 2], READING_SCALE);
        if (selectedBox && selected !== main.position) transform = revealBox(transform, selectedBox, size, rtl);
      } else if (prior.boxes !== boxes && selected !== undefined && selectedBox) {
        const oldBox = prior.boxes.get(selected);
        if (oldBox) {
          const anchored = moveTo(leadingEdge(selectedBox, rtl),
            transform.apply(leadingEdge(oldBox, prior.rtl)), transform.k);
          transform = revealBox(anchored, selectedBox, size, rtl);
        }
      }
      if (pendingFocus) transform = revealBox(transform, pendingFocus, size, rtl);
      if (transform.x !== current.current.x || transform.y !== current.current.y || transform.k !== current.current.k) {
        element.interrupt().call(behavior.transform, transform);
      }
      previous.current = { boxes, rtl };
    }
    if (pendingFocus) {
      lastFocus.current = focus;
      scene.current?.querySelector<HTMLElement>(`[data-node-position="${focus!.position}"]`)?.focus({ preventScroll: true });
    }
  }, [behavior, boxes, fit, focus, minimum, onInitialView, roots, rtl, selected, selectedBox, size]);

  const apply = (transform: ZoomTransform) => {
    const view = viewport.current!;
    const selection = select(view).interrupt();
    const duration = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
      ? 0 : Number.parseFloat(getComputedStyle(view).getPropertyValue("--gx-dur")) || 180;
    if (duration) selection.transition().duration(duration).call(behavior.transform, transform);
    else selection.call(behavior.transform, transform);
  };
  const scaleTo = (factor: number) => {
    if (!size) return;
    const center: Point = [size.width / 2, size.height / 2];
    const view = current.current;
    const point = selectedBox ? leadingEdge(selectedBox, rtl) : view.invert(center);
    const screen = view.apply(point);
    const inView = screen[0] >= 0 && screen[0] <= size.width && screen[1] >= 0 && screen[1] <= size.height;
    const transform = moveTo(point, inView ? screen : center, Math.max(minimum, Math.min(MAX_ZOOM, factor)));
    apply(selectedBox ? revealBox(transform, selectedBox, size, rtl) : transform);
  };

  const drawing = useMemo(() => <>
    <svg aria-hidden="true" className="mind-map-links">
      {visible.map((node) => {
        const parent = node.parents.at(-1);
        const source = parent === undefined ? undefined : boxes?.get(parent);
        const target = boxes?.get(node.position);
        if (!source || !target) return null;
        const from = source.x + (rtl ? 0 : source.width);
        const to = target.x + (rtl ? target.width : 0);
        const fromY = source.y + source.height / 2;
        const toY = target.y + target.height / 2;
        const middle = (from + to) / 2;
        return <path key={node.position} d={`M ${from} ${fromY} C ${middle} ${fromY}, ${middle} ${toY}, ${to} ${toY}`} />;
      })}
    </svg>
    {visible.map((node) => {
      const box = boxes?.get(node.position);
      return renderNode(node, { left: box?.x ?? 0, top: box?.y ?? 0 });
    })}
  </>, [boxes, renderNode, rtl, visible]);

  return <>
    <div className="mind-map-zoom" role="group" aria-label="Map zoom">
      <button type="button" className="gx-icon-button" aria-label="Zoom out" disabled={!fit || scale <= minimum}
        onClick={() => scaleTo(current.current.k / ZOOM_STEP)}><Minus size={16} aria-hidden="true" /></button>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" aria-label="Actual size" disabled={!fit}
        onClick={() => scaleTo(1)}>{Number((scale * 100).toFixed(scale >= 0.1 ? 0 : 1))}%</button>
      <button type="button" className="gx-icon-button" aria-label="Zoom in" disabled={!fit || scale >= MAX_ZOOM}
        onClick={() => scaleTo(current.current.k * ZOOM_STEP)}><Plus size={16} aria-hidden="true" /></button>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={!fit} onClick={() => { if (fit) apply(fit); }}>Fit</button>
    </div>
    <div ref={viewport} className="mind-map-scroll" role="tree" aria-label="Mind map tree">
      <div ref={scene} className="mind-map-scene" data-layout-ready={Boolean(boxes)}>{drawing}</div>
    </div>
  </>;
}
