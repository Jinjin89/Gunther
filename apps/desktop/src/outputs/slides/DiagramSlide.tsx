import { useId } from "react";
import { plainText, type DiagramShape } from "./parseSlide";
import { Frame, Rich } from "./Rich";

const WIDTH = 1088;
const HEIGHT = 420;
const NODE_WIDTH = 240;

/** A bounded, code-rendered DAG. Invalid or crowded graphs have already fallen back to the source table. */
export function DiagramSlide({ shape }: { shape: DiagramShape }) {
  const arrowId = `diagram-arrow-${useId().replaceAll(":", "")}`;
  const nodes = shape.nodes.map(({ key, label }) => {
    const rank = shape.layers.findIndex((layer) => layer.includes(key));
    const layer = shape.layers[rank] ?? [];
    const index = layer.indexOf(key);
    const x = shape.layers.length === 1 ? WIDTH / 2 : 130 + rank * ((WIDTH - 260) / (shape.layers.length - 1));
    const y = HEIGHT / 2 + (index - (layer.length - 1) / 2) * 140;
    return { key, label, rank, x, y };
  });
  const position = new Map(nodes.map((node) => [node.key, node]));
  const incoming = new Set(shape.edges.map((edge) => edge.toKey));
  const edges = shape.edges.map((edge) => {
    const from = position.get(edge.fromKey);
    const to = position.get(edge.toKey);
    if (!from || !to) return null;
    const startX = from.x + NODE_WIDTH / 2;
    const endX = to.x - NODE_WIDTH / 2;
    const midX = (startX + endX) / 2;
    const midY = (from.y + to.y) / 2;
    return { ...edge, from, to, startX, endX, midX, midY, path: `M ${startX} ${from.y} H ${midX} V ${to.y} H ${endX}` };
  }).filter((edge): edge is NonNullable<typeof edge> => edge !== null);

  return <Frame layout={shape.layout} title={shape.title} lead={shape.lead} after={shape.after}>
    <div className="outputs-s-diagram" role="group" aria-label="Directed relationship diagram">
      <svg className="outputs-s-diagram-lines" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} preserveAspectRatio="none" aria-hidden="true">
        <defs><marker id={arrowId} markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
        {edges.map((edge, index) => <path key={index} d={edge.path} markerEnd={`url(#${arrowId})`} />)}
      </svg>
      {nodes.map((node) => <div key={node.key} className={`outputs-s-diagram-node${incoming.has(node.key) ? "" : " is-origin"}`} style={{ left: `${node.x / WIDTH * 100}%`, top: `${node.y / HEIGHT * 100}%` }}>
        <Rich text={node.label} />
      </div>)}
      {edges.map((edge, index) => <div key={index} className="outputs-s-diagram-edge" style={{ left: `${edge.midX / WIDTH * 100}%`, top: `${edge.midY / HEIGHT * 100}%` }}>
        <Rich text={edge.relationship} />
      </div>)}
      <ol className="outputs-sr-only" aria-label="Diagram relationships">
        {shape.edges.map((edge, index) => <li key={index}>{edge.from} — {plainText(edge.relationship)} — {edge.to}; sources {edge.refs.join(", ")}</li>)}
      </ol>
    </div>
  </Frame>;
}
