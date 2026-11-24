import type { ConversationCitation, LibraryContents, OutputActivityOpen, SessionMessage, TrashItem } from "@gunther/contracts";
import { ArrowLeft, Camera, FileText, Link2, Mic2, NotebookText, Plus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { KnowledgeBase } from "../atlas";
import type { Notify } from "../App";
import { BulkImport } from "../components/BulkImport";
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
import { useEscape } from "../shortcuts/shortcuts";
import { moveToTrash } from "../trash/trash";
import { LibraryChatsSection } from "./LibraryChatsSection";
import { LibraryColumn } from "./LibraryColumn";
import { EvidencePane, type EvidenceTarget } from "./EvidencePane";
import { LibraryInspector, type InspectorTab } from "./LibraryInspector";
import { SessionWorkspace, type LibraryChatActions } from "./SessionWorkspace";
import { useLibraryChats } from "./useLibraryChats";
import "./libraryWorkspace.css";

type Middle = { kind: "chat" } | { kind: "source"; id: string } | { kind: "output"; target: OutputTarget };
const chat: Middle = { kind: "chat" };
const outputKey = (target: OutputTarget) => target.artifactId ? `version:${target.artifactId}`
  : target.lineageId ? `lineage:${target.lineageId}` : `conversation:${target.conversationId}`;

function useColumnCollapse(rule: "open" | "narrow" | "reader") {
  const [manual, setManual] = useState<{ rule: typeof rule; collapsed: boolean }>();
  useEffect(() => setManual(undefined), [rule]);
  const collapsed = manual?.rule === rule ? manual.collapsed : rule !== "open";
  const setCollapsed = useCallback((value: boolean) => setManual({ rule, collapsed: value }), [rule]);
  return { collapsed, setCollapsed };
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
  const [inspector, setInspector] = useState<{ tab: InspectorTab; detail: EvidenceTarget | null }>({ tab: "studio", detail: null });
  const [webEnabled, setWebEnabled] = useState(false);
  const [originalPdf, setOriginalPdf] = useState(false);
  const clearDetail = useCallback(() => setInspector((current) => current.detail ? { ...current, detail: null } : current), []);
  const [selection, setSelection] = useSourceSelection(base.id);
  const [middle, setMiddle] = useState<Middle>(() => {
    const id = persistentStorage.getItem(`gunther:open-source:${base.id}`);
    return id ? { kind: "source", id } : chat;
  });
  const [screenWidth, setScreenWidth] = useState(() => window.innerWidth);
  const [sourcesOpen, setSourcesOpen] = useState(true);
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
  const inspectorColumn = useColumnCollapse(screenWidth < 1_080 ? "narrow" : "open");
  const focusSources = () => {
    sourceColumn.setCollapsed(false);
    setSourcesOpen(true);
    window.requestAnimationFrame(() => workspace.current?.querySelector<HTMLInputElement>(".contents-filter input")?.focus());
  };
  const choose = useCallback((next: Middle) => {
    if (middle.kind === "output" && next.kind === "output" && outputKey(middle.target) === outputKey(next.target)) return true;
    if (!discardOutputEdit(dirty)) return false;
    setMiddle(next);
    clearDetail();
    return true;
  }, [clearDetail, dirty, middle]);
  const openOutput = (target: OutputTarget) => choose({ kind: "output", target });
  const askFromReader = (question: string, submit: boolean) => {
    if (choose(chat)) chatActions.current?.ask(question, submit);
  };
  const openFullSource = (id: string) => onOpenSource(id, sources.map((source) => source.id));
  const closeNarrowLibrary = useCallback(() => {
    if (screenWidth < 840) sourceColumn.setCollapsed(true);
  }, [screenWidth, sourceColumn.setCollapsed]);
  const returnToChat = useCallback(() => {
    if (!choose(chat)) return false;
    closeNarrowLibrary();
    return true;
  }, [choose, closeNarrowLibrary]);
  const chats = useLibraryChats(base.id, onNotify, clearDetail, returnToChat);
  const contextKey = middle.kind === "chat" ? `chat:${chats.activeId ?? ""}:${chats.selectedMessageId ?? ""}`
    : middle.kind === "source" ? `source:${middle.id}` : `output:${outputKey(middle.target)}`;
  const selectedMessage = chats.activeSession?.id === chats.activeId
    ? chats.activeSession.messages.find((message) => message.id === chats.selectedMessageId && message.role === "assistant") ?? null : null;
  const selectTab = useCallback((tab: InspectorTab) => {
    setInspector((current) => ({ ...current, tab }));
    inspectorColumn.setCollapsed(false);
  }, [inspectorColumn.setCollapsed]);
  const closeInspector = useCallback(() => inspectorColumn.setCollapsed(true), [inspectorColumn.setCollapsed]);
  const openEvidence = useCallback((citations: ConversationCitation[], index: number, key = contextKey, passageSelector = false, displayIndex?: number) => {
    if (!citations[index]) return;
    setInspector({ tab: "evidence", detail: { contextKey: key, citations, index, passageSelector, ...(displayIndex === undefined ? {} : { displayIndex }) } });
    inspectorColumn.setCollapsed(false);
  }, [contextKey, inspectorColumn.setCollapsed]);
  const cite = useCallback((citation: ConversationCitation | null, index?: number) => {
    if (citation) openEvidence([citation], 0, contextKey, false, index);
    else clearDetail();
  }, [clearDetail, contextKey, openEvidence]);
  const inspectSources = (message: SessionMessage) => {
    chats.setSelectedMessageId(message.id);
    setInspector({ tab: "evidence", detail: null });
    inspectorColumn.setCollapsed(false);
  };
  const inspectCitation = (message: SessionMessage, index: number) => {
    chats.setSelectedMessageId(message.id);
    openEvidence(message.citations, index, `chat:${message.sessionId}:${message.id}`);
  };
  useEffect(() => {
    setInspector((current) => current.detail && current.detail.contextKey !== contextKey ? { ...current, detail: null } : current);
  }, [contextKey]);
  const openChat = (id: string) => {
    if (returnToChat()) void chats.loadSession(id);
  };
  const createChat = () => {
    if (returnToChat()) void chats.createSession();
  };
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
    if (screenWidth >= 840 || sourceColumn.collapsed) return;
    const closeOutside = (event: PointerEvent) => {
      const column = workspace.current?.querySelector(".library-sources");
      if (column && event.target instanceof Node && !column.contains(event.target)) sourceColumn.setCollapsed(true);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [screenWidth, sourceColumn.collapsed, sourceColumn.setCollapsed]);
  useEscape(() => sourceColumn.setCollapsed(true), screenWidth < 840 && !sourceColumn.collapsed);
  useEffect(() => {
    if (!outputActivity || !workspaceId) return;
    choose({ kind: "output", target: outputActivity });
    onOutputActivity?.(outputActivity);
  }, [choose, onOutputActivity, outputActivity, workspaceId]);
  useEffect(() => { persistentStorage.removeItem(`gunther:open-source:${base.id}`); }, [base.id]);
  const chatVisible = middle.kind === "chat";
  useEffect(() => { onChatVisible?.(chatVisible); }, [chatVisible, onChatVisible]);

  const source = middle.kind === "source" ? sources.find((item) => item.id === middle.id) : undefined;
  return <div className="library-workspace" ref={workspace}>
    <LibraryColumn workspace={workspace} collapsed={sourceColumn.collapsed} slideOver={screenWidth < 840}
      onToggle={() => sourceColumn.setCollapsed(!sourceColumn.collapsed)}>
      <LibraryChatsSection baseId={base.id} chats={chats} onOpen={openChat} onCreate={createChat} />
      <SourceTree baseId={base.id} contents={contents} onContents={setContents} selection={selection} selectedIds={sourceIds}
        open={sourcesOpen} onOpenChange={setSourcesOpen} onScope={setSelection} activeSourceId={source?.id ?? null}
        onSource={(id) => { if (choose({ kind: "source", id })) closeNarrowLibrary(); }} onTrashed={onTrashed} />
      <KeyResultsGroup baseId={base.id} sourceIds={selection.mode === "selected" ? selection.sourceIds : undefined}
        outputs={studio.artifacts} onNotify={onNotify} onInspect={(passages) => openEvidence(passages, 0, contextKey, true)} />
      <footer className="contents-add">
          <PopMenu label="Add a source" align="start" triggerClass="gx-btn gx-btn-primary gx-btn-sm" trigger={<><Plus size={14} />Add source</>} items={[
            { label: "Note or pasted text", icon: NotebookText, onSelect: () => onAdd("note") },
            { label: "Document", icon: FileText, onSelect: () => onAdd("file") },
            { label: "Photo or scan", icon: Camera, onSelect: () => onAdd("image") },
            { label: "Web page", icon: Link2, onSelect: () => onAdd("link") },
            { label: "Recording", icon: Mic2, onSelect: () => onAdd("recording") },
          ]} />
          <BulkImport knowledgeBaseId={base.id} compact />
      </footer>
    </LibraryColumn>
    <section className="library-middle" aria-label="Library workspace">
      <div className="library-chat" hidden={!chatVisible}>
        <SessionWorkspace base={contents ? { ...base, sourceCount: sources.length } : base} visible={chatVisible}
          chats={chats}
          sourceIds={scope.sourceIds} sourceScopeEmpty={scopeEmpty} onFocusSources={focusSources}
          chatActions={chatActions} onNotify={onNotify} onCite={cite} onInspectSources={inspectSources}
          onInspectCitation={inspectCitation} onWebStatus={setWebEnabled} />
      </div>
      {!chatVisible && <div className="library-middle-page">
        <div className="library-middle-bar"><button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => choose(chat)}><ArrowLeft size={14} />Back to chat</button></div>
        {source && <SourceSummary key={source.id} source={source} onOpen={() => openFullSource(source.id)} onCite={cite}
          onAsk={askFromReader} />}
        {middle.kind === "source" && !source && <p className="library-reading">{contents ? "Source unavailable." : "Loading source…"}</p>}
        {target && workspaceId && <OpenOutput key={outputKey(target)} baseId={base.id} workspaceId={workspaceId}
          target={target} studio={studio} scope={scope} scopeEmpty={scopeEmpty} dirty={dirty} onNotify={onNotify}
          onCite={cite} onOpen={openOutput} onWideReader={onWideReader} onAskChat={(question) => askFromReader(question, true)}
          onTrash={(item) => void trashOutput(item, true)} />}
      </div>}
    </section>
    <LibraryInspector workspace={workspace} screenWidth={screenWidth} collapsed={inspectorColumn.collapsed} onCollapse={closeInspector}
      tab={inspector.tab} onTab={selectTab} evidenceCount={middle.kind === "chat" ? selectedMessage?.citations.length ?? 0 : 0}
      running={studio.jobs.size > 0 || studio.activities.some((item) => item.state === "running" || item.state === "waiting")}
      detailOpen={Boolean(inspector.detail)} onBack={clearDetail} originalPdf={originalPdf}
      evidence={<EvidencePane session={chats.activeSession} selected={middle.kind === "chat" ? selectedMessage : null} chatVisible={chatVisible}
        sources={sources} selectedCount={sourceIds.length} allSelected={selection.mode === "all"} webEnabled={webEnabled}
        detail={inspector.detail} onOpen={(citations, index) => openEvidence(citations, index)}
        onStep={(index) => setInspector((current) => current.detail ? { ...current, detail: { ...current.detail, index } } : current)}
        onBack={clearDetail} onOpenSource={(id) => { clearDetail(); openFullSource(id); }} onOriginalPdfChange={setOriginalPdf} />}
      studio={<StudioColumn studio={studio} scope={scope} scopeEmpty={scopeEmpty} disabled={!workspaceId}
        onOpen={openOutput} onTrash={(item) => void trashOutput(item)} />} />
  </div>;
}
