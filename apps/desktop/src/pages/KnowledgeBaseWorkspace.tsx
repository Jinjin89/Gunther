import type {
  Artifact,
  ArtifactAudience,
  ArtifactFormat,
  ArtifactSummary,
  KnowledgeUnit,
  SourceDetail,
  SourceKind,
  SourceSummary,
  KnowledgeTopic,
} from "@gunther/contracts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AudioLines,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Camera,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Copy,
  Download,
  ExternalLink,
  FilePlus2,
  FileText,
  GitBranch,
  History,
  Layers3,
  Link2,
  LoaderCircle,
  MessageSquareText,
  Mic2,
  NotebookText,
  Play,
  Plus,
  Quote,
  Settings2,
  ShieldCheck,
  Sparkles,
  Target,
  UsersRound,
  X,
} from "lucide-react";
import type { AtlasMode, AtlasPopulation, AtlasZoom, KnowledgeBase, KnowledgeChapter } from "../atlas";
import { knowledgeApi, recordingAssetUrl, sourceAssetUrl } from "../api";
import { WebSnapshotCard } from "../components/WebSnapshotCard";
import { SourceEvidence } from "../components/SourceEvidence";
import { TopicManager } from "../components/TopicManager";
import { LibraryGlyph } from "../design/LibraryGlyph";
import "../knowledge.css";
import { SessionWorkspace } from "./SessionWorkspace";

interface KnowledgeBaseWorkspaceProps {
  base: KnowledgeBase;
  workspaceId: string | null;
  mode: AtlasMode;
  selectedChapterId: string;
  onMode: (mode: AtlasMode) => void;
  onChapter: (id: string) => void;
  onBack: () => void;
  onEvidence: (chapter: KnowledgeChapter) => void;
  onAdd: (kind?: "note" | "link" | "file" | "image" | "table" | "recording") => void;
  onRecord: (context: "lecture" | "meeting" | "memo") => void;
  onExport: () => void;
  onEdit: () => void;
  onNotify: (message: string) => void;
  /** Open a source in its own page; `queue` lists this library's sources for J / K. */
  onOpenSource?: (id: string, queue: string[]) => void;
}

const modeLabels: Array<{ id: AtlasMode; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "sources", label: "Sources" },
  { id: "ask", label: "Ask" },
  { id: "outputs", label: "Outputs" },
];

const materialLabel = (kind: SourceKind | "reference") => ({
  note: "Note",
  paper: "Paper",
  link: "Link",
  file: "Document",
  image: "Photo or scan",
  table: "Dataset",
  recording: "Recording",
  course: "Course",
  reference: "Reference",
})[kind];

const materialIcon = (kind: SourceKind | "reference") => kind === "recording" || kind === "course"
  ? AudioLines
  : kind === "link" || kind === "reference"
    ? Link2
    : kind === "note"
      ? NotebookText
      : FileText;

function BaseHeader({ base, mode, onMode, onBack, onExport, onEdit }: Pick<KnowledgeBaseWorkspaceProps, "base" | "mode" | "onMode" | "onBack" | "onExport" | "onEdit">) {
  return (
    <header className="base-workspace-header">
      <button className="base-back" onClick={onBack} aria-label="Back to library"><ArrowLeft size={15} /></button>
      <LibraryGlyph base={base} size="lg" />
      <div className="base-title-group">
        <span>{base.eyebrow}</span>
        <strong>{base.title}</strong>
      </div>
      <nav className="base-mode-switch" aria-label="Library views">
        {modeLabels.map((item) => <button key={item.id} className={mode === item.id ? "is-active" : ""} aria-current={mode === item.id ? "page" : undefined} onClick={() => onMode(item.id)}>{item.label}</button>)}
      </nav>
      <button className="base-edit" onClick={onEdit} aria-label="Edit library details" title="Edit library details"><Settings2 size={14} /></button>
      <button className="base-export" onClick={onExport}><Download size={14} />Export</button>
    </header>
  );
}

function ChapterRail({ base, selectedId, onSelect }: { base: KnowledgeBase; selectedId: string; onSelect: (id: string) => void }) {
  return (
    <aside className="chapter-rail">
      <div className="chapter-rail-heading">
        <span>The field</span>
        <small>{base.chapters.length} chapters</small>
      </div>
      <div className="chapter-spine">
        {base.chapters.map((chapter) => (
          <button key={chapter.id} className={selectedId === chapter.id ? "is-active" : ""} onClick={() => onSelect(chapter.id)}>
            <i><span /></i>
            <span className="chapter-number">{chapter.number}</span>
            <span className="chapter-rail-copy"><strong>{chapter.title}</strong><small>{chapter.question}</small></span>
            <span className={`chapter-status status-${chapter.status}`} />
          </button>
        ))}
      </div>
    </aside>
  );
}

