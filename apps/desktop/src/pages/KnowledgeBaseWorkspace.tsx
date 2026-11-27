import type { Artifact, ConversationCitation, LibraryContents, OutputActivityOpen, OutputScope, SessionMessage, TrashItem } from "@gunther/contracts";
import { ArrowLeft, Camera, FileText, Link2, Mic2, NotebookText, PanelLeft, Plus } from "lucide-react";
import { useCallback, useEffect, useEffectEvent, useId, useLayoutEffect, useMemo, useReducer, useRef, useState, type PointerEvent } from "react";
import { isTurnResult, type OutputDone } from "../api";
import type { KnowledgeBase } from "../atlas";
import type { Notify } from "../App";
import { BulkImport } from "../components/BulkImport";
import { AnswerCitationNavigation } from "../components/evidence/EvidenceDetail";
import { PopMenu } from "../contents/SectionMenu";
import { SourceSummary } from "../contents/SourceSummary";
import { SourceTree } from "../contents/SourceTree";
import { allSources, outputScope, selectedSources, useSourceSelection } from "../contents/sourceScope";
import { useTraces } from "../developer/useTraces";
import { useDragWidth } from "../hooks/useDragWidth";
import { KeyResultsGroup } from "../keyResults/KeyResultsGroup";
import { useModelMenu } from "../models/askModel";
import { outputActivityTarget, outputTargetKey, planAddress } from "../outputs/artifactConversations";
import { discardOutputEdit } from "../outputs/EditPane";
import { OpenOutput } from "../outputs/OpenOutput";
import type { EditFlush } from "../outputs/PlanningWorkspace";
import { StudioColumn, type StudioRun } from "../outputs/StudioColumn";
import { latestVersions, type OutputTarget, type OutputTrashTarget, type PlanAddress, type SourceState, useStudio } from "../outputs/useStudio";
import { persistentStorage } from "../persistentStorage";
import { useEscape, useShortcut } from "../shortcuts/shortcuts";
import { moveToTrash } from "../trash/trash";
import { LibraryChatsSection } from "./LibraryChatsSection";
import { LibraryColumn, type ColumnLayout } from "./LibraryColumn";
import { EvidencePane, type EvidenceTarget, type TraceTarget } from "./EvidencePane";
import { InspectorRail, LibraryInspector, type InspectorTab } from "./LibraryInspector";
import { SessionWorkspace, type LibraryChatActions } from "./SessionWorkspace";
import { useLibraryChats } from "./useLibraryChats";
import "./libraryWorkspace.css";

type FollowedRun = StudioRun & { result: Promise<OutputDone | null> | undefined };
type Middle = { kind: "chat" } | { kind: "source"; id: string } | { kind: "output"; target: OutputTarget; run?: FollowedRun | undefined };
const chat: Middle = { kind: "chat" };

function useColumnCollapse(rule: "open" | "narrow" | "reader") {
  const [manual, setManual] = useState<{ rule: typeof rule; collapsed: boolean }>();
  useEffect(() => setManual(undefined), [rule]);
  const collapsed = manual?.rule === rule ? manual.collapsed : rule !== "open";
  const setCollapsed = useCallback((value: boolean) => setManual({ rule, collapsed: value }), [rule]);
  return { collapsed, setCollapsed };
}

type SidePanel = "library" | "inspector";
type PanelTrigger = "library" | InspectorTab;
const RAIL = 44;
const MIDDLE_MIN = 480;
const LIBRARY_MIN = 200;
const LIBRARY_MAX = 360;
const LIBRARY_DEFAULT = 260;
const INSPECTOR_MIN = 280;

