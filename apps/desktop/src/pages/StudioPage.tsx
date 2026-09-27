import {
  ArrowRight,
  Check,
  ChevronDown,
  Download,
  Eye,
  FileCode2,
  GripVertical,
  Link2,
  LockKeyhole,
  MonitorPlay,
  MoreHorizontal,
  Palette,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useMemo, useState } from "react";
import { prototypeScenes, prototypeUnits, type KnowledgeUnit, type StoryScene } from "../prototype";

interface StudioPageProps {
  onNotify: (message: string) => void;
}

function SlideCanvas({ scene }: { scene: StoryScene }) {
  if (scene.layout === "evidence") {
    return (
      <div className="slide-surface slide-evidence-layout">
        <div className="slide-brand-row"><span>GUNTHER / FIELD GUIDE</span><em>02</em></div>
        <div className="slide-evidence-copy"><span className="slide-kicker">EVIDENCE-BACKED IDENTITY</span><h2>CD3D is strong evidence—<br />not a verdict.</h2><p>Use a coherent marker program and keep the biological context attached to the claim.</p><div className="slide-claim"><span>CD3D</span><em>marker of</em><span>T cell</span></div></div>
        <div className="slide-quote"><span>3 supporting fragments</span><blockquote>“Do not call a T cell from one marker alone; look for coherent CD3D, CD3E and TRBC expression.”</blockquote><small>Cell annotation workshop · 00:18:42</small></div>
        <div className="slide-citation">Human · PBMC · scRNA-seq</div>
      </div>
    );
  }

  if (scene.layout === "comparison") {
    return (
      <div className="slide-surface slide-comparison-layout">
        <div className="slide-brand-row"><span>GUNTHER / FIELD GUIDE</span><em>03</em></div>
        <span className="slide-kicker">DECISION PATTERN</span><h2>Use a coherent marker program.</h2>
        <div className="comparison-columns">
          <article><i>01</i><strong>Identify compartment</strong><p>CD3D · CD3E · TRBC1/2</p><span>Strong positive evidence</span></article>
          <article><i>02</i><strong>Refine subtype</strong><p>IL7R · CCR7 · LTB</p><span>Contextual expression program</span></article>
          <article><i>03</i><strong>Check conflicts</strong><p>LST1 · MS4A1 · NKG7</p><span>Negative and doublet evidence</span></article>
        </div>
        <footer><span><ShieldCheck size={13} />5 trusted claims</span><span>3 Knowledge Units · pinned revision</span></footer>
      </div>
    );
  }

  return (
    <div className="slide-surface slide-title-layout">
      <div className="slide-brand-row"><span>GUNTHER / FIELD GUIDE</span><em>01</em></div>
      <div className="slide-title-copy"><span className="slide-kicker">BIOLOGY / CELL IDENTITY</span><h2>What makes a<br />T cell a T cell?</h2><p>An evidence-backed path from genes to a defensible cell annotation.</p></div>
      <div className="slide-network" aria-hidden="true"><span className="network-a">CD3D</span><span className="network-b">T cell</span><span className="network-c">PBMC</span><i className="line-a" /><i className="line-b" /></div>
      <footer><span>3 scenes</span><span>5 live knowledge bindings</span><span>12 source fragments</span></footer>
    </div>
  );
}