function LearnView({ base, selectedChapterId, onChapter, onEvidence, onAdd }: Pick<KnowledgeBaseWorkspaceProps, "base" | "selectedChapterId" | "onChapter" | "onEvidence" | "onAdd">) {
  const chapterIndex = Math.max(0, base.chapters.findIndex((item) => item.id === selectedChapterId));
  const chapter = base.chapters[chapterIndex] ?? base.chapters[0]!;
  const sourceCount = chapter.sourceIds.length;
  const previous = base.chapters[chapterIndex - 1];
  const next = base.chapters[chapterIndex + 1];

  return (
    <div className="learn-layout page-enter">
      <ChapterRail base={base} selectedId={chapter.id} onSelect={onChapter} />
      <article className="chapter-reader" key={chapter.id}>
        <header className="chapter-reader-header">
          <span className="chapter-overline">Chapter {chapter.number} · {chapter.status}</span>
          <h1>{chapter.title}</h1>
          <p className="chapter-question">{chapter.question}</p>
          <p className="chapter-summary">{chapter.summary}</p>
        </header>

        <div className="chapter-decision">
          <Target size={17} />
          <span><small>{chapter.decision.label}</small><strong>{chapter.decision.answer}</strong></span>
        </div>

        <section className="chapter-section">
          <div className="chapter-section-title"><span>What this chapter establishes</span><small>{chapter.progress}% shaped</small></div>
          <div className="takeaway-list">
            {chapter.takeaways.map((takeaway) => <div key={takeaway}><Check size={14} /><p>{takeaway}</p></div>)}
          </div>
        </section>

        {chapter.topics.length > 0 && (
          <section className="chapter-section">
            <div className="chapter-section-title"><span>Inside this chapter</span><small>Open only when needed</small></div>
            <div className="topic-list">
              {chapter.topics.map((topic) => (
                <details key={topic.id} className="topic-row">
                  <summary>
                    <span><strong>{topic.title}</strong><small>{topic.summary}</small></span>
                    <i><PlusMinus /></i>
                  </summary>
                  {(topic.markers || topic.caution) && (
                    <div className="topic-detail">
                      {topic.markers && <div><span>Supporting signals</span><p>{topic.markers.map((marker) => <code key={marker}>{marker}</code>)}</p></div>}
                      {topic.caution && <div className="topic-caution"><CircleHelp size={14} /><p>{topic.caution}</p></div>}
                    </div>
                  )}
                </details>
              ))}
            </div>
          </section>
        )}

        <button className="evidence-callout" onClick={() => onEvidence(chapter)}>
          <span className="evidence-icon"><Quote size={16} /></span>
          <span><small>Grounding</small><strong>{sourceCount > 0 ? `${sourceCount} sources support this chapter` : "This outline is waiting for sources"}</strong></span>
          <ArrowRight size={15} />
        </button>

        <footer className="reader-pagination">
          <button disabled={!previous} onClick={() => previous && onChapter(previous.id)}><ChevronLeft size={15} /><span><small>Previous</small><strong>{previous?.title ?? "Beginning"}</strong></span></button>
          <button disabled={!next} onClick={() => next && onChapter(next.id)}><span><small>Next</small><strong>{next?.title ?? "Field complete"}</strong></span><ChevronRight size={15} /></button>
        </footer>
      </article>
      <aside className="chapter-context">
        <span className="context-label">Why it matters</span>
        <p>{chapter.question}</p>
        <div className="chapter-progress-ring" style={{ "--progress": `${chapter.progress * 3.6}deg` } as React.CSSProperties}>
          <span><strong>{chapter.progress}%</strong><small>shaped</small></span>
        </div>
        <div className="context-note"><Sparkles size={14} /><p>The configured interpretation engine can suggest connections, but only your accepted changes become trusted knowledge.</p></div>
        <button onClick={() => onAdd()}>Add to this chapter <ArrowRight size={13} /></button>
      </aside>
    </div>
  );
}

function OverviewView({ base, onMode, onAdd }: Pick<KnowledgeBaseWorkspaceProps, "base" | "onMode" | "onAdd">) {
  const shapedChapters = base.chapters.filter((chapter) => chapter.status === "grounded").length;
  const sourceCount = base.indexedSourceCount ?? base.sourceCount;
  return <div className="base-overview page-enter">
    <header className="base-overview-hero">
      <div><span className="atlas-eyebrow">Library overview</span><h1>{base.title}</h1><p>{base.description}</p></div>
      <button className="primary-button" onClick={() => onAdd()}>Add a source</button>
    </header>
    <blockquote><small>Guiding question</small><p>{base.question}</p></blockquote>
    <section className="base-overview-actions" aria-label="Library activities">
      <button onClick={() => onMode("sources")}><span className="material-action-icon"><FileText size={18} /></span><span><strong>Sources</strong><small>{sourceCount} preserved · add, read, and organize originals</small></span><ArrowRight size={14} /></button>
      <button onClick={() => onMode("ask")}><span className="material-action-icon is-audio"><MessageSquareText size={18} /></span><span><strong>Ask</strong><small>Explore this library with answers grounded in its sources</small></span><ArrowRight size={14} /></button>
      <button onClick={() => onMode("outputs")}><span className="material-action-icon is-meeting"><Sparkles size={18} /></span><span><strong>Outputs</strong><small>Turn accepted knowledge into notes, guides, and briefs</small></span><ArrowRight size={14} /></button>
    </section>
    <div className="base-overview-grid">
      <section><header><strong>Library health</strong><small>Only reviewed knowledge counts</small></header><div className="base-health-stats"><span><strong>{sourceCount}</strong><small>sources</small></span><span><strong>{shapedChapters}</strong><small>grounded areas</small></span><span><strong>{base.progress}%</strong><small>shaped</small></span></div></section>
      <section><header><strong>Knowledge areas</strong><small>{base.chapters.length} working areas</small></header><div className="overview-area-list">{base.chapters.slice(0, 5).map((chapter) => <article key={chapter.id}><i className={`status-${chapter.status}`} /><span><strong>{chapter.title}</strong><small>{chapter.question}</small></span><em>{chapter.status}</em></article>)}</div></section>
    </div>
  </div>;
}