function useWorkspaceColumns(wideReader: boolean) {
  const workspace = useRef<HTMLDivElement>(null);
  const libraryColumn = useRef<HTMLDivElement>(null);
  const inspectorColumn = useRef<HTMLElement>(null);
  const [screenWidth, setScreenWidth] = useState(() => window.innerWidth);
  const [workspaceWidth, setWorkspaceWidth] = useState<number | null>(null);
  const [libraryBox, setLibraryBox] = useState<{ track: number; width: number }>();
  const focusRequest = useRef<{ trigger: PanelTrigger; previous?: Element } | null>(null);
  const rememberFocus = useCallback((trigger?: PanelTrigger) => {
    const previous = document.activeElement;
    const owner = previous?.closest<HTMLElement>("[data-panel-focus]");
    if (previous && owner && workspace.current?.contains(owner)) {
      focusRequest.current = { trigger: trigger ?? owner.dataset.panelFocus as PanelTrigger, previous };
    }
  }, []);
  const focusPanel = useCallback((trigger: PanelTrigger) => { focusRequest.current = { trigger }; }, []);
  useLayoutEffect(() => {
    const request = focusRequest.current;
    focusRequest.current = null;
    if (!request) return;
    const { previous, trigger } = request;
    if (previous && (
      (previous.isConnected && !previous.closest("[hidden]")) ||
      (document.activeElement !== previous && document.activeElement !== document.body)
    )) return;
    const buttons = workspace.current?.querySelectorAll<HTMLButtonElement>(`button[data-panel-focus="${trigger}"]`);
    Array.from(buttons ?? []).find((button) => !button.closest("[hidden]"))?.focus({ preventScroll: true });
  });

  useLayoutEffect(() => {
    const root = workspace.current;
    const left = libraryColumn.current;
    if (!root || !left) return;
    const measureWindow = () => {
      rememberFocus();
      setScreenWidth(window.innerWidth);
      setWorkspaceWidth(root.getBoundingClientRect().width);
    };
    measureWindow();
    window.addEventListener("resize", measureWindow);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (entry.target === root) {
          rememberFocus();
          setWorkspaceWidth(entry.contentBoxSize?.[0]?.inlineSize ?? entry.contentRect.width);
        } else if (entry.target === left) {
          const track = Number.parseFloat(left.style.width);
          const width = entry.borderBoxSize?.[0]?.inlineSize ?? left.getBoundingClientRect().width;
          setLibraryBox((box) => box?.track === track && box.width === width ? box : { track, width });
        }
      }
    });
    observer?.observe(root);
    observer?.observe(left, { box: "border-box" });
    return () => {
      window.removeEventListener("resize", measureWindow);
      observer?.disconnect();
    };
  }, [rememberFocus]);

  const narrowLibrary = screenWidth < 840;
  const narrowInspector = screenWidth < 1_080;
  const libraryPreference = useColumnCollapse(wideReader && screenWidth < 1_200 ? "reader" : narrowLibrary ? "narrow" : "open");
  const inspectorPreference = useColumnCollapse(narrowInspector ? "narrow" : "open");
  const setLibraryCollapsed = libraryPreference.setCollapsed;
  const setInspectorCollapsed = inspectorPreference.setCollapsed;
  const [lastOpenedPanel, setLastOpenedPanel] = useState<SidePanel>("inspector");

  const available = workspaceWidth ?? Infinity;
  const strip = available < MIDDLE_MIN + 2 * RAIL;
  const libraryRoom = available - MIDDLE_MIN - RAIL;
  const librarySize = useDragWidth({
    storageKey: "gunther:library-sources-width", min: LIBRARY_MIN, max: Math.min(LIBRARY_MAX, libraryRoom),
    fallback: LIBRARY_DEFAULT, side: "left",
  });
  const libraryRequested = Math.min(LIBRARY_MAX, Math.max(LIBRARY_MIN, librarySize.chosenWidth ?? LIBRARY_DEFAULT));
  const librarySlideOver = narrowLibrary || libraryRequested > libraryRoom;
  const libraryTrack = strip ? 0 : libraryPreference.collapsed || librarySlideOver ? RAIL : librarySize.width;
  // Use the intended border-box width until this track's measurement arrives.
  const libraryWidth = libraryBox?.track === libraryTrack ? libraryBox.width : libraryTrack;
  const remaining = available - libraryWidth;
  const inspectorMax = Math.min(960, Math.floor(remaining / 2), Math.floor(remaining - MIDDLE_MIN));
  const inspectorDefault = Math.min(400, Math.max(336, Math.round(remaining / 3)));
  const inspectorSize = useDragWidth({
    storageKey: "gunther:library-studio-width", min: INSPECTOR_MIN, max: inspectorMax, fallback: inspectorDefault,
  });
  const inspectorSlideOver = narrowInspector || inspectorMax < INSPECTOR_MIN;

  const libraryWantsOverlay = !libraryPreference.collapsed && librarySlideOver;
  const inspectorWantsOverlay = !inspectorPreference.collapsed && inspectorSlideOver;
  const activeOverlay = libraryWantsOverlay && inspectorWantsOverlay ? lastOpenedPanel
    : libraryWantsOverlay ? "library" : inspectorWantsOverlay ? "inspector" : null;
  const libraryCollapsed = libraryPreference.collapsed || (librarySlideOver && activeOverlay !== "library");
  const inspectorCollapsed = inspectorPreference.collapsed || (inspectorSlideOver && activeOverlay !== "inspector");
  useEffect(() => {
    if (!libraryWantsOverlay || !inspectorWantsOverlay) return;
    if (activeOverlay === "library") setInspectorCollapsed(true);
    else setLibraryCollapsed(true);
  }, [activeOverlay, inspectorWantsOverlay, libraryWantsOverlay, setInspectorCollapsed, setLibraryCollapsed]);

  const reveal = useCallback((trigger: PanelTrigger) => {
    rememberFocus(trigger);
    if (trigger === "library") {
      setLastOpenedPanel("library");
      setLibraryCollapsed(false);
    } else {
      setLastOpenedPanel("inspector");
      setInspectorCollapsed(false);
    }
  }, [rememberFocus, setInspectorCollapsed, setLibraryCollapsed]);
  const hide = useCallback((panel: SidePanel) => {
    if (panel === "library") setLibraryCollapsed(true);
    else setInspectorCollapsed(true);
  }, [setInspectorCollapsed, setLibraryCollapsed]);
  const close = useCallback((trigger: PanelTrigger) => {
    focusPanel(trigger);
    hide(trigger === "library" ? "library" : "inspector");
  }, [focusPanel, hide]);
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    const target = event.target;
    if (!activeOverlay || !(target instanceof Element) || !event.currentTarget.contains(target)) return;
    const column = activeOverlay === "library" ? libraryColumn : inspectorColumn;
    if (!column.current?.contains(target) && !target.closest(".library-panel-strip button")) hide(activeOverlay);
  };

  const library: ColumnLayout = { collapsed: libraryCollapsed, slideOver: librarySlideOver, track: libraryTrack, size: librarySize };
  const inspector: ColumnLayout = { collapsed: inspectorCollapsed, slideOver: inspectorSlideOver,
    track: strip ? 0 : inspectorCollapsed || inspectorSlideOver ? RAIL : inspectorSize.width, size: inspectorSize };
  return { workspace, libraryColumn, inspectorColumn, library, inspector, strip, activeOverlay, reveal, close, hide, focusPanel, onPointerDown };
}

