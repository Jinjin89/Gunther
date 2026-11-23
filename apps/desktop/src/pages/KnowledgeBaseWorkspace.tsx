import type { LibraryContents, OutputActivityOpen, TrashItem } from "@gunther/contracts";
import { ArrowLeft, Camera, FileText, Link2, Mic2, NotebookText, Plus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { KnowledgeBase } from "../atlas";
import { BulkImport } from "../components/BulkImport";
import { PopMenu } from "../contents/SectionMenu";
import { SourceSummary } from "../contents/SourceSummary";
import { SourceTree } from "../contents/SourceTree";
import { outputScope, selectedSources, useSourceSelection } from "../contents/sourceScope";
import { discardOutputEdit } from "../outputs/EditPane";
import { OpenOutput } from "../outputs/OpenOutput";
import { StudioColumn } from "../outputs/StudioColumn";
import { type OutputTarget, useStudio } from "../outputs/useStudio";
import { persistentStorage } from "../persistentStorage";
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
  onNotify: (message: string) => void;
  onOpenSource: (id: string, queue: string[]) => void;
  onTrashed: (entry: TrashItem) => void;
  outputActivity?: OutputActivityOpen | null | undefined;
  onOutputActivity?: ((target: OutputActivityOpen) => void) | undefined;
  editDirty?: { current: boolean };
  onChatVisible?: (visible: boolean) => void;
}) {
  const [contents, setContents] = useState<LibraryContents | null>(null);
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
    if (discardOutputEdit(dirty)) setMiddle(next);
  }, [dirty, middle]);
  const openOutput = (target: OutputTarget) => choose({ kind: "output", target });
  const openFullSource = (id: string) => onOpenSource(id, sources.map((source) => source.id));

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
        onTrashed={onTrashed} addSource={<>
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
          chatActions={chatActions} onNotify={onNotify} onOpenSource={openFullSource} />
      </div>
      {!chatVisible && <div className="library-middle-page">
        <div className="library-middle-bar"><button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => choose(chat)}><ArrowLeft size={14} />Back to chat</button></div>
        {source && <SourceSummary key={source.id} source={source} onOpen={() => openFullSource(source.id)}
          onAsk={(question, submit) => { setMiddle(chat); chatActions.current?.ask(question, submit); }} />}
        {middle.kind === "source" && !source && <p className="library-reading">{contents ? "Source unavailable." : "Loading source…"}</p>}
        {target && workspaceId && <OpenOutput key={outputKey(target)} baseId={base.id} workspaceId={workspaceId}
          target={target} studio={studio} scope={scope} scopeEmpty={scopeEmpty} dirty={dirty} onNotify={onNotify}
          onOpenSource={openFullSource} onOpen={openOutput} onWideReader={onWideReader} />}
      </div>}
    </section>
    <LibraryColumn column="studio" workspace={workspace} collapsed={studioColumn.collapsed}
      onToggle={() => studioColumn.setCollapsed(!studioColumn.collapsed)}>
      <StudioColumn studio={studio} scope={scope} scopeEmpty={scopeEmpty} disabled={!workspaceId} onOpen={openOutput} />
    </LibraryColumn>
  </div>;
}
