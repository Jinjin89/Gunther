import type { GraphNode, KnowledgeGraph } from "@gunther/contracts";
import { Network, X } from "lucide-react";
import { useMemo, useState } from "react";

interface Position extends GraphNode {
  x: number;
  y: number;
}

const typeClass = (type: string) => `node-${type.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;

export function GraphExplorer({ graph, preview = false }: { graph: KnowledgeGraph; preview?: boolean }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const width = 900;
  const height = preview ? 360 : 510;

  const positions = useMemo(() => {
    const count = graph.nodes.length;
    return new Map(
      graph.nodes.map((node, index): [string, Position] => {
        if (count === 1) return [node.id, { ...node, x: width / 2, y: height / 2 }];
        const angle = (index / Math.max(count, 1)) * Math.PI * 2 - Math.PI / 2;
        const orbit = Math.min(width, height) * (count > 6 ? 0.36 : 0.31);
        const modulation = index % 2 === 0 ? 1 : 0.72;
        return [
          node.id,
          {
            ...node,
            x: width / 2 + Math.cos(angle) * orbit * modulation,
            y: height / 2 + Math.sin(angle) * orbit,
          },
        ];
      }),
    );
  }, [graph.nodes, height]);

  const selected = selectedId ? positions.get(selectedId) : undefined;
  const connections = selected
    ? graph.edges.filter((edge) => edge.source === selected.id || edge.target === selected.id)
    : [];

  if (graph.nodes.length === 0) {
    return (
      <div className="empty-state graph-empty">
        <Network size={26} aria-hidden="true" />
        <strong>Your map is ready to grow</strong>
        <span>Import a source containing knowledge relationships.</span>
      </div>
    );
  }

  return (
    <div className={`graph-explorer ${preview ? "is-preview" : ""}`}>
      <svg
        className="knowledge-graph"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`Knowledge graph with ${graph.nodes.length} entities and ${graph.edges.length} associations`}
      >
        <defs>
          <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" />
          </marker>
        </defs>
        <g className="graph-edges">
          {graph.edges.map((edge) => {
            const source = positions.get(edge.source);
            const target = positions.get(edge.target);
            if (!source || !target) return null;
            const highlighted = selectedId === null || edge.source === selectedId || edge.target === selectedId;
            return (
              <g key={edge.id} className={highlighted ? "is-connected" : "is-dimmed"}>
                <line
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  markerEnd="url(#arrow)"
                />
                {!preview && (
                  <text x={(source.x + target.x) / 2} y={(source.y + target.y) / 2 - 7}>
                    {edge.label.replaceAll("_", " ")}
                  </text>
                )}
              </g>
            );
          })}
        </g>
        <g className="graph-nodes">
          {[...positions.values()].map((node) => {
            const connected =
              selectedId === null ||
              selectedId === node.id ||
              connections.some((edge) => edge.source === node.id || edge.target === node.id);
            return (
              <g
                key={node.id}
                className={`${typeClass(node.type)} ${connected ? "is-connected" : "is-dimmed"} ${selectedId === node.id ? "is-selected" : ""}`}
                transform={`translate(${node.x} ${node.y})`}
                onClick={() => setSelectedId(selectedId === node.id ? null : node.id)}
                role="button"
                aria-label={`${node.label}, ${node.type}, ${node.assertionCount} associations`}
              >
                <circle r={preview ? 34 : 40} />
                <text className="node-label" textAnchor="middle" y="4">
                  {node.label.length > 15 ? `${node.label.slice(0, 14)}…` : node.label}
                </text>
                {!preview && (
                  <text className="node-type" textAnchor="middle" y="59">
                    {node.type}
                  </text>
                )}
              </g>
            );
          })}
        </g>
      </svg>
      {!preview && selected && (
        <div className="graph-selection" aria-live="polite">
          <div>
            <span className="eyebrow">Selected entity</span>
            <strong>{selected.label}</strong>
            <span>{selected.type} · {connections.length} associations</span>
          </div>
          <button className="icon-button" onClick={() => setSelectedId(null)} aria-label="Clear selection">
            <X size={17} />
          </button>
        </div>
      )}
    </div>
  );
}
