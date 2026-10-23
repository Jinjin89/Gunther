import type { Artifact, ArtifactBriefState, ArtifactConversationMessage, ArtifactSummary, ArtifactTurnInput, ArtifactTurnOut, BuildOutputRequest, ConversationCitation, EditArtifactBriefRequest, OutputKind, OutputScope, OutputStyle } from "@gunther/contracts";
import { outputStyles } from "@gunther/contracts";
import { invoke } from "@tauri-apps/api/core";
import { ArrowRight, ChevronDown, ChevronRight, CircleAlert, Copy, Download, FileDown, History, ListChecks, LoaderCircle, Maximize2, MessageSquare, Minimize2, PanelsTopLeft, Pencil, Plus, Presentation, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { isTurnResult, knowledgeApi, OutputStoppedError, type ArtifactTurnEvent } from "../api";
import type { KnowledgeBase } from "../atlas";
import { EvidencePanel } from "../components/evidence/EvidencePanel";
import { openSettings } from "../components/SettingsLayout";
import { ArtifactMenu as PopMenu } from "./ArtifactMenu";
import { BrandMark } from "../design/BrandMark";
import { useModelMenu, useDeviceChoice, usableChoice, useWebSearch } from "../models/askModel";
import { persistentStorage } from "../persistentStorage";
import { ApproachPanel } from "./ApproachPanel";
import { ArtifactAgentPlan, outputActivity } from "./ArtifactAgentPlan";
import { ArtifactBriefPanel } from "./ArtifactBriefPanel";
import { ArtifactComposer } from "./ArtifactComposer";
import { ArtifactMessageList, LiveTurn } from "./ArtifactMessages";
import { ArtifactWorkspaceLayout, PreviewHeightHandle, usePreviewControls } from "./ArtifactWorkspaceLayout";
import { artifactRequestId, EARLIER_WORK, useArtifactConversations, type ArtifactConversation } from "./artifactConversations";
import { EditPane } from "./EditPane";
import { LazySlidesView } from "./LazySlides";
import { useLiveOutput } from "./liveOutput";
import { MaterialSelectionPanel } from "./MaterialSelectionPanel";
import { OutlineEditor } from "./OutlineEditor";
import { PrintRoot } from "./PrintRoot";
import { prepareForPrint } from "./printReady";
import { SlideReviewPanel } from "./SlideReviewPanel";
import { ReportView } from "./ReportView";
import { describeScope, type ScopeCounts } from "./ScopePicker";
import { type DeckStyle, type DeckTheme, parseSlides, withAccent, withTheme } from "./sections";
import { withDeckMarker } from "./deck/marker";
import { isDeckV2, isReportV2 } from "./deck/detect";
import { downloadDocx, downloadPptx } from "./deck/download";
import "./outputs.css";

export interface OutputsPageProps {
  base: KnowledgeBase; workspaceId: string | null; onNotify: (message: string) => void; onAdd: () => void;
  onOpenSource?: ((id: string, queue: string[]) => void) | undefined;
  fromDiscussion?: string | null | undefined; onFromDiscussion?: (() => void) | undefined;
  focused?: boolean | undefined; onFocus?: ((value: boolean) => void) | undefined;
  /** Embedders without a transcription service can keep the microphone inactive. */
  allowVoiceInput?: boolean;
}
const kindLabel = (kind: OutputKind) => kind === "slides" ? "Slides" : "Report";
const styles: Record<OutputStyle, string> = { auto: "Auto", overview: "Overview", field_guide: "Field guide", teaching_path: "Teaching path", decision_brief: "Decision brief" };
const describe = (item: ArtifactSummary) => [kindLabel(item.kind), item.style ? styles[item.style as OutputStyle] ?? item.style.replaceAll("_", " ") : "", item.audience[0]!.toUpperCase() + item.audience.slice(1), item.origin === "edit" ? "Edited" : item.origin === "revise" ? "Revised" : ""].filter(Boolean).join(" · ");
const messageOf = (reason: unknown) => reason instanceof Error ? reason.message : typeof reason === "string" ? reason : "The request could not be completed.";
const USER_WORDS = { start: "Just start", continue: "Continue" } as const;
const clip = (text: string, length: number) => text.length > length ? `${text.slice(0, length - 1)}…` : text;

/** What is being worked on: a message to the agent, a rebuild through the old builder (from the Approach and Outline panels), or a run started before this page was open. */
interface JobContext { conversationId: string; turnId: string; kind: "turn" | "rebuild" | "follow"; text: string }
/** What the next message is about, because the person pointed at it. Counted from 0. */
interface Pointing { section: number; quote?: string | undefined }

/** The versions of each artifact, newest first, one list per artifact. */
const lineagesOf = (items: ArtifactSummary[]) => {
  const byLineage = new Map<string, ArtifactSummary[]>();
  for (const item of items) byLineage.set(item.lineageId, [...(byLineage.get(item.lineageId) ?? []), item]);
  return [...byLineage.values()].map((versions) => versions.sort((a, b) => b.versionNumber - a.versionNumber));
};
/** The artifacts a conversation holds: the ones its works are, and the ones whose versions name it. */
function ownedBy(conversation: ArtifactConversation, items: ArtifactSummary[]) {
  const keys = new Set(conversation.detail?.works.map((work) => work.key) ?? []);
  return lineagesOf(items).filter((versions) => keys.has(versions[0]!.lineageId) || versions.some((item) => (item.conversationId || (conversation.id === EARLIER_WORK ? conversation.id : "")) === conversation.id));
}

export function OutputsPage({ base, workspaceId, onNotify, onAdd, onOpenSource, fromDiscussion, onFromDiscussion, focused: controlledFocus, onFocus, allowVoiceInput = true }: OutputsPageProps) {
  const { store, state, active } = useArtifactConversations(workspaceId, base.id);
  const [history, setHistory] = useState<ArtifactSummary[]>([]);
  const [open, setOpen] = useState<Artifact | null>(null);
  const [loading, setLoading] = useState(true);
  const [opening, setOpening] = useState(false);
  const [generalError, setGeneralError] = useState<string | null>(null);
  const [briefError, setBriefError] = useState<string | null>(null);
  const [library, setLibrary] = useState<ScopeCounts | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [localFocused, setLocalFocused] = useState(false);
  const [focusRequest, setFocusRequest] = useState(0);
  const [job, setJob] = useState<JobContext | null>(null);
  const [evidence, setEvidence] = useState<{ citation: ConversationCitation; index: number } | null>(null);
  // What the next message is about: a slide or words the person pointed at, or else the slide on screen.
  const [pointing, setPointing] = useState<Pointing | null>(null);
  const [onScreen, setOnScreen] = useState<number | null>(null);
  const [jump, setJump] = useState<{ index: number; nonce: number } | null>(null);
  const jumps = useRef(0);
  const layoutKey = `gunther:artifact-layout:${workspaceId ?? "unverified"}:${base.id}`;
  const [controlsExpanded, setControlsExpanded] = usePreviewControls(`${layoutKey}:controls`);
  const [controlsHeight, setControlsHeight] = useState(() => { try { const value = Number(persistentStorage.getItem(`${layoutKey}:height`)); return value >= 120 && value <= 480 ? value : 380; } catch { return 380; } });
  useEffect(() => { try { persistentStorage.setItem(`${layoutKey}:height`, String(controlsHeight)); } catch { /* Resizing is still available. */ } }, [layoutKey, controlsHeight]);
  const focused = controlledFocus ?? localFocused;
  const focusPreview = useCallback((value: boolean) => { if (value) setControlsExpanded(false); if (onFocus) onFocus(value); else setLocalFocused(value); }, [onFocus, setControlsExpanded]);
  const { menu, unavailable: menuUnavailable } = useModelMenu();
  const [deviceChoice, setDeviceChoice] = useDeviceChoice();
  const choice = usableChoice(menu, active.choice, deviceChoice);
  const web = useWebSearch();
  const live = useLiveOutput();
  const building = live.live !== null;
  const activeJob = job?.conversationId === active.id && building;
  const busy = building || editing || saving || checking;
  const noModel = menu !== null && menu.models.length === 0;
  const controller = useRef<AbortController | null>(null);
  const followController = useRef<AbortController | null>(null);
  const jobRef = useRef<JobContext | null>(null);
  const openingToken = useRef(0);
  const outlinePanel = useRef<HTMLDivElement>(null);
  const messagesPanel = useRef<HTMLDivElement>(null);
  const messagesContent = useRef<HTMLDivElement>(null);
  const followingMessages = useRef(true);
  const detail = active.detail;
  const messages = useMemo(() => detail?.messages ?? [], [detail]);
  const pending = detail?.pending ?? null;
  useEffect(() => { followingMessages.current = true; if (messagesPanel.current) messagesPanel.current.scrollTop = messagesPanel.current.scrollHeight; }, [active.id]);
  useEffect(() => {
    const panel = messagesPanel.current; const content = messagesContent.current;
    if (!panel || !content || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => { if (followingMessages.current) panel.scrollTop = panel.scrollHeight; }); observer.observe(content); return () => observer.disconnect();
  }, []);
  useEffect(() => { if (followingMessages.current && messagesPanel.current) messagesPanel.current.scrollTop = messagesPanel.current.scrollHeight; }, [messages]);
  // Pointing belongs to the version it was made on.
  useEffect(() => { setPointing(null); }, [open?.id]);
  const groups = useMemo(() => lineagesOf(history), [history]);
  const activeGroups = useMemo(() => ownedBy(active, history), [active, history]);
  const versions = open ? groups.find((items) => items[0]?.lineageId === open.lineageId) ?? [open] : [];
  const head = versions[0] ?? null;
  const latest = !open || !head || head.id === open.id;
  const changeable = open !== null && open.origin !== "legacy";
  const lastReply = [...messages].reverse().find((message) => message.role === "assistant") ?? null;
  const activeLive = activeJob ? live.live : null;
  const scope = active.scope;
  const scopeText = scope.mode === "library" ? library ? describeScope(library) : "Reading the library…" : describeScope({ sources: scope.sourceIds.length, units: scope.unitIds.length, sessions: scope.sessionIds.length });
  const emptyScope = scope.mode === "library" ? library !== null && library.sources + library.units === 0 : scope.sourceIds.length + scope.unitIds.length + scope.sessionIds.length === 0;
  const sendDisabled = loading || opening || !workspaceId || menu === null || noModel || ((library === null || emptyScope) && !open) || (building && !activeJob) || editing || saving || checking;
  // A new artifact's parts are all "added": nothing is marked as changed in it.
  const changed = useMemo(() => (open?.provenance.changes ?? []).every((change) => change.what === "added") ? [] : (open?.provenance.changes ?? []).filter((change) => change.what !== "deleted" && change.position !== null).map((change) => change.position! - 1), [open]);
  const works = detail?.works ?? [];
  const work = works.find((item) => item.key === open?.lineageId) ?? works.find((item) => item.key === detail?.active) ?? null;
  const briefState: ArtifactBriefState | null = detail ? { brief: detail.brief, spec: work?.spec ?? null } : null;
  const unit = open?.kind === "report" ? "Section" : "Slide";
  const pointedHeading = pointing && open ? open.sections[pointing.section]?.heading || open.outline[pointing.section]?.heading || "" : "";
  const aboutLabel = !pointing || !open ? null : pointing.quote ? `“${clip(pointing.quote, 70)}”` : [`${unit} ${pointing.section + 1}`, pointedHeading].filter(Boolean).join(" · ");

  const show = useCallback((artifact: Artifact) => {
    setOpen(artifact); setEditing(false); setSettingsOpen(false); setVersionsOpen(false); setEvidence(null); setGeneralError(null);
    store.update(store.snapshot().activeId, { selectedArtifactId: artifact.id });
  }, [store]);
  const openVersion = async (item: ArtifactSummary) => {
    if (!workspaceId || editing || saving) return null;
    const token = ++openingToken.current;
    setOpening(true);
    try {
      const artifact = await knowledgeApi.artifact(base.id, item.id, workspaceId);
      if (token !== openingToken.current) return null;
      show(artifact); return artifact;
    } catch (reason) { if (token === openingToken.current) setGeneralError(messageOf(reason)); return null; }
    finally { if (token === openingToken.current) setOpening(false); }
  };
  /** The version to show when a conversation is opened: the one last shown, else the artifact being worked on, else the newest. */
  const startingPoint = (conversation: ArtifactConversation, items: ArtifactSummary[]) => {
    const own = ownedBy(conversation, items);
    return items.find((item) => item.id === conversation.selectedArtifactId && own.some((versions) => versions[0]!.lineageId === item.lineageId))
      ?? own.find((versions) => versions[0]!.lineageId === conversation.detail?.active)?.[0] ?? own[0]?.[0];
  };
  const chooseConversation = async (conversation: ArtifactConversation) => {
    if (editing || saving) return;
    openingToken.current += 1; setOpen(null); setOpening(false); setGeneralError(null); setEditing(false); setSettingsOpen(false);
    store.select(conversation.id);
    let current = conversation;
    if (conversation.saved && !conversation.detail) {
      try { await store.open(conversation.id); current = store.get(conversation.id) ?? conversation; } catch (reason) { setGeneralError(messageOf(reason)); }
    }
    const selected = startingPoint(current, history);
    if (selected) await openVersion(selected);
  };
  const newConversation = () => {
    openingToken.current += 1; setOpening(false); setOpen(null); setGeneralError(null); setEditing(false); setSettingsOpen(false);
    store.create(active.scope, choice); setFocusRequest((value) => value + 1);
  };

  useEffect(() => {
    let alive = true;
    controller.current?.abort(); followController.current?.abort(); jobRef.current = null; setJob(null); live.stop();
    openingToken.current += 1; setLoading(true); setOpen(null); setHistory([]); setLibrary(null); setGeneralError(null); setEditing(false);
    if (!workspaceId) { setGeneralError("The local workspace identity has not been verified yet."); setLoading(false); return () => { alive = false; }; }
    void (async () => {
      try {
        const items = await knowledgeApi.artifacts(base.id, workspaceId);
        if (!alive) return;
        setHistory(items);
        // The conversations come from the service; this device's own are sent to it first, once.
        let conversation = await store.load(items);
        if (!alive) return;
        // A turn still running (followed below, perhaps before the list was here) belongs to the conversation it names.
        const running = jobRef.current;
        if (running?.kind === "turn" && running.conversationId !== conversation.id && store.get(running.conversationId)) {
          store.select(running.conversationId); await store.open(running.conversationId).catch(() => undefined); conversation = store.active();
        }
        const item = startingPoint(conversation, items);
        if (item) { const token = ++openingToken.current; const artifact = await knowledgeApi.artifact(base.id, item.id, workspaceId); if (alive && token === openingToken.current && store.active().id === conversation.id) show(artifact); }
      } catch (reason) { if (alive) setGeneralError(messageOf(reason)); }
      finally { if (alive) setLoading(false); }
    })();
    return () => { alive = false; controller.current?.abort(); followController.current?.abort(); openingToken.current += 1; };
  }, [base.id, workspaceId, store, show]);

  /** The saved versions again (the one just saved joins them even when the list is stale). */
  const refreshArtifacts = async (saved?: Artifact) => {
    let items: ArtifactSummary[];
    try { items = await knowledgeApi.artifacts(base.id, workspaceId!); } catch { items = history; }
    const known = new Map(history.map((item) => [item.id, item]));
    items.forEach((item) => known.set(item.id, item));
    if (saved) known.set(saved.id, saved);
    items = [...known.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    setHistory(items);
  };
  /** A turn ended: read what the service kept, and show the version it saved. */
  const settleTurn = async (conversationId: string, done: ArtifactTurnOut) => {
    await refreshArtifacts(done.artifact ?? undefined);
    await store.sync(conversationId).catch((reason) => setGeneralError(messageOf(reason)));
    if (done.artifact && store.snapshot().activeId === conversationId) show(done.artifact);
    if (done.artifact) onNotify(`${kindLabel(done.artifact.kind)} version ${done.artifact.versionNumber} saved.`);
  };
  /** A rebuild (from the Approach or Outline panel) saved a version. */
  const finishBuild = async (artifact: Artifact, conversationId: string | null) => {
    await refreshArtifacts(artifact);
    const owner = artifact.conversationId || conversationId;
    if (owner) await store.sync(owner).catch(() => undefined);
    if (!conversationId && owner && store.get(owner)) store.select(owner);
    if (!conversationId || store.snapshot().activeId === owner) show(artifact);
    onNotify(`${kindLabel(artifact.kind)} version ${artifact.versionNumber} saved.`);
  };

  useEffect(() => {
    if (!workspaceId) return;
    const abort = new AbortController(); followController.current = abort;
    let context: JobContext | null = null;
    const heard = (event: ArtifactTurnEvent) => {
      if (abort.signal.aborted) return;
      if (!context) {
        // Following a run that started before this page was open: what it was asked comes first.
        context = { conversationId: store.snapshot().activeId, turnId: "", kind: "follow", text: event.type === "resumed" ? event.question : "Artifact generation" };
        jobRef.current = context; setJob({ ...context }); live.start();
      }
      if (event.type === "turn") {
        Object.assign(context, { conversationId: event.conversationId, kind: "turn" });
        const known = store.get(event.conversationId);
        if (known) { store.select(known.id); if (known.saved && !known.detail) void store.open(known.id).catch(() => undefined); }
        setJob({ ...context });
      }
      if (event.type === "brief") store.setBrief(context.conversationId, { brief: event.brief, spec: event.spec });
      live.hear(event);
    };
    void knowledgeApi.followOutputBuild(base.id, workspaceId, heard, abort.signal).then(async (done) => {
      if (!done || abort.signal.aborted) return;
      if (isTurnResult(done)) await settleTurn(context?.conversationId ?? store.snapshot().activeId, done);
      else await finishBuild(done, null);
    }).catch(async (reason) => {
      if (abort.signal.aborted) return;
      if (context?.kind === "turn") await store.sync(context.conversationId).catch(() => undefined);
      if (context && !(reason instanceof OutputStoppedError)) setGeneralError(messageOf(reason));
      else if (!context) setGeneralError(messageOf(reason));
    }).finally(() => { if (!abort.signal.aborted && context && jobRef.current === context) { live.stop(); jobRef.current = null; setJob(null); setStopping(false); } });
    return () => abort.abort();
  }, [base.id, workspaceId, store]);

  useEffect(() => {
    if (!fromDiscussion || loading) return;
    const conversation = store.create({ mode: "selection", sourceIds: [], unitIds: [], sessionIds: [fromDiscussion] }, choice);
    void chooseConversation(conversation); setFocusRequest((value) => value + 1); onFromDiscussion?.();
  }, [fromDiscussion, loading]);

  /** What the message is about: the words or slide pointed at, else the slide on screen (not pointed at). */
  const pointer = (): ArtifactTurnInput["focus"] | undefined => {
    // An earlier version's slides need not be the agent's: the message then points at nothing.
    if (open !== null && !latest) return undefined;
    if (pointing) return { section: pointing.section + 1, selected: true, ...(pointing.quote ? { quote: pointing.quote } : {}) };
    if (open?.kind === "slides" && onScreen !== null) return { section: onScreen + 1, selected: false };
    return undefined;
  };
  /** Send a message to the agent, and follow it until the turn ends. */
  const sendTurn = async (turn: { kind: NonNullable<ArtifactTurnInput["kind"]>; text?: string; focus?: ArtifactTurnInput["focus"] }) => {
    if (!workspaceId || building) return;
    const conversationId = active.id;
    const asking = turn.kind === "start" || turn.kind === "continue" ? undefined : turn.focus ?? pointer();
    const payload: ArtifactTurnInput = { clientRequestId: artifactRequestId(), kind: turn.kind, ...(turn.text ? { text: turn.text } : {}), ...(asking ? { focus: asking } : {}), ...(choice.model ? { model: choice.model, effort: choice.effort } : {}), ...(web.enabled ? { web: true } : {}) };
    followController.current?.abort();
    const abort = new AbortController(); controller.current = abort;
    const context: JobContext = { conversationId, turnId: payload.clientRequestId, kind: "turn", text: turn.text || (turn.kind === "start" || turn.kind === "continue" ? USER_WORDS[turn.kind] : "") };
    jobRef.current = context; setJob(context); setGeneralError(null); setStopping(false); setSettingsOpen(false); setPointing(null);
    store.update(conversationId, { draft: "" });
    live.start();
    const hear = (event: ArtifactTurnEvent) => { if (event.type === "brief") store.setBrief(conversationId, { brief: event.brief, spec: event.spec }); live.hear(event); };
    try {
      await store.ensure(conversationId);
      const done = await knowledgeApi.artifactTurnStream(base.id, conversationId, payload, workspaceId, hear, abort.signal);
      if (abort.signal.aborted) return;
      await settleTurn(conversationId, done);
    } catch (reason) {
      if (abort.signal.aborted) return;
      const stopped = reason instanceof OutputStoppedError;
      // The service keeps a failed or stopped turn; read it to show it. If it kept nothing, say why here and give the words back.
      await store.sync(conversationId).catch(() => undefined);
      const kept = store.messages(conversationId).at(-1);
      if (!stopped && !(kept?.role === "assistant" && kept.kind === "failed")) setGeneralError(messageOf(reason));
      if (!store.messages(conversationId).some((message) => message.clientRequestId === payload.clientRequestId) && turn.text && !store.get(conversationId)?.draft) store.update(conversationId, { draft: turn.text });
      onNotify(stopped ? "Stopped. Nothing was saved." : "The request wasn’t handled.");
    } finally { if (!abort.signal.aborted && jobRef.current === context) { live.stop(); jobRef.current = null; setJob(null); setStopping(false); } }
  };
  /** The old builder, for the Approach and Outline panels: a new version from the words changed there. */
  const rebuild = async (request: BuildOutputRequest, instruction: string, runner = knowledgeApi.buildOutputStream) => {
    if (!workspaceId || building) return;
    followController.current?.abort();
    const abort = new AbortController(); controller.current = abort;
    const context: JobContext = { conversationId: active.id, turnId: request.clientRequestId, kind: "rebuild", text: instruction };
    jobRef.current = context; setJob(context); setGeneralError(null); setStopping(false); setSettingsOpen(false);
    live.start();
    try {
      const artifact = await runner(base.id, request, workspaceId, live.hear, abort.signal);
      if (abort.signal.aborted) return;
      await finishBuild(artifact, context.conversationId);
    } catch (reason) {
      if (abort.signal.aborted) return;
      const stopped = reason instanceof OutputStoppedError;
      if (!stopped) setGeneralError(messageOf(reason));
      onNotify(stopped ? "Stopped. Nothing was saved." : "The artifact wasn’t saved.");
    } finally { if (!abort.signal.aborted && jobRef.current === context) { live.stop(); jobRef.current = null; setJob(null); setStopping(false); } }
  };
  const requestFor = (extra: Partial<BuildOutputRequest> = {}): BuildOutputRequest => {
    const kind = open?.kind ?? "report";
    const style = open?.style && outputStyles.includes(open.style as OutputStyle) ? open.style as OutputStyle : null;
    return { clientRequestId: artifactRequestId(), kind, audience: open?.audience ?? "scientist", ...(kind === "report" ? style ? { style } : {} : { ...(open?.provenance.use ? { use: open.provenance.use } : {}), theme: parseSlides(open?.content ?? "")[0]?.theme ?? "classic", ...(open ? { designVersion: parseSlides(open.content)[0]?.designVersion === 1 ? 1 : 0 } : {}) }), scope: active.scope, conversationId: active.id, conversationTitle: active.title, ...(choice.model ? { model: choice.model, effort: choice.effort } : {}), ...(web.enabled ? { web: true } : {}), ...extra };
  };
  const send = (text: string) => void sendTurn({ kind: pending ? "answer" : "message", text });
  const reviewSlides = () => {
    if (!open) return;
    const target = open.id;
    const request = requestFor();
    void rebuild(request, "Review rendered slides", (id, payload, workspace, hear, signal) =>
      knowledgeApi.reviewSlidesStream(id, target, { clientRequestId: payload.clientRequestId, ...(choice.model ? { model: choice.model, effort: choice.effort } : {}) }, workspace, hear, signal));
  };
  const retry = (message: ArtifactConversationMessage) => {
    const asked = messages.slice(0, messages.findIndex((item) => item.id === message.id)).reverse().find((item) => item.role === "user");
    if (!asked) return;
    const focus = asked.payload.focus as ArtifactTurnInput["focus"] | undefined;
    void sendTurn({ kind: asked.kind as NonNullable<ArtifactTurnInput["kind"]>, ...(asked.text ? { text: asked.text } : {}), ...(focus ? { focus } : {}) });
  };
  const stop = async () => { if (!workspaceId) return; setStopping(true); try { await knowledgeApi.stopOutputBuild(base.id, workspaceId); } catch (reason) { setStopping(false); setGeneralError(messageOf(reason)); } };
  const saveEdit = async (content: string, note = "What you typed has not been re-checked yet.") => {
    if (!open || !workspaceId) return null; setSaving(true); setGeneralError(null);
    try { const artifact = await knowledgeApi.editOutput(base.id, open.id, { clientRequestId: artifactRequestId(), content }, workspaceId); await finishBuild(artifact, active.id); onNotify(`Saved as version ${artifact.versionNumber}. ${note}`); return artifact; }
    catch (reason) { setGeneralError(`The edit wasn’t saved. ${messageOf(reason)}`); return null; } finally { setSaving(false); }
  };
  // The deck's accent is part of its Markdown (the title slide's marker), so changing it is an edit.
  const setAccent = (accent: string) => { if (!open) return; const content = withAccent(open.content, accent); if (content !== open.content) void saveEdit(content, `The accent is ${accent}.`); };
  const setTheme = (theme: string) => { if (!open) return; const content = parseSlides(open.content)[0]?.designVersion === 2 ? (["paper", "ink", "editorial"].includes(theme) ? withDeckMarker(open.content, { theme: theme as DeckTheme }) : open.content) : withTheme(open.content, theme); if (content !== open.content) void saveEdit(content, `The style is ${theme}.`); };
  const setDeckTheme = (theme: string) => { if (!open) return; const content = withDeckMarker(open.content, { theme: theme as DeckTheme }); if (content !== open.content) void saveEdit(content, `The theme is ${theme}.`); };
  /**
   * A designed deck's style is the title marker's `style:`. The deck is recoloured at once (saved as a version, so the
   * person sees it), then the backend draws every slide again in that style: the next version. If that fails the recoloured
   * deck stays and the error is shown as for any other action; picking the style again tries again.
   */
  const setDeckStyle = async (style: DeckStyle) => {
    if (!open || !workspaceId) return;
    const content = withDeckMarker(open.content, { style, theme: "paper" });
    let target: Artifact = open;
    if (content !== open.content) {
      const saved = await saveEdit(content, `The style is ${style}.`);
      if (!saved) return;
      target = saved;
    }
    const model = choice.model ? { model: choice.model, effort: choice.effort } : {};
    void rebuild(requestFor(), `Redesign the slides in ${style}`, (id, payload, workspace, hear, signal) =>
      knowledgeApi.restyleSlidesStream(id, target.id, { clientRequestId: payload.clientRequestId, style, ...model, conversationId: active.id, conversationTitle: active.title }, workspace, hear, signal));
  };
  const checkAgain = async () => { if (!open || !workspaceId) return; setChecking(true); try { setOpen(await knowledgeApi.checkOutput(base.id, open.id, workspaceId)); onNotify("Checked against the sources."); } catch (reason) { setGeneralError(messageOf(reason)); } finally { setChecking(false); } };
  const copy = async (artifact = open) => { if (!artifact) return; try { await navigator.clipboard.writeText(artifact.content); onNotify(`Version ${artifact.versionNumber} copied as Markdown.`); } catch { onNotify("Clipboard access was unavailable. Download the Markdown file instead."); } };
  const download = (artifact = open) => { if (!artifact) return; const url = URL.createObjectURL(new Blob([artifact.content], { type: "text/markdown;charset=utf-8" })); const anchor = document.createElement("a"); anchor.href = url; anchor.download = `${base.id}-${artifact.kind}-v${artifact.versionNumber}.md`; anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); onNotify(`Downloaded version ${artifact.versionNumber} as Markdown.`); };
  const exportFile = async (make: (artifact: NonNullable<typeof open>) => Promise<string>) => { if (!open) return; try { onNotify(`Downloaded ${await make(open)}.`); } catch (reason) { setGeneralError(`The file wasn’t made. ${messageOf(reason)}`); } };
  const exportPdf = async () => { if (!open) return; try { const finish = await prepareForPrint(); try { if ("__TAURI_INTERNALS__" in window && /Mac/i.test(navigator.userAgent)) await invoke("print_output"); else window.print(); } finally { finish(); } onNotify("In the print window, choose PDF to save it."); } catch (reason) { setGeneralError(messageOf(reason)); } };
  const cite = (citation: ConversationCitation, index: number) => setEvidence({ citation, index });
  const runRebuild = (extra: Partial<BuildOutputRequest>, instruction: string) => { if (open && latest) void rebuild(requestFor({ brief: open.brief, supersedesArtifactId: open.id, ...extra }), instruction); };
  /** A row of a change list: show that version and take the preview to the part. */
  const openPart = (artifactId: string | null, position: number | null) => {
    const item = artifactId ? history.find((entry) => entry.id === artifactId) : null;
    const go = () => { if (position !== null) setJump({ index: position - 1, nonce: ++jumps.current }); };
    if (item && open?.id !== item.id) void openVersion(item).then((artifact) => { if (artifact) go(); });
    else go();
  };
  const editBrief = async (change: EditArtifactBriefRequest) => {
    if (!workspaceId || !detail) return;
    setBriefError(null);
    try { store.setBrief(active.id, await knowledgeApi.editArtifactBrief(base.id, active.id, change, workspaceId)); }
    catch (reason) { setBriefError(`The brief wasn’t changed. ${messageOf(reason)}`); }
  };
  const artifactMenu = (item: ArtifactSummary) => <PopMenu label={`Actions for ${item.title}`} items={[
    { label: "Open", icon: FileDown, disabled: editing || saving, onSelect: () => void openVersion(item) },
    { label: "Versions", icon: History, onSelect: () => { void openVersion(item).then((artifact) => { if (artifact) setVersionsOpen(true); }); } },
    { label: "Edit", icon: Pencil, disabled: busy || item.origin === "legacy", onSelect: () => { void openVersion(item).then((artifact) => { if (artifact) setEditing(true); }); } },
    { label: "Copy Markdown", icon: Copy, divider: true, onSelect: () => { void openVersion(item).then((artifact) => { if (artifact) void copy(artifact); }); } },
    { label: "Download Markdown", icon: Download, onSelect: () => { void openVersion(item).then((artifact) => { if (artifact) download(artifact); }); } },
  ]} />;

  const settings = <section className="aw-inline-settings artifact-generation-settings" aria-label="Artifact settings">
    {web.available && <label className="aw-web-option">Search the web too<input type="checkbox" role="switch" aria-label="Search the web too" checked={web.preferred} disabled={busy} onChange={(event) => web.setEnabled(event.target.checked)} /></label>}
    <small>{scopeText}</small>
  </section>;

  const stored = job ? messages.some((message) => message.clientRequestId === job.turnId) : false;
  const chat = <>
    <header className="aw-pane-head"><div><h1>Conversation</h1><p>{active.title}</p></div><span className="aw-live-dot" /></header>
    <div ref={messagesPanel} className="aw-message-scroll" onScroll={() => { const panel = messagesPanel.current; if (panel) followingMessages.current = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 60; }}><div ref={messagesContent}>
      {state.problem && <p role="alert" className="outputs-hint">{state.problem}</p>}
      {messages.length === 0 && !activeJob && <div className="aw-welcome"><BrandMark size={35} /><h2>{open ? "Refine it here." : "What would you like to make?"}</h2><p>{open ? "Ask for a change, ask about its sources, or start another report or slide deck in this conversation." : "Choose sources, describe a report or presentation, then refine it here."}</p><div className="artifact-create-shortcuts"><button type="button" onClick={() => { store.update(active.id, { draft: "Create a report from the selected material." }); setFocusRequest((value) => value + 1); }}>Report</button><button type="button" onClick={() => { store.update(active.id, { draft: "Create a slide deck from the selected material." }); setFocusRequest((value) => value + 1); }}>Slides</button></div></div>}
      {noModel && <div className="outputs-setup-note"><p>Artifacts need a model. Set one up under Settings → Models.</p><button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => openSettings("models")}>Open Settings<ArrowRight size={13} /></button></div>}
      {menuUnavailable && <p role="alert">Models couldn’t be loaded. Check the connection or open Settings.</p>}
      {!noModel && emptyScope && !open && <div className="outputs-setup-note"><p>Nothing to build from yet</p><button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={onAdd}>Add sources<ArrowRight size={13} /></button></div>}
      {open && !latest && <p className="outputs-hint">This is an earlier version. <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => head && void openVersion(head)}>Open latest version {head?.versionNumber}</button></p>}
      {open?.origin === "legacy" && <p className="outputs-hint">This version came from the old builder. Rebuild it to edit it.</p>}
      <ArtifactMessageList messages={messages} pending={pending} history={history} citations={open?.citations ?? []} kind={work?.kind ?? open?.kind ?? null} busy={building} earlier={active.id === EARLIER_WORK}
        onOption={(text) => void sendTurn({ kind: "answer", text })} onStart={() => void sendTurn({ kind: "start" })} onContinue={() => void sendTurn({ kind: "continue" })} onRetry={retry}
        onOpenPart={openPart} onOpenVersion={(item) => void openVersion(item)} onCite={(index) => { const citation = open?.citations[index]; if (citation) cite(citation, index); }} />
      {activeJob && job && job.kind !== "rebuild" && job.text && !stored && <div className="aw-message is-user"><div><small>You</small><p>{job.text}</p></div></div>}
      {activeLive && <LiveTurn live={activeLive} kind={open?.kind ?? work?.kind ?? "report"} stopping={stopping} onStop={() => void stop()} />}
      {building && !activeJob && job && <p className="outputs-hint">Working in <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => { const conversation = store.get(job.conversationId); if (conversation) void chooseConversation(conversation); }}>{store.get(job.conversationId)?.title ?? "another conversation"}</button></p>}
      {generalError && <div className="gx-banner is-error outputs-error" role="alert"><CircleAlert size={16} /><span><strong>Could not complete the request</strong><small>{generalError}</small></span></div>}
    </div></div>
    <ArtifactComposer key={active.id} draft={active.draft} onDraft={(draft) => store.update(active.id, { draft })} aboutLabel={aboutLabel} onClearAbout={() => setPointing(null)} scopeText={scopeText} menu={menu} choice={choice} onChoice={(next) => { store.update(active.id, { choice: next }); setDeviceChoice(next); }} disabled={sendDisabled} busy={activeJob} stopping={stopping} answering={pending !== null} onSend={send} onStop={() => void stop()} settings={settings} settingsOpen={settingsOpen} onSettings={() => setSettingsOpen(!settingsOpen)} focusRequest={focusRequest} allowVoiceInput={allowVoiceInput} />
  </>;
  const preview = <>
    <div className="aw-preview-toolbar aw-main-preview-toolbar"><strong>Preview</strong><select aria-label="Preview artifact" value={open?.lineageId ?? ""} disabled={loading || editing || saving || opening} onChange={(event) => { const item = activeGroups.find((items) => items[0]?.lineageId === event.target.value)?.[0]; if (item) void openVersion(item); }}>
      {!open && <option value="">{loading ? "Loading…" : "Choose an artifact"}</option>}{activeGroups.map((items) => <option key={items[0]!.lineageId} value={items[0]!.lineageId}>{items[0]!.title} · {kindLabel(items[0]!.kind)}</option>)}</select>
      {open && <select aria-label="Artifact versions" value={open.id} disabled={editing || saving || opening} onChange={(event) => { const item = versions.find((entry) => entry.id === event.target.value); if (item) void openVersion(item); }}>{versions.map((item) => <option key={item.id} value={item.id}>v{item.versionNumber}</option>)}</select>}
      <button type="button" className={`aw-toolbar-details${controlsExpanded ? " is-selected" : ""}`} aria-label={controlsExpanded ? "Hide preview controls" : "Show preview controls"} aria-expanded={controlsExpanded} onClick={() => setControlsExpanded(!controlsExpanded)}><PanelsTopLeft size={14} /><span>Details</span></button>
      {open?.outline.length ? <button type="button" className="gx-icon-button" aria-label="Show outline" title="Outline" onClick={() => { setControlsExpanded(true); requestAnimationFrame(() => outlinePanel.current?.scrollIntoView({ block: "nearest" })); }}><ListChecks size={15} /></button> : null}
      {open && <PopMenu label="Export artifact" triggerLabel="Export artifact" triggerClass="gx-icon-button" trigger={<Download size={15} />} items={[{ label: "Export PDF", icon: FileDown, disabled: editing, onSelect: () => void exportPdf() }, { label: "Copy Markdown", icon: Copy, onSelect: () => void copy() }, { label: "Download .md", icon: Download, onSelect: () => download() }, ...(open.kind === "slides" && isDeckV2(open.content) ? [{ label: "Download PowerPoint", icon: Presentation, disabled: editing, onSelect: () => void exportFile(downloadPptx) }] : []), ...(open.kind === "report" && isReportV2(open.content) ? [{ label: "Download Word", icon: FileDown, disabled: editing, onSelect: () => void exportFile(downloadDocx) }] : [])]} />}
      <button type="button" className="gx-icon-button" aria-label={focused ? "Exit preview focus" : "Expand preview"} aria-pressed={focused} onClick={() => focusPreview(!focused)}>{focused ? <Minimize2 size={15} /> : <Maximize2 size={15} />}</button>
    </div>
    <div className="aw-preview-controls" id="artifact-preview-controls" hidden={!controlsExpanded} style={{ height: "var(--aw-controls-height)" }}>
      <header className="aw-controls-heading"><strong>Artifacts</strong><span>{activeGroups.length} from this conversation</span><button type="button" className="gx-icon-button" aria-label="Collapse details area" onClick={() => setControlsExpanded(false)}><ChevronDown size={14} /></button></header>
      <aside className="artifact-saved-artifacts" aria-label="Saved artifacts"><ul className="aw-artifact-tray">{activeGroups.map((items) => { const item = items[0]!; return <li className="artifact-tray-entry" key={item.lineageId}><button type="button" className={`aw-artifact-card${open?.lineageId === item.lineageId ? " is-active" : ""}`} aria-current={open?.lineageId === item.lineageId ? "page" : undefined} aria-label={`Preview ${item.title}`} disabled={editing || saving || opening} onClick={() => void openVersion(item)}><span className="aw-file-icon">{item.kind === "slides" ? <Presentation size={19} /> : <FileDown size={19} />}</span><strong>{item.title}</strong><small>{kindLabel(item.kind)}<span>v{item.versionNumber}</span></small></button>{artifactMenu(item)}</li>; })}</ul>{activeGroups.length === 0 && <p className="outputs-hint">No artifacts yet.</p>}</aside>
      <ArtifactAgentPlan live={activeLive} latest={lastReply} stopping={stopping} onStop={() => void stop()} />
      <div className="aw-controls-brief"><ArtifactBriefPanel state={briefState} disabled={building} error={briefError} onEdit={(change) => void editBrief(change)} /></div>
      {open && <div ref={outlinePanel} className="aw-controls-outline"><ApproachPanel artifact={open} busy={busy || !latest || !changeable} onRebuild={(approach) => runRebuild({ approach }, "Rebuild with the updated approach")} onCite={(index) => { const citation = open.citations[index]; if (citation) cite(citation, index); }} /><OutlineEditor key={open.id} review={open.provenance.skill?.outlineReview} outline={open.outline} label={open.kind === "slides" ? "slides" : "sections"} max={open.kind === "slides" ? 14 : 8} busy={busy || !latest || !changeable} onRebuild={(outline) => runRebuild({ outline }, "Rebuild with the updated outline")} /></div>}
      {versionsOpen && <section className="artifacts-version-list" aria-label="Artifact version history">{versions.map((item) => <button type="button" key={item.id} disabled={item.id === open?.id} className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => void openVersion(item)}>v{item.versionNumber} · {item.title}</button>)}</section>}
    </div>
    {controlsExpanded ? <PreviewHeightHandle value={controlsHeight} onChange={setControlsHeight} /> : activeLive && <button type="button" className="aw-compact-agent" aria-label="Show agent plan" onClick={() => setControlsExpanded(true)}><LoaderCircle className="spin" size={11} /><span>{outputActivity(activeLive)}</span><ChevronRight size={12} /></button>}
    {open ? <div className={`aw-preview-scroll${open.kind === "slides" ? " is-slides" : ""}`}>
      {workspaceId && <SlideReviewPanel key={open.id} artifact={open} baseId={base.id} workspaceId={workspaceId} disabled={busy || !latest || !changeable || noModel} onReview={reviewSlides} onFollowUp={send} />}
      <div className="artifact-reading-actions">{changeable && latest && !editing && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={busy} onClick={() => setEditing(true)}><Pencil size={12} />Edit</button>}{open.sections.some((section) => !section.checked) && changeable && latest && !editing && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={busy || noModel} onClick={() => void checkAgain()}><ShieldCheck size={12} />Check again</button>}</div>
      {editing ? <EditPane key={open.id} artifact={open} saving={saving} onSave={(content) => void saveEdit(content)} onCancel={() => { setEditing(false); setGeneralError(null); }} onCite={cite} /> : open.kind === "slides" ? <div className="aw-deck"><h2 className="outputs-title">{open.title}</h2><LazySlidesView artifact={open} onCite={cite} onSlide={setOnScreen} onAsk={changeable && latest ? (index) => setPointing({ section: index }) : undefined} goTo={jump} changed={changed} onAccent={changeable ? setAccent : undefined} onTheme={changeable ? setTheme : undefined} onDeckTheme={changeable ? setDeckTheme : undefined} onDeckStyle={changeable ? setDeckStyle : undefined} onNotice={onNotify} accentDisabled={busy || !latest} /></div> : <article className={`aw-paper outputs-document is-saved${isReportV2(open.content) ? " is-studio" : ""}`} aria-label="Document preview"><h2 className="outputs-title">{open.title}</h2><p className="outputs-doc-meta">{[describe(open), new Date(open.createdAt).toLocaleDateString(), open.modelLabel ?? (open.origin === "edit" ? "typed by hand" : "")].filter(Boolean).join(" · ")}</p><ReportView artifact={open} onCite={cite} onAsk={changeable && latest ? (section, quote) => setPointing({ section, quote }) : undefined} goTo={jump} changed={changed} /><footer className="outputs-doc-foot"><ShieldCheck size={14} />{open.origin === "legacy" ? `From ${open.unitCount} accepted knowledge units` : `Built from ${describeScope(open.inputs)}`} · seal {open.manifestHash.slice(0, 12)}…</footer></article>}
    </div> : <div className="aw-preview-empty"><ListChecks size={30} /><h3>Your artifacts live here.</h3><p>Documents and slide decks from this conversation appear here. Create them through the conversation.</p></div>}
  </>;
  return <div className={`outputs-page page-enter${open?.kind === "slides" ? " is-deck" : ""}`}>
    <ArtifactWorkspaceLayout key={`${workspaceId}:${base.id}`} storageKey={layoutKey} focused={focused} onFocus={focusPreview} controlsHeight={controlsHeight}
      sources={<MaterialSelectionPanel key={base.id} baseId={base.id} scope={active.scope} onChange={(next: OutputScope) => store.update(active.id, { scope: next })} onLoaded={setLibrary} disabled={activeJob || editing || saving || !workspaceId} onAdd={onAdd} />}
      conversations={<section className="aw-conversations" aria-label="Conversations"><header><h2>Conversations</h2><button type="button" className="gx-icon-button" aria-label="New conversation" disabled={loading || editing || saving || !workspaceId} onClick={newConversation}><Plus size={16} /></button></header><div className="aw-conversation-list">{state.conversations.map((conversation) => <button type="button" key={conversation.id} title={conversation.title} disabled={editing || saving} className={conversation.id === active.id ? "is-active" : ""} aria-current={conversation.id === active.id ? "page" : undefined} onClick={() => void chooseConversation(conversation)}>{building && job?.conversationId === conversation.id ? <LoaderCircle size={15} className="spin" /> : <MessageSquare size={15} />}<span><strong>{conversation.title}</strong><small>{conversation.waiting ? "Waiting for your answer" : conversation.artifactCount ? `${conversation.artifactCount} ${conversation.artifactCount === 1 ? "artifact" : "artifacts"}` : "Continue here"}</small></span></button>)}</div></section>}
      chat={chat} preview={preview} />
    {open && <PrintRoot artifact={open} />}{evidence && <EvidencePanel citation={evidence.citation} index={evidence.index} onClose={() => setEvidence(null)} onOpenSource={onOpenSource ? (id) => { setEvidence(null); onOpenSource(id, [id]); } : undefined} />}
  </div>;
}
