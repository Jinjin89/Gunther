import type {
  Artifact,
  ArtifactAudience,
  ArtifactSummary,
  BuildOutputRequest,
  ConversationCitation,
  OutlineItem,
  OutputKind,
  OutputScope,
  OutputStyle,
  ReviseOutputRequest,
} from "@gunther/contracts";
import { outputStyles } from "@gunther/contracts";
import { invoke } from "@tauri-apps/api/core";
import { ArrowRight, Check, CircleAlert, Copy, Download, FileDown, History, LoaderCircle, Pencil, Plus, ShieldCheck, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KnowledgeBase } from "../atlas";
import { knowledgeApi, OutputStoppedError } from "../api";
import { EvidencePanel } from "../components/evidence/EvidencePanel";
import { openSettings } from "../components/SettingsLayout";
import { useModelMenu, useWebSearch } from "../models/askModel";
import { ApproachPanel } from "./ApproachPanel";
import { EditPane } from "./EditPane";
import { LazySlidesView } from "./LazySlides";
import { LiveSections } from "./LiveSections";
import { useLiveOutput } from "./liveOutput";
import { OutlineEditor } from "./OutlineEditor";
import { PrintRoot } from "./PrintRoot";
import { ReportView } from "./ReportView";
import { RevisePanel } from "./RevisePanel";
import { describeScope, ScopePicker, type ScopeCounts } from "./ScopePicker";
import "./outputs.css";

export interface OutputsPageProps {
  base: KnowledgeBase;
  workspaceId: string | null;
  onNotify: (message: string) => void;
  /** Open Capture, to add sources. */
  onAdd: () => void;
  /** Open a source in its own page; `queue` lists the sources to step through. */
  onOpenSource?: ((id: string, queue: string[]) => void) | undefined;
  /** An Ask conversation to start a new output from: chosen as its material once the page has loaded. */
  fromDiscussion?: string | null | undefined;
  onFromDiscussion?: (() => void) | undefined;
}

const WHOLE_LIBRARY: OutputScope = { mode: "library", sourceIds: [], unitIds: [], sessionIds: [] };
const KIND_LABEL: Record<OutputKind, string> = { report: "Report", slides: "Slides" };
const STYLE_LABEL: Record<OutputStyle, string> = {
  auto: "Auto",
  overview: "Overview",
  field_guide: "Field guide",
  teaching_path: "Teaching path",
  decision_brief: "Decision brief",
};
const AUDIENCES: Array<{ value: ArtifactAudience; label: string }> = [
  { value: "scientist", label: "Scientist" },
  { value: "student", label: "Student" },
  { value: "collaborator", label: "Collaborator" },
];
/** What a deck is for; with Auto the skill tells from the brief, and makes a talk when it can't. */
type DeckChoice = "auto" | "talk" | "read";
const USES: Array<{ value: DeckChoice; label: string }> = [
  { value: "auto", label: "Auto" },
  { value: "talk", label: "A talk" },
  { value: "read", label: "Reading" },
];
const ORIGIN_TAG: Partial<Record<Artifact["origin"], string>> = { edit: "Edited", revise: "Revised" };

const isStyle = (value: string | null): value is OutputStyle => outputStyles.some((item) => item === value);
const styleLabel = (value: string | null) => (value ? (isStyle(value) ? STYLE_LABEL[value] : value.replaceAll("_", " ")) : "");
const audienceLabel = (value: ArtifactAudience) => AUDIENCES.find((item) => item.value === value)?.label ?? value;
const newRequestId = () => globalThis.crypto?.randomUUID?.() ?? `output_${Date.now()}_${Math.random().toString(36).slice(2)}`;
const SECTION_LIMIT: Record<OutputKind, number> = { report: 8, slides: 14 };
const failure = (reason: unknown, fallback: string) => (reason instanceof Error ? reason.message : fallback);

/** What kind of thing a version is, in a line: "Report · Overview · Scientist". */
const describeVersion = (item: Pick<ArtifactSummary, "kind" | "style" | "audience">) =>
  [KIND_LABEL[item.kind], styleLabel(item.style), audienceLabel(item.audience)].filter(Boolean).join(" · ");

/**
 * Outputs: build a report or slides from the library (or what was picked), read it here,
 * change it by typing or by asking, and reopen any version. The agents' work is shown as it
 * happens; nothing is saved when a build fails or is stopped.
 */
