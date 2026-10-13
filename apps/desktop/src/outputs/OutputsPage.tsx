import type { Artifact, ArtifactAudience, ArtifactSummary, BuildOutputRequest, ConversationCitation, OutputKind, OutputScope, OutputStyle } from "@gunther/contracts";
import { outputStyles } from "@gunther/contracts";
import { invoke } from "@tauri-apps/api/core";
import { ArrowRight, ChevronDown, ChevronRight, CircleAlert, Copy, Download, FileDown, History, ListChecks, LoaderCircle, Maximize2, MessageSquare, Minimize2, PanelsTopLeft, Pencil, Plus, Presentation, ShieldCheck, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { knowledgeApi, OutputStoppedError, type OutputEvent } from "../api";
import type { KnowledgeBase } from "../atlas";
import { EvidencePanel } from "../components/evidence/EvidencePanel";
import { openSettings } from "../components/SettingsLayout";
import { ArtifactMenu as PopMenu } from "./ArtifactMenu";
import { BrandMark } from "../design/BrandMark";
import { useModelMenu, useDeviceChoice, usableChoice, useWebSearch } from "../models/askModel";
import { persistentStorage } from "../persistentStorage";
import { ApproachPanel } from "./ApproachPanel";
import { ArtifactAgentPlan, outputActivity } from "./ArtifactAgentPlan";
import { ArtifactComposer, type ArtifactActionMode } from "./ArtifactComposer";
import { ArtifactWorkspaceLayout, PreviewHeightHandle, usePreviewControls } from "./ArtifactWorkspaceLayout";
import { artifactRequestId, libraryScope, useArtifactConversations, type ArtifactConversation, type ArtifactRequest, type ArtifactTurn } from "./artifactConversations";
import { EditPane } from "./EditPane";
import { LazySlidesView } from "./LazySlides";
import { LiveSections } from "./LiveSections";
import { useLiveOutput } from "./liveOutput";
import { MaterialSelectionPanel } from "./MaterialSelectionPanel";
import { OutlineEditor } from "./OutlineEditor";
import { PrintRoot } from "./PrintRoot";
import { ReportView } from "./ReportView";
import { describeScope, type ScopeCounts } from "./ScopePicker";
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
const audiences: ArtifactAudience[] = ["scientist", "student", "collaborator"];
const describe = (item: ArtifactSummary) => [kindLabel(item.kind), item.style ? styles[item.style as OutputStyle] ?? item.style.replaceAll("_", " ") : "", item.audience[0]!.toUpperCase() + item.audience.slice(1), item.origin === "edit" ? "Edited" : item.origin === "revise" ? "Revised" : ""].filter(Boolean).join(" · ");
const messageOf = (reason: unknown) => reason instanceof Error ? reason.message : typeof reason === "string" ? reason : "The request could not be completed.";
interface JobContext { conversationId: string; turnId: string; request: ArtifactRequest | null }

export function OutputsPage({ base, workspaceId, onNotify, onAdd, onOpenSource, fromDiscussion, onFromDiscussion, focused: controlledFocus, onFocus, allowVoiceInput = true }: OutputsPageProps) {
  const { store, state, active } = useArtifactConversations(workspaceId, base.id);
  const [history, setHistory] = useState<ArtifactSummary[]>([]);
  const [open, setOpen] = useState<Artifact | null>(null);
  const [loading, setLoading] = useState(true);
  const [opening, setOpening] = useState(false);
  const [generalError, setGeneralError] = useState<string | null>(null);
  const [library, setLibrary] = useState<ScopeCounts | null>(null);
  const [mode, setMode] = useState<ArtifactActionMode>("create");
  const [kind, setKind] = useState<OutputKind>("report");
  const [style, setStyle] = useState<OutputStyle>("auto");
  const [audience, setAudience] = useState<ArtifactAudience>("scientist");
  const [deckUse, setDeckUse] = useState<"auto" | "talk" | "read">("auto");
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
  const layoutKey = `gunther:artifact-layout:${workspaceId ?? "unverified"}:${base.id}`;
  const [controlsExpanded, setControlsExpanded] = usePreviewControls(`${layoutKey}:controls`);
  const [controlsHeight, setControlsHeight] = useState(() => { try { const value = Number(persistentStorage.getItem(`${layoutKey}:height`)); return value >= 120 && value <= 480 ? value : 320; } catch { return 320; } });
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
  useEffect(() => { followingMessages.current = true; if (messagesPanel.current) messagesPanel.current.scrollTop = messagesPanel.current.scrollHeight; }, [active.id]);
  useEffect(() => {
    const panel = messagesPanel.current; const content = messagesContent.current;
    if (!panel || !content || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => { if (followingMessages.current) panel.scrollTop = panel.scrollHeight; }); observer.observe(content); return () => observer.disconnect();
  }, []);
  useEffect(() => { if (followingMessages.current && messagesPanel.current) messagesPanel.current.scrollTop = messagesPanel.current.scrollHeight; }, [active.turns]);
  const groups = useMemo(() => {
    const byLineage = new Map<string, ArtifactSummary[]>();
    for (const item of history) byLineage.set(item.lineageId, [...(byLineage.get(item.lineageId) ?? []), item]);
    return [...byLineage.values()].map((items) => items.sort((a, b) => b.versionNumber - a.versionNumber));
  }, [history]);
  const activeGroups = groups.filter((items) => active.lineageIds.includes(items[0]!.lineageId));
  const versions = open ? groups.find((items) => items[0]?.lineageId === open.lineageId) ?? [open] : [];
  const head = versions[0] ?? null;
  const latest = !open || !head || head.id === open.id;
  const changeable = open !== null && open.origin !== "legacy";
  const latestTurn = active.turns.at(-1) ?? null;
  const activeLive = activeJob ? live.live : null;
  const error = latestTurn?.status === "failed" ? latestTurn.error : generalError;
  const scope = active.scope;
  const scopeText = scope.mode === "library" ? library ? describeScope(library) : "Reading the library…" : describeScope({ sources: scope.sourceIds.length, units: scope.unitIds.length, sessions: scope.sessionIds.length });
  const emptyScope = scope.mode === "library" ? library !== null && library.sources + library.units === 0 : scope.sourceIds.length + scope.unitIds.length + scope.sessionIds.length === 0;
  const sendDisabled = loading || opening || !workspaceId || menu === null || noModel || (mode !== "modify" && (library === null || emptyScope)) || (mode !== "create" && (!open || !latest || (mode === "modify" && !changeable))) || (building && !activeJob) || editing || saving || checking;

  const show = useCallback((artifact: Artifact) => {
    setOpen(artifact); setKind(artifact.kind); setAudience(artifact.audience);
    if (artifact.style && outputStyles.includes(artifact.style as OutputStyle)) setStyle(artifact.style as OutputStyle);
    setMode(artifact.origin === "legacy" ? "rebuild" : "modify");
    setEditing(false); setSettingsOpen(false); setVersionsOpen(false); setEvidence(null); setGeneralError(null);
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
  const chooseConversation = async (conversation: ArtifactConversation) => {
    if (editing || saving) return;
    openingToken.current += 1; setOpen(null); setOpening(false); setGeneralError(null); setEditing(false); setSettingsOpen(false); setMode("create");
    store.select(conversation.id);
    const selected = history.find((item) => item.id === conversation.selectedArtifactId) ?? groups.find((items) => conversation.lineageIds.includes(items[0]!.lineageId))?.[0];
    if (selected) await openVersion(selected);
  };
  const newConversation = () => {
    openingToken.current += 1; setOpening(false); setOpen(null); setGeneralError(null); setEditing(false); setMode("create"); setStyle("auto"); setSettingsOpen(false);
    store.create(active.scope, choice); setFocusRequest((value) => value + 1);
  };

  useEffect(() => {
    let alive = true;
    controller.current?.abort(); followController.current?.abort(); jobRef.current = null; setJob(null); live.stop();
    openingToken.current += 1; setLoading(true); setOpen(null); setHistory([]); setLibrary(null); setGeneralError(null); setEditing(false); setMode("create");
    if (!workspaceId) { setGeneralError("The local workspace identity has not been verified yet."); setLoading(false); return () => { alive = false; }; }
    void (async () => {
      try {
        const items = await knowledgeApi.artifacts(base.id, workspaceId);
        if (!alive) return;
        setHistory(items); const conversation = store.reconcile(items);
        const item = items.find((entry) => entry.id === conversation.selectedArtifactId) ?? items.find((entry) => conversation.lineageIds.includes(entry.lineageId));
        if (item) { const token = ++openingToken.current; const artifact = await knowledgeApi.artifact(base.id, item.id, workspaceId); if (alive && token === openingToken.current && store.active().id === conversation.id) show(artifact); }
      } catch (reason) { if (alive) setGeneralError(messageOf(reason)); }
      finally { if (alive) setLoading(false); }
    })();
    return () => { alive = false; controller.current?.abort(); followController.current?.abort(); openingToken.current += 1; };
  }, [base.id, workspaceId, store, show]);

  const finish = async (artifact: Artifact, context: JobContext | null) => {
    if (context) store.finish(context.conversationId, context.turnId, artifact);
    let items: ArtifactSummary[];
    try { items = await knowledgeApi.artifacts(base.id, workspaceId!); } catch { items = history; }
    const saved = new Map(history.map((item) => [item.id, item]));
    items.forEach((item) => saved.set(item.id, item)); saved.set(artifact.id, artifact);
    items = [...saved.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    setHistory(items); store.reconcile(items);
    if (!context || store.active().id === (artifact.conversationId || context.conversationId)) show(artifact);
    onNotify(`${kindLabel(artifact.kind)} version ${artifact.versionNumber} saved.`);
  };
  useEffect(() => {
    if (!workspaceId) return;
    const abort = new AbortController(); followController.current = abort;
    let context: JobContext | null = null;
    const heard = (event: OutputEvent) => {
      if (abort.signal.aborted) return;
      if (!context) {
        const text = event.type === "resumed" ? event.question : "Artifact generation";
      const found = store.snapshot().conversations.flatMap((conversation) => conversation.turns.map((turn) => ({ conversation, turn }))).reverse().find(({ turn }) => (turn.status === "interrupted" || turn.status === "running") && (turn.instruction === text || (turn.request?.operation === "build" ? turn.request.payload.brief : turn.request?.payload.instruction) === text));
        const conversation = found?.conversation ?? store.create();
        const turn = found?.turn ?? { id: artifactRequestId(), instruction: text, request: null, status: "running" as const, createdAt: new Date().toISOString(), result: null, error: null };
        store.begin(conversation.id, { ...turn, status: "running" });
        context = { conversationId: conversation.id, turnId: turn.id, request: turn.request };
        jobRef.current = context; setJob(context);
      }
      live.hear(event);
    };
    void knowledgeApi.followOutputBuild(base.id, workspaceId, heard, abort.signal).then(async (artifact) => { if (artifact && !abort.signal.aborted) await finish(artifact, context); }).catch((reason) => {
      if (abort.signal.aborted) return;
      if (context) store.settle(context.conversationId, context.turnId, reason instanceof OutputStoppedError ? "stopped" : "failed", null, messageOf(reason));
      else setGeneralError(messageOf(reason));
    }).finally(() => { if (!abort.signal.aborted && context && jobRef.current === context) { live.stop(); jobRef.current = null; setJob(null); setStopping(false); } });
    return () => abort.abort();
  }, [base.id, workspaceId, store]);

  useEffect(() => {
    if (!fromDiscussion || loading) return;
    const conversation = store.create({ mode: "selection", sourceIds: [], unitIds: [], sessionIds: [fromDiscussion] }, choice);
    void chooseConversation(conversation); setFocusRequest((value) => value + 1); onFromDiscussion?.();
  }, [fromDiscussion, loading]);

  const execute = async (request: ArtifactRequest, conversationId = active.id, instruction?: string) => {
    if (!workspaceId || building) return;
    const conversation = store.get(conversationId);
    if (!conversation) return;
    followController.current?.abort();
    const abort = new AbortController(); controller.current = abort;
    const text = instruction || (request.operation === "build" ? request.payload.brief : request.payload.instruction) || "Rebuild with the updated outline";
    const context: JobContext = { conversationId, turnId: request.payload.clientRequestId, request };
    jobRef.current = context; setJob(context); setGeneralError(null); setStopping(false); setSettingsOpen(false);
    store.begin(conversationId, { id: context.turnId, instruction: text, request, status: "running", createdAt: new Date().toISOString(), result: null, error: null });
    live.start();
    try {
      const artifact = request.operation === "build" ? await knowledgeApi.buildOutputStream(base.id, request.payload, workspaceId, live.hear, abort.signal) : await knowledgeApi.reviseOutputStream(base.id, request.artifactId, request.payload, workspaceId, live.hear, abort.signal);
      if (abort.signal.aborted) return;
      await finish(artifact, context);
    } catch (reason) {
      if (abort.signal.aborted) return;
      const stopped = reason instanceof OutputStoppedError;
      store.settle(conversationId, context.turnId, stopped ? "stopped" : "failed", null, messageOf(reason));
      onNotify(stopped ? "Stopped. Nothing was saved." : "The artifact wasn’t saved.");
    } finally { if (!abort.signal.aborted && jobRef.current === context) { live.stop(); jobRef.current = null; setJob(null); setStopping(false); } }
  };
  const requestFor = (extra: Partial<BuildOutputRequest> = {}): BuildOutputRequest => ({ clientRequestId: artifactRequestId(), kind, audience, ...(kind === "report" ? { style } : deckUse !== "auto" ? { use: deckUse } : {}), scope: active.scope, conversationId: active.id, conversationTitle: active.title, ...(choice.model ? { model: choice.model, effort: choice.effort } : {}), ...(web.enabled ? { web: true } : {}), ...extra });
  const send = (text: string, sectionIndex: number | null) => {
    const title = active.untitled ? text.slice(0, 160) : active.title;
    if (mode === "modify" && open) void execute({ operation: "revise", artifactId: open.id, payload: { clientRequestId: artifactRequestId(), instruction: text, ...(sectionIndex === null ? {} : { sectionIndex }), conversationId: active.id, conversationTitle: title, ...(choice.model ? { model: choice.model, effort: choice.effort } : {}) } });
    else void execute({ operation: "build", payload: requestFor({ brief: text, conversationTitle: title, ...(mode === "rebuild" && open ? { supersedesArtifactId: open.id } : {}) }) });
  };
  const stop = async () => { if (!workspaceId) return; setStopping(true); try { await knowledgeApi.stopOutputBuild(base.id, workspaceId); } catch (reason) { setStopping(false); setGeneralError(messageOf(reason)); } };
  const saveEdit = async (content: string) => {
    if (!open || !workspaceId) return; setSaving(true); setGeneralError(null);
    try { const artifact = await knowledgeApi.editOutput(base.id, open.id, { clientRequestId: artifactRequestId(), content }, workspaceId); await finish(artifact, null); onNotify(`Saved as version ${artifact.versionNumber}. What you typed has not been re-checked yet.`); }
    catch (reason) { setGeneralError(`The edit wasn’t saved. ${messageOf(reason)}`); } finally { setSaving(false); }
  };
  const checkAgain = async () => { if (!open || !workspaceId) return; setChecking(true); try { setOpen(await knowledgeApi.checkOutput(base.id, open.id, workspaceId)); onNotify("Checked against the sources."); } catch (reason) { setGeneralError(messageOf(reason)); } finally { setChecking(false); } };
  const copy = async (artifact = open) => { if (!artifact) return; try { await navigator.clipboard.writeText(artifact.content); onNotify(`Version ${artifact.versionNumber} copied as Markdown.`); } catch { onNotify("Clipboard access was unavailable. Download the Markdown file instead."); } };
  const download = (artifact = open) => { if (!artifact) return; const url = URL.createObjectURL(new Blob([artifact.content], { type: "text/markdown;charset=utf-8" })); const anchor = document.createElement("a"); anchor.href = url; anchor.download = `${base.id}-${artifact.kind}-v${artifact.versionNumber}.md`; anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); onNotify(`Downloaded version ${artifact.versionNumber} as Markdown.`); };
  const exportPdf = async () => { if (!open) return; try { await document.fonts?.ready; if ("__TAURI_INTERNALS__" in window && /Mac/i.test(navigator.userAgent)) await invoke("print_output"); else window.print(); onNotify("In the print window, choose PDF to save it."); } catch (reason) { setGeneralError(messageOf(reason)); } };
  const cite = (citation: ConversationCitation, index: number) => setEvidence({ citation, index });
  const configure = (nextMode: ArtifactActionMode) => { setMode(nextMode); setFocusRequest((value) => value + 1); };
  const runRebuild = (extra: Partial<BuildOutputRequest>, instruction: string) => { if (open && latest) void execute({ operation: "build", payload: requestFor({ brief: open.brief, supersedesArtifactId: open.id, ...extra }) }, active.id, instruction); };
  const artifactMenu = (item: ArtifactSummary) => <PopMenu label={`Actions for ${item.title}`} items={[
    { label: "Open", icon: FileDown, disabled: editing || saving, onSelect: () => void openVersion(item) },
    { label: "Versions", icon: History, onSelect: () => { void openVersion(item).then((artifact) => { if (artifact) setVersionsOpen(true); }); } },
    { label: "Edit", icon: Pencil, disabled: busy || item.origin === "legacy", onSelect: () => { void openVersion(item).then((artifact) => { if (artifact) setEditing(true); }); } },
    { label: "Revise", icon: Sparkles, disabled: busy || noModel || item.origin === "legacy", onSelect: () => { void openVersion(item).then((artifact) => { if (artifact) configure("modify"); }); } },
    { label: "Rebuild", icon: Sparkles, disabled: busy, onSelect: () => { void openVersion(item).then((artifact) => { if (artifact) { configure("rebuild"); setSettingsOpen(true); } }); } },
    { label: "Copy Markdown", icon: Copy, divider: true, onSelect: () => { void openVersion(item).then((artifact) => { if (artifact) void copy(artifact); }); } },
    { label: "Download Markdown", icon: Download, onSelect: () => { void openVersion(item).then((artifact) => { if (artifact) download(artifact); }); } },
  ]} />;

  const settings = <section className="aw-inline-settings artifact-generation-settings" aria-label="Artifact settings">
    {mode !== "modify" && <><label>Audience<select aria-label="Audience" value={audience} disabled={busy} onChange={(event) => setAudience(event.target.value as ArtifactAudience)}>{audiences.map((item) => <option key={item} value={item}>{item[0]!.toUpperCase() + item.slice(1)}</option>)}</select></label>{kind === "report" ? <label>Style<select aria-label="Style" value={style} disabled={busy} onChange={(event) => setStyle(event.target.value as OutputStyle)}>{outputStyles.map((item) => <option key={item} value={item}>{styles[item]}</option>)}</select></label> : <label>For<select aria-label="Deck use" value={deckUse} disabled={busy} onChange={(event) => setDeckUse(event.target.value as typeof deckUse)}><option value="auto">Auto</option><option value="talk">A talk</option><option value="read">Reading</option></select></label>}</>}
    {web.available && <label className="aw-web-option">Search the web too<input type="checkbox" role="switch" aria-label="Search the web too" checked={web.preferred} disabled={busy} onChange={(event) => web.setEnabled(event.target.checked)} /></label>}
    <small>{mode === "modify" ? "The selected artifact keeps its original sources." : scopeText}</small>
  </section>;

  const chat = <>
    <header className="aw-pane-head"><div><h1>Conversation</h1><p>{active.title}</p></div><span className="aw-live-dot" /></header>
    <div ref={messagesPanel} className="aw-message-scroll" onScroll={() => { const panel = messagesPanel.current; if (panel) followingMessages.current = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 60; }}><div ref={messagesContent}>
      {state.problem && <p role="alert" className="outputs-hint">{state.problem}</p>}
      {active.turns.length === 0 && <div className="aw-welcome"><BrandMark size={35} /><h2>{open ? "Refine it here." : "What would you like to make?"}</h2><p>{open ? "Describe a change, or create another report or slide deck in this conversation." : "Choose sources, describe a report or presentation, then refine it here."}</p><div className="artifact-create-shortcuts"><button type="button" onClick={() => { setKind("report"); configure("create"); store.update(active.id, { draft: "Create a report from the selected material." }); }}>Report</button><button type="button" onClick={() => { setKind("slides"); configure("create"); store.update(active.id, { draft: "Create a slide deck from the selected material." }); }}>Slides</button></div></div>}
      {noModel && <div className="outputs-setup-note"><p>Artifacts need a model. Set one up under Settings → Models.</p><button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => openSettings("models")}>Open Settings<ArrowRight size={13} /></button></div>}
      {menuUnavailable && <p role="alert">Models couldn’t be loaded. Check the connection or open Settings.</p>}
      {!noModel && emptyScope && mode !== "modify" && <div className="outputs-setup-note"><p>Nothing to build from yet</p><button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={onAdd}>Add sources<ArrowRight size={13} /></button></div>}
      {open && !latest && <p className="outputs-hint">This is an earlier version. <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => head && void openVersion(head)}>Open latest version {head?.versionNumber}</button></p>}
      {open?.origin === "legacy" && <p className="outputs-hint">This version came from the old builder. Rebuild it to edit it.</p>}
      <div className="aw-messages" role="log" aria-label="Artifact messages">{active.turns.map((turn) => <div key={turn.id} className="artifact-conversation-turn"><div className="aw-message is-user"><div><small>You</small><p>{turn.instruction}</p></div></div>{turn.status !== "running" && <div className="aw-message is-assistant"><span className="aw-agent-mark"><BrandMark size={17} /></span><div><p>{turn.status === "saved" ? `${turn.result ? kindLabel(turn.result.kind) : "Artifact"} saved as version ${turn.result?.versionNumber ?? ""}.` : turn.status === "stopped" ? "Stopped. Nothing was saved." : turn.status === "interrupted" ? "This request was interrupted. Checking for its saved result." : "The artifact wasn’t saved."}</p>{turn.result && <button type="button" className="artifact-result-link" onClick={() => void openVersion(turn.result!)}>{turn.result.title}<ChevronRight size={12} /></button>}{(turn.status === "failed" || turn.status === "interrupted") && turn.request && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={building} onClick={() => void execute(turn.request!, active.id, turn.instruction)}>Retry</button>}</div></div>}</div>)}</div>
      {activeLive && <><div className="aw-message is-assistant" role="status"><span className="aw-agent-mark"><BrandMark size={17} busy /></span><p>{outputActivity(activeLive)}</p></div><details className="artifact-live-details"><summary>Generation details</summary><LiveSections state={activeLive} kind={kind} stopping={stopping} onStop={() => void stop()} only={job?.request?.operation === "revise" ? job.request.payload.sectionIndex ?? null : undefined} /></details></>}
      {building && !activeJob && job && <p className="outputs-hint">Working in <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => { const conversation = store.get(job.conversationId); if (conversation) void chooseConversation(conversation); }}>{store.get(job.conversationId)?.title ?? "another conversation"}</button></p>}
      {error && <div className="gx-banner is-error outputs-error" role="alert"><CircleAlert size={16} /><span><strong>{latestTurn?.status === "failed" ? latestTurn.request?.operation === "revise" ? "The change wasn’t saved" : "The artifact wasn’t saved" : "Could not complete the request"}</strong><small>{error}</small></span></div>}
    </div></div>
    <ArtifactComposer key={active.id} draft={active.draft} onDraft={(draft) => store.update(active.id, { draft })} mode={mode} onMode={configure} kind={kind} onKind={setKind} artifact={open} scopeText={scopeText} menu={menu} choice={choice} onChoice={(next) => { store.update(active.id, { choice: next }); setDeviceChoice(next); }} disabled={sendDisabled} busy={activeJob} stopping={stopping} onSend={send} onStop={() => void stop()} settings={settings} settingsOpen={settingsOpen} onSettings={() => setSettingsOpen(!settingsOpen)} focusRequest={focusRequest} allowVoiceInput={allowVoiceInput} />
  </>;
  const preview = <>
    <div className="aw-preview-toolbar aw-main-preview-toolbar"><strong>Preview</strong><select aria-label="Preview artifact" value={open?.lineageId ?? ""} disabled={loading || editing || saving || opening} onChange={(event) => { const item = activeGroups.find((items) => items[0]?.lineageId === event.target.value)?.[0]; if (item) void openVersion(item); }}>
      {!open && <option value="">{loading ? "Loading…" : "Choose an artifact"}</option>}{activeGroups.map((items) => <option key={items[0]!.lineageId} value={items[0]!.lineageId}>{items[0]!.title} · {kindLabel(items[0]!.kind)}</option>)}</select>
      {open && <select aria-label="Artifact versions" value={open.id} disabled={editing || saving || opening} onChange={(event) => { const item = versions.find((entry) => entry.id === event.target.value); if (item) void openVersion(item); }}>{versions.map((item) => <option key={item.id} value={item.id}>v{item.versionNumber}</option>)}</select>}
      <button type="button" className={`aw-toolbar-details${controlsExpanded ? " is-selected" : ""}`} aria-label={controlsExpanded ? "Hide preview controls" : "Show preview controls"} aria-expanded={controlsExpanded} onClick={() => setControlsExpanded(!controlsExpanded)}><PanelsTopLeft size={14} /><span>Details</span></button>
      {open?.outline.length ? <button type="button" className="gx-icon-button" aria-label="Show outline" title="Outline" onClick={() => { setControlsExpanded(true); requestAnimationFrame(() => outlinePanel.current?.scrollIntoView({ block: "nearest" })); }}><ListChecks size={15} /></button> : null}
      {open && <PopMenu label="Export artifact" triggerLabel="Export artifact" triggerClass="gx-icon-button" trigger={<Download size={15} />} items={[{ label: "Export PDF", icon: FileDown, disabled: editing, onSelect: () => void exportPdf() }, { label: "Copy Markdown", icon: Copy, onSelect: () => void copy() }, { label: "Download .md", icon: Download, onSelect: () => download() }]} />}
      <button type="button" className="gx-icon-button" aria-label={focused ? "Exit preview focus" : "Expand preview"} aria-pressed={focused} onClick={() => focusPreview(!focused)}>{focused ? <Minimize2 size={15} /> : <Maximize2 size={15} />}</button>
    </div>
    <div className="aw-preview-controls" id="artifact-preview-controls" hidden={!controlsExpanded} style={{ height: "var(--aw-controls-height)" }}>
      <header className="aw-controls-heading"><strong>Artifacts</strong><span>{activeGroups.length} from this conversation</span><button type="button" className="gx-icon-button" aria-label="Collapse details area" onClick={() => setControlsExpanded(false)}><ChevronDown size={14} /></button></header>
      <aside className="artifact-saved-artifacts" aria-label="Saved artifacts"><ul className="aw-artifact-tray">{activeGroups.map((items) => { const item = items[0]!; return <li className="artifact-tray-entry" key={item.lineageId}><button type="button" className={`aw-artifact-card${open?.lineageId === item.lineageId ? " is-active" : ""}`} aria-current={open?.lineageId === item.lineageId ? "page" : undefined} aria-label={`Preview ${item.title}`} disabled={editing || saving || opening} onClick={() => void openVersion(item)}><span className="aw-file-icon">{item.kind === "slides" ? <Presentation size={19} /> : <FileDown size={19} />}</span><strong>{item.title}</strong><small>{kindLabel(item.kind)}<span>v{item.versionNumber}</span></small></button>{artifactMenu(item)}</li>; })}</ul>{activeGroups.length === 0 && <p className="outputs-hint">No artifacts yet.</p>}</aside>
      <ArtifactAgentPlan live={activeLive} latest={latestTurn} stopping={stopping} onStop={() => void stop()} />
      {open && <div ref={outlinePanel} className="aw-controls-outline"><ApproachPanel artifact={open} busy={busy || !latest || !changeable} onRebuild={(approach) => runRebuild({ approach }, "Rebuild with the updated approach")} /><OutlineEditor key={open.id} outline={open.outline} label={open.kind === "slides" ? "slides" : "sections"} max={open.kind === "slides" ? 14 : 8} busy={busy || !latest || !changeable} onRebuild={(outline) => runRebuild({ outline }, "Rebuild with the updated outline")} /></div>}
      {versionsOpen && <section className="artifacts-version-list" aria-label="Artifact version history">{versions.map((item) => <button type="button" key={item.id} disabled={item.id === open?.id} className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => void openVersion(item)}>v{item.versionNumber} · {item.title}</button>)}</section>}
    </div>
    {controlsExpanded ? <PreviewHeightHandle value={controlsHeight} onChange={setControlsHeight} /> : activeLive && <button type="button" className="aw-compact-agent" aria-label="Show agent plan" onClick={() => setControlsExpanded(true)}><LoaderCircle className="spin" size={11} /><span>{outputActivity(activeLive)}</span><ChevronRight size={12} /></button>}
    {open ? <div className={`aw-preview-scroll${open.kind === "slides" ? " is-slides" : ""}`}>
      <div className="artifact-reading-actions">{changeable && latest && !editing && <><button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={busy} onClick={() => setEditing(true)}><Pencil size={12} />Edit</button><button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" aria-label="Ask Gunther to change…" disabled={busy || noModel} onClick={() => configure("modify")}><Sparkles size={12} />Revise</button></>}{!editing && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={busy} onClick={() => configure("rebuild")}><Sparkles size={12} />Rebuild</button>}{open.sections.some((section) => !section.checked) && changeable && latest && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={busy || noModel} onClick={() => void checkAgain()}><ShieldCheck size={12} />Check again</button>}</div>
      {editing ? <EditPane key={open.id} artifact={open} saving={saving} onSave={(content) => void saveEdit(content)} onCancel={() => { setEditing(false); setGeneralError(null); }} onCite={cite} /> : open.kind === "slides" ? <div className="aw-deck"><h2 className="outputs-title">{open.title}</h2><LazySlidesView artifact={open} onCite={cite} /></div> : <article className="aw-paper outputs-document is-saved" aria-label="Document preview"><h2 className="outputs-title">{open.title}</h2><p className="outputs-doc-meta">{[describe(open), new Date(open.createdAt).toLocaleDateString(), open.modelLabel ?? (open.origin === "edit" ? "typed by hand" : "")].filter(Boolean).join(" · ")}</p><ReportView artifact={open} onCite={cite} /><footer className="outputs-doc-foot"><ShieldCheck size={14} />{open.origin === "legacy" ? `From ${open.unitCount} accepted knowledge units` : `Built from ${describeScope(open.inputs)}`} · seal {open.manifestHash.slice(0, 12)}…</footer></article>}
    </div> : <div className="aw-preview-empty"><ListChecks size={30} /><h3>Your artifacts live here.</h3><p>Documents and slide decks from this conversation appear here. Create them through the conversation.</p></div>}
  </>;
  return <div className={`outputs-page page-enter${open?.kind === "slides" ? " is-deck" : ""}`}>
    <ArtifactWorkspaceLayout key={`${workspaceId}:${base.id}`} storageKey={layoutKey} focused={focused} onFocus={focusPreview} controlsHeight={controlsHeight}
      sources={<MaterialSelectionPanel key={base.id} baseId={base.id} scope={active.scope} onChange={(next: OutputScope) => store.update(active.id, { scope: next })} onLoaded={setLibrary} disabled={activeJob || editing || saving || !workspaceId} onAdd={onAdd} />}
      conversations={<section className="aw-conversations" aria-label="Conversations"><header><h2>Conversations</h2><button type="button" className="gx-icon-button" aria-label="New conversation" disabled={loading || editing || saving || !workspaceId} onClick={newConversation}><Plus size={16} /></button></header><div className="aw-conversation-list">{state.conversations.map((conversation) => <button type="button" key={conversation.id} title={conversation.title} disabled={editing || saving} className={conversation.id === active.id ? "is-active" : ""} aria-current={conversation.id === active.id ? "page" : undefined} onClick={() => void chooseConversation(conversation)}>{building && job?.conversationId === conversation.id ? <LoaderCircle size={15} className="spin" /> : <MessageSquare size={15} />}<span><strong>{conversation.title}</strong><small>{conversation.turns.length ? `${conversation.turns.length} requests` : "Continue here"}</small></span></button>)}</div></section>}
      chat={chat} preview={preview} />
    {open && <PrintRoot artifact={open} />}{evidence && <EvidencePanel citation={evidence.citation} index={evidence.index} onClose={() => setEvidence(null)} onOpenSource={onOpenSource ? (id) => { setEvidence(null); onOpenSource(id, [id]); } : undefined} />}
  </div>;
}
