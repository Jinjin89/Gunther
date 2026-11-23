import type { Artifact, ConversationCitation } from "@gunther/contracts";
import { hierarchy, tree } from "d3-hierarchy";
import { ChevronDown, ChevronRight, MessageSquarePlus } from "lucide-react";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { CitationDetails } from "../components/evidence/CitationButton";
import { AnswerBody, citationNumbers } from "../pages/AnswerBody";
import { SectionStatus } from "./ReportView";
import { documentUnits } from "./sections";

type Node = NonNullable<ReturnType<typeof documentUnits>[number]["node"]> & {
  position: number;
  children: Node[];
};

function mapNodes(artifact: Artifact) {
  const roots: Node[] = [];
  const nodes = new Map<number, Node>();
  documentUnits(artifact.content, artifact.kind).forEach(({ node }, position) => {
    if (!node) return;
    const item: Node = { ...node, position, children: [] };
    const parent = node.parents.at(-1);
    if (parent === undefined) roots.push(item);
    else nodes.get(parent)?.children.push(item);
    nodes.set(position, item);
  });
  return { roots, nodes };
}

type MapProps = {
  artifact: Artifact;
  onCite?: ((citation: ConversationCitation, index: number) => void) | undefined;
};

function mapCitations({ artifact, onCite }: MapProps) {
  return {
    numbers: citationNumbers(artifact.citations),
    onCitation: onCite ? (index: number) => {
      const citation = artifact.citations[index];
      if (citation) onCite(citation, index);
    } : undefined,
  };
}

function Outline({ nodes, citations }: { nodes: Node[]; citations: ReturnType<typeof mapCitations> }) {
  return <ul>{nodes.map((node) => <li key={node.position}>
    <AnswerBody content={node.markdown} {...citations} />
    {node.children.length > 0 && <Outline nodes={node.children} citations={citations} />}
  </li>)}</ul>;
}

/** The full outline used in print and in the Markdown editor's preview. */
export function MindMapOutline(props: MapProps) {
  const { artifact } = props;
  const { roots } = useMemo(() => mapNodes(artifact), [artifact]);
  return <CitationDetails.Provider value={artifact.citations}>
    <section className="mind-map-outline" aria-label="Mind map outline">
      {roots.length ? <Outline nodes={roots} citations={mapCitations(props)} /> : <p>No readable nodes.</p>}
    </section>
  </CitationDetails.Provider>;
}

const CARD_WIDTH = 240;
const COLUMN = CARD_WIDTH + 48;
const MARGIN = 12;

export function MindMapView({ artifact, onCite, onAskChat, onPart, goTo, changed }: MapProps & {
  onAskChat?: ((question: string) => void) | undefined;
  onPart?: ((index: number) => void) | undefined;
  goTo?: { index: number; nonce: number } | null | undefined;
  changed?: readonly number[] | undefined;
}) {
  const { roots, nodes } = useMemo(() => mapNodes(artifact), [artifact]);
  const [expanded, setExpanded] = useState(() => new Set(
    [...nodes.values()].filter((node) => node.depth < 2).map((node) => node.position),
  ));
  const [rowHeight, setRowHeight] = useState(64);
  const canvas = useRef<HTMLDivElement>(null);
  const jumped = useRef<number | null>(null);
  const layout = useMemo(() => {
    const root = roots[0];
    return root && tree<Node>().nodeSize([rowHeight + 16, COLUMN])(
      hierarchy(root, (node) => expanded.has(node.position) ? node.children : []),
    );
  }, [roots, expanded, rowHeight]);

  useLayoutEffect(() => {
    const cards = [...(canvas.current?.querySelectorAll<HTMLElement>(".mind-map-node") ?? [])];
    const measure = () => setRowHeight(Math.max(64, ...cards.map((card) => Math.ceil(card.getBoundingClientRect().height))));
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    cards.forEach((card) => observer.observe(card));
    return () => observer.disconnect();
  }, [layout]);

  useLayoutEffect(() => {
    if (!goTo) return;
    const target = nodes.get(goTo.index);
    if (target) setExpanded((current) => new Set([...current, ...target.parents]));
  }, [goTo, nodes]);
  useLayoutEffect(() => {
    if (!goTo || jumped.current === goTo.nonce) return;
    const node = canvas.current?.querySelector(`[data-node-position="${goTo.index}"]`);
    if (node) {
      jumped.current = goTo.nonce;
      node.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    }
  }, [goTo, layout]);

  if (!layout) return <p>No readable nodes.</p>;
  const drawn = layout.descendants().sort((a, b) => a.data.position - b.data.position);
  const first = Math.min(...drawn.map((node) => node.x));
  const height = Math.max(...drawn.map((node) => node.x)) - first + rowHeight + 2 * MARGIN;
  const width = Math.max(...drawn.map((node) => node.y)) + CARD_WIDTH + 2 * MARGIN;
  const top = (x: number) => x - first + MARGIN;
  const citations = mapCitations({ artifact, onCite });
  const toggle = (position: number) => setExpanded((current) => {
    const next = new Set(current);
    if (next.has(position)) next.delete(position);
    else next.add(position);
    return next;
  });

  return <CitationDetails.Provider value={artifact.citations}>
    <section className="mind-map-scroll" aria-label="Mind map">
      <div ref={canvas} className="mind-map-canvas" style={{ width, height }}>
        <svg aria-hidden="true" className="mind-map-links" width={width} height={height}>
          {layout.links().map(({ source, target }) => {
            const from = source.y + MARGIN + CARD_WIDTH;
            const to = target.y + MARGIN;
            const elbow = (from + to) / 2;
            const leftCenter = top(source.x) + rowHeight / 2;
            const rightCenter = top(target.x) + rowHeight / 2;
            return <path key={target.data.position} d={`M ${from} ${leftCenter} H ${elbow} V ${rightCenter} H ${to}`} />;
          })}
        </svg>
        {drawn.map(({ data: node, x, y }) => {
          const branch = node.children.length > 0;
          const open = expanded.has(node.position);
          const Chevron = open ? ChevronDown : ChevronRight;
          return <article key={node.position} className="mind-map-node" aria-label={node.label}
            data-node-position={node.position} tabIndex={branch ? undefined : 0}
            style={{ width: CARD_WIDTH, left: y + MARGIN, top: top(x) + rowHeight / 2 }}
            onFocus={() => onPart?.(node.position)}
            onClick={branch ? (event) => {
              if (event.target instanceof Element && !event.target.closest("button, a")) {
                onPart?.(node.position);
                toggle(node.position);
              }
            } : undefined}>
            <div className="mind-map-node-label">
              {branch && <button type="button" className="gx-icon-button mind-map-toggle"
                aria-expanded={open} aria-label={`${open ? "Collapse" : "Expand"} ${node.label}`}
                onClick={() => toggle(node.position)}><Chevron size={14} aria-hidden="true" /></button>}
              <AnswerBody content={node.markdown} {...citations} />
            </div>
            {(onAskChat || changed?.includes(node.position)) && <div className="mind-map-actions">
              {changed?.includes(node.position) && <em className="outputs-changed">Changed</em>}
              {onAskChat && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm mind-map-ask"
                aria-label={`Ask about ${node.label}`}
                onClick={() => onAskChat(`Tell me about ${[...node.ancestors, node.label].join(" › ")}`)}>
                <MessageSquarePlus size={13} aria-hidden="true" />Ask
              </button>}
            </div>}
            <SectionStatus info={artifact.sections[node.position]} />
          </article>;
        })}
      </div>
    </section>
  </CitationDetails.Provider>;
}
