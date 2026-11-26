import { ChevronDown, ChevronRight, CircleAlert, MessageSquarePlus } from "lucide-react";
import { lazy, Suspense, useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, ReactNode } from "react";
import { CitationDetails } from "../components/evidence/CitationButton";
import { mapMarkdown } from "../components/markdown/rich/mapMarkdown";
import { parseRichMarkdown } from "../components/markdown/rich/plugins";
import { AnswerBody } from "../pages/AnswerBody";
import { outputCitations, OutputPartTools, SectionStatus, type OutputContentProps } from "./OutputParts";
import { documentUnits } from "./sections";
import "./map.css";

const MapCanvas = lazy(() => import("./MapCanvas"));

export type MapNode = NonNullable<ReturnType<typeof documentUnits>[number]["node"]> & {
  position: number;
  siblingIndex: number;
  children: MapNode[];
};

function readMap({ content, kind }: OutputContentProps["artifact"]) {
  const roots: MapNode[] = [];
  const nodes = new Map<number, MapNode>();
  const unreadable: { position: number; text: string }[] = [];
  documentUnits(content, kind).forEach(({ node, text }, position) => {
    if (!node) {
      unreadable.push({ position, text });
      return;
    }
    const parent = node.parents.at(-1);
    const siblings = parent === undefined ? roots : nodes.get(parent)!.children;
    const item = { ...node, position, siblingIndex: siblings.length + 1, children: [] };
    siblings.push(item);
    nodes.set(position, item);
  });
  return { roots, nodes, unreadable };
}

function labelText(markdown: string) {
  const text: string[] = [];
  mapMarkdown(parseRichMarkdown(markdown), (node) => {
    if (node.type === "text" || node.type === "inlineCode" || node.type === "inlineMath") text.push(node.value);
    if (node.type === "image" || node.type === "imageReference") text.push(node.alt ?? "");
  });
  return text.join("").trim();
}

const MAX_OUTLINE_INDENT = 5; // Leave room for text on narrow panes and A4 paper.
const ITEM_CONTROLS = "button, a[href], input, select, textarea, summary";
type Citations = ReturnType<typeof outputCitations>;
type RenderOutlineItem = (node: MapNode, label: ReactNode, children: ReactNode) => ReactNode;

function OutlineList({ nodes, citations, renderItem, top = true }: {
  nodes: MapNode[];
  citations: Citations;
  renderItem?: RenderOutlineItem | undefined;
  top?: boolean;
}) {
  return <ul className="mind-map-outline-level" role={renderItem ? top ? "tree" : "group" : "list"}
    aria-label={top && renderItem ? "Mind map outline" : undefined}>
    {nodes.map((node) => {
      const label = <div className="mind-map-outline-label" data-depth={node.depth}
        style={{ "--map-indent-level": Math.min(node.depth, MAX_OUTLINE_INDENT) } as CSSProperties}>
        {node.depth > MAX_OUTLINE_INDENT && <small>Level {node.depth + 1}</small>}
        <AnswerBody content={node.markdown} {...citations} />
      </div>;
      const children = node.children.length > 0 &&
        <OutlineList nodes={node.children} citations={citations} renderItem={renderItem} top={false} />;
      return <li key={node.position} role={renderItem ? "none" : undefined}>
        {renderItem ? renderItem(node, label, children) : <>{label}{children}</>}
      </li>;
    })}
  </ul>;
}

function UnreadableNodes({ rows }: { rows: ReturnType<typeof readMap>["unreadable"] }) {
  if (!rows.length) return null;
  return <aside className="mind-map-unreadable" aria-label="Unreadable map positions">
    <p>{rows.length === 1 ? "One map position could not be placed." : `${rows.length} map positions could not be placed.`}</p>
    <ul>{rows.map(({ position, text }) => <li key={position}>Position {position + 1} · {text.trim()}</li>)}</ul>
  </aside>;
}

/** The full outline used in print and in the Markdown editor's preview. */
export function MindMapOutline(props: OutputContentProps) {
  const { artifact } = props;
  const { roots, unreadable } = useMemo(() => readMap(artifact), [artifact]);
  return <CitationDetails.Provider value={artifact.citations}>
    <section className="mind-map-outline" aria-label="Mind map outline">
      {roots.length ? <OutlineList nodes={roots} citations={outputCitations(props)} /> : <p>No readable nodes.</p>}
      <UnreadableNodes rows={unreadable} />
    </section>
  </CitationDetails.Provider>;
}