export function OutputsPage({ base, workspaceId, onNotify, onAdd, onOpenSource, fromDiscussion, onFromDiscussion }: OutputsPageProps) {
  const [kind, setKind] = useState<OutputKind>("report");
  const [style, setStyle] = useState<OutputStyle>("auto");
  const [use, setUse] = useState<DeckChoice>("auto");
  const [audience, setAudience] = useState<ArtifactAudience>("scientist");
  const [brief, setBrief] = useState("");
  const [scope, setScope] = useState<OutputScope>(WHOLE_LIBRARY);
  const [picking, setPicking] = useState(false);
  const [library, setLibrary] = useState<ScopeCounts | null>(null);
  const [history, setHistory] = useState<ArtifactSummary[]>([]);
  const [open, setOpen] = useState<Artifact | null>(null);
  const [loading, setLoading] = useState(true);
  const [opening, setOpening] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorTitle, setErrorTitle] = useState("The output wasn’t saved");
  const [stopping, setStopping] = useState(false);
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [asking, setAsking] = useState(false);
  const [checking, setChecking] = useState(false);
  /** A revision in progress: its section (null: the whole output); undefined: a build. */
  const [revisionOf, setRevisionOf] = useState<number | null | undefined>(undefined);
  const [evidence, setEvidence] = useState<{ citation: ConversationCitation; index: number } | null>(null);
  const live = useLiveOutput();
  const { menu } = useModelMenu();
  const webSearch = useWebSearch();
  const retry = useRef<(() => Promise<void>) | null>(null);
  const listening = useRef<AbortController | null>(null);
  /** Say what went wrong, under a title that says what it was. */
  const fail = (title: string, reason: unknown, fallback: string) => {
    setErrorTitle(title);
    setError(failure(reason, fallback));
  };
  const building = live.live !== null;
  const busy = building || editing || saving || checking;
  const noModel = menu !== null && menu.models.length === 0;
  const emptyLibrary = library !== null && library.sources === 0 && library.units === 0;

  /** Show a version, and set the controls to what it was built with. */
  const show = useCallback((artifact: Artifact) => {
    setOpen(artifact);
    setKind(artifact.kind);
    if (isStyle(artifact.style)) setStyle(artifact.style);
    setAudience(artifact.audience);
    setBrief(artifact.brief);
    setScope(artifact.scope ?? WHOLE_LIBRARY);
    setEvidence(null);
    setEditing(false);
    setAsking(false);
  }, []);

  const finish = useCallback(async (artifact: Artifact, message?: string) => {
    show(artifact);
    if (workspaceId) {
      try {
        setHistory(await knowledgeApi.artifacts(base.id, workspaceId));
      } catch {
        // The version is saved; the list catches up the next time it is read.
      }
    }
    onNotify(message ?? `${KIND_LABEL[artifact.kind]} version ${artifact.versionNumber} saved.`);
  }, [base.id, onNotify, show, workspaceId]);

  // What this library holds, the history, and the latest version.
  useEffect(() => {
    let active = true;
    setLoading(true);
    setOpen(null);
    setHistory([]);
    setError(null);
    setScope(WHOLE_LIBRARY);
    setBrief("");
    setLibrary(null);
    setEditing(false);
    setAsking(false);
    void Promise.all([knowledgeApi.sources(base.id), knowledgeApi.knowledgeUnits(base.id)]).then(([sources, units]) => {
      if (active) setLibrary({ sources: sources.length, units: units.filter((unit) => unit.status === "trusted").length, sessions: 0 });
    }).catch(() => undefined);
    if (!workspaceId) {
      setError("The local workspace identity has not been verified yet.");
      setLoading(false);
      return () => { active = false; };
    }
    void (async () => {
      try {
        const items = await knowledgeApi.artifacts(base.id, workspaceId);
        if (!active) return;
        setHistory(items);
        if (items[0]) {
          const detail = await knowledgeApi.artifact(base.id, items[0].id, workspaceId);
          if (active) show(detail);
        }
      } catch (reason) {
        if (active) fail("The history couldn’t be loaded", reason, "Output history could not be loaded.");
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => { active = false; };
  }, [base.id, workspaceId, show]);

  // A build that is still going when the page opens (it carries on when the page is left).
  useEffect(() => {
    if (!workspaceId) return undefined;
    const listen = new AbortController();
    void knowledgeApi.followOutputBuild(base.id, workspaceId, live.hear, listen.signal).then(async (artifact) => {
      if (artifact) await finish(artifact);
    }).catch((reason) => {
      if (listen.signal.aborted) return;
      if (reason instanceof OutputStoppedError) onNotify("Stopped. Nothing was saved.");
      else fail("The output wasn’t saved", reason, "The output could not be built.");
    }).finally(() => {
      if (!listen.signal.aborted) {
        live.stop();
        setStopping(false);
      }
    });
    return () => listen.abort();
  }, [base.id, workspaceId]);

  useEffect(() => () => listening.current?.abort(), []);

  const head = useMemo(() => {
    if (!open) return null;
    return history.filter((item) => item.lineageId === open.lineageId).sort((a, b) => b.versionNumber - a.versionNumber)[0] ?? null;
  }, [history, open]);
  const openIsLatest = !open || !head || head.id === open.id;
  /** A version from the old builder has no citations or scope, so it cannot be changed. */
  const changeable = open !== null && open.origin !== "legacy";
  const unchecked = open ? open.sections.filter((section) => !section.checked).length : 0;

  const openVersion = useCallback(async (item: ArtifactSummary) => {
    if (!workspaceId) return;
    setOpening(item.id);
    try {
      show(await knowledgeApi.artifact(base.id, item.id, workspaceId));
      setError(null);
    } catch (reason) {
      fail("That version couldn’t be opened", reason, "That version could not be reopened.");
    } finally {
      setOpening(null);
    }
  }, [base.id, show, workspaceId]);

  /** Hear a build or a revision until it is saved, fails or is stopped. */
  const work = async (start: (signal: AbortSignal) => Promise<Artifact>, options: { failed?: string; message?: (artifact: Artifact) => string } = {}) => {
    const abort = new AbortController();
    listening.current = abort;
    setError(null);
    setStopping(false);
    try {
      const artifact = await start(abort.signal);
      await finish(artifact, options.message?.(artifact));
    } catch (reason) {
      if (abort.signal.aborted) return;
      if (reason instanceof OutputStoppedError) onNotify("Stopped. Nothing was saved.");
      else fail(options.failed ?? "The output wasn’t saved", reason, "The output could not be built.");
    } finally {
      if (!abort.signal.aborted) {
        live.stop();
        setStopping(false);
        setRevisionOf(undefined);
      }
    }
  };

  const run = async (request: BuildOutputRequest) => {
    if (!workspaceId) {
      setError("The local workspace identity must be verified before building an output.");
      return;
    }
    retry.current = () => run(request);
    live.start();
    await work((signal) => knowledgeApi.buildOutputStream(base.id, request, workspaceId, live.hear, signal));
  };

  const revise = async (request: ReviseOutputRequest) => {
    if (!workspaceId || !open) return;
    const target = open;
    retry.current = () => revise(request);
    setAsking(false);
    setRevisionOf(request.sectionIndex ?? null);
    live.start({ title: target.title, sections: target.sections.map((section) => ({ heading: section.heading, goal: "" })) });
    await work(
      (signal) => knowledgeApi.reviseOutputStream(base.id, target.id, request, workspaceId, live.hear, signal),
      { failed: "The change wasn’t saved", message: (artifact) => `Changed as asked: version ${artifact.versionNumber} saved.` },
    );
  };

  const requestFor = (extra: Partial<BuildOutputRequest> = {}): BuildOutputRequest => ({
    clientRequestId: newRequestId(),
    kind,
    ...(kind === "report" ? { style } : {}),
    ...(kind === "slides" && use !== "auto" ? { use } : {}),
    audience,
    ...(brief.trim() ? { brief: brief.trim() } : {}),
    scope,
    ...(open ? { supersedesArtifactId: open.id } : {}),
    ...(webSearch.enabled ? { web: true } : {}),
    ...extra,
  });

  const build = async () => {
    if (open && !openIsLatest && head) {
      await openVersion(head);
      onNotify(`Opened the latest version, ${head.versionNumber}. Rebuild from there.`);
      return;
    }
    await run(requestFor());
  };

  const stop = async () => {
    if (!workspaceId) return;
    setStopping(true);
    try {
      await knowledgeApi.stopOutputBuild(base.id, workspaceId);
    } catch (reason) {
      setStopping(false);
      fail("It couldn’t be stopped", reason, "It could not be stopped.");
    }
  };

  const saveEdit = async (content: string) => {
    if (!workspaceId || !open) return;
    setSaving(true);
    setError(null);
    try {
      const saved = await knowledgeApi.editOutput(base.id, open.id, { clientRequestId: newRequestId(), content }, workspaceId);
      await finish(saved, `Saved as version ${saved.versionNumber}. What you typed has not been re-checked yet.`);
    } catch (reason) {
      fail("The edit wasn’t saved", reason, "The edit could not be saved.");
    } finally {
      setSaving(false);
    }
  };

  const checkAgain = async () => {
    if (!workspaceId || !open) return;
    setChecking(true);
    setError(null);
    try {
      setOpen(await knowledgeApi.checkOutput(base.id, open.id, workspaceId));
      onNotify("Checked against the sources.");
    } catch (reason) {
      fail("It couldn’t be checked", reason, "It could not be checked.");
    } finally {
      setChecking(false);
    }
  };

  const startNew = () => {
    setOpen(null);
    setError(null);
    setEvidence(null);
    setAsking(false);
    retry.current = null;
  };

  // Coming from a research answer: a new output, with that conversation as its material.
  useEffect(() => {
    if (!fromDiscussion || loading) return;
    startNew();
    setScope({ mode: "selection", sourceIds: [], unitIds: [], sessionIds: [fromDiscussion] });
    onFromDiscussion?.();
    // Once per hand-off, when the page has loaded and would otherwise show the latest version.
  }, [fromDiscussion, loading]);

  const downloadMarkdown = () => {
    if (!open) return;
    const url = URL.createObjectURL(new Blob([open.content], { type: "text/markdown;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${base.id}-${open.kind}-v${open.versionNumber}.md`;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
    onNotify(`Downloaded version ${open.versionNumber} as Markdown.`);
  };

  const copyMarkdown = async () => {
    if (!open) return;
    try {
      await navigator.clipboard.writeText(open.content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_500);
      onNotify(`Version ${open.versionNumber} copied as Markdown.`);
    } catch {
      onNotify("Clipboard access was unavailable. Download the Markdown file instead.");
    }
  };

  /** Print the open version (laid out for paper by PrintRoot); the print sheet's PDF button saves the file. */
  const exportPdf = async () => {
    if (!open) return;
    try {
      // Fonts that are still loading would print in their stand-ins.
      await document.fonts?.ready;
    } catch {
      // Printing goes ahead with what is there.
    }
    try {
      // macOS's web view ignores window.print(), so the app's own shell opens the print sheet.
      if ("__TAURI_INTERNALS__" in window && /Mac/i.test(navigator.userAgent)) await invoke("print_output");
      else window.print();
      onNotify("In the print window, choose PDF to save it.");
    } catch (reason) {
      fail("It couldn’t be printed", reason, "The print window could not be opened.");
    }
  };

  const cite = (citation: ConversationCitation, index: number) => setEvidence({ citation, index });
  const scopeText = scope.mode === "library"
    ? (library ? describeScope(library) : "Reading the library…")
    : describeScope({ sources: scope.sourceIds.length, units: scope.unitIds.length, sessions: scope.sessionIds.length });
  const buildLabel = building
    ? "Building…"
    : open ? (openIsLatest ? "Rebuild" : `Open latest version ${head?.versionNumber ?? ""}`.trim()) : "Build";

  return <div className={`outputs-page page-enter ${open?.kind === "slides" ? "is-deck" : ""}`}>
    <header className="outputs-intro">
      <span className="atlas-eyebrow">Outputs</span>
      <h1>Turn what you’ve gathered into a report or slides.</h1>
      <p>Pick what to use and what it is for. Gunther plans it, looks things up, writes it and checks it against your sources, then saves it as a version you can reopen.</p>
    </header>
    <div className="outputs-layout">
      <section className="outputs-controls" aria-label="Build an output">
        <div className="outputs-field">
          <span className="outputs-label">Use</span>
          <div className="outputs-segment" role="group" aria-label="Use">
            <button type="button" className={scope.mode === "library" ? "is-active" : ""} aria-pressed={scope.mode === "library"} disabled={busy} onClick={() => setScope(WHOLE_LIBRARY)}>Whole library</button>
            <button type="button" className={scope.mode === "selection" ? "is-active" : ""} aria-pressed={scope.mode === "selection"} disabled={busy} onClick={() => setPicking(true)}>Choose…</button>
          </div>
          <small className="outputs-hint">{scopeText}</small>
        </div>
        <label className="outputs-field">
          <span className="outputs-label">Brief</span>
          <textarea value={brief} onChange={(event) => setBrief(event.target.value)} placeholder="What should it cover, and for whom?" rows={3} maxLength={2000} disabled={busy} />
        </label>
        <div className="outputs-field">
          <span className="outputs-label">Make</span>
          <div className="outputs-segment" role="group" aria-label="Make">
            {(Object.keys(KIND_LABEL) as OutputKind[]).map((item) => <button key={item} type="button" className={kind === item ? "is-active" : ""} aria-pressed={kind === item} disabled={busy} onClick={() => setKind(item)}>{KIND_LABEL[item]}</button>)}
          </div>
        </div>
        {kind === "report" && <div className="outputs-field">
          <span className="outputs-label">Style</span>
          <div className="outputs-segment is-wrap" role="group" aria-label="Style">
            {outputStyles.map((item) => <button key={item} type="button" className={style === item ? "is-active" : ""} aria-pressed={style === item} disabled={busy} onClick={() => setStyle(item)}>{STYLE_LABEL[item]}</button>)}
          </div>
        </div>}
        {kind === "slides" && <div className="outputs-field">
          <span className="outputs-label">For</span>
          <div className="outputs-segment" role="group" aria-label="For">
            {USES.map((item) => <button key={item.value} type="button" className={use === item.value ? "is-active" : ""} aria-pressed={use === item.value} disabled={busy} onClick={() => setUse(item.value)}>{item.label}</button>)}
          </div>
          <small className="outputs-hint">{use === "read" ? "Each slide reads on its own." : use === "talk" ? "Few words on the slides; the rest in the speaker notes." : "Told from the brief; a talk when it doesn’t say."}</small>
        </div>}
        <div className="outputs-field">
          <span className="outputs-label">Audience</span>
          <div className="outputs-segment" role="group" aria-label="Audience">
            {AUDIENCES.map((item) => <button key={item.value} type="button" className={audience === item.value ? "is-active" : ""} aria-pressed={audience === item.value} disabled={busy} onClick={() => setAudience(item.value)}>{item.label}</button>)}
          </div>
        </div>
        {webSearch.available && <label className="outputs-field outputs-web">
          <span><span className="outputs-label">Search the web too</span><small className="outputs-hint">Only to fill gaps: background, comparisons, the latest. Shown as added.</small></span>
          <input type="checkbox" role="switch" aria-label="Search the web too" checked={webSearch.preferred} disabled={busy} onChange={(event) => webSearch.setEnabled(event.target.checked)} />
        </label>}
        <button type="button" className="gx-btn gx-btn-primary outputs-build" disabled={loading || busy || !workspaceId || noModel || emptyLibrary} onClick={() => void build()}>
          {building ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}{buildLabel}
        </button>
        <section className="outputs-history">
          <header>
            <span><History size={13} /><strong>History</strong></span>
            <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={startNew} disabled={busy}><Plus size={12} />New</button>
          </header>
          {history.length === 0 && !loading
            ? <p>Nothing built yet.</p>
            : <ul>{history.map((item) => <li key={item.id}>
              <button type="button" className={open?.id === item.id ? "is-active" : ""} disabled={opening === item.id || busy} onClick={() => void openVersion(item)}>
                <span>
                  <strong>{item.title}</strong>
                  <small>{describeVersion(item)}{ORIGIN_TAG[item.origin] ? ` · ${ORIGIN_TAG[item.origin]}` : ""}</small>
                </span>
                <em>{opening === item.id ? "Opening…" : `v${item.versionNumber}`}</em>
              </button>
            </li>)}</ul>}
        </section>
      </section>
      <article className={`outputs-document ${open ? "is-saved" : ""}`}>
        {building && live.live && <LiveSections state={live.live} kind={revisionOf !== undefined && open ? open.kind : kind} stopping={stopping} only={revisionOf} onStop={() => void stop()} />}
        {!building && error && <div className="gx-banner is-error outputs-error" role="alert">
          <CircleAlert size={16} />
          <span><strong>{errorTitle}</strong><small>{error}</small></span>
          {retry.current && !editing && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => void retry.current?.()}>Retry</button>}
        </div>}
        {!building && open && <>
          <header className="outputs-doc-head">
            <span>{[describeVersion(open), `v${open.versionNumber}`, new Date(open.createdAt).toLocaleDateString(), open.modelLabel ?? (open.origin === "edit" ? "typed by hand" : "")].filter(Boolean).join(" · ")}</span>
            <div>
              {changeable && !editing && openIsLatest && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={busy} onClick={() => { setEditing(true); setAsking(false); }}><Pencil size={13} />Edit</button>}
              {changeable && !editing && !openIsLatest && head && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => void openVersion(head)}>Open latest</button>}
              {changeable && !editing && openIsLatest && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={busy || noModel} aria-expanded={asking} onClick={() => setAsking(!asking)}><Sparkles size={13} />Ask Gunther to change…</button>}
              {changeable && !editing && unchecked > 0 && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={busy || noModel} onClick={() => void checkAgain()}>{checking ? <LoaderCircle className="spin" size={13} /> : <ShieldCheck size={13} />}{checking ? "Checking…" : "Check again"}</button>}
              <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={editing} onClick={() => void exportPdf()}><FileDown size={13} />Export PDF</button>
              <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => void copyMarkdown()}>{copied ? <Check size={13} /> : <Copy size={13} />}{copied ? "Copied" : "Copy Markdown"}</button>
              <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={downloadMarkdown}><Download size={13} />Download .md</button>
            </div>
          </header>
          {!changeable && <p className="outputs-hint outputs-legacy-note">This version came from the old builder. Rebuild it to edit it.</p>}
          <h2 className="outputs-title">{open.title}</h2>
          {asking && !editing && <RevisePanel key={open.id} artifact={open} busy={busy} onRevise={(instruction, sectionIndex) => void revise({ clientRequestId: newRequestId(), instruction, ...(sectionIndex === null ? {} : { sectionIndex }) })} onCancel={() => setAsking(false)} />}
          {editing
            ? <EditPane key={open.id} artifact={open} saving={saving} onSave={(content) => void saveEdit(content)} onCancel={() => { setEditing(false); setError(null); }} onCite={cite} />
            : <>
              <ApproachPanel artifact={open} busy={busy || !openIsLatest || !changeable} onRebuild={(approach) => void run(requestFor({ approach }))} />
              <OutlineEditor outline={open.outline} label={open.kind === "slides" ? "slides" : "sections"} max={SECTION_LIMIT[open.kind]} busy={busy || !openIsLatest || !changeable} onRebuild={(outline: OutlineItem[]) => void run(requestFor({ outline }))} />
              {open.kind === "slides" ? <LazySlidesView artifact={open} onCite={cite} /> : <ReportView artifact={open} onCite={cite} />}
            </>}
          <footer className="outputs-doc-foot"><ShieldCheck size={14} />{open.origin === "legacy" ? `From ${open.unitCount} accepted knowledge ${open.unitCount === 1 ? "unit" : "units"}` : `Built from ${describeScope(open.inputs)}`} · seal {open.manifestHash.slice(0, 12)}…</footer>
        </>}
        {!building && !open && !error && !loading && noModel && <div className="gx-empty-state outputs-empty">
          <h2>Outputs need a model</h2>
          <p>Outputs need a model. Set one up under Settings → Models.</p>
          <button type="button" className="gx-btn gx-btn-primary" onClick={() => openSettings("models")}>Open Settings<ArrowRight size={13} /></button>
        </div>}
        {!building && !open && !error && !loading && !noModel && emptyLibrary && <div className="gx-empty-state outputs-empty">
          <h2>Nothing to build from yet</h2>
          <p>Add sources to this library, or save a useful answer from Ask as knowledge.</p>
          <button type="button" className="gx-btn gx-btn-primary" onClick={onAdd}>Add sources<ArrowRight size={13} /></button>
        </div>}
        {!building && !open && !error && !loading && !noModel && !emptyLibrary && <div className="gx-empty-state outputs-empty">
          <h2>Ready when you are</h2>
          <p>Choose what to use and what it is for, then press Build. You will see the approach and the plan first, then each part as it is written, read through and revised.</p>
        </div>}
      </article>
    </div>
    {open && <PrintRoot artifact={open} />}
    {picking && <ScopePicker baseId={base.id} initial={scope} onApply={(next) => { setScope(next); setPicking(false); }} onClose={() => setPicking(false)} />}
    {evidence && <EvidencePanel citation={evidence.citation} index={evidence.index} onClose={() => setEvidence(null)} onOpenSource={onOpenSource ? (id) => { setEvidence(null); onOpenSource(id, [id]); } : undefined} />}
  </div>;
}
