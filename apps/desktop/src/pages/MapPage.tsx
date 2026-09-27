import type { KnowledgeGraph } from "@gunther/contracts";
import { CheckCircle2, Filter, Focus, GitCompareArrows, Route, Search, Sparkles } from "lucide-react";
import { useState } from "react";
import { GraphExplorer } from "../components/GraphExplorer";
import type { DomainId } from "../prototype";

export function MapPage({ graph, domain }: { graph: KnowledgeGraph; domain: DomainId }) {
  const [mode, setMode] = useState<"neighborhood" | "path" | "evidence">("neighborhood");
  const types = [...new Set(graph.nodes.map((node) => node.type))];

  return (
    <div className="page-stack prototype-map-page">
      <section className="library-heading map-heading">
        <div><span className="eyebrow">Purposeful graph views</span><h1>Knowledge map</h1><p>Explore a neighborhood, reasoning path, or evidence conflict—not an unreadable global hairball.</p></div>
        <div className="map-view-modes">
          <button className={mode === "neighborhood" ? "is-active" : ""} onClick={() => setMode("neighborhood")}><Focus size={14} />Neighborhood</button>
          <button className={mode === "path" ? "is-active" : ""} onClick={() => setMode("path")}><Route size={14} />Path</button>
          <button className={mode === "evidence" ? "is-active" : ""} onClick={() => setMode("evidence")}><GitCompareArrows size={14} />Evidence</button>
        </div>
      </section>

      <section className="map-workspace-grid">
        <article className="panel map-prototype-panel">
          <header className="map-toolbar">
            <label><Search size={14} /><input defaultValue={domain === "biology" ? "CD3D" : ""} placeholder="Focus on a Knowledge Unit…" /></label>
            <span className="map-context-pill"><Focus size={12} />{domain === "all" ? "Cross-domain" : domain} lens</span>
            <button><Filter size={13} />Filter</button>
          </header>
          <div className="map-legend" aria-label="Entity types">
            {types.map((type) => (
              <span key={type} className={`legend-${type.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`}><i aria-hidden="true" />{type}</span>
            ))}
          </div>
          <GraphExplorer graph={graph} />
        </article>

        <aside className="map-insights">
          <article className="panel map-focus-card">
            <span className="eyebrow">Current focus</span>
            <div className="focus-identity"><i>G</i><span><strong>{domain === "mathematics" ? "Backpropagation" : domain === "management" ? "Network effects" : "CD3D"}</strong><small>{domain === "mathematics" ? "Method" : domain === "management" ? "Concept" : "Gene"}</small></span></div>
            <dl><div><dt>Direct claims</dt><dd>3</dd></div><div><dt>Evidence sources</dt><dd>4</dd></div><div><dt>Related units</dt><dd>5</dd></div></dl>
          </article>
          <article className="panel graph-insight-card">
            <header><Sparkles size={14} /><span>Suggested connection</span></header>
            <strong>CD247 may reinforce this neighborhood</strong>
            <p>It appears beside CD3D and CD3E in two source fragments but is not yet connected to the T-cell identity unit.</p>
            <button>Review suggestion</button>
          </article>
          <article className="panel graph-path-summary">
            <header><CheckCircle2 size={14} /><span>Strongest path</span></header>
            <div><span>CD3D</span><em>marker of</em><span>T cell</span></div>
            <small>3 sources · 96% extraction certainty · human reviewed</small>
          </article>
        </aside>
      </section>
    </div>
  );
}