function MapItem({ node, label, selected, open, groupSize, marked, style, children,
  onSelect, onToggle, onKey, onInspect }: {
  node: MapNode;
  label: ReactNode;
  selected: boolean;
  open: boolean | undefined;
  groupSize: number;
  marked: string | undefined;
  style?: CSSProperties | undefined;
  children?: ReactNode;
  onSelect: () => void;
  onToggle?: (() => void) | undefined;
  onKey: (event: KeyboardEvent<HTMLDivElement>) => void;
  onInspect: () => void;
}) {
  const body = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    // Only the roving item's rich-content controls participate in Tab order.
    for (const control of body.current?.querySelectorAll<HTMLElement>(ITEM_CONTROLS) ?? []) {
      if (selected) control.removeAttribute("tabindex");
      else control.tabIndex = -1;
    }
  });
  const Chevron = open ? ChevronDown : ChevronRight;
  return <div role="treeitem" aria-label={node.label} aria-selected={selected} aria-expanded={open}
    aria-level={node.depth + 1} aria-setsize={groupSize} aria-posinset={node.siblingIndex}
    data-node-position={node.position} data-depth={node.depth} tabIndex={selected ? 0 : -1}
    className={style ? "mind-map-node" : "mind-map-outline-item"} style={style}
    onFocus={(event) => { if (event.target === event.currentTarget) onSelect(); }}
    onKeyDown={(event) => { if (event.target === event.currentTarget) onKey(event); }}>
    <div className="mind-map-node-row" onClick={(event) => {
      if (event.target instanceof Element && !event.target.closest(ITEM_CONTROLS)) {
        (onToggle ?? onSelect)();
      }
    }}>
      <div ref={body} className="mind-map-node-label">{label}</div>
      {onToggle && <button type="button" className="gx-icon-button mind-map-toggle" tabIndex={-1}
        aria-expanded={open} aria-label={`${open ? "Collapse" : "Expand"} ${node.label}`}
        onClick={onToggle}>
        <Chevron size={16} aria-hidden="true" />
        {!open && <span aria-hidden="true">+{node.children.length}</span>}
      </button>}
      {marked && <button type="button" className="gx-icon-button mind-map-node-status" tabIndex={selected ? 0 : -1}
        aria-label={`${marked} · ${node.label}`} title={marked} onClick={onInspect}>
        <CircleAlert size={16} aria-hidden="true" />
      </button>}
    </div>
    {children}
  </div>;
}

type Mode = "map" | "outline";
type FocusRequest = { position: number; scrollOnly?: boolean; keyboardTrigger?: HTMLButtonElement | undefined };

