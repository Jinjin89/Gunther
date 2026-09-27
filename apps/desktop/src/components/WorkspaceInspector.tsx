import { ArrowRight, BookOpenCheck, Check, FileStack, GitBranch, History, Link2, Presentation, ShieldCheck, Sparkles } from "lucide-react";
import type { KnowledgeUnit } from "../prototype";
import type { WorkspaceView } from "./NavigationRail";

interface WorkspaceInspectorProps {
  unit: KnowledgeUnit;
  pendingCount: number;
  onNavigate: (view: WorkspaceView) => void;
}

export function WorkspaceInspector({ unit, pendingCount, onNavigate }: WorkspaceInspectorProps) {
  const evidenceCount = unit.claims.reduce((sum, claim) => sum + claim.evidenceCount, 0);
  return (
    <aside className="workspace-inspector prototype-inspector">
      <header>
        <span className="eyebrow">Current knowledge context</span>
        <strong>{unit.title}</strong>
        <p>{unit.summary}</p>
      </header>

      <div className="inspector-unit-state">
        <span className={`status-${unit.status}`}><ShieldCheck size={13} />{unit.status}</span>
        {unit.domains.map((domain) => <span key={domain} className={`domain-tag domain-tag-${domain}`}>{domain}</span>)}
      </div>

      <section className="inspector-section">
        <div className="inspector-section-title"><span>Knowledge anatomy</span><GitBranch size={14} /></div>
        <div className="knowledge-anatomy-grid">
          <button onClick={() => onNavigate("library")}><GitBranch size={14} /><strong>{unit.claims.length}</strong><small>Claims</small></button>
          <button onClick={() => onNavigate("library")}><BookOpenCheck size={14} /><strong>{evidenceCount}</strong><small>Evidence</small></button>
          <button onClick={() => onNavigate("map")}><Link2 size={14} /><strong>{unit.related.length}</strong><small>Relations</small></button>
          <button onClick={() => onNavigate("studio")}><Presentation size={14} /><strong>{unit.usedIn.length}</strong><small>Artifacts</small></button>
        </div>
      </section>

      <section className="inspector-section">
        <div className="inspector-section-title"><span>Evidence quality</span><ShieldCheck size={14} /></div>
        <div className="evidence-quality">
          <span><em>Traceability</em><i><b style={{ width: "96%" }} /></i><strong>96</strong></span>
          <span><em>Agreement</em><i><b style={{ width: "88%" }} /></i><strong>88</strong></span>
          <span><em>Freshness</em><i><b style={{ width: "79%" }} /></i><strong>79</strong></span>
        </div>
      </section>

      <section className="inspector-section">
        <div className="inspector-section-title"><span>Used in</span><FileStack size={14} /></div>
        <div className="inspector-artifacts">
          {unit.usedIn.length > 0 ? unit.usedIn.map((artifact, index) => <button key={artifact} onClick={() => onNavigate("studio")}><i>{index + 1}</i><span><strong>{artifact === "story-tcell-identity" ? "T-cell identity field guide" : artifact === "canvas-pbmc" ? "PBMC annotation canvas" : artifact}</strong><small>{artifact.startsWith("story") ? "Story · live binding" : "Canvas · current"}</small></span><ArrowRight size={12} /></button>) : <p>Not used in an artifact yet.</p>}
        </div>
      </section>

      <button className="inspector-review-card" onClick={() => onNavigate("review")}>
        <span className="review-count">{pendingCount}</span>
        <span><strong>Changes need judgment</strong><small>Review before they become trusted.</small></span>
        <ArrowRight size={14} />
      </button>

      <div className="inspector-revision"><History size={13} /><span><small>Current revision</small><strong>Revision 8 · saved locally</strong></span><Check size={12} /></div>
      <div className="model-card"><Sparkles size={14} /><span><small>Interpretation engine</small><strong>DeepSeek · proposal only</strong></span><i className="is-live" /></div>
    </aside>
  );
}
