import { persistentStorage } from "../persistentStorage";
import type { OutputActivityOpen } from "@gunther/contracts";
import { useState } from "react";
import { ArrowLeft, Camera, FileText, Link2, Mic2, Plus } from "lucide-react";
import { normalizeAtlasMode, type AtlasMode, type KnowledgeBase } from "../atlas";
import { knowledgeApi } from "../api";
import { BulkImport } from "../components/BulkImport";
import { LibraryContents } from "../contents/LibraryContents";
import { PopMenu } from "../contents/SectionMenu";
import { LibraryGlyph } from "../design/LibraryGlyph";
import { LibraryWorkspaceNavigation } from "../components/LibraryWorkspaceNavigation";
import { OutputsPage } from "../outputs/OutputsPage";
import { SessionWorkspace } from "./SessionWorkspace";

interface KnowledgeBaseWorkspaceProps {
  base: KnowledgeBase;
  workspaceId: string | null;
  mode: AtlasMode;
  onMode: (mode: AtlasMode) => void;
  onBack: () => void;
  onAdd: (kind?: "note" | "link" | "file" | "image" | "table" | "recording") => void;
  onRecord: (context: "lecture" | "meeting" | "memo") => void;
  onExport: () => void;
  onEdit: () => void;
  onNotify: (message: string) => void;
  /** Open a source in its own page; `queue` lists this library's sources for J / K. */
  onOpenSource?: (id: string, queue: string[]) => void;
  /** The app supplies the navigation in its titlebar; standalone workspaces keep a header. */
  showHeader?: boolean;
  previewFocused?: boolean;
  onPreviewFocus?: (value: boolean) => void;
  outputActivity?: OutputActivityOpen | null | undefined;
  onOutputActivity?: ((target: OutputActivityOpen) => void) | undefined;
}

function BaseHeader({ base, mode, onMode, onBack, onExport, onEdit }: Pick<KnowledgeBaseWorkspaceProps, "base" | "mode" | "onMode" | "onBack" | "onExport" | "onEdit">) {
  return (
    <header className="base-workspace-header">
      <button className="base-back" onClick={onBack} aria-label="Back to library"><ArrowLeft size={15} /></button>
      <LibraryGlyph base={base} size="lg" />
      <div className="base-title-group">
        <span>{base.eyebrow}</span>
        <strong>{base.title}</strong>
      </div>
      <LibraryWorkspaceNavigation mode={mode} onMode={onMode} onEdit={onEdit} onExport={onExport} />
    </header>
  );
}

/** The Contents tab: the library's sections and sources as arranged, and a panel about what is chosen. */
function ContentsView({ base, onAdd, onOpenSource, onAsk, onAskLibrary, onEdit }: Pick<KnowledgeBaseWorkspaceProps, "base" | "onAdd" | "onEdit"> & { onOpenSource: KnowledgeBaseWorkspaceProps["onOpenSource"] | undefined; onAsk: (sectionId: string) => void; onAskLibrary: () => void }) {
  return <div className="contents-page page-enter">
    <LibraryContents baseId={base.id} library={{ title: base.title, eyebrow: base.eyebrow, description: base.description }}
      onOpenSource={(id, queue) => onOpenSource?.(id, queue)} onAsk={onAsk} onAskLibrary={onAskLibrary} onEditLibrary={onEdit}
      onAddSource={() => onAdd()}
      toolbarEnd={<>
        <BulkImport knowledgeBaseId={base.id} compact />
        <PopMenu label="Add a source" triggerClass="gx-btn gx-btn-primary gx-btn-sm" trigger={<><Plus size={13} />Add source</>} items={[
          { label: "Document", icon: FileText, onSelect: () => onAdd("file") },
          { label: "Photo or scan", icon: Camera, onSelect: () => onAdd("image") },
          { label: "Web page", icon: Link2, onSelect: () => onAdd("link") },
          { label: "Recording", icon: Mic2, onSelect: () => onAdd("recording") },
        ]} />
      </>} />
  </div>;
}

export function KnowledgeBaseWorkspace(props: KnowledgeBaseWorkspaceProps) {
  // The Contents tab replaced Overview and Sources: their old names still open it.
  const mode = normalizeAtlasMode(props.mode);
  // A conversation chosen as the material of a new report (from a research answer), used once.
  const [reportFrom, setReportFrom] = useState<string | null>(null);
  const askSection = async (id: string) => {
    try {
      const session = await knowledgeApi.createSession(props.base.id, { focusChapterId: id, selectedSourceIds: [] });
      persistentStorage.setItem(`gunther:active-session:${props.base.id}`, session.id);
      props.onMode("ask");
    } catch (reason) { props.onNotify(reason instanceof Error ? reason.message : "Could not open a conversation about this section."); }
  };
  return (
    <div className={`base-workspace${mode === "outputs" ? " has-outputs-workspace" : ""}`}>
      {props.showHeader !== false && <BaseHeader {...props} mode={mode} />}
      <div className="base-workspace-body">
        {mode === "contents" && <ContentsView base={props.base} onAdd={props.onAdd} onEdit={props.onEdit} onOpenSource={props.onOpenSource} onAsk={(id) => void askSection(id)} onAskLibrary={() => props.onMode("ask")} />}
        {mode === "ask" && <SessionWorkspace base={props.base} onAdd={props.onAdd} onNotify={props.onNotify} onOpenSource={(id) => props.onOpenSource?.(id, [id])} onMakeReport={(sessionId) => { setReportFrom(sessionId); props.onMode("outputs"); }} />}
        {mode === "outputs" && <OutputsPage base={props.base} workspaceId={props.workspaceId} focused={props.previewFocused} onFocus={props.onPreviewFocus} onNotify={props.onNotify} onAdd={props.onAdd} onOpenSource={props.onOpenSource} fromDiscussion={reportFrom} onFromDiscussion={() => setReportFrom(null)}
          outputActivity={props.outputActivity} onOutputActivity={props.onOutputActivity} />}
      </div>
    </div>
  );
}
