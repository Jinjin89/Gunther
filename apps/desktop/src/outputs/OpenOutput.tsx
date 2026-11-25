import type { Artifact, ArtifactConversationMessage, ArtifactTurnInput, ConversationCitation, OutputKind, OutputScope } from "@gunther/contracts";
import { invoke } from "@tauri-apps/api/core";
import { Trash2 } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiRequestError, isTurnResult, knowledgeApi } from "../api";
import { CitationDetails } from "../components/evidence/CitationButton";
import { isWebCitation } from "../components/evidence/citationKind";
import { KeyResultsStatus } from "../keyResults/KeyResultsStatus";
import { AnswerBody, citationNumbers, verificationNotice } from "../pages/AnswerBody";
import { ArtifactMenu } from "./ArtifactMenu";
import { ArtifactMessageList } from "./ArtifactMessages";
import { artifactRequestId, EARLIER_WORK, libraryScope, scopeLabel } from "./artifactConversations";
import { withDeckMarker } from "./deck/marker";
import { discardOutputEdit, EditPane } from "./EditPane";
import { outputActivity } from "./liveOutput";
import { OutlineEditor } from "./OutlineEditor";
import { outputViews } from "./OutputViews";
import { prepareForPrint } from "./printReady";
import { PrintRoot } from "./PrintRoot";
import { parseSlides, withAccent, withTheme, type DeckStyle, type DeckTheme } from "./sections";
import { StudioWebSwitch } from "./StudioWebSwitch";
import { type OutputListener, type OutputTarget, type OutputTrashTarget, type useStudio } from "./useStudio";
import "./outputs.css";