export function KnowledgeBaseWorkspace({ base, workspaceId, onAdd, onNotify, onOpenSource, onTrashed, outputActivity,
  onOutputActivity, editDirty, onChatVisible }: {
  base: KnowledgeBase; workspaceId: string | null;
  onAdd: (kind: "file" | "image" | "link" | "recording" | "note") => void;
  onNotify: Notify;
  onOpenSource: (id: string, queue: string[]) => void;
  onTrashed: (entry: TrashItem, reopen?: OutputActivityOpen) => void;
  outputActivity?: OutputActivityOpen | null | undefined;
  onOutputActivity?: ((target: OutputActivityOpen) => void) | undefined;
  editDirty?: { current: boolean };
  onChatVisible?: (visible: boolean) => void;
}) {
  const [contents, setContents] = useState<LibraryContents | null>(null);
  const [sourceLoadFailed, setSourceLoadFailed] = useState(false);
  const [inspector, setInspector] = useState<{ tab: InspectorTab; detail: EvidenceTarget | TraceTarget | null }>({ tab: "studio", detail: null });
  const [web, setWeb] = useState({ available: false, enabled: false });
  const [chatOnline, setChatOnline] = useState(true);
  const clearDetail = useCallback(() => setInspector((current) => current.detail ? { ...current, detail: null } : current), []);
  const [selection, setSelection] = useSourceSelection(base.id);
  const [middle, setMiddle] = useState<Middle>(() => {
    const id = persistentStorage.getItem(`gunther:open-source:${base.id}`);
    return id ? { kind: "source", id } : chat;
  });
  const [sourcesOpen, setSourcesOpen] = useState(true);
  const [wideReaderKey, setWideReaderKey] = useState<string | null>(null);
  const ownDirty = useRef(false);
  const edit = editDirty ?? ownDirty;
  const [, redrawOnEditTransition] = useReducer((revision: number) => revision + 1, 0);
  const dirty = useMemo(() => ({
    get current() { return edit.current; },
    set current(value) {
      if (edit.current === value) return;
      edit.current = value;
      redrawOnEditTransition();
    },
  }), [edit]);
  const sources = contents?.sources ?? [];
  const liveSourceIds = new Set(sources.map((source) => source.id));
  const sourceIds = selectedSources(selection, [...liveSourceIds]);
  const scope = outputScope(selection, sourceIds);
  const sourceState = (selected: OutputScope): SourceState => {
    if (selected.mode === "selection" && !selected.sourceIds.length) {
      // Studio verifies the trust and membership of these selected records when it resolves the scope.
      return selected.unitIds.length || selected.sessionIds.length ? "ready" : "empty";
    }
    if (sourceLoadFailed) return "error";
    if (!contents) return "loading";
    const hasMaterial = selected.mode === "library" ? liveSourceIds.size > 0 :
      selected.sourceIds.every((id) => liveSourceIds.has(id));
    return hasMaterial ? "ready" : "empty";
  };
  const currentSourceState = sourceState(scope);
  const scopeEmpty = scope.mode === "selection" && currentSourceState === "empty";
  const { menu, unavailable } = useModelMenu();
  const modelMenu = unavailable ? null : menu;
  const studio = useStudio(base.id, workspaceId);
  // The result stays available when the reader closes and reopens during a run.
  const directRuns = useRef(new Map<string, Promise<OutputDone | null>>());
  const direct: typeof studio.direct = (id, ...args) => {
    const result = studio.direct(id, ...args);
    if (!directRuns.current.has(id)) {
      directRuns.current.set(id, result);
      const clear = () => directRuns.current.delete(id);
      void result.then(clear, clear);
    }
    return result;
  };
  const chatActions = useRef<LibraryChatActions>(null);
  const editFlush = useRef<EditFlush["current"]>(null);
  const target = middle.kind === "output" ? middle.target : null;
  const [readerRevision, setReaderRevision] = useState(0);
  const readerKey = target ? `${outputTargetKey(target)}:${readerRevision}` : null;
  const [outputState, setOutputState] = useState<{ key: string | null; artifact: Artifact | null; part: number | null }>({ key: null, artifact: null, part: null });
  const [sourceCount, setSourceCount] = useState<{ id: string; count: number } | null>(null);
  const onSourceCount = useCallback((id: string, count: number) => setSourceCount((current) =>
    current?.id === id && current.count === count ? current : { id, count }), []);
  const artifact = readerKey !== null && outputState.key === readerKey ? outputState.artifact : null;
  const displayedVersionId = artifact?.id;
  const selectedOutput = artifact ?? (target?.artifactId ? studio.artifacts.find((version) => version.id === target.artifactId) : undefined);
  const activeLineageId = selectedOutput?.lineageId ?? target?.lineageId ?? null;
  const activePlan = target && !target.lineageId && !target.artifactId ? planAddress(target) : null;
  const savedPlan = activePlan ? studio.savedForPlan(activePlan) : undefined;
  const editsAreClean = !dirty.current;
  const readerStudio = { ...studio, direct };
  if (activePlan && !editsAreClean) {
    const matchesPlan = (address: PlanAddress) =>
      address.conversationId === activePlan.conversationId && address.planId === activePlan.planId;
    readerStudio.savedForPlan = (address) => matchesPlan(address) ? undefined : studio.savedForPlan(address);
    const detail = studio.details[activePlan.conversationId];
    if (detail?.works.some((work) => work.plan?.planId === activePlan.planId && work.versionId)) {
      // Keep the editor mounted while its plan acquires a version in another window.
      readerStudio.details = { ...studio.details, [detail.id]: {
        ...detail, works: detail.works.map((work) => work.plan?.planId === activePlan.planId ? { ...work, versionId: null } : work),
      } };
    }
  }
  const partInView = readerKey !== null && outputState.key === readerKey ? outputState.part : null;
  const onArtifactChange = useCallback((next: Artifact | null) => setOutputState((current) =>
    current.key === readerKey && current.artifact === next ? current : { key: readerKey, artifact: next, part: current.key === readerKey ? current.part : null }), [readerKey]);
  const onPartChange = useCallback((part: number | null) => setOutputState((current) =>
    current.key === readerKey && current.part === part ? current : { key: readerKey, artifact: current.key === readerKey ? current.artifact : null, part }), [readerKey]);
  const wideReader = readerKey !== null && readerKey === wideReaderKey;
  const onWideReader = useCallback((wide: boolean) => setWideReaderKey(wide ? readerKey : null), [readerKey]);
  const { workspace, libraryColumn, inspectorColumn, library, inspector: inspectorLayout, strip,
    activeOverlay, reveal, close, hide, focusPanel, onPointerDown } = useWorkspaceColumns(wideReader);
  const libraryId = useId();
  const inspectorId = useId();
  const toggleLibrary = () => library.collapsed ? reveal("library") : close("library");
  const backToEvidence = () => { focusPanel("evidence"); clearDetail(); };
  const focusSources = () => {
    reveal("library");
    setSourcesOpen(true);
    window.requestAnimationFrame(() => workspace.current?.querySelector<HTMLInputElement>(".contents-filter input")?.focus());
  };
  const choose = useCallback((next: Middle | (() => Middle)) => {
    const resolved = typeof next === "function" ? null : next;
    if (resolved && middle.kind === "output" && resolved.kind === "output" && outputTargetKey(middle.target) === outputTargetKey(resolved.target)
      && (!resolved.target.artifactId || resolved.target.artifactId === displayedVersionId)) {
      if (middle.run !== resolved.run || (!middle.target.typeId && resolved.target.typeId)) setMiddle(resolved);
      return true;
    }
    if (!discardOutputEdit(dirty)) return false;
    const chosen = resolved ?? (next as () => Middle)();
    if (chosen.kind === "output") setReaderRevision((revision) => revision + 1);
    setMiddle(chosen);
    clearDetail();
    return true;
  }, [clearDetail, dirty, displayedVersionId, middle]);
  const backToChat = () => {
    const flush = target && editFlush.current;
    if (flush) void flush().then((saved) => { if (saved) choose(chat); });
    else choose(chat);
  };
  const openOutput = (target: OutputTarget) => choose({ kind: "output", target });
  const openStudioOutput = (nextTarget: OutputTarget | (() => OutputTarget), run?: StudioRun) => {
    const build = (target: OutputTarget): Extract<Middle, { kind: "output" }> => {
      const result = target.conversationId ? directRuns.current.get(target.conversationId) : undefined;
      return { kind: "output", target, run: target.artifactId && run ? { ...run, result } : undefined };
    };
    let resolvedTarget: OutputTarget | null = null;
    const opened = typeof nextTarget === "function"
      ? choose(() => {
        const built = build(nextTarget());
        resolvedTarget = built.target;
        return built;
      })
      : choose(build((resolvedTarget = nextTarget)));
    if (opened && resolvedTarget?.planId && !resolvedTarget.artifactId && !resolvedTarget.lineageId && activeOverlay === "inspector") {
      close("studio");
    }
    return opened;
  };
  useEffect(() => {
    if (middle.kind !== "output" || !savedPlan || !editsAreClean) return;
    const next = { ...middle.target, artifactId: savedPlan.id, lineageId: savedPlan.lineageId };
    setMiddle((current) => current === middle && !dirty.current ? { kind: "output", target: next } : current);
  }, [dirty, editsAreClean, middle, savedPlan]);
  const refreshVisiblePlan = useEffectEvent(() => {
    if (activePlan && !studio.planning.locals[activePlan.planId]) {
      void studio.read(activePlan.conversationId).catch(() => undefined);
    }
  });
  useEffect(() => { if (contents) refreshVisiblePlan(); }, [contents, modelMenu]);
  const followedActivity = middle.kind === "output" ? middle.run?.activity : undefined;
  const finishedActivity = followedActivity && studio.activities.find((item) => item.id === followedActivity.id
    && item.startedAt === followedActivity.startedAt && (item.state === "done" || item.state === "failed"));
  const savedTarget = finishedActivity?.state === "done" ? finishedActivity.open : null;
  const activityVersionId = savedTarget?.view === "library" && savedTarget.tab === "outputs" ? savedTarget.artifactId : null;
  const activityComplete = finishedActivity && (!activityVersionId || studio.artifacts.some((item) => item.id === activityVersionId));
  useEffect(() => {
    if (middle.kind !== "output" || !middle.run || !displayedVersionId) return;
    const { run, target } = middle;
    let following = true;
    const finish = (savedId: string | null | undefined) => {
      if (!following) return;
      const next = savedId && savedId !== target.artifactId && !dirty.current ? { ...target, artifactId: savedId } : target;
      setMiddle((current) => current === middle ? { kind: "output", target: next } : current);
    };
    if (displayedVersionId !== target.artifactId) {
      finish(null);
    } else if (run.result) {
      const finishFromResult = (done: OutputDone | null) => {
        if (done) finish(isTurnResult(done) ? done.artifact?.id : done.id);
        else if (!run.activity || activityComplete) finish(activityVersionId);
      };
      void run.result.then(finishFromResult, () => finishFromResult(null));
    } else if (activityComplete) {
      finish(activityVersionId);
    }
    return () => { following = false; };
  }, [activityComplete, activityVersionId, dirty, displayedVersionId, middle]);
  const askFromReader = (question: string, submit: boolean) => {
    if (choose(chat)) chatActions.current?.ask(question, submit);
  };
  const openFullSource = (id: string) => onOpenSource(id, sources.map((source) => source.id));
  const closeLibraryOverlay = useCallback(() => {
    if (activeOverlay === "library") close("library");
  }, [activeOverlay, close]);
  const returnToChat = useCallback(() => {
    if (!choose(chat)) return false;
    closeLibraryOverlay();
    return true;
  }, [choose, closeLibraryOverlay]);
  const chats = useLibraryChats(base.id, onNotify, clearDetail, returnToChat);
  const traces = useTraces();
  const contextKey = middle.kind === "chat" ? `chat:${chats.activeId ?? ""}:${chats.selectedMessageId ?? ""}`
    : middle.kind === "source" ? `source:${middle.id}` : `output:${readerKey}:${artifact?.id ?? ""}`;
  const selectedMessage = chats.activeSession?.id === chats.activeId
    ? chats.activeSession.messages.find((message) => message.id === chats.selectedMessageId && message.role === "assistant") ?? null : null;
  const selectTab = useCallback((tab: InspectorTab) => {
    setInspector((current) => ({ ...current, tab }));
    reveal(tab);
  }, [reveal]);
  const toggleInspector = (tab: InspectorTab) => {
    if (!inspectorLayout.collapsed && inspector.tab === tab) close(tab);
    else selectTab(tab);
  };
  const stepEvidence = useCallback((index: number) => setInspector((current) => {
    const detail = current.detail;
    return detail && !("kind" in detail) && index >= 0 && index < detail.citations.length
      ? { ...current, detail: { ...detail, index } } : current;
  }), []);
  const hasEvidenceDetail = inspector.tab === "evidence" && !inspectorLayout.collapsed && inspector.detail !== null;
  const evidenceDetail = hasEvidenceDetail && inspector.detail && !("kind" in inspector.detail)
    ? inspector.detail : null;
  useShortcut("mod+\\", toggleLibrary);
  useShortcut("mod+.", () => toggleInspector(inspector.tab));
  useShortcut("mod+shift+e", () => selectTab(inspector.tab === "evidence" ? "studio" : "evidence"));
  useShortcut("[", () => { if (evidenceDetail) stepEvidence(evidenceDetail.index - 1); }, { enabled: Boolean(evidenceDetail), allowInInputs: false });
  useShortcut("]", () => { if (evidenceDetail) stepEvidence(evidenceDetail.index + 1); }, { enabled: Boolean(evidenceDetail), allowInInputs: false });
  const openEvidence = useCallback((citations: ConversationCitation[], index: number, key = contextKey, passageSelector = false) => {
    if (!citations[index]) return;
    setInspector({ tab: "evidence", detail: { contextKey: key, citations, index, passageSelector } });
    reveal("evidence");
  }, [contextKey, reveal]);
  const inspectSources = (message: SessionMessage) => {
    chats.setSelectedMessageId(message.id);
    setInspector({ tab: "evidence", detail: null });
    reveal("evidence");
  };
  const inspectCitation = (message: SessionMessage, index: number) => {
    chats.setSelectedMessageId(message.id);
    openEvidence(message.citations, index, `chat:${message.sessionId}:${message.id}`);
  };
  const openTrace = (message: SessionMessage) => {
    chats.setSelectedMessageId(message.id);
    setInspector({ tab: "evidence", detail: { kind: "trace", contextKey: `chat:${message.sessionId}:${message.id}`,
      sessionId: message.sessionId, messageId: message.id } });
    reveal("evidence");
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
  const trashOutput = async (item: OutputTrashTarget, fromReader = false, reopen = target) => {
    if (!workspaceId) return;
    const conversation = "conversation" in item;
    const affected = fromReader || (conversation
      ? (selectedOutput?.conversationId ?? target?.conversationId) === item.id
      : activeLineageId === item.lineageId);
    if (conversation) {
      const works = new Map(studio.works.filter((work) => work.conversationId === item.id)
        .map((work) => [work.lineageId ?? work.workKey, work.title]));
      for (const work of studio.details[item.id]?.works ?? []) works.set(work.key, work.title);
      for (const version of latestVersions(studio.artifacts)) {
        if (studio.owner(version) === item.id) works.set(version.lineageId, version.title);
      }
      if (works.size > 1 && !window.confirm(`Move this conversation and all ${works.size} works to Trash?\n\n${[...works.values()].join("\n")}`)) return;
    }
    // The editor clears its dirty state when the move succeeds and closes it.
    if (affected && !discardOutputEdit({ current: dirty.current })) return;
    try {
      const entry = await moveToTrash({
        kind: "output", id: item.id, libraryId: base.id, workspaceId, conversation,
      });
      studio.forgetTrashed(item);
      if (affected) setMiddle((current) => current === middle ? chat : current);
      const destination = fromReader && !conversation
        ? { ...reopen, artifactId: item.id, lineageId: item.lineageId }
        : reopen;
      onTrashed(entry, destination ? { view: "library", libraryId: base.id, tab: "outputs", ...destination } : undefined);
    } catch (reason) {
      onNotify(reason instanceof Error ? reason.message : "The output could not move to Trash.");
    }
  };
  const discardSetup = (address: PlanAddress) => {
    if (activePlan?.planId === address.planId && activePlan.conversationId === address.conversationId && !choose(chat)) return;
    studio.planning.discardLocal(address.planId);
  };

  useEscape(() => {
    if (activeOverlay === "library") close("library");
    else if (hasEvidenceDetail) backToEvidence();
    else if (activeOverlay === "inspector") close(inspector.tab);
  }, hasEvidenceDetail || activeOverlay !== null);
  const openActivityOutput = useEffectEvent((activity: OutputActivityOpen) => {
    const key = outputTargetKey(activity);
    const running = studio.activities.find((item) => {
      const target = outputActivityTarget(item.open);
      return target && outputTargetKey(target) === key && (item.state === "running" || item.state === "waiting");
    });
    openStudioOutput(activity, running ? { activity: running } : undefined);
    onOutputActivity?.(activity);
  });
  useEffect(() => {
    if (outputActivity && workspaceId) openActivityOutput(outputActivity);
  }, [outputActivity, workspaceId]);
  useEffect(() => { persistentStorage.removeItem(`gunther:open-source:${base.id}`); }, [base.id]);
  const chatVisible = middle.kind === "chat";
  useEffect(() => { onChatVisible?.(chatVisible); }, [chatVisible, onChatVisible]);
  const detailCitation = evidenceDetail?.citations[evidenceDetail.index];
  const answerCitations = chatVisible ? selectedMessage?.citations : undefined;
  const detailIndex = evidenceDetail && !evidenceDetail.passageSelector && evidenceDetail.contextKey === contextKey && detailCitation && answerCitations
    ? (evidenceDetail.citations === answerCitations ? evidenceDetail.index : answerCitations.findIndex((item) => item.id === detailCitation.id)) : -1;
  const showInAnswer = (index: number) => {
    const closeAfterLocating = activeOverlay === "inspector";
    if (!chatActions.current?.locateCitation(index, closeAfterLocating)) onNotify("No matching citation in the selected answer.");
    else if (closeAfterLocating) hide("inspector");
  };

  const source = middle.kind === "source" ? sources.find((item) => item.id === middle.id) : undefined;
  const running = studio.jobs.size > 0 || studio.activities.some((item) => item.state === "running" || item.state === "waiting");
  const libraryToggleLabel = `${library.collapsed ? "Expand" : "Collapse"} Library`;
  const libraryTrigger = <button type="button" className="gx-icon-button" data-panel-focus="library"
    aria-label={libraryToggleLabel} title={libraryToggleLabel} aria-controls={libraryId}
    aria-expanded={!library.collapsed} onClick={toggleLibrary}><PanelLeft size={16} /></button>;
  const inspectorTriggers = <InspectorRail panelId={inspectorId} expanded={inspectorLayout.collapsed ? null : inspector.tab}
    running={running} onToggle={toggleInspector} />;
  return <div className={`library-workspace${strip ? " has-panel-strip" : ""}`} ref={workspace} onPointerDown={onPointerDown}>
    {strip && <div className="library-panel-strip" role="group" aria-label="Workspace panels">
      {libraryTrigger}{inspectorTriggers}
    </div>}
    <LibraryColumn columnRef={libraryColumn} panelId={libraryId} layout={library} trigger={strip ? null : libraryTrigger}>
      <LibraryChatsSection baseId={base.id} chats={chats} onOpen={openChat} onCreate={createChat} />
      <SourceTree baseId={base.id} contents={contents} onContents={setContents} loadFailed={sourceLoadFailed} onLoadFailedChange={setSourceLoadFailed}
        selection={selection} selectedIds={sourceIds}
        open={sourcesOpen} onOpenChange={setSourcesOpen} onScope={setSelection} activeSourceId={source?.id ?? null}
        onSource={(id) => { if (choose({ kind: "source", id })) closeLibraryOverlay(); }} onTrashed={onTrashed} />
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
          sourceIds={scope.sourceIds} sourceScopeEmpty={scopeEmpty} sourceScopeSelected={selection.mode === "selected"} onFocusSources={focusSources} onClearSources={() => setSelection(allSources)}
          chatActions={chatActions} onNotify={onNotify} onInspectSources={inspectSources} onInspectCitation={inspectCitation}
          onOpenTrace={openTrace} traceEnabled={traces.on} onOnlineStatus={setChatOnline} onWebStatus={setWeb} />
      </div>
      {!chatVisible && <div className="library-middle-page">
        <div className="library-middle-bar"><button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={backToChat}><ArrowLeft size={14} />Back to chat</button></div>
        {source && <SourceSummary key={source.id} source={source} onOpen={() => openFullSource(source.id)} onCite={openEvidence}
          onAsk={askFromReader} />}
        {middle.kind === "source" && !source && <p className="library-reading">{contents ? "Source unavailable." : "Loading source…"}</p>}
        {target && workspaceId && <OpenOutput key={readerKey} baseId={base.id} workspaceId={workspaceId}
          target={target} studio={readerStudio} scope={scope} sourceState={sourceState} modelMenu={modelMenu}
          webAvailable={web.available} dirty={dirty} editFlush={editFlush} onNotify={onNotify}
          onCite={(version, index) => {
            onArtifactChange(version);
            openEvidence(version.citations, index, `output:${readerKey}:${version.id}`);
          }}
          onArtifactChange={onArtifactChange} onPartChange={onPartChange} onOpen={openOutput} onWideReader={onWideReader} onAskChat={(question) => askFromReader(question, true)}
          onTrash={(item) => void trashOutput(item, true)} />}
      </div>}
    </section>
    <LibraryInspector columnRef={inspectorColumn} panelId={inspectorId} layout={inspectorLayout}
      rail={strip ? null : inspectorTriggers} onCollapse={strip ? undefined : () => close(inspector.tab)}
      tab={inspector.tab} onTab={selectTab} evidenceCount={middle.kind === "chat" ? selectedMessage?.citations.length ?? 0
        : middle.kind === "source" ? (source && sourceCount?.id === source.id ? sourceCount.count : 0) : artifact?.citations.length ?? 0}
      briefUpdating={chatVisible && chats.activeId !== null && chats.briefBusy === chats.activeId}
      running={running}
      evidence={<AnswerCitationNavigation.Provider value={answerCitations ? { detailIndex: detailIndex >= 0 ? detailIndex : undefined, showInAnswer } : null}>
        <EvidencePane session={chats.activeSession} selected={middle.kind === "chat" ? selectedMessage : null} chatVisible={chatVisible}
          sources={sources} selectedSourceIds={sourceIds} allSelected={selection.mode === "all"} webEnabled={web.enabled} onFocusSources={focusSources}
          output={artifact} partInView={partInView} baseId={base.id} workspaceId={workspaceId} sourceId={source?.id ?? null} onSourceCount={onSourceCount}
          brief={chats.activeSession && chats.activeSession.id === chats.activeId ? {
            updating: chats.briefBusy === chats.activeSession.id, readOnly: !chatOnline || chats.activeSession.archived,
            save: (input) => chats.saveBrief(chats.activeSession!.id, input), retry: () => void chats.refreshBrief(chats.activeSession!.id),
            ask: chats.activeSession.archived ? undefined : (text) => chatActions.current?.ask(text, false),
          } : undefined}
          traceEnabled={traces.on} onOpenTrace={selectedMessage ? () => openTrace(selectedMessage) : undefined} onNotify={onNotify}
          detail={inspector.detail} onOpen={(citations, index, passageSelector) => openEvidence(citations, index, contextKey, passageSelector)}
          onStep={stepEvidence}
          onBack={backToEvidence} onOpenSource={(id) => { clearDetail(); openFullSource(id); }}
          onOpenLibrarySource={(id) => { if (choose({ kind: "source", id }) && activeOverlay === "inspector") close("evidence"); }} />
      </AnswerCitationNavigation.Provider>}
      studio={<StudioColumn studio={studio} scope={scope} sourceState={currentSourceState} sourceCount={sourceIds.length} disabled={!workspaceId}
        activeOutput={target && workspaceId ? { ...target, lineageId: activeLineageId } : null}
        onOpen={openStudioOutput} onTrash={(item, reopen) => void trashOutput(item, false, reopen)} onDiscard={discardSetup} onNotify={onNotify} />} />
  </div>;
}