function MaterialsView({ base, onAdd, onOpenSource }: Pick<KnowledgeBaseWorkspaceProps, "base" | "onAdd" | "onOpenSource">) {
  const [sources, setSources] = useState<SourceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null);
  const [selectedSource, setSelectedSource] = useState<SourceDetail | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [reviewingAssertionId, setReviewingAssertionId] = useState<string | null>(null);

  const closeSource = useCallback(() => {
    setSelectedSourceId(null);
    setSelectedSource(null);
    setSourceError(null);
  }, []);

  const openSource = useCallback((sourceId: string) => {
    // Clear the previous payload in the same render that changes the id so a
    // fast row switch can never flash details from the earlier source.
    setSelectedSource(null);
    setSourceError(null);
    setSelectedSourceId(sourceId);
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setSources(await knowledgeApi.sources(base.id));
      setUnavailable(false);
    } catch {
      setUnavailable(true);
    } finally {
      setLoading(false);
    }
  }, [base.id]);

  useEffect(() => {
    void refresh();
    const onSourcesUpdated = () => void refresh();
    window.addEventListener("gunther:sources-updated", onSourcesUpdated);
    return () => window.removeEventListener("gunther:sources-updated", onSourcesUpdated);
  }, [refresh]);

  useEffect(() => {
    if (!sources.some((source) => ["queued", "running", "pending"].includes(source.processing?.state ?? ""))) return;
    let active = true;
    const timer = window.setTimeout(() => {
      void knowledgeApi.sources(base.id).then((items) => { if (active) setSources(items); }).catch(() => { if (active) setUnavailable(true); });
    }, 2500);
    return () => { active = false; window.clearTimeout(timer); };
  }, [base.id, sources]);

  useEffect(() => {
    const sourceKey = `gunther:open-source:${base.id}`;
    const sourceId = window.localStorage.getItem(sourceKey);
    if (!sourceId) return;
    window.localStorage.removeItem(sourceKey);
    if (onOpenSource) onOpenSource(sourceId, [sourceId]);
    else openSource(sourceId);
  }, [base.id, onOpenSource, openSource]);

  useEffect(() => {
    if (!selectedSourceId) return;
    let active = true;
    setSelectedSource(null);
    setSourceError(null);
    void knowledgeApi.source(selectedSourceId).then((source) => {
      if (active) setSelectedSource(source);
    }).catch((reason) => {
      if (active) setSourceError(reason instanceof Error ? reason.message : "This source could not be opened.");
    });
    return () => { active = false; };
  }, [selectedSourceId]);

  useEffect(() => {
    if (!selectedSourceId) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeSource();
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [closeSource, selectedSourceId]);

  const reviewAssertion = async (assertionId: string, status: "verified" | "disputed") => {
    setReviewingAssertionId(assertionId);
    try {
      const updated = await knowledgeApi.updateAssertionStatus(assertionId, {
        status,
        reason: status === "verified" ? "Reviewed from the source detail" : "Disputed from the source detail",
      });
      setSelectedSource((current) => current ? {
        ...current,
        assertions: current.assertions.map((assertion) => assertion.id === updated.id ? updated : assertion),
      } : current);
      window.dispatchEvent(new CustomEvent("gunther:inbox-updated"));
    } finally {
      setReviewingAssertionId(null);
    }
  };

  const materials = sources.length > 0
    ? sources.map((source) => ({
      id: source.id,
      title: source.title,
      kind: source.kind as SourceKind | "reference",
      detail: `${source.processing?.state ?? "preserved"} · ${source.assertionCount} candidate claims · ${source.entityCount} entities`,
      date: new Date(source.createdAt).toLocaleDateString(),
      indexed: true,
    }))
    : base.sources.map((source) => ({
      id: source.id,
      title: source.title,
      kind: "reference" as const,
      detail: source.scope,
      date: source.publisher,
      indexed: false,
    }));
  const recordingCount = sources.filter((source) => source.kind === "recording" || source.kind === "course").length;

  return (
    <div className="materials-view page-enter">
      <header className="materials-intro">
        <div><span className="atlas-eyebrow">Sources</span><h1>Original material, kept in context.</h1><p>Documents, recordings, photos, links, data, and notes are all first-class sources. Every derivative stays connected to its original.</p></div>
        <button className="primary-button" onClick={() => onAdd()}><FilePlus2 size={15} />Add source</button>
      </header>

      <section className="material-action-grid is-source-types" aria-label="Source types">
        <button onClick={() => onAdd("file")}><span className="material-action-icon"><FileText size={18} /></span><span><strong>Document</strong><small>Paper, slides, Markdown, text, or data</small></span><ArrowRight size={14} /></button>
        <button onClick={() => onAdd("image")}><span className="material-action-icon is-photo"><Camera size={18} /></span><span><strong>Photo or scan</strong><small>Page, whiteboard, diagram, or screenshot</small></span><ArrowRight size={14} /></button>
        <button onClick={() => onAdd("link")}><span className="material-action-icon is-link"><Link2 size={18} /></span><span><strong>Web page</strong><small>URL plus the context you want to remember</small></span><ArrowRight size={14} /></button>
        <button onClick={() => onAdd("recording")}><span className="material-action-icon is-audio"><Mic2 size={18} /></span><span><strong>Recording</strong><small>Live transcript or imported audio</small></span><ArrowRight size={14} /></button>
      </section>

      <section className="material-collection">
        <header><span><strong>All sources</strong><small>{(() => { const count = materials.length || base.indexedSourceCount || 0; return `${count} ${count === 1 ? "source" : "sources"} · ${recordingCount} ${recordingCount === 1 ? "recording" : "recordings"}`; })()}</small></span><div><button onClick={() => onAdd()}><FilePlus2 size={13} />Add source</button></div></header>
        {materials.length > 0 && <div className="material-list">{materials.map((material) => {
          const Icon = materialIcon(material.kind);
          return <button type="button" className="material-row" disabled={!material.indexed} key={material.id} onClick={() => onOpenSource ? onOpenSource(material.id, materials.filter((item) => item.indexed).map((item) => item.id)) : openSource(material.id)}><span className={`material-kind-icon kind-${material.kind}`}><Icon size={16} /></span><span><small>{materialLabel(material.kind)} · {material.date}</small><strong>{material.title}</strong><p>{material.detail}</p></span><span className="material-state"><i />{material.indexed ? "Open source" : "Reference"}<ChevronRight size={13} /></span></button>;
        })}</div>}
        {!loading && materials.length === 0 && <div className="materials-empty"><span><Layers3 size={22} /></span><h2>This library is ready for its first source.</h2><p>Start with whichever source you already have. You never need to begin with a particular format.</p><div><button className="primary-button" onClick={() => onAdd()}><FilePlus2 size={14} />Choose a source</button></div></div>}
        {loading && materials.length === 0 && <div className="materials-loading">Opening this library’s materials…</div>}
        {unavailable && materials.length > 0 && <p className="materials-offline">Showing curated references. Indexed local materials will appear when the knowledge service reconnects.</p>}
      </section>
      {selectedSourceId && createPortal(<div className="source-detail-overlay" role="presentation" onMouseDown={closeSource}>
        <aside className="source-detail-drawer" role="dialog" aria-modal="true" aria-label={selectedSource?.title ?? "Source detail"} onMouseDown={(event) => event.stopPropagation()} tabIndex={-1} autoFocus>
          <header><span><small>Original source</small><strong>{selectedSource?.title ?? "Opening source…"}</strong></span><button type="button" onClick={closeSource} aria-label="Close source"><X size={16} /></button></header>
          {!selectedSource && !sourceError && <div className="source-detail-loading"><LoaderCircle className="spin" size={18} />Opening the preserved source…</div>}
          {sourceError && <div className="source-detail-error"><CircleHelp size={18} /><span><strong>Source unavailable</strong><small>{sourceError}</small></span></div>}
          {selectedSource && <div className="source-detail-body">
            <div className="source-detail-meta"><span>{materialLabel(selectedSource.kind)}</span><span>{new Date(selectedSource.createdAt).toLocaleString()}</span><span>{selectedSource.assertionCount} suggestions</span></div>
            {selectedSource.webSnapshot && <WebSnapshotCard snapshot={selectedSource.webSnapshot} />}
            <SourceEvidence key={selectedSource.id} sourceId={selectedSource.id} baseId={base.id} assetId={selectedSource.asset?.id} />
            {selectedSource.asset?.mediaType.startsWith("image/") && <img className="source-image-preview" src={sourceAssetUrl(selectedSource.asset.id)} alt={selectedSource.title} />}
            {selectedSource.asset && !selectedSource.webSnapshot && <section className="source-original-card"><span><FileText size={18} /><span><strong>{selectedSource.asset.originalName}</strong><small>{selectedSource.asset.mediaType} · {selectedSource.asset.sizeBytes >= 1_048_576 ? `${(selectedSource.asset.sizeBytes / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(selectedSource.asset.sizeBytes / 1024))} KB`} · original unchanged</small></span></span><a href={sourceAssetUrl(selectedSource.asset.id)} download={selectedSource.asset.originalName}><Download size={14} />Download original</a></section>}
            {(() => { const recordingId = selectedSource.content.match(/Local recording:\s*(rec_[a-f0-9]{24})/)?.[1]; return recordingId ? <section className="source-recording-player"><span><AudioLines size={16} /><strong>Original recording</strong></span><audio controls src={recordingAssetUrl(recordingId)} /></section> : null; })()}
            <section className="source-readable-content"><header><strong>Readable content</strong><small>{selectedSource.content.length > 30_000 ? "Preview · full text remains indexed" : "Preserved text and extraction"}</small></header><pre>{selectedSource.content.slice(0, 30_000)}</pre></section>
            <section className="source-claim-review"><header><strong>AI suggestions</strong><small>Nothing becomes trusted knowledge until you decide.</small></header>{selectedSource.assertions.length ? <div>{selectedSource.assertions.map((assertion) => <article key={assertion.id}><span><small>{assertion.evidence[0]?.locator ?? "source text"}</small><strong>{assertion.subject.label} <em>{assertion.predicate.replaceAll("_", " ")}</em> {assertion.object.label}</strong><blockquote>“{assertion.evidence[0]?.quote ?? "Evidence unavailable"}”</blockquote></span><div><i className={`claim-status is-${assertion.status}`}>{assertion.status}</i>{assertion.status === "provisional" && <><button type="button" disabled={reviewingAssertionId === assertion.id} onClick={() => void reviewAssertion(assertion.id, "disputed")}>Dispute</button><button type="button" className="is-verify" disabled={reviewingAssertionId === assertion.id} onClick={() => void reviewAssertion(assertion.id, "verified")}><Check size={12} />Verify</button></>}</div></article>)}</div> : <p>No explicit knowledge suggestions were extracted. The original remains available as evidence.</p>}</section>
          </div>}
        </aside>
      </div>, document.body)}
    </div>
  );
}