const message = (reason: unknown) => reason instanceof Error ? reason.message : "The request could not be completed.";
const sameIds = (a: string[], b: string[]) => a.length === b.length && a.every((id) => b.includes(id));
const sameScope = (a: OutputScope, b: OutputScope) => a.mode === b.mode && (
  a.mode === "library" || (sameIds(a.sourceIds, b.sourceIds) && sameIds(a.unitIds, b.unitIds) && sameIds(a.sessionIds, b.sessionIds))
);
export function OpenOutput({ baseId, workspaceId, target, studio, scope, scopeEmpty, webAvailable,
  dirty, onNotify, onCite, onOpen, onWideReader, onAskChat, onTrash, onArtifactChange, onPartChange }: {
  baseId: string; workspaceId: string; target: OutputTarget; studio: ReturnType<typeof useStudio>;
  scope: OutputScope; scopeEmpty: boolean; webAvailable: boolean; dirty: { current: boolean };
  onNotify: (message: string) => void; onCite: (artifact: Artifact, index: number) => void;
  onOpen: (target: OutputTarget) => void;
  onWideReader: (wide: boolean) => void;
  onAskChat: (question: string) => void;
  onTrash: (target: OutputTrashTarget) => void;
  onArtifactChange: (artifact: Artifact | null) => void;
  onPartChange: (index: number | null) => void;
}) {
  const [picked, pickVersion] = useState(target.artifactId ?? null);
  const [opened, setOpened] = useState<Artifact | null>(null);
  const [panel, setPanel] = useState<"versions" | "outline" | "style" | null>(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState("");
  const [web, setWeb] = useState(false);
  const [focus, setFocus] = useState<ArtifactTurnInput["focus"]>();
  const [jump, setJump] = useState<{ index: number; nonce: number } | null>(null);
  const partInView = useRef<number | null>(null);
  const refine = useRef<HTMLTextAreaElement>(null);
  const styleRun = useRef<{ running: DeckStyle; pending: DeckStyle | null } | null>(null);
  const [pendingStyle, setPendingStyle] = useState<DeckStyle | null>(null);
  const [restyling, setRestyling] = useState(false);
  const conversationId = target.conversationId ?? (opened ? studio.owner(opened) : null);
  const conversation = conversationId ? studio.details[conversationId] : undefined;
  const lineageId = target.lineageId ?? opened?.lineageId ?? conversation?.lineageId;
  const versions = studio.artifacts.filter((item) => lineageId ? item.lineageId === lineageId
    : target.conversationId && studio.owner(item) === target.conversationId).sort((a, b) => b.versionNumber - a.versionNumber);
  const head = versions[0];
  const shownId = picked ?? head?.id;
  const unavailable = studio.artifactsLoaded && shownId
    && !studio.artifacts.some((item) => item.id === shownId);
  const artifact = opened?.id === shownId && !unavailable ? opened : null;
  const cite = (_citation: ConversationCitation, index: number) => { if (artifact) onCite(artifact, index); };
  const job = conversationId ? studio.jobs.get(conversationId) : undefined;
  const activity = studio.activities.find((item) => item.open?.view === "library" && item.open.tab === "outputs"
    && (item.open.conversationId ?? item.open.lineageId) === conversationId && item.state === "running");
  const runVersion = job?.artifactId ?? (activity?.open?.view === "library" && activity.open.tab === "outputs"
    ? activity.open.artifactId : undefined);
  const workingOnVersion = Boolean(shownId && runVersion === shownId);
  const failure = conversationId ? studio.failures[conversationId] : undefined;
  const latest = !head || head.id === artifact?.id;
  const changeable = artifact !== null && artifact.origin !== "legacy";
  const busy = Boolean(job) || checking || saving || updating || restyling;
  const useScope = (lineageId && conversation?.refinementScopes?.[lineageId]) || artifact?.scope || conversation?.scope || libraryScope();
  const pending = conversation?.pending;
  const last = conversation?.messages.at(-1);
  const onDirtyChange = useCallback((value: boolean) => { dirty.current = value; }, [dirty]);
  const onPart = useCallback((index: number | null) => { partInView.current = index; onPartChange(index); }, [onPartChange]);

  useLayoutEffect(() => { partInView.current = null; onPartChange(null); }, [baseId, shownId, workspaceId, onPartChange]);
  useEffect(() => { setError(""); setFocus(undefined); }, [baseId, shownId, workspaceId]);
  useEffect(() => { onArtifactChange(artifact); }, [artifact, onArtifactChange]);
  const { read, follow, reload } = studio;
  useEffect(() => {
    if (!conversationId) return;
    void read(conversationId).catch((reason) => setError(message(reason)));
    follow(conversationId);
  }, [conversationId, read, follow]);
  useEffect(() => () => { styleRun.current = null; }, []);

  const send = async (turn: Omit<ArtifactTurnInput, "clientRequestId">) => {
    if (!artifact || !conversationId || !changeable || busy || editing) return;
    setError("");
    const material = artifact.scope ?? libraryScope();
    const result = await studio.send(conversationId, { ...turn, targetLineageId: artifact.lineageId }, material);
    if (result && "outcome" in result && result.artifact && latest) {
      const section = turn.focus?.section;
      if (section !== undefined) {
        const id = artifact.outline[section - 1]?.id;
        const index = id ? result.artifact.outline.findIndex((item) => item.id === id) : section - 1;
        if (index >= 0) setJump((current) => ({ index, nonce: (current?.nonce ?? 0) + 1 }));
      }
      pickVersion(null);
    }
  };
  const refineOutput = (text: string) => {
    if (!text.trim() || !artifact || !changeable || busy || editing) return;
    const part = latest ? focus ?? (partInView.current !== null ? { section: partInView.current + 1, selected: false } : undefined) : undefined;
    void send({ kind: pending ? "answer" : "message", text, ...(part ? { focus: part } : {}),
      ...(webAvailable && web ? { web: true } : {}) });
    setDraft(""); setFocus(undefined);
  };
  const retry = (answer: ArtifactConversationMessage) => {
    if (conversation?.lastTurn?.build && conversationId) { void studio.retry(conversationId, conversation.lastTurn.build); return; }
    const earlier = conversation?.messages.slice(0, conversation.messages.indexOf(answer)).reverse().find((item) => item.role === "user");
    if (!earlier) return;
    const { focus: requested, rebuild, model, effort, web } = earlier.payload as Partial<ArtifactTurnInput>;
    void send({ kind: earlier.kind as NonNullable<ArtifactTurnInput["kind"]>, text: earlier.text, ...(requested ? { focus: requested } : {}),
      ...(rebuild ? { rebuild } : {}), ...(model ? { model } : {}), ...(effort ? { effort } : {}), ...(web ? { web } : {}) });
  };
  const updateScope = async () => {
    if (!artifact || !conversationId || scopeEmpty || busy) return;
    setUpdating(true); setError("");
    try {
      await studio.ensure(conversationId, artifact.scope ?? libraryScope());
      studio.remember(await knowledgeApi.updateArtifactConversation(baseId, conversationId, {
        targetLineageId: artifact.lineageId, scope,
      }, workspaceId));
    } catch (reason) { setError(message(reason)); } finally { setUpdating(false); }
  };

  const save = async (content: string, parent = artifact) => {
    if (!parent) return null;
    setSaving(true); setError("");
    try {
      const saved = await knowledgeApi.editOutput(baseId, parent.id, { clientRequestId: artifactRequestId(), content }, workspaceId);
      studio.rememberVersion(saved);
      pickVersion(saved.id); setOpened(saved); setEditing(false);
      onNotify(`Saved as version ${saved.versionNumber}.`);
      return saved;
    } catch (reason) { setError(message(reason)); return null; }
    finally { setSaving(false); }
  };
  const check = async () => {
    if (!artifact) return;
    setChecking(true); setError("");
    try {
      const checked = await knowledgeApi.checkOutput(baseId, artifact.id, workspaceId);
      setOpened(checked);
      onNotify(checked.sections.map((section) => verificationNotice(section.coverage)).find(Boolean)
        ?? (checked.sections.every((section) => section.checked) ? "Checked against the sources." : "The source check is incomplete."));
    } catch (reason) { setError(message(reason)); } finally { setChecking(false); }
  };
  const reviewSlides = async () => {
    if (!artifact || !conversationId) return;
    const reviewed = await studio.direct(conversationId, artifact, "Review rendered slides", (hear, signal) =>
      knowledgeApi.reviewSlidesStream(baseId, artifact.id, { clientRequestId: artifactRequestId() }, workspaceId, hear, signal));
    if (reviewed && "id" in reviewed) pickVersion(reviewed.id);
  };
  const runReaderAction = async (label: string, listen: OutputListener) => {
    if (!artifact || !conversationId || busy || activity || editing) return;
    setError("");
    const updated = await studio.direct(conversationId, artifact, label, listen);
    if (updated && !isTurnResult(updated)) {
      setOpened((current) => current?.id === updated.id ? updated : current);
    }
  };
  const restyle = async (style: DeckStyle) => {
    if (!artifact || !conversationId || !latest) return;
    if (styleRun.current) {
      styleRun.current.pending = style === styleRun.current.running ? null : style;
      setPendingStyle(styleRun.current.pending);
      return;
    }
    if (busy || parseSlides(artifact.content)[0]?.style === style) return;
    const run = { running: style, pending: null as DeckStyle | null };
    styleRun.current = run;
    setRestyling(true);
    let parent = artifact;
    try {
      while (styleRun.current === run) {
        const content = withDeckMarker(parent.content, { style: run.running, theme: "paper" });
        const coloured = content === parent.content ? parent : await save(content, parent);
        if (!coloured || styleRun.current !== run) break;
        const redesigned = await studio.direct(conversationId, coloured, "Designing the slides", (hear, signal) =>
          knowledgeApi.restyleSlidesStream(baseId, coloured.id, { clientRequestId: artifactRequestId(), style: run.running }, workspaceId, hear, signal));
        if (!redesigned || !("id" in redesigned) || styleRun.current !== run) break;
        pickVersion(redesigned.id);
        parent = redesigned;
        if (!run.pending || run.pending === parseSlides(parent.content)[0]?.style) break;
        run.running = run.pending; run.pending = null; setPendingStyle(null);
      }
    } finally {
      if (styleRun.current === run) { styleRun.current = null; setRestyling(false); setPendingStyle(null); }
    }
  };

  const withSavedVersion = async (use: (version: Artifact) => void | Promise<void>) => {
    if (!artifact) return;
    try {
      await use(await knowledgeApi.artifact(baseId, artifact.id, workspaceId));
    } catch (reason) {
      if (reason instanceof ApiRequestError && reason.status === 404) setOpened(null);
      setError(message(reason));
    }
  };
  const exportFile = (make: (version: Artifact) => Promise<string>) =>
    void withSavedVersion(async (version) => onNotify(`Downloaded ${await make(version)}.`));
  const print = async () => {
    try {
      const done = await prepareForPrint();
      try { if ("__TAURI_INTERNALS__" in window && /Mac/i.test(navigator.userAgent)) await invoke("print_output"); else window.print(); }
      finally { done(); }
    } catch (reason) { setError(message(reason)); }
  };
  const download = (version: Artifact) => {
    const url = URL.createObjectURL(new Blob([version.content], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url; link.download = `${baseId}-${version.kind}-v${version.versionNumber}.md`; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const point = (index: number, quote?: string) => {
    setFocus({ section: index + 1, selected: true, ...(quote ? { quote } : {}) });
    refine.current?.focus();
  };
  const problem = error || failure?.message || (unavailable ? "This output is unavailable." : "");
  const targetType = studio.types.find((item) => item.id === target.typeId);
  const title = artifact?.title || (!unavailable && opened?.title) || job?.live.title || targetType?.label
    || activity?.title || conversation?.title || "Output";
  const changed = artifact?.supersedesArtifactId
    ? (artifact.provenance.changes ?? []).flatMap((change) => change.position && change.what !== "deleted" ? [change.position - 1] : [])
    : [];
  const stop = () => {
    if (!conversationId) return;
    const request = job ? studio.stop(conversationId) : activity ? knowledgeApi.stopActivity(activity.id) : knowledgeApi.stopArtifactTurn(baseId, conversationId, workspaceId);
    void request.catch((reason) => setError(message(reason)));
  };
  const switchVersion = (id: string) => {
    if (!discardOutputEdit(dirty)) return false;
    setEditing(false); pickVersion(id); setJump(null);
    return true;
  };
  const choosePanel = (next: typeof panel) => setPanel((current) => current === next ? null : next);
  const type = artifact && (studio.types.find((item) => item.shape === artifact.kind
    && (item.id === artifact.style || item.legacyStyles?.includes(artifact.style ?? "")))
    ?? studio.types.find((item) => item.shape === artifact.kind));
  const { Reader, Review, wide = false, printLabel, savedStage, file } = outputViews[artifact?.kind ?? targetType?.shape ?? conversation?.shape ?? "report"];
  const writingAttachment = Boolean(savedStage && (!runVersion || workingOnVersion)
    && job?.live.running.some((step) => step.kind === "stage" && step.id === savedStage));
  const working = workingOnVersion || writingAttachment;
  useEffect(() => {
    // An attachment can take a while after its text version has already been saved.
    if (writingAttachment && !shownId) void reload();
  }, [writingAttachment, shownId, reload]);
  useEffect(() => {
    if (!shownId) return;
    let alive = true;
    void knowledgeApi.artifact(baseId, shownId, workspaceId).then((version) => {
      if (alive) setOpened(version);
    }).catch((reason) => { if (alive) setError(message(reason)); });
    return () => { alive = false; };
  }, [baseId, shownId, workspaceId, working]);
  const hasWebEvidence = Boolean(artifact?.citations.some(isWebCitation)
    || artifact?.provenance.searched?.some((hit) => hit.kind === "web"));
  useEffect(() => {
    if (!shownId || !hasWebEvidence) return;
    let generation = 0;
    const refresh = () => {
      const request = ++generation;
      void knowledgeApi.artifact(baseId, shownId, workspaceId).then((version) => {
        if (request !== generation) return;
        setOpened((current) => current?.id === version.id ? { ...current,
          citations: version.citations, provenance: { ...current.provenance, searched: version.provenance.searched ?? [] },
        } : current);
      }).catch(() => undefined);
    };
    window.addEventListener("gunther:sources-updated", refresh);
    return () => { ++generation; window.removeEventListener("gunther:sources-updated", refresh); };
  }, [baseId, hasWebEvidence, shownId, workspaceId]);
  const capabilities = type?.capabilities;
  const unitLabel = (kind: OutputKind | null) => studio.types.find((item) => item.shape === kind)?.capabilities?.unitLabel ?? "part";
  const unit = capabilities?.unitLabel ?? "part";
  const outline = capabilities?.outline && Boolean(artifact?.outline.length);
  const formats = capabilities?.exports ?? [];
  const printAction = formats.includes("pdf") ? "Export PDF" : printLabel;
  const styling = capabilities?.design && panel === "style" && changeable;
  useLayoutEffect(() => onWideReader(wide), [wide, onWideReader]);
  const allowPoint = changeable && latest && !editing;

  return <CitationDetails.Provider value={artifact?.citations ?? []}><article className="library-reading output-reader" aria-label="Open output">
    <header className="library-reading-heading">
      <h1>{title}</h1>
      {artifact && <ArtifactMenu label="Output actions" items={[
        { label: "Versions", onSelect: () => choosePanel("versions") },
        ...(outline ? [{ label: "Outline", onSelect: () => choosePanel("outline") }] : []),
        { label: "Edit", disabled: busy || !latest || !changeable, onSelect: () => { pickVersion(artifact.id); setEditing(true); setPanel(null); } },
        { label: "Check again", disabled: busy || !latest || !changeable, onSelect: () => void check() },
        ...(capabilities?.design ? [{ label: "Style", disabled: editing, onSelect: () => choosePanel("style") }] : []),
        ...(printAction ? [{ label: printAction, divider: true, disabled: editing, onSelect: () => void withSavedVersion(print) }] : []),
        ...(formats.includes("markdown") ? [
          { label: "Copy Markdown", onSelect: () => void withSavedVersion(async (version) => {
            await navigator.clipboard.writeText(version.content);
            onNotify("Copied Markdown.");
          }) },
          { label: "Download Markdown", onSelect: () => void withSavedVersion(download) },
        ] : []),
        ...(file && formats.includes(file.format) && file.available(artifact, working)
          ? [{ label: file.label, onSelect: () => exportFile(file.download) }] : []),
        { label: "Move to Trash", icon: Trash2, divider: true, danger: true, disabled: saving,
          onSelect: () => onTrash({ id: artifact.id, lineageId: artifact.lineageId }) },
      ]} />}
    </header>
    {problem && <p role="alert" className="output-error">{problem}</p>}
    {artifact?.keyResults && <KeyResultsStatus key={artifact.keyResults.id} baseId={baseId} initial={artifact.keyResults} />}
    {(job || activity) && <div className="output-run" role="status">
      <span>{job ? outputActivity(job.live) : activity?.detail || activity?.title}</span>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={job?.stopping} onClick={stop}>Stop</button>
    </div>}
    {job?.live.reply && <AnswerBody content={job.live.reply} numbers={[]} />}
    {!artifact && !job && !activity && !problem && <p>{conversation?.lastTurn?.error || (shownId ? "Loading version…" : "Waiting for the output…")}</p>}
    {artifact && <>
      {panel === "versions" && <ul className="output-versions" aria-label="Versions">{versions.map((version) => <li key={version.id}>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" aria-current={version.id === artifact.id ? "page" : undefined} onClick={() => switchVersion(version.id)}>Version {version.versionNumber}</button>
      </li>)}</ul>}
      {panel === "outline" && outline && <OutlineEditor outline={artifact.outline} label={`${unit}s`} max={type?.partLimit}
        review={artifact.provenance.skill?.outlineReview} busy={busy || !changeable || !latest || editing}
        onRebuild={(outline) => void send({ kind: "rebuild", rebuild: { artifactId: artifact.id, outline } })} />}
      {editing ? <EditPane key={artifact.id} artifact={artifact} saving={saving} onSave={(content) => void save(content)}
        onCancel={() => { if (discardOutputEdit(dirty)) { setEditing(false); pickVersion(null); } }} onCite={cite} onDirtyChange={onDirtyChange} />
        : <>
          {Review && capabilities?.design && <Review artifact={artifact} baseId={baseId} workspaceId={workspaceId} disabled={busy || !latest || !changeable} onReview={() => void reviewSlides()} onFollowUp={refineOutput} />}
          <Reader artifact={artifact} onCite={cite} onPart={onPart} onAskChat={onAskChat}
            onAsk={allowPoint ? point : undefined} goTo={jump} changed={changed} onNotice={onNotify}
            type={type || undefined} work={{ working, disabled: busy || Boolean(activity), run: runReaderAction }}
            design={styling ? {
              onAccent: (accent) => void save(withAccent(artifact.content, accent)),
              onTheme: (theme) => void save(withTheme(artifact.content, theme)),
              onDeckTheme: (theme: DeckTheme) => void save(withDeckMarker(artifact.content, { theme })),
              onDeckStyle: (style) => void restyle(style),
              accentDisabled: busy || !latest, deckStyleDisabled: (!restyling && busy) || !latest, pendingDeckStyle: pendingStyle,
            } : undefined} />
        </>}
      {printAction && <PrintRoot artifact={artifact} />}
    </>}
    {conversation && conversation.messages.length > 0 && <details className="output-history">
      <summary>Change history</summary>
      <ArtifactMessageList messages={conversation.messages} pending={pending ?? null} history={studio.artifacts}
        citations={artifact?.citations ?? []} kind={artifact?.kind ?? null} unitLabel={unitLabel} lineageId={lineageId ?? null} busy={busy}
        onOption={refineOutput} onStart={() => void send({ kind: "start" })} onContinue={() => void send({ kind: "continue" })}
        onRetry={retry} onOpenVersion={(version) => onOpen({ conversationId: studio.owner(version), lineageId: version.lineageId, artifactId: version.id })}
        onOpenPart={(id, position) => {
          const version = studio.artifacts.find((item) => item.id === id);
          if (version && version.lineageId !== lineageId) { onOpen({ conversationId: studio.owner(version), lineageId: version.lineageId, artifactId: version.id }); return; }
          if (id && !switchVersion(id)) return;
          if (position) setJump({ index: position - 1, nonce: Date.now() });
        }} onCite={(index) => { const citation = artifact?.citations[index]; if (citation) cite(citation, index); }} earlier={conversationId === EARLIER_WORK} />
    </details>}
    {!job && last && (last.kind === "reply" || last.kind === "answer") && <AnswerBody content={last.text} numbers={citationNumbers(artifact?.citations ?? [])}
      onCitation={(index) => { const citation = artifact?.citations[index]; if (citation) cite(citation, index); }} />}
    {!job && last?.kind === "paused" && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => void send({ kind: "continue" })}>Continue</button>}
    {changeable && !editing && <form className="output-refine" onSubmit={(event) => { event.preventDefault(); refineOutput(draft); }}>
      {latest && !sameScope(useScope, scope) && <div className="output-scope">
        <span>Using {useScope.mode === "selection" ? "this output’s " : ""}{scopeLabel(useScope)}</span>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={busy || scopeEmpty} onClick={() => void updateScope()}>
          {scope.mode === "library" ? "Update to all sources" : `Update to current ${scope.sourceIds.length}`}
        </button>
      </div>}
      {pending && <div className="output-question"><p>{pending.question}</p>{pending.options.map((option) => <button key={option} type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={busy} onClick={() => refineOutput(option)}>{option}</button>)}</div>}
      {focus && <small>About {focus.quote ? `“${focus.quote}”` : `${unit} ${focus.section}`}
        <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => setFocus(undefined)}>Clear</button></small>}
      <label>Refine<textarea ref={refine} value={draft} maxLength={20_000} rows={2} onChange={(event) => setDraft(event.target.value)} /></label>
      <StudioWebSwitch available={webAvailable} checked={web} onChange={setWeb} />
      <button type="submit" className="gx-btn gx-btn-primary gx-btn-sm" disabled={busy || !draft.trim()}>Refine</button>
    </form>}
  </article></CitationDetails.Provider>;
}
