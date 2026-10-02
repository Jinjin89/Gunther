import type {
  SourceDetail,
  SourceKind,
  SourceSummary,
  KnowledgeTopic,
} from "@gunther/contracts";
import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import {
  AudioLines,
  ArrowLeft,
  ArrowRight,
  Camera,
  Check,
  ChevronRight,
  CircleHelp,
  Download,
  FilePlus2,
  FileText,
  Layers3,
  Link2,
  LoaderCircle,
  MessageSquareText,
  Mic2,
  NotebookText,
  Settings2,
  Sparkles,
  X,
} from "lucide-react";
import type { AtlasMode, KnowledgeBase, KnowledgeChapter } from "../atlas";
import { knowledgeApi, recordingAssetUrl, sourceAssetUrl } from "../api";
import { WebSnapshotCard } from "../components/WebSnapshotCard";
import { SourceEvidence } from "../components/SourceEvidence";
import { TopicManager } from "../components/TopicManager";
import { BulkImport } from "../components/BulkImport";
import { LibraryGlyph } from "../design/LibraryGlyph";
import "../knowledge.css";
import { OutputsPage } from "../outputs/OutputsPage";
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
  { id: "ask", label: "Ask" },
  { id: "overview", label: "Overview" },
  { id: "sources", label: "Sources" },
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
        <BulkImport knowledgeBaseId={base.id} />
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

export function KnowledgeBaseWorkspace(props: KnowledgeBaseWorkspaceProps) {
  const [topics, setTopics] = useState<KnowledgeTopic[]>([]);
  const [topicError, setTopicError] = useState("");
  const [topicReload, setTopicReload] = useState(0);
  // A conversation chosen as the material of a new report (from a research answer), used once.
  const [reportFrom, setReportFrom] = useState<string | null>(null);
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
        {props.mode === "overview" && <>{topicError && <p role="alert">{topicError}</p>}<TopicManager key={props.base.id} baseId={props.base.id} topics={topics} onChange={() => setTopicReload((value) => value + 1)} onAsk={(id) => void askTopic(id)} {...(props.onOpenSource ? { onOpenSource: (id: string) => props.onOpenSource?.(id, [id]) } : {})} /></>}
        {props.mode === "sources" && <MaterialsView {...props} />}
        {props.mode === "ask" && <SessionWorkspace base={topicBase} selectedChapterId={props.selectedChapterId} onAdd={props.onAdd} onNotify={props.onNotify} onOpenSource={(id) => props.onOpenSource?.(id, [id])} onMakeReport={(sessionId) => { setReportFrom(sessionId); props.onMode("outputs"); }} />}
        {props.mode === "outputs" && <OutputsPage base={props.base} workspaceId={props.workspaceId} onNotify={props.onNotify} onAdd={props.onAdd} onOpenSource={props.onOpenSource} fromDiscussion={reportFrom} onFromDiscussion={() => setReportFrom(null)} />}
      </div>
    </div>
  );
}