function PlusMinus() {
  return <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M2 6h8M6 2v8" /></svg>;
}

function FieldMap({ base, selectedId, onChapter }: { base: KnowledgeBase; selectedId: string; onChapter: (id: string) => void }) {
  return (
    <div className="field-map">
      <div className="field-map-line" />
      {base.chapters.map((chapter, index) => (
        <button key={chapter.id} className={`field-map-node ${selectedId === chapter.id ? "is-active" : ""}`} onClick={() => onChapter(chapter.id)} style={{ "--node-index": index } as React.CSSProperties}>
          <span className="field-node-orbit"><i /></span>
          <small>{chapter.number}</small>
          <strong>{chapter.title}</strong>
          <em>{chapter.progress}%</em>
        </button>
      ))}
    </div>
  );
}

function ChapterMap({ base, chapter, onEvidence }: { base: KnowledgeBase; chapter: KnowledgeChapter; onEvidence: () => void }) {
  return (
    <div className="chapter-map">
      <div className="chapter-map-center">
        <span>{chapter.number}</span>
        <strong>{chapter.title}</strong>
        <small>{chapter.question}</small>
      </div>
      {chapter.topics.map((topic, index) => {
        const count = Math.max(chapter.topics.length, 1);
        const angle = ((index / count) * Math.PI * 2) - Math.PI / 2;
        const x = 50 + Math.cos(angle) * 35;
        const y = 50 + Math.sin(angle) * 34;
        return <div key={topic.id} className="chapter-topic-node" style={{ left: `${x}%`, top: `${y}%` }}><i /><strong>{topic.title}</strong><small>{topic.markers?.slice(0, 3).join(" · ") || "Working topic"}</small></div>;
      })}
      {chapter.topics.length === 0 && <div className="empty-map-note">Add sources to grow topics inside this chapter.</div>}
      <button className="map-evidence-button" onClick={onEvidence}><BookOpen size={14} />{chapter.sourceIds.length} sources</button>
      <svg className="chapter-map-rings" viewBox="0 0 800 500" preserveAspectRatio="none" aria-hidden="true"><ellipse cx="400" cy="250" rx="274" ry="173" /><ellipse cx="400" cy="250" rx="165" ry="105" /></svg>
    </div>
  );
}

