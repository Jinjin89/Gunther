import { persistentStorage } from "../persistentStorage";
import type { KnowledgeTopic } from "@gunther/contracts";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Camera, Download, FileText, Link2, Mic2, Plus, Settings2 } from "lucide-react";
import { normalizeAtlasMode, type AtlasMode, type KnowledgeBase, type KnowledgeChapter } from "../atlas";
import { knowledgeApi } from "../api";
import { BulkImport } from "../components/BulkImport";
import { LibraryContents } from "../contents/LibraryContents";
import { PopMenu } from "../contents/SectionMenu";
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
  { id: "contents", label: "Contents" },
  { id: "outputs", label: "Outputs" },
];

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

/** The Contents tab: the library's sections and sources as arranged, and a panel about what is chosen. */
function ContentsView({ base, onAdd, onOpenSource, onAsk, onAskLibrary, onEdit, onChanged }: Pick<KnowledgeBaseWorkspaceProps, "base" | "onAdd" | "onEdit"> & { onOpenSource: KnowledgeBaseWorkspaceProps["onOpenSource"] | undefined; onAsk: (sectionId: string) => void; onAskLibrary: () => void; onChanged: () => void }) {
  const [count, setCount] = useState<number | null>(null);
  return <div className="contents-page page-enter">
    <LibraryContents baseId={base.id} library={{ title: base.title, eyebrow: base.eyebrow, description: base.description }}
      onOpenSource={(id, queue) => onOpenSource?.(id, queue)} onAsk={onAsk} onAskLibrary={onAskLibrary} onEditLibrary={onEdit}
      onAddSource={() => onAdd()} onChanged={onChanged} onLoaded={setCount}
      toolbarEnd={<>
        <BulkImport knowledgeBaseId={base.id} compact />
        <PopMenu label="Add a source" triggerClass="gx-btn gx-btn-primary gx-btn-sm" trigger={<><Plus size={13} />Add source</>} items={[
          { label: "Document", icon: FileText, onSelect: () => onAdd("file") },
          { label: "Photo or scan", icon: Camera, onSelect: () => onAdd("image") },
          { label: "Web page", icon: Link2, onSelect: () => onAdd("link") },
          { label: "Recording", icon: Mic2, onSelect: () => onAdd("recording") },
        ]} />
      </>}
      afterList={count === 0 && base.sources.length > 0 ? <section className="contents-references" aria-label="References">
        <h2>References</h2>
        <ul>{base.sources.map((source) => <li key={source.id}><span>{source.title}</span><small>{[source.scope, source.publisher].filter(Boolean).join(" · ")}</small></li>)}</ul>
      </section> : null} />
  </div>;
}

export function KnowledgeBaseWorkspace(props: KnowledgeBaseWorkspaceProps) {
  // The Contents tab replaced Overview and Sources: their old names still open it.
  const mode = normalizeAtlasMode(props.mode);
  const [topics, setTopics] = useState<KnowledgeTopic[]>([]);
  const [topicReload, setTopicReload] = useState(0);
  // A conversation chosen as the material of a new report (from a research answer), used once.
  const [reportFrom, setReportFrom] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    setTopics([]);
    void knowledgeApi.topics(props.base.id).then((items) => { if (active) setTopics(items); }).catch(() => undefined);
    const update = () => setTopicReload((value) => value + 1);
    window.addEventListener("gunther:topics-updated", update);
    return () => { active = false; window.removeEventListener("gunther:topics-updated", update); };
  }, [props.base.id, topicReload]);
  // Ask lists the sections as its focus choices.
  const topicBase = useMemo(() => ({ ...props.base, chapters: [
    ...props.base.chapters,
    ...topics.map((topic): KnowledgeChapter => ({
      id: topic.id, number: "", title: topic.title, question: topic.description,
      summary: topic.description, status: "outline", progress: 0, sourceIds: [],
      takeaways: [], topics: [], decision: { label: "Section", answer: "Explore linked evidence" },
    })),
  ] }), [props.base, topics]);
  const askSection = async (id: string) => {
    try {
      const session = await knowledgeApi.createSession(props.base.id, { focusChapterId: id, selectedSourceIds: [] });
      persistentStorage.setItem(`gunther:active-session:${props.base.id}`, session.id);
      props.onChapter(id); props.onMode("ask");
    } catch (reason) { props.onNotify(reason instanceof Error ? reason.message : "Could not open a conversation about this section."); }
  };
  return (
    <div className="base-workspace">
      <BaseHeader {...props} mode={mode} />
      <div className="base-workspace-body">
        {mode === "contents" && <ContentsView base={props.base} onAdd={props.onAdd} onEdit={props.onEdit} onOpenSource={props.onOpenSource} onAsk={(id) => void askSection(id)} onAskLibrary={() => props.onMode("ask")} onChanged={() => setTopicReload((value) => value + 1)} />}
        {mode === "ask" && <SessionWorkspace base={topicBase} selectedChapterId={props.selectedChapterId} onAdd={props.onAdd} onNotify={props.onNotify} onOpenSource={(id) => props.onOpenSource?.(id, [id])} onMakeReport={(sessionId) => { setReportFrom(sessionId); props.onMode("outputs"); }} />}
        {mode === "outputs" && <OutputsPage base={props.base} workspaceId={props.workspaceId} onNotify={props.onNotify} onAdd={props.onAdd} onOpenSource={props.onOpenSource} fromDiscussion={reportFrom} onFromDiscussion={() => setReportFrom(null)} />}
      </div>
    </div>
  );
}
