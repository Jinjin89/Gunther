import type { SourceSummary } from "@gunther/contracts";
import {
  ArrowRight,
  BookOpen,
  Camera,
  FileText,
  Globe2,
  Inbox,
  Link2,
  Mic2,
  NotebookPen,
  Plus,
  Search,
  Sparkles,
  Table2,
} from "lucide-react";
import { useEffect, useState } from "react";
import type { KnowledgeBase } from "../atlas";
import { knowledgeApi } from "../api";

export type HomeCaptureKind = "note" | "link" | "file" | "image" | "recording" | "table";

interface HomePageProps {
  bases: KnowledgeBase[];
  inboxCount: number;
  onCapture: (kind?: HomeCaptureKind) => void;
  onSearch: () => void;
  onOpenBase: (id: string) => void;
  onOpenLibraries: () => void;
  onCreateBase: () => void;
  onOpenInbox: () => void;
}

const captureActions = [
  { id: "note", label: "Quick note", detail: "Write a thought before it disappears", icon: NotebookPen, tone: "clay" },
  { id: "file", label: "Document", detail: "PDF, paper, slides, or text", icon: FileText, tone: "blue" },
  { id: "image", label: "Photo or scan", detail: "Capture a page, board, or diagram", icon: Camera, tone: "violet" },
  { id: "link", label: "Web page", detail: "Save a link with your own context", icon: Link2, tone: "sand" },
  { id: "recording", label: "Recording", detail: "Live transcript or imported audio", icon: Mic2, tone: "green" },
  { id: "table", label: "Table or data", detail: "Paste structured rows with their header", icon: Table2, tone: "web" },
] as const;

const sourceLabel = (source: SourceSummary) => ({
  note: "Note",
  paper: "Paper",
  link: "Web page",
  file: "Document",
  image: "Photo or scan",
  table: "Dataset",
  recording: "Recording",
  course: "Course",
}[source.kind]);

export function HomePage({ bases, inboxCount, onCapture, onSearch, onOpenBase, onOpenLibraries, onCreateBase, onOpenInbox }: HomePageProps) {
  const [recentSources, setRecentSources] = useState<SourceSummary[]>([]);

  useEffect(() => {
    void knowledgeApi.sources().then((items) => setRecentSources(items.slice(0, 4))).catch(() => undefined);
  }, []);

  const recentBases = bases.slice(0, 3);

  return (
    <div className="home-page page-enter">
      <header className="home-welcome">
        <div>
          <span className="atlas-eyebrow">Your knowledge workspace</span>
          <h1>Capture first. Shape it when you’re ready.</h1>
          <p>Bring in anything worth keeping. Gunther preserves the source, helps you understand it, and lets you decide where it belongs.</p>
        </div>
        <button className="home-search" onClick={onSearch}>
          <Search size={17} />
          <span>Search your knowledge and the web</span>
          <kbd>⌘K</kbd>
        </button>
      </header>

      <section className="home-capture-card" aria-labelledby="capture-heading">
        <header>
          <div>
            <span className="home-section-icon"><Plus size={16} /></span>
            <span><strong id="capture-heading">Capture something</strong><small>No knowledge base required. Organize it now or later.</small></span>
          </div>
          <button className="text-button" onClick={() => onCapture()}>All capture options <ArrowRight size={13} /></button>
        </header>
        <div className="home-capture-grid">
          {captureActions.map(({ id, label, detail, icon: Icon, tone }) => (
            <button key={id} onClick={() => onCapture(id)}>
              <span className={`capture-action-icon tone-${tone}`}><Icon size={19} /></span>
              <span><strong>{label}</strong><small>{detail}</small></span>
              <ArrowRight size={13} />
            </button>
          ))}
          <button onClick={onSearch}>
            <span className="capture-action-icon tone-web"><Globe2 size={19} /></span>
            <span><strong>Web search</strong><small>Research first, then save what matters</small></span>
            <ArrowRight size={13} />
          </button>
        </div>
      </section>

      <div className="home-dashboard">
        <section className="home-panel home-libraries-panel">
          <header><span><BookOpen size={15} /><strong>Continue a knowledge base</strong></span><button onClick={onOpenLibraries}>View all <ArrowRight size={12} /></button></header>
          <div className="home-library-list">
            {recentBases.map((base) => (
              <button key={base.id} onClick={() => onOpenBase(base.id)}>
                <span className={`home-base-monogram color-${base.color}`}>{base.title.split(/\s+/).slice(0, 2).map((word) => word[0]).join("")}</span>
                <span><strong>{base.title}</strong><small>{base.indexedSourceCount ?? base.sourceCount} sources · {base.chapterCount} chapters</small></span>
                <ArrowRight size={13} />
              </button>
            ))}
            {recentBases.length === 0 && <div className="home-empty-library"><span><strong>Create a home when the subject becomes clear.</strong><small>You can also keep capturing to Inbox first.</small></span><button type="button" className="quiet-button" onClick={onCreateBase}><Plus size={13} />New knowledge base</button></div>}
          </div>
        </section>

        <section className="home-panel home-inbox-panel">
          <header><span><Inbox size={15} /><strong>Needs your attention</strong></span><button onClick={onOpenInbox}>Open inbox <ArrowRight size={12} /></button></header>
          <button className="home-inbox-summary" onClick={onOpenInbox}>
            <span className={inboxCount ? "has-items" : ""}>{inboxCount}</span>
            <span><strong>{inboxCount ? "Items waiting to be organized or reviewed" : "Everything is caught up"}</strong><small>{inboxCount ? "Nothing moves into trusted knowledge without you." : "New captures can stay here until you choose a home."}</small></span>
          </button>
          <div className="home-trust-note"><Sparkles size={14} /><span>AI can suggest structure. You approve what becomes knowledge.</span></div>
        </section>
      </div>

      {recentSources.length > 0 && <section className="home-recent-sources">
        <header><strong>Recently captured</strong><small>Original sources stay preserved</small></header>
        <div>{recentSources.map((source) => <article key={source.id}><span className={`recent-source-kind kind-${source.kind}`}>{source.kind === "recording" || source.kind === "course" ? <Mic2 size={14} /> : <FileText size={14} />}</span><span><small>{sourceLabel(source)} · {new Date(source.createdAt).toLocaleDateString()}</small><strong>{source.title}</strong></span><em>{source.assertionCount} suggested claims</em></article>)}</div>
      </section>}
    </div>
  );
}