function PopulationMap({ populations }: { populations: AtlasPopulation[] }) {
  const [selectedId, setSelectedId] = useState(populations[0]?.id ?? "");
  const selected = populations.find((population) => population.id === selectedId) ?? populations[0];
  return (
    <div className="population-workspace">
      <div className="population-map">
        <div className="population-cloud cloud-one" /><div className="population-cloud cloud-two" /><div className="population-cloud cloud-three" />
        {populations.map((population) => (
          <button key={population.id} className={`population-node family-${population.family.toLowerCase().replaceAll(" ", "-")} ${selectedId === population.id ? "is-active" : ""}`} style={{ left: `${population.x}%`, top: `${population.y}%` }} onClick={() => setSelectedId(population.id)}>
            <i />{population.label}<small>{Math.round(population.confidence * 100)}%</small>
          </button>
        ))}
        <span className="map-axis map-axis-x">transcriptional neighborhood →</span>
        <span className="map-axis map-axis-y">relative structure →</span>
      </div>
      {selected && <aside className="population-detail">
        <span className="atlas-eyebrow">Demo population</span>
        <h3>{selected.label}</h3>
        <p>{selected.note}</p>
        <div className="confidence-bar"><span><small>Annotation confidence</small><strong>{Math.round(selected.confidence * 100)}%</strong></span><i><b style={{ width: `${selected.confidence * 100}%` }} /></i></div>
        <div className="marker-program"><small>Supporting program</small><p>{selected.markers.map((marker) => <code key={marker}>{marker}</code>)}</p></div>
        <div className="demo-warning"><ShieldCheck size={14} /><span>Illustrative PBMC 3k labels. Validate against your own data and experimental context.</span></div>
      </aside>}
    </div>
  );
}

function AtlasView({ base, selectedChapterId, onChapter, onEvidence }: Pick<KnowledgeBaseWorkspaceProps, "base" | "selectedChapterId" | "onChapter" | "onEvidence">) {
  const [zoom, setZoom] = useState<AtlasZoom>("field");
  const chapter = base.chapters.find((item) => item.id === selectedChapterId) ?? base.chapters[0]!;
  const hasPopulations = (base.populations?.length ?? 0) > 0;
  return (
    <div className="atlas-map-view page-enter">
      <header className="map-view-header">
        <div><span className="atlas-eyebrow">Semantic atlas</span><h1>{zoom === "field" ? "The whole field, at a glance." : zoom === "chapter" ? chapter.title : "Cell identity landscape"}</h1><p>{zoom === "field" ? base.question : zoom === "chapter" ? chapter.question : "A demonstration view of broad PBMC populations and annotation confidence."}</p></div>
        <div className="zoom-switch" aria-label="Atlas detail level">
          {(["field", "chapter", "topic"] as AtlasZoom[]).map((level) => <button key={level} disabled={level === "topic" && !hasPopulations} className={zoom === level ? "is-active" : ""} onClick={() => setZoom(level)}>{level}</button>)}
        </div>
      </header>
      <div className="atlas-canvas">
        {zoom === "field" && <FieldMap base={base} selectedId={chapter.id} onChapter={(id) => { onChapter(id); setZoom("chapter"); }} />}
        {zoom === "chapter" && <ChapterMap base={base} chapter={chapter} onEvidence={() => onEvidence(chapter)} />}
        {zoom === "topic" && base.populations && <PopulationMap populations={base.populations} />}
      </div>
      <footer className="atlas-legend"><span><i className="legend-grounded" />Grounded</span><span><i className="legend-growing" />Growing</span><span><GitBranch size={13} />Click a node to move from field → chapter → evidence</span></footer>
    </div>
  );
}