export function MindMapView(props: OutputContentProps) {
  const { artifact, onAskChat, onAsk, onPart, goTo, changed } = props;
  const { roots, nodes, unreadable } = useMemo(() => readMap(artifact), [artifact]);
  const [mode, setMode] = useState<Mode | null>(null);
  const [expanded, setExpanded] = useState(() => new Set(roots.map((node) => node.position)));
  const [selection, setSelection] = useState<number | undefined>(roots[0]?.position);
  const [focus, setFocus] = useState<FocusRequest | null>(null);
  const [inspect, setInspect] = useState<FocusRequest | null>(null);
  const outline = useRef<HTMLDivElement>(null);
  const selectionDetails = useRef<HTMLElement>(null);
  const reported = useRef<number>(-1);
  const lastJump = useRef<number | undefined>(undefined);
  const isOutline = mode === "outline";
  const selected = selection === undefined ? undefined : nodes.get(selection);
  const selectedLabel = useMemo(() => selected ? labelText(selected.label) : "", [selected]);
  const visible = useMemo(() => [...nodes.values()].filter((node) =>
    isOutline || node.parents.every((parent) => expanded.has(parent))), [nodes, isOutline, expanded]);
  const citations = outputCitations(props);

  const select = useCallback((position: number) => {
    setSelection(position);
    if (reported.current !== position) {
      reported.current = position;
      onPart?.(position);
    }
  }, [onPart]);
  const focusNode = useCallback((position: number) => {
    select(position);
    setFocus({ position });
  }, [select]);
  const toggle = (node: MapNode) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(node.position)) next.delete(node.position);
      else next.add(node.position);
      return next;
    });
    focusNode(node.position);
  };
  useLayoutEffect(() => {
    if (!goTo || goTo.nonce === lastJump.current) return;
    const target = nodes.get(goTo.index);
    if (!target) return;
    lastJump.current = goTo.nonce;
    setExpanded((current) => new Set([...current, ...target.parents]));
    focusNode(target.position);
  }, [goTo, nodes, focusNode]);
  useLayoutEffect(() => {
    const list = outline.current;
    if (!isOutline || !list || !focus) return;
    const target = list.querySelector<HTMLElement>(`[data-node-position="${focus.position}"]`);
    const row = target?.firstElementChild;
    if (!target || !row) return;
    if (!focus.scrollOnly) target.focus({ preventScroll: true });

    const page = list.closest<HTMLElement>(".library-middle-page");
    if (!page) {
      row.scrollIntoView?.({ block: "nearest" });
      return;
    }
    const viewport = page.getBoundingClientRect();
    const header = page.querySelector(".library-middle-bar")?.getBoundingClientRect();
    const footerHeight = selectionDetails.current?.getBoundingClientRect().height ?? 0;
    const visibleTop = Math.max(viewport.top, header?.bottom ?? viewport.top);
    const visibleBottom = viewport.bottom - footerHeight;
    // Leave room for the focus outline beside the sticky header and footer.
    const top = visibleTop + 4;
    const bottom = visibleBottom - 4;
    const bounds = row.getBoundingClientRect();
    if (bounds.top < top || bounds.height > bottom - top) page.scrollTop += bounds.top - top;
    else if (bounds.bottom > bottom) page.scrollTop += bounds.bottom - bottom;

    const trigger = focus.keyboardTrigger;
    if (trigger && trigger === trigger.ownerDocument.activeElement) {
      const button = trigger.getBoundingClientRect();
      if (button.bottom <= visibleTop || button.top >= visibleBottom) target.focus({ preventScroll: true });
    }
  }, [focus, isOutline]);
  useLayoutEffect(() => {
    if (!inspect) return;
    selectionDetails.current?.focus({ preventScroll: true });
    selectionDetails.current?.scrollIntoView?.({ block: "nearest" });
  }, [inspect]);

  const switchMode = (next: Mode, keyboardTrigger?: HTMLButtonElement) => {
    // The outline exposes every node. Keep its selection visible when returning to the canvas.
    if (next === "map" && selected) setExpanded((current) => new Set([...current, ...selected.parents]));
    setMode(next);
    setFocus(next === "outline" && selected ? { position: selected.position, scrollOnly: true, keyboardTrigger } : null);
  };
  const onInitialView = useCallback((narrow: boolean) => {
    setMode((current) => current ?? (narrow ? "outline" : "map"));
  }, []);
  const onKey = (event: KeyboardEvent<HTMLDivElement>, node: MapNode) => {
    const index = visible.indexOf(node);
    const open = isOutline || expanded.has(node.position);
    const parent = node.parents.at(-1);
    const branch = node.children.length > 0;
    let position: number | undefined;
    switch (event.key) {
      case "ArrowUp": position = visible[index - 1]?.position; break;
      case "ArrowDown": position = visible[index + 1]?.position; break;
      case "Home": position = visible[0]?.position; break;
      case "End": position = visible.at(-1)?.position; break;
      case "ArrowRight":
        if (branch && !open) toggle(node);
        else position = node.children[0]?.position;
        break;
      case "ArrowLeft":
        if (branch && open && !isOutline) toggle(node);
        else position = parent;
        break;
      case "Enter":
        if (branch && !isOutline) toggle(node);
        break;
      default: return;
    }
    event.preventDefault();
    if (position !== undefined) focusNode(position);
  };
  const renderItem = (node: MapNode, label: ReactNode, children?: ReactNode, style?: CSSProperties) => {
    const parent = node.parents.at(-1);
    const info = artifact.sections[node.position];
    const marked = info && (!info.checked || info.issues.length) ? "Needs review"
      : changed?.includes(node.position) ? "Changed" : undefined;
    const branch = node.children.length > 0;
    return <MapItem key={node.position} node={node} label={label} selected={selection === node.position}
      open={branch ? isOutline || expanded.has(node.position) : undefined}
      groupSize={parent === undefined ? roots.length : nodes.get(parent)!.children.length}
      marked={marked} style={style} onSelect={() => focusNode(node.position)}
      onToggle={!isOutline && branch ? () => toggle(node) : undefined} onKey={(event) => onKey(event, node)}
      onInspect={() => { select(node.position); setInspect({ position: node.position }); }}>
      {children}
    </MapItem>;
  };

  return <CitationDetails.Provider value={artifact.citations}>
    <section className="mind-map-reader" aria-label="Mind map">
      {roots.length > 0 ? <>
        <div className="mind-map-modes" role="group" aria-label="View">
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" aria-pressed={!isOutline} onClick={() => switchMode("map")}>Map</button>
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" aria-pressed={isOutline}
            onClick={(event) => switchMode("outline", event.detail === 0 ? event.currentTarget : undefined)}>Outline</button>
        </div>
        {isOutline ? <div ref={outline} className="mind-map-outline">
          <OutlineList nodes={roots} citations={citations} renderItem={renderItem} />
        </div> : <Suspense fallback={<p role="status">Loading map…</p>}>
          <MapCanvas roots={roots} visible={visible} selected={selection} focus={focus} onInitialView={onInitialView}
            renderNode={(node, style) => renderItem(node, <AnswerBody content={node.markdown} {...citations} />, undefined, style)} />
        </Suspense>}
        {selected && <section ref={selectionDetails} className="mind-map-selection" aria-label="Selected node" tabIndex={-1}>
          <div className="mind-map-selection-bar">
            <strong>{selectedLabel}</strong>
            {onAskChat && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm"
              aria-label={`Ask about ${selectedLabel}`}
              onClick={() => onAskChat(`Tell me about ${[...selected.ancestors, selected.label].join(" › ")}`)}>
              <MessageSquarePlus size={16} aria-hidden="true" /> Ask
            </button>}
            <OutputPartTools position={selected.position} onAsk={onAsk} changed={changed} label="Refine" />
          </div>
          <SectionStatus info={artifact.sections[selected.position]} />
        </section>}
      </> : <p>No readable nodes.</p>}
      <UnreadableNodes rows={unreadable} />
    </section>
  </CitationDetails.Provider>;
}