export function StudioPage({ onNotify }: StudioPageProps) {
  const [selectedId, setSelectedId] = useState(prototypeScenes[0]!.id);
  const [bindingMode, setBindingMode] = useState<"live" | "pinned">("live");
  const scene = prototypeScenes.find((item) => item.id === selectedId) ?? prototypeScenes[0]!;
  const boundUnits = useMemo(
    () => scene.unitIds
      .map((id) => prototypeUnits.find((unit) => unit.id === id))
      .filter((unit): unit is KnowledgeUnit => Boolean(unit)),
    [scene],
  );

  const exportHtml = () => {
    const html = `<!doctype html><html><head><meta charset="utf-8"><title>T-cell identity field guide</title><style>body{font-family:system-ui;margin:0;background:#edf3ef;color:#173329}section{width:960px;height:540px;margin:40px auto;padding:64px;background:white;box-sizing:border-box;border:1px solid #dce7e1}h1{font:64px Georgia;margin:80px 0 20px}small{color:#187c61;letter-spacing:.14em}</style></head><body><section><small>BIOLOGY / CELL IDENTITY</small><h1>What makes a T cell a T cell?</h1><p>An evidence-backed path from genes to a defensible cell annotation.</p></section></body></html>`;
    const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "t-cell-identity-field-guide.html";
    anchor.click();
    URL.revokeObjectURL(url);
    onNotify("Self-contained HTML exported with its knowledge manifest.");
  };

  return (
    <div className="page-stack studio-page">
      <section className="studio-heading">
        <div><span className="eyebrow">Story and scene studio</span><h1>T-cell identity field guide</h1><p>Compose living knowledge into a clear story. Every element remembers where it came from.</p></div>
        <div className="studio-actions"><button className="button button-secondary" onClick={() => onNotify("Presentation preview opened.")}><Eye size={14} />Preview</button><button className="export-split" onClick={exportHtml}><Download size={14} />Export HTML<span><ChevronDown size={12} /></span></button><button className="button button-primary" onClick={() => onNotify("Editable PPTX export prepared. Interactive graph will use an SVG fallback.")}><MonitorPlay size={14} />Export PPTX</button></div>
      </section>

      <section className="story-studio-shell panel">
        <aside className="scene-sidebar">
          <header><span>Scenes</span><button><Plus size={14} /></button></header>
          <div className="scene-list">
            {prototypeScenes.map((item) => (
              <button key={item.id} className={item.id === selectedId ? "is-active" : ""} onClick={() => { setSelectedId(item.id); setBindingMode(item.binding); }}>
                <GripVertical size={12} />
                <span className={`scene-thumb scene-thumb-${item.layout}`}><i>{item.number}</i><strong>{item.title}</strong><em /></span>
                <small>{item.number}</small>
              </button>
            ))}
          </div>
          <button className="add-scene-button" onClick={() => onNotify("A blank scene was added to the outline.")}><Plus size={14} />Add scene</button>
        </aside>

        <div className="scene-stage">
          <header className="scene-toolbar">
            <div><button title="Theme"><Palette size={14} /></button><button title="AI layout"><Sparkles size={14} /></button><i /><span>16:9 · 100%</span></div>
            <div><span className="scene-saved"><Check size={12} />Saved</span><button><MoreHorizontal size={15} /></button></div>
          </header>
          <div className="scene-canvas-wrap"><SlideCanvas scene={scene} /></div>
          <footer className="scene-notes"><span>Speaker notes</span><p>{scene.purpose}. Explain the confidence and boundary without reading the slide verbatim.</p></footer>
        </div>

        <aside className="scene-inspector">
          <div className="scene-inspector-tabs"><button className="is-active">Bindings</button><button>Design</button></div>
          <section>
            <header><span>Knowledge bindings</span><Link2 size={13} /></header>
            <p>Scene content stays connected to its originating knowledge.</p>
            <div className="binding-mode">
              <button className={bindingMode === "live" ? "is-active" : ""} onClick={() => setBindingMode("live")}><RefreshCw size={13} /><span><strong>Live</strong><small>Follow reviewed updates</small></span></button>
              <button className={bindingMode === "pinned" ? "is-active" : ""} onClick={() => setBindingMode("pinned")}><LockKeyhole size={13} /><span><strong>Pinned</strong><small>Keep this revision</small></span></button>
            </div>
          </section>
          <section>
            <header><span>Bound units</span><em>{boundUnits.length}</em></header>
            <div className="bound-unit-list">
              {boundUnits.map((unit) => <button key={unit.id}><i>{unit.title.slice(0, 1)}</i><span><strong>{unit.title}</strong><small>Revision 8 · {unit.claims.length} claims</small></span><ArrowRight size={12} /></button>)}
            </div>
            <button className="add-binding-button"><Plus size={13} />Bind knowledge</button>
          </section>
          <section className="upstream-state">
            <header><span>Upstream state</span><ShieldCheck size={13} /></header>
            <div><Check size={14} /><span><strong>Everything is current</strong><small>No reviewed knowledge changed.</small></span></div>
          </section>
          <section className="export-readiness">
            <header><span>Export readiness</span><FileCode2 size={13} /></header>
            <span><i /><em>Fonts embedded</em><Check size={11} /></span>
            <span><i /><em>Citations resolved</em><Check size={11} /></span>
            <span><i /><em>1 SVG fallback</em><ArrowRight size={11} /></span>
          </section>
        </aside>
      </section>
    </div>
  );
}