const studioAudiences: Array<{ value: ArtifactAudience; label: string }> = [
  { value: "scientist", label: "Scientist" },
  { value: "student", label: "Student" },
  { value: "collaborator", label: "Collaborator" },
];
const studioFormats: Array<{ value: ArtifactFormat; label: string }> = [
  { value: "field_guide", label: "Field guide" },
  { value: "teaching_path", label: "Teaching path" },
  { value: "decision_brief", label: "Decision brief" },
];
const studioFormatLabel = (value: ArtifactFormat) => (
  studioFormats.find((item) => item.value === value)?.label ?? value
);
const studioAudienceLabel = (value: ArtifactAudience) => (
  studioAudiences.find((item) => item.value === value)?.label ?? value
);
const artifactRequestId = () => globalThis.crypto?.randomUUID?.()
  ?? `artifact_${Date.now()}_${Math.random().toString(36).slice(2)}`;
type ArtifactGenerationAttempt = { key: string; requestId: string };
const artifactAttemptStorageKey = (workspaceId: string, baseId: string) =>
  `gunther:artifact-attempt:${workspaceId}:${baseId}`;
const loadArtifactGenerationAttempt = (workspaceId: string, baseId: string): ArtifactGenerationAttempt | null => {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(artifactAttemptStorageKey(workspaceId, baseId)) ?? "null") as Partial<ArtifactGenerationAttempt> | null;
    if (!parsed || typeof parsed.key !== "string" || typeof parsed.requestId !== "string" || !/^[A-Za-z0-9_-]{8,128}$/.test(parsed.requestId)) return null;
    return { key: parsed.key, requestId: parsed.requestId };
  } catch {
    return null;
  }
};
const saveArtifactGenerationAttempt = (workspaceId: string, baseId: string, attempt: ArtifactGenerationAttempt) => {
  try {
    window.localStorage.setItem(artifactAttemptStorageKey(workspaceId, baseId), JSON.stringify(attempt));
  } catch {
    // The in-memory key still protects retries for the current app session.
  }
};
const clearArtifactGenerationAttempt = (workspaceId: string, baseId: string) => {
  try {
    window.localStorage.removeItem(artifactAttemptStorageKey(workspaceId, baseId));
  } catch {
    // A failed cleanup is harmless: a different request shape replaces it.
  }
};

