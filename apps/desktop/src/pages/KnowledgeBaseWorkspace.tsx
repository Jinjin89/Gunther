import type { ConversationCitation, LibraryContents, OutputActivityOpen, TrashItem } from "@gunther/contracts";
import { ArrowLeft, Camera, FileText, Link2, Mic2, NotebookText, Plus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { KnowledgeBase } from "../atlas";
import type { Notify } from "../App";
import { BulkImport } from "../components/BulkImport";
import { EvidencePanel } from "../components/evidence/EvidencePanel";
import { PopMenu } from "../contents/SectionMenu";
import { SourceSummary } from "../contents/SourceSummary";
import { SourceTree } from "../contents/SourceTree";
import { outputScope, selectedSources, useSourceSelection } from "../contents/sourceScope";
import { KeyResultsGroup } from "../keyResults/KeyResultsGroup";
import { discardOutputEdit } from "../outputs/EditPane";
import { OpenOutput } from "../outputs/OpenOutput";
import { StudioColumn } from "../outputs/StudioColumn";
import { type OutputTarget, type OutputTrashTarget, useStudio } from "../outputs/useStudio";
import { persistentStorage } from "../persistentStorage";
import { moveToTrash } from "../trash/trash";
import { LibraryColumn } from "./LibraryColumn";
import { SessionWorkspace, type LibraryChatActions } from "./SessionWorkspace";
import "./libraryWorkspace.css";

type Middle = { kind: "chat" } | { kind: "source"; id: string } | { kind: "output"; target: OutputTarget };
const chat: Middle = { kind: "chat" };
const outputKey = (target: OutputTarget) => target.artifactId ? `version:${target.artifactId}`
  : target.lineageId ? `lineage:${target.lineageId}` : `conversation:${target.conversationId}`;

function useColumnCollapse(rule: "open" | "narrow" | "reader") {
  const [manual, setManual] = useState<{ rule: typeof rule; collapsed: boolean }>();
  useEffect(() => setManual(undefined), [rule]);
  const collapsed = manual?.rule === rule ? manual.collapsed : rule !== "open";
  return { collapsed, setCollapsed: (value: boolean) => setManual({ rule, collapsed: value }) };
}

export function KnowledgeBaseWorkspace({ base, workspaceId, onAdd, onNotify, onOpenSource, onTrashed, outputActivity,
  onOutputActivity, editDirty, onChatVisible }: {
  base: KnowledgeBase; workspaceId: string | null;
  onAdd: (kind: "file" | "image" | "link" | "recording" | "note") => void;
  onNotify: Notify;
  onOpenSource: (id: string, queue: string[]) => void;
  onTrashed: (entry: TrashItem) => void;
  outputActivity?: OutputActivityOpen | null | undefined;
  onOutputActivity?: ((target: OutputActivityOpen) => void) | undefined;
  editDirty?: { current: boolean };
  onChatVisible?: (visible: boolean) => void;
}) {
  const [contents, setContents] = useState<LibraryContents | null>(null);
  const [evidence, setEvidence] = useState<{
    citation: ConversationCitation;
    index?: number | undefined;
    passages?: ConversationCitation[];
  } | null>(null);
  const [selection, setSelection] = useSourceSelection(base.id);
  const [middle, setMiddle] = useState<Middle>(() => {
    const id = persistentStorage.getItem(`gunther:open-source:${base.id}`);
    return id ? { kind: "source", id } : chat;
  });
  const [screenWidth, setScreenWidth] = useState(() => window.innerWidth);
  const [wideReaderKey, setWideReaderKey] = useState<string | null>(null);
  const ownDirty = useRef(false);
  const dirty = editDirty ?? ownDirty;
  const sources = contents?.sources ?? [];
  const sourceIds = selectedSources(selection, sources.map((source) => source.id));
  const scope = outputScope(selection, sourceIds);
  const scopeEmpty = selection.mode === "selected" && sourceIds.length === 0;
  const studio = useStudio(base.id, workspaceId);
  const workspace = useRef<HTMLDivElement>(null);
  const chatActions = useRef<LibraryChatActions>(null);
  const target = middle.kind === "output" ? middle.target : null;
  const readerKey = target ? outputKey(target) : null;
  const wideReader = readerKey !== null && readerKey === wideReaderKey;
  const onWideReader = useCallback((wide: boolean) => setWideReaderKey(wide ? readerKey : null), [readerKey]);
  const sourceColumn = useColumnCollapse(wideReader && screenWidth < 1_200 ? "reader" : screenWidth < 840 ? "narrow" : "open");
  const studioColumn = useColumnCollapse(screenWidth < 1_080 ? "narrow" : "open");
  const focusSources = () => {
    sourceColumn.setCollapsed(false);
    window.requestAnimationFrame(() => workspace.current?.querySelector<HTMLInputElement>(".contents-filter input")?.focus());
  };
  const choose = useCallback((next: Middle) => {
    if (middle.kind === "output" && next.kind === "output" && outputKey(middle.target) === outputKey(next.target)) return;
    if (discardOutputEdit(dirty)) { setMiddle(next); setEvidence(null); }
  }, [dirty, middle]);
  const openOutput = (target: OutputTarget) => choose({ kind: "output", target });
  const openFullSource = (id: string) => onOpenSource(id, sources.map((source) => source.id));
  const cite = (citation: ConversationCitation | null, index?: number) =>
    setEvidence(citation ? { citation, index } : null);
  const trashOutput = async (item: OutputTrashTarget, fromReader = false) => {
    if (!workspaceId) return;
    const selected = target && studio.artifacts.find((version) => version.id === target.artifactId);
    const conversation = "conversation" in item;
    const affected = fromReader || (conversation
      ? (selected?.conversationId ?? target?.conversationId) === item.id
      : (selected?.lineageId ?? target?.lineageId) === item.lineageId);
    // The editor clears its dirty state when the move succeeds and closes it.
    if (affected && !discardOutputEdit({ current: dirty.current })) return;
    try {
      const entry = await moveToTrash({
        kind: "output", id: item.id, libraryId: base.id, workspaceId, conversation,
      });
      studio.forgetTrashed(item);
      if (affected) setMiddle((current) => current === middle ? chat : current);
      onTrashed(entry);
    } catch (reason) {
      onNotify(reason instanceof Error ? reason.message : "The output could not move to Trash.");
    }
  };

  useEffect(() => {
    const resize = () => setScreenWidth(window.innerWidth);
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  useEffect(() => {
    if (!outputActivity || !workspaceId) return;
    choose({ kind: "output", target: outputActivity });
    onOutputActivity?.(outputActivity);
  }, [choose, onOutputActivity, outputActivity, workspaceId]);
  useEffect(() => { persistentStorage.removeItem(`gunther:open-source:${base.id}`); }, [base.id]);
  useEffect(() => {
    const openChat = (event: Event) => {
      if ((event as CustomEvent<{ libraryId: string }>).detail?.libraryId === base.id) setMiddle(chat);
    };
    window.addEventListener("gunther:open-session", openChat);
    return () => window.removeEventListener("gunther:open-session", openChat);
  }, [base.id]);
  const chatVisible = middle.kind === "chat";
  useEffect(() => { onChatVisible?.(chatVisible); }, [chatVisible, onChatVisible]);

  const source = middle.kind === "source" ? sources.find((item) => item.id === middle.id) : undefined;
  return <div className="library-workspace" ref={workspace}>
    <LibraryColumn column="sources" workspace={workspace} collapsed={sourceColumn.collapsed}
      onToggle={() => sourceColumn.setCollapsed(!sourceColumn.collapsed)}>
      <SourceTree baseId={base.id} contents={contents} onContents={setContents} selection={selection} selectedIds={sourceIds}
        onScope={setSelection} activeSourceId={source?.id ?? null} onSource={(id) => choose({ kind: "source", id })}
        onTrashed={onTrashed} keyResults={(filter) => <KeyResultsGroup baseId={base.id} filter={filter}
          outputs={studio.artifacts} onNotify={onNotify} onInspect={(passages) => {
            if (passages[0]) setEvidence({ citation: passages[0], passages });
          }} />}
        addSource={<>
          <PopMenu label="Add a source" align="start" triggerClass="gx-btn gx-btn-primary gx-btn-sm" trigger={<><Plus size={14} />Add source</>} items={[
            { label: "Note or pasted text", icon: NotebookText, onSelect: () => onAdd("note") },
            { label: "Document", icon: FileText, onSelect: () => onAdd("file") },
            { label: "Photo or scan", icon: Camera, onSelect: () => onAdd("image") },
            { label: "Web page", icon: Link2, onSelect: () => onAdd("link") },
            { label: "Recording", icon: Mic2, onSelect: () => onAdd("recording") },
          ]} />
          <BulkImport knowledgeBaseId={base.id} compact />
        </>} />
    </LibraryColumn>
    <section className="library-middle" aria-label="Library workspace">
      <div className="library-chat" hidden={!chatVisible}>
        <SessionWorkspace base={contents ? { ...base, sourceCount: sources.length } : base} visible={chatVisible}
          sourceIds={scope.sourceIds} sourceScopeEmpty={scopeEmpty} onFocusSources={focusSources}
          chatActions={chatActions} onNotify={onNotify} onCite={cite} />
      </div>
      {!chatVisible && <div className="library-middle-page">
        <div className="library-middle-bar"><button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => choose(chat)}><ArrowLeft size={14} />Back to chat</button></div>
        {source && <SourceSummary key={source.id} source={source} onOpen={() => openFullSource(source.id)} onCite={cite}
          onAsk={(question, submit) => { setMiddle(chat); chatActions.current?.ask(question, submit); }} />}
        {middle.kind === "source" && !source && <p className="library-reading">{contents ? "Source unavailable." : "Loading source…"}</p>}
        {target && workspaceId && <OpenOutput key={outputKey(target)} baseId={base.id} workspaceId={workspaceId}
          target={target} studio={studio} scope={scope} scopeEmpty={scopeEmpty} dirty={dirty} onNotify={onNotify}
          onCite={cite} onOpen={openOutput} onWideReader={onWideReader}
          onTrash={(item) => void trashOutput(item, true)} />}
      </div>}
    </section>
    <LibraryColumn column="studio" workspace={workspace} collapsed={studioColumn.collapsed}
      onToggle={() => studioColumn.setCollapsed(!studioColumn.collapsed)}>
      <StudioColumn studio={studio} scope={scope} scopeEmpty={scopeEmpty} disabled={!workspaceId}
        onOpen={openOutput} onTrash={(item) => void trashOutput(item)} />
    </LibraryColumn>
    {evidence && <EvidencePanel {...evidence} onClose={() => setEvidence(null)}
      onOpenSource={(id) => { setEvidence(null); openFullSource(id); }} />}
  </div>;
}
