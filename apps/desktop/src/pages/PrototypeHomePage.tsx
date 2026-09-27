import {
  ArrowRight,
  BookOpenCheck,
  CheckCircle2,
  Clock3,
  GitCompareArrows,
  Layers3,
  Presentation,
  Route,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { prototypeDeltas, prototypeUnits, type DomainId, type KnowledgeDelta, type KnowledgeUnit } from "../prototype";
import type { WorkspaceView } from "../components/NavigationRail";

interface PrototypeHomePageProps {
  domain: DomainId;
  onNavigate: (view: WorkspaceView) => void;
  onCapture: () => void;
  onSelectUnit: (unit: KnowledgeUnit) => void;
}

const deltaIcons = {
  new: Sparkles,
  reinforced: CheckCircle2,
  narrowed: Route,
  contradicted: GitCompareArrows,
};

const deltaLabels = {
  new: "New",
  reinforced: "Reinforced",
  narrowed: "Narrowed",
  contradicted: "Contradiction",
};

function DeltaRow({ delta }: { delta: KnowledgeDelta }) {
  const Icon = deltaIcons[delta.type];
  return (
    <button className={`delta-row delta-${delta.type}`}>
      <span className="delta-icon"><Icon size={15} /></span>
      <span className="delta-copy">
        <span><i>{deltaLabels[delta.type]}</i><time>{delta.time}</time></span>
        <strong>{delta.title}</strong>
        <small>{delta.detail}</small>
        <em>{delta.source}</em>
      </span>
      <ArrowRight size={14} />
    </button>
  );
}

export function PrototypeHomePage({ domain, onNavigate, onCapture, onSelectUnit }: PrototypeHomePageProps) {
  const filteredDeltas = domain === "all" ? prototypeDeltas : prototypeDeltas.filter((delta) => delta.domain === domain);
  const spotlight = domain === "mathematics"
    ? prototypeUnits.find((unit) => unit.id === "unit-backprop")!
    : domain === "management"
      ? prototypeUnits.find((unit) => unit.id === "unit-swot")!
      : prototypeUnits[0]!;

  return (
    <div className="page-stack prototype-home">
      <section className="prototype-hero">
        <div className="prototype-hero-copy">
          <span className="eyebrow">Wednesday · your living knowledge</span>
          <h1>Your knowledge changed<br />in useful ways.</h1>
          <p>Three sources strengthened one idea, narrowed another, and uncovered a contradiction worth reviewing.</p>
          <div className="hero-actions">
            <button className="button button-primary" onClick={onCapture}><Sparkles size={15} />Capture what you learned</button>
            <button className="button button-secondary" onClick={() => onNavigate("review")}>Review 3 proposals<ArrowRight size={14} /></button>
          </div>
        </div>
        <div className="knowledge-today-card">
          <header><span>Knowledge health</span><ShieldCheck size={16} /></header>
          <div className="health-score"><strong>91</strong><span>/ 100<small>Evidence coverage</small></span></div>
          <div className="health-bars">
            <span><i style={{ width: "94%" }} /><em>Traceable claims</em><strong>94%</strong></span>
            <span><i style={{ width: "87%" }} /><em>Reviewed knowledge</em><strong>87%</strong></span>
            <span><i style={{ width: "76%" }} /><em>Current knowledge</em><strong>76%</strong></span>
          </div>
          <footer><Clock3 size={13} />Last interpretation 12 minutes ago</footer>
        </div>
      </section>

      <section className="prototype-dashboard-grid">
        <article className="panel delta-panel">
          <header className="prototype-panel-header">
            <div><span className="eyebrow">Since your last visit</span><h2>Knowledge delta</h2></div>
            <button onClick={() => onNavigate("review")}>Review all <ArrowRight size={13} /></button>
          </header>
          <div className="delta-list">
            {filteredDeltas.slice(0, 3).map((delta) => <DeltaRow key={delta.id} delta={delta} />)}
            {filteredDeltas.length === 0 && <p className="prototype-empty">No recent changes in this domain.</p>}
          </div>
        </article>

        <article className="panel spotlight-panel">
          <header className="prototype-panel-header">
            <div><span className="eyebrow">Knowledge Unit spotlight</span><h2>Continue understanding</h2></div>
            <span className="trusted-pill"><ShieldCheck size={12} />Trusted</span>
          </header>
          <button className="spotlight-unit" onClick={() => { onSelectUnit(spotlight); onNavigate("library"); }}>
            <div className="unit-orbit" aria-hidden="true"><i /><i /><i /><span>{spotlight.title.slice(0, 2)}</span></div>
            <span className="spotlight-copy">
              <small>{spotlight.kind} · {spotlight.domains.join(" + ")}</small>
              <strong>{spotlight.title}</strong>
              <p>{spotlight.summary}</p>
            </span>
          </button>
          <div className="spotlight-metrics">
            <span><BookOpenCheck size={14} /><strong>{spotlight.claims.length}</strong><small>claims</small></span>
            <span><Layers3 size={14} /><strong>{spotlight.evidence.length + 1}</strong><small>sources</small></span>
            <span><Presentation size={14} /><strong>{spotlight.usedIn.length}</strong><small>artifacts</small></span>
          </div>
          <button className="open-unit-button" onClick={() => { onSelectUnit(spotlight); onNavigate("library"); }}>Open Knowledge Unit <ArrowRight size={14} /></button>
        </article>
      </section>

      <section className="home-lower-grid">
        <article className="panel continue-story-card" onClick={() => onNavigate("studio")}>
          <div className="story-miniature">
            <span>BIOLOGY / CELL IDENTITY</span>
            <strong>What makes a<br />T cell a T cell?</strong>
            <div><i /><i /><i /></div>
          </div>
          <div className="story-card-copy">
            <span className="eyebrow">Continue composing</span>
            <h3>T-cell identity field guide</h3>
            <p>3 scenes · 5 live bindings · updated 18 min ago</p>
            <span className="story-progress"><i /><em>Story structure 72%</em></span>
          </div>
          <ArrowRight size={17} />
        </article>

        <article className="panel knowledge-path-card">
          <header><span><Route size={15} />Active knowledge path</span><button onClick={() => onNavigate("map")}>Open map</button></header>
          <div className="knowledge-path">
            <span><i className="path-gene">G</i><strong>CD3D</strong><small>Gene</small></span>
            <em>marker of</em>
            <span><i className="path-cell">T</i><strong>T cell</strong><small>Cell type</small></span>
            <em>identified by</em>
            <span><i className="path-method">M</i><strong>PBMC workflow</strong><small>Method</small></span>
          </div>
        </article>
      </section>
    </div>
  );
}