export function StudioView({ base, workspaceId, onNotify, onMode }: Pick<KnowledgeBaseWorkspaceProps, "base" | "workspaceId" | "onNotify" | "onMode">) {
  const [audience, setAudience] = useState<ArtifactAudience>("scientist");
  const [format, setFormat] = useState<ArtifactFormat>("field_guide");
  const [units, setUnits] = useState<KnowledgeUnit[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([]);
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [openingArtifactId, setOpeningArtifactId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const generationAttemptRef = useRef<ArtifactGenerationAttempt | null>(null);

  const openArtifact = useCallback(async (summary: ArtifactSummary) => {
    if (!workspaceId) {
      setError("The local workspace identity has not been verified yet.");
      return;
    }
    setOpeningArtifactId(summary.id);
    try {
      const detail = await knowledgeApi.artifact(base.id, summary.id, workspaceId);
      setSelectedArtifact(detail);
      setAudience(detail.audience);
      setFormat(detail.format);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The saved Output could not be reopened.");
    } finally {
      setOpeningArtifactId(null);
    }
  }, [base.id, workspaceId]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setSelectedArtifact(null);
    setArtifacts([]);
    generationAttemptRef.current = workspaceId ? loadArtifactGenerationAttempt(workspaceId, base.id) : null;
    if (!workspaceId) {
      setUnits([]);
      setError("The local workspace identity has not been verified yet.");
      setLoading(false);
      return () => { active = false; };
    }
    void (async () => {
      try {
        const [unitItems, history] = await Promise.all([
          knowledgeApi.knowledgeUnits(base.id),
          knowledgeApi.artifacts(base.id, workspaceId),
        ]);
        if (!active) return;
        setUnits(unitItems.filter((item) => item.status === "trusted"));
        setArtifacts(history);
        setError(null);
        if (history[0]) {
          const detail = await knowledgeApi.artifact(base.id, history[0].id, workspaceId);
          if (!active) return;
          setSelectedArtifact(detail);
          setAudience(detail.audience);
          setFormat(detail.format);
        }
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "Output history could not be loaded.");
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => { active = false; };
  }, [base.id, workspaceId]);

  const selectedLineageHead = useMemo(() => {
    if (!selectedArtifact) return null;
    return artifacts
      .filter((item) => item.lineageId === selectedArtifact.lineageId)
      .sort((left, right) => right.versionNumber - left.versionNumber)[0] ?? null;
  }, [artifacts, selectedArtifact]);
  const selectedIsHead = !selectedArtifact
    || !selectedLineageHead
    || selectedLineageHead.id === selectedArtifact.id;

  const generateArtifact = async () => {
    if (!workspaceId) {
      setError("The local workspace identity must be verified before creating an Output.");
      return;
    }
    if (selectedArtifact && !selectedIsHead && selectedLineageHead) {
      await openArtifact(selectedLineageHead);
      onNotify(`Opened the current lineage head, version ${selectedLineageHead.versionNumber}. Review it before generating another version.`);
      return;
    }
    const requestKey = JSON.stringify({
      baseId: base.id,
      audience,
      format,
      acceptedUnitIds: units.map((unit) => unit.id),
      supersedesArtifactId: selectedArtifact?.id ?? null,
    });
    const pending = generationAttemptRef.current ?? loadArtifactGenerationAttempt(workspaceId, base.id);
    const requestId = pending?.key === requestKey ? pending.requestId : artifactRequestId();
    generationAttemptRef.current = { key: requestKey, requestId };
    saveArtifactGenerationAttempt(workspaceId, base.id, generationAttemptRef.current);
    setGenerating(true);
    setError(null);
    try {
      const created = await knowledgeApi.createArtifact(base.id, {
        clientRequestId: requestId,
        format,
        audience,
        acceptedUnitIds: units.map((unit) => unit.id),
        ...(selectedArtifact ? { supersedesArtifactId: selectedArtifact.id } : {}),
      }, workspaceId);
      generationAttemptRef.current = null;
      clearArtifactGenerationAttempt(workspaceId, base.id);
      setSelectedArtifact(created);
      const history = await knowledgeApi.artifacts(base.id, workspaceId);
      setArtifacts(history);
      onNotify(`${studioFormatLabel(created.format)} version ${created.versionNumber} saved to Output history.`);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "The Output could not be saved.";
      setError(message);
      if (/current Artifact lineage head|conflicted with another writer/i.test(message)) {
        try {
          const history = await knowledgeApi.artifacts(base.id, workspaceId);
          setArtifacts(history);
          const latest = selectedArtifact
            ? history.find((item) => item.lineageId === selectedArtifact.lineageId)
            : history[0];
          if (latest) await openArtifact(latest);
          onNotify("Output history changed in another request. Gunther refreshed the latest saved version.");
        } catch {
          // Keep the original conflict visible if the refresh is also unavailable.
        }
      }
    } finally {
      setGenerating(false);
    }
  };

  const downloadMarkdown = () => {
    if (!selectedArtifact) return;
    const url = URL.createObjectURL(new Blob([selectedArtifact.content], { type: "text/markdown;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${base.id}-${selectedArtifact.format.replaceAll("_", "-")}-v${selectedArtifact.versionNumber}.md`;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
    onNotify(`Exported saved Output version ${selectedArtifact.versionNumber} with pinned provenance.`);
  };

  const copyMarkdown = async () => {
    if (!selectedArtifact) return;
    try {
      await navigator.clipboard.writeText(selectedArtifact.content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_500);
      onNotify(`Saved Output version ${selectedArtifact.versionNumber} copied as Markdown.`);
    } catch {
      onNotify("Clipboard access was unavailable. Download the saved Markdown file instead.");
    }
  };

  return (
    <div className="studio-view page-enter">
      <header className="studio-intro"><span className="atlas-eyebrow">Outputs</span><h1>Turn reviewed knowledge into something useful.</h1><p>Every build is saved as an immutable version with pinned Knowledge Unit revisions. Reopen or export the exact result later.</p></header>
      <div className="studio-layout">
        <section className="studio-controls">
          <div className="studio-control-group"><label>Audience</label><div>{studioAudiences.map((item) => <button key={item.value} className={audience === item.value ? "is-active" : ""} onClick={() => setAudience(item.value)}>{item.label}</button>)}</div></div>
          <div className="studio-control-group"><label>Output</label><div>{studioFormats.map((item) => <button key={item.value} className={format === item.value ? "is-active" : ""} onClick={() => setFormat(item.value)}>{item.label}</button>)}</div></div>
          <div className="studio-source-summary"><Layers3 size={16} /><span><strong>{loading ? "Loading accepted knowledge…" : `${units.length} accepted unit${units.length === 1 ? "" : "s"}`}</strong><small>{units.reduce((sum, unit) => sum + unit.evidenceCount, 0)} evidence links · current trusted revisions</small></span>{loading ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />}</div>
          <button className="studio-generate" disabled={loading || generating || units.length === 0 || !workspaceId} onClick={() => void generateArtifact()}>{generating ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}{selectedArtifact ? selectedIsHead ? `Generate version ${selectedArtifact.versionNumber + 1}` : `Open latest version ${selectedLineageHead?.versionNumber ?? ""}` : "Build and save output"}</button>
          {selectedArtifact && <div className="studio-export-actions"><button type="button" onClick={() => void copyMarkdown()}>{copied ? <Check size={13} /> : <Copy size={13} />}{copied ? "Copied" : "Copy saved Markdown"}</button><button type="button" onClick={downloadMarkdown}><Download size={13} />Download .md</button></div>}
          <section className="studio-history">
            <header><span><History size={13} /><strong>Output history</strong></span><button type="button" onClick={() => { setSelectedArtifact(null); generationAttemptRef.current = null; if (workspaceId) clearArtifactGenerationAttempt(workspaceId, base.id); setError(null); }}><Plus size={12} />New</button></header>
            {artifacts.length === 0 && !loading ? <p>No saved Outputs yet.</p> : <div>{artifacts.map((item) => <button type="button" key={item.id} className={selectedArtifact?.id === item.id ? "is-active" : ""} disabled={openingArtifactId === item.id} onClick={() => void openArtifact(item)}><span><strong>{item.title}</strong><small>{studioFormatLabel(item.format)} · {studioAudienceLabel(item.audience)} · {item.unitCount} pinned units</small></span><em>{openingArtifactId === item.id ? "Opening…" : `v${item.versionNumber}`}</em></button>)}</div>}
          </section>
          {error && <p className="studio-control-error" role="status"><CircleHelp size={13} />{error}</p>}
        </section>
        <article className={`studio-document ${selectedArtifact ? "is-generated" : ""}`}>
          <header><span>Gunther · {selectedArtifact ? `${studioFormatLabel(selectedArtifact.format)} · v${selectedArtifact.versionNumber}` : studioFormatLabel(format)}</span><small>{selectedArtifact ? `Saved ${new Date(selectedArtifact.createdAt).toLocaleString()}` : `For a ${audience}`}</small></header>
          <h2>{selectedArtifact?.title ?? base.title}</h2>
          <p className="studio-deck">{selectedArtifact?.provenance.knowledgeBaseQuestion ?? base.question}</p>
          {!selectedArtifact && error && <div className="studio-output-empty is-error"><CircleHelp size={20} /><strong>Output history is unavailable</strong><p>{error}</p></div>}
          {!selectedArtifact && !loading && !error && units.length === 0 && <div className="studio-output-empty"><ShieldCheck size={21} /><strong>No accepted knowledge yet</strong><p>Ask a grounded question, turn a useful answer into a suggestion, then accept it in Inbox. Outputs never fabricate a finished library from raw sources.</p><button type="button" className="quiet-button" onClick={() => onMode("ask")}>Go to Ask <ArrowRight size={12} /></button></div>}
          {selectedArtifact?.unitSnapshots.map((snapshot, index) => <section key={snapshot.unitId}><span>{String(index + 1).padStart(2, "0")}</span><div><h3>{snapshot.title}</h3><p>{snapshot.content}</p><small>Pinned revision {snapshot.revisionNumber} · {snapshot.evidenceCount} evidence link{snapshot.evidenceCount === 1 ? "" : "s"} · revision ID {snapshot.revisionId} · source message {snapshot.sourceMessageId}</small></div></section>)}
          {!selectedArtifact && !loading && !error && units.length > 0 && <div className="studio-output-empty"><Sparkles size={21} /><strong>Ready to create a saved Output</strong><p>Choose the audience and shape on the left. Building creates version 1; later regeneration appends a new immutable version.</p></div>}
          <footer><ShieldCheck size={14} />{selectedArtifact ? `Accepted-only snapshot · manifest ${selectedArtifact.manifestHash.slice(0, 12)}… · ${selectedArtifact.acceptedUnitIds.length} pinned revisions` : "Every saved section pins an accepted unit revision and its evidence provenance."}</footer>
        </article>
      </div>
    </div>
  );
}

export function KnowledgeBaseWorkspace(props: KnowledgeBaseWorkspaceProps) {
  const [topics, setTopics] = useState<KnowledgeTopic[]>([]);
  const [topicError, setTopicError] = useState("");
  const [topicReload, setTopicReload] = useState(0);
  useEffect(() => {
    let active = true;
    setTopics([]);
    void knowledgeApi.topics(props.base.id).then((items) => { if (active) { setTopics(items); setTopicError(""); } }).catch(() => { if (active) setTopicError("Topics could not be loaded. Existing sources remain available."); });
    const update = () => setTopicReload((value) => value + 1);
    window.addEventListener("gunther:topics-updated", update);
    return () => { active = false; window.removeEventListener("gunther:topics-updated", update); };
  }, [props.base.id, topicReload]);
  const topicBase = useMemo(() => ({ ...props.base, chapters: [
    ...props.base.chapters,
    ...topics.map((topic): KnowledgeChapter => ({
      id: topic.id, number: "", title: topic.title, question: topic.description,
      summary: topic.description, status: "outline", progress: 0, sourceIds: [],
      takeaways: [], topics: [], decision: { label: "Topic", answer: "Explore linked evidence" },
    })),
  ] }), [props.base, topics]);
  const askTopic = async (id: string) => {
    try {
      const session = await knowledgeApi.createSession(props.base.id, { focusChapterId: id, selectedSourceIds: [] });
      window.localStorage.setItem(`gunther:active-session:${props.base.id}`, session.id);
      props.onChapter(id); props.onMode("ask");
    } catch (reason) { props.onNotify(reason instanceof Error ? reason.message : "Could not open topic conversation."); }
  };
  return (
    <div className="base-workspace">
      <BaseHeader {...props} />
      <div className="base-workspace-body">
        {props.mode === "overview" && <OverviewView {...props} />}
        {props.mode === "overview" && <>{topicError && <p role="alert">{topicError}</p>}<TopicManager key={props.base.id} baseId={props.base.id} topics={topics} onChange={() => setTopicReload((value) => value + 1)} onAsk={(id) => void askTopic(id)} /></>}
        {props.mode === "sources" && <MaterialsView {...props} />}
        {props.mode === "ask" && <SessionWorkspace base={topicBase} selectedChapterId={props.selectedChapterId} onChapter={props.onChapter} onAdd={props.onAdd} onEvidence={props.onEvidence} onNotify={props.onNotify} />}
        {props.mode === "outputs" && <StudioView {...props} />}
      </div>
    </div>
  );
}
