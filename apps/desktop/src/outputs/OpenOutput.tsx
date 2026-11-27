import type { Artifact, ArtifactConversationMessage, ArtifactPart, ConversationCitation, ModelMenu, OutputKind, OutputScope, OutputType, PlanPart, PlanPartEdit, SavedArtifactTurnInput } from "@gunther/contracts";
import { invoke } from "@tauri-apps/api/core";
import { MessageSquarePlus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ApiRequestError, isTurnResult, knowledgeApi } from "../api";
import { CitationDetails } from "../components/evidence/CitationButton";
import { isWebCitation } from "../components/evidence/citationKind";
import { KeyResultsStatusView, useKeyResultsStatus } from "../keyResults/KeyResultsStatus";
import { AnswerBody, verificationNotice } from "../pages/AnswerBody";
import { ArtifactMenu } from "./ArtifactMenu";
import { ArtifactMessageList } from "./ArtifactMessages";
import { artifactRequestId, EARLIER_WORK, libraryScope, scopeLabel } from "./artifactConversations";
import { AudioDurationScope } from "./AudioOverview";
import { withDeckMarker } from "./deck/marker";
import { discardOutputEdit, EditPane } from "./EditPane";
import { outputActivity } from "./liveOutput";
import { normalizeSavedOutline, OutlineEditor, outlineIssue, partEdits } from "./OutlineEditor";
import { outputIcon, resolveOutputType } from "./outputCatalog";
import { outputCitations } from "./OutputParts";
import { outputViews } from "./OutputViews";
import { ApprovedPlanDisclosure, PlanDetailReader, PlanningWorkspace, sameScope, SavedPlanPreferences, type EditFlush } from "./PlanningWorkspace";
import { prepareForPrint } from "./printReady";
import { PrintRoot } from "./PrintRoot";
import { parseSlides, withAccent, withTheme, type DeckStyle, type DeckTheme } from "./sections";
import { StudioWebSwitch } from "./StudioWebSwitch";
import { type OutputListener, type OutputTarget, type OutputTrashTarget, type SourceState, type useStudio } from "./useStudio";
import "./outputs.css";

const message = (reason: unknown) => reason instanceof Error ? reason.message : "The request could not be completed.";
const REVIEW_SLIDES = "Review rendered slides";

function earlierSiblings(element: Element) {
  const siblings: Element[] = [];
  for (let sibling = element.previousElementSibling; sibling; sibling = sibling.previousElementSibling) siblings.push(sibling);
  return siblings;
}

function useSlideStageBudget(enabled: boolean) {
  const reader = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    const article = reader.current;
    const page = article?.closest<HTMLElement>(".library-middle-page");
    if (!enabled || !article || !page) return;

    let frame: number | null = null;
    let disposed = false;
    let slides: HTMLElement | null = null;
    let observed = new Set<Element>();
    const schedule = () => {
      if (disposed || frame !== null) return;
      frame = requestAnimationFrame(() => { frame = null; measure(); });
    };
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(schedule);
    const measure = () => {
      slides = article.querySelector<HTMLElement>(":scope > .outputs-slides");
      const stage = slides?.querySelector<HTMLElement>(".outputs-stage");
      const thumbs = slides?.querySelector<HTMLElement>(".outputs-thumbs");
      const next = new Set<Element>([page]);
      if (slides && stage && thumbs) {
        for (const element of earlierSiblings(slides)) next.add(element);
        for (const element of earlierSiblings(thumbs)) if (element !== stage) next.add(element);
        next.add(thumbs);
      }
      for (const element of observed) if (!next.has(element)) resize?.unobserve(element);
      for (const element of next) if (!observed.has(element)) resize?.observe(element, { box: "border-box" });
      observed = next;

      if (!slides || !stage || !thumbs) {
        article.style.removeProperty("--stage-overhead");
        return;
      }
      if (!page.clientHeight || !thumbs.offsetHeight) return;
      const stageBounds = stage.getBoundingClientRect();
      const above = stageBounds.top - page.getBoundingClientRect().top + page.scrollTop;
      // The gap can include a fit or layout notice between the stage and the thumbnails.
      const gap = Math.max(Number.parseFloat(getComputedStyle(slides).rowGap) || 14,
        thumbs.getBoundingClientRect().top - stageBounds.bottom);
      const below = gap + thumbs.offsetHeight + 12;
      const overhead = `${Math.max(240, Math.ceil(window.innerHeight - page.clientHeight + above + below))}px`;
      if (article.style.getPropertyValue("--stage-overhead") !== overhead) article.style.setProperty("--stage-overhead", overhead);
    };
    // The lazy deck and status rows can appear after the reader mounts.
    const changes = new MutationObserver((records) => {
      if (records.some(({ target }) => target === article || target === slides)) schedule();
    });
    changes.observe(article, { childList: true, subtree: true });
    window.addEventListener("resize", schedule);
    void document.fonts?.ready.then(schedule);
    measure();
    return () => {
      disposed = true;
      if (frame !== null) cancelAnimationFrame(frame);
      resize?.disconnect();
      changes.disconnect();
      window.removeEventListener("resize", schedule);
      article.style.removeProperty("--stage-overhead");
    };
  }, [enabled]);
  return reader;
}

export interface OpenOutputProps {
  baseId: string; workspaceId: string; target: OutputTarget; studio: ReturnType<typeof useStudio>;
  scope: OutputScope; sourceState: (scope: OutputScope) => SourceState; modelMenu: ModelMenu | null;
  webAvailable: boolean; dirty: { current: boolean }; editFlush: EditFlush;
  onNotify: (message: string) => void; onCite: (artifact: Artifact, index: number) => void;
  onOpen: (target: OutputTarget) => void;
  onWideReader: (wide: boolean) => void;
  onAskChat: (question: string) => void;
  onTrash: (target: OutputTrashTarget) => void;
  onArtifactChange: (artifact: Artifact | null) => void;
  onPartChange: (index: number | null) => void;
}

function SavedOutline({ artifact, type, parts, busy, readOnly, dirty, onRebuild }: {
  artifact: Artifact; type: OutputType; busy: boolean; readOnly: boolean; dirty: { current: boolean };
  parts: ArtifactPart[] | undefined;
  onRebuild: (outline: PlanPartEdit[]) => Promise<boolean>;
}) {
  const [original, setOriginal] = useState<PlanPart[]>([]);
  const [draft, setDraft] = useState<PlanPart[] | null>(null);
  const [problem, setProblem] = useState("");
  const trigger = useRef<HTMLButtonElement>(null);
  const editor = useRef<HTMLDivElement>(null);
  const wasEditing = useRef(false);
  useEffect(() => {
    let active = true;
    void normalizeSavedOutline(artifact.id, artifact.outline, parts).then((rows) => {
      if (active) { setOriginal(rows); setProblem(""); }
    }).catch((reason: unknown) => { if (active) setProblem(message(reason)); });
    return () => { active = false; };
  }, [artifact.id, artifact.outline, parts]);
  useLayoutEffect(() => {
    if (Boolean(draft) === wasEditing.current) return;
    wasEditing.current = Boolean(draft);
    (draft ? editor.current?.querySelector("input") : trigger.current)?.focus();
  }, [draft]);
  useEffect(() => () => { dirty.current = false; }, [dirty]);
  const value = draft ?? original;
  const max = type.partLimit ?? original.length;
  const invalid = Boolean(draft && outlineIssue(draft, max));
  const cancel = () => { setDraft(null); dirty.current = false; };
  return <div className="reader-track saved-outline" data-track="prose" ref={editor}>
    {problem && <p className="output-error" role="alert">{problem}</p>}
    {value.length > 0 && <>
      <OutlineEditor value={value} onChange={(rows) => {
        setDraft(rows);
        dirty.current = JSON.stringify(partEdits(rows)) !== JSON.stringify(partEdits(original));
      }} unitLabel={type.capabilities.unitLabel} max={max} layouts={type.outlineLayouts ?? []} firstPartRole={type.firstPartRole}
        review={artifact.provenance.skill?.outlineReview} readOnly={!draft || readOnly} disabled={busy} />
      {!readOnly && <div className="plan-actions">
        {draft ? <>
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={busy} onClick={cancel}>Cancel</button>
          <button type="button" className="gx-btn gx-btn-primary gx-btn-sm" disabled={busy || invalid}
            onClick={() => void onRebuild(partEdits(draft)).then((saved) => { if (saved) cancel(); })}>Rebuild with this outline</button>
        </> : <button ref={trigger} type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={busy}
          onClick={() => setDraft(original)}>Edit outline</button>}
      </div>}
      {readOnly && draft && <div className="plan-actions">
        <p className="plan-hint">Open the latest version to rebuild its outline.</p>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={cancel}>Discard outline edits</button>
      </div>}
    </>}
  </div>;
}

export function OpenOutput({ baseId, workspaceId, target, studio, scope, sourceState, modelMenu, webAvailable,
  dirty, editFlush, onNotify, onCite, onOpen, onWideReader, onAskChat, onTrash, onArtifactChange, onPartChange }: OpenOutputProps) {
  const [picked, pickVersion] = useState(target.artifactId ?? null);
  const [opened, setOpened] = useState<Artifact | null>(null);
  const [panel, setPanel] = useState<"versions" | "outline" | "style" | null>(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState("");
  const sending = useRef(false);
  const [refineOpen, setRefineOpen] = useState(false);
  const [web, setWeb] = useState(false);
  const [focus, setFocus] = useState<SavedArtifactTurnInput["focus"]>();
  const [jump, setJump] = useState<{ index: number; nonce: number } | null>(null);
  const partInView = useRef<number | null>(null);
  const refine = useRef<HTMLTextAreaElement>(null);
  const refineTrigger = useRef<HTMLButtonElement>(null);
  const refineOrigin = useRef<{ part: number | null; trigger: HTMLButtonElement | undefined } | undefined>(undefined);
  const focusRefineOnOpen = useRef(false);
  const refineId = useId();
  const styleRun = useRef<{ running: DeckStyle; pending: DeckStyle | null } | null>(null);
  const [pendingStyle, setPendingStyle] = useState<DeckStyle | null>(null);
  const [restyling, setRestyling] = useState(false);
  const contentDirty = useRef(false);
  const preferencesDirty = useRef(false);
  const readerEdits = useMemo(() => {
    const track = (field: { current: boolean }) => ({
      get current() { return field.current; },
      set current(value: boolean) {
        field.current = value;
        dirty.current = contentDirty.current || preferencesDirty.current;
      },
    });
    return { content: track(contentDirty), preferences: track(preferencesDirty) };
  }, [dirty]);
  const conversationId = target.conversationId ?? (opened ? studio.owner(opened) : null);
  const conversation = conversationId ? studio.details[conversationId] : undefined;
  const address = conversationId && target.planId ? { conversationId, planId: target.planId } : null;
  const local = address ? studio.planning.locals[address.planId] : undefined;
  const planWork = address && conversation?.works.find((work) => work.plan?.planId === address.planId || (!work.plan && work.key === address.planId));
  const planSaved = address ? studio.savedForPlan(address) : undefined;
  const lineageId = target.lineageId ?? opened?.lineageId ?? planSaved?.lineageId ??
    (address ? planWork?.versionId ? planWork.key : null : conversation?.lineageId);
  const versions = studio.artifacts.filter((item) => lineageId ? item.lineageId === lineageId
    : !address && target.conversationId && studio.owner(item) === target.conversationId).sort((a, b) => b.versionNumber - a.versionNumber);
  const head = versions[0];
  const shownId = picked ?? head?.id ?? planSaved?.id ?? planWork?.versionId;
  const unavailable = studio.artifactsLoaded && shownId
    && !studio.artifacts.some((item) => item.id === shownId) && planWork?.versionId !== shownId;
  const artifact = opened?.id === shownId && !unavailable ? opened : null;
  const savedSlides = artifact?.kind === "slides" && !editing;
  const reader = useSlideStageBudget(savedSlides);
  const keyResults = useKeyResultsStatus(baseId, artifact?.keyResults, true);
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
  const savedWork = artifact && conversation?.works.find((item) => item.key === artifact.lineageId && item.versionId !== null);
  const pending = savedWork?.pending !== undefined ? savedWork.pending : savedWork?.plan ? savedWork.plan.pending :
    artifact && conversation?.active === artifact.lineageId ? conversation.pending : null;
  const pendingId = pending?.messageId;
  const currentPendingId = useRef(pendingId);
  currentPendingId.current = pendingId;
  const otherQuestions = new Set(conversation?.works.map((work) => (work.pending ?? work.plan?.pending)?.messageId));
  otherQuestions.add(conversation?.pending?.messageId);
  otherQuestions.delete(pendingId);
  const history = conversation?.messages.filter((item) => !otherQuestions.has(item.id)) ?? [];
  const last = history.at(-1);
  const onDirtyChange = useCallback((value: boolean) => { readerEdits.content.current = value; }, [readerEdits]);
  const onPart = useCallback((index: number | null) => { partInView.current = index; onPartChange(index); }, [onPartChange]);

  useLayoutEffect(() => {
    partInView.current = null;
    refineOrigin.current = undefined;
    onPartChange(null);
  }, [baseId, shownId, workspaceId, onPartChange]);
  useLayoutEffect(() => {
    if (refineOpen && focusRefineOnOpen.current && refine.current) {
      focusRefineOnOpen.current = false;
      refine.current.focus();
    }
  }, [refineOpen]);
  useEffect(() => { setError(""); setFocus(undefined); }, [baseId, shownId, workspaceId]);
  useEffect(() => { if (pendingId) setRefineOpen(true); }, [pendingId]);
  useEffect(() => { onArtifactChange(artifact); }, [artifact, onArtifactChange]);
  const { read, follow, reload } = studio;
  const openingLocal = Boolean(local && !planWork);
  const [completedRead, setCompletedRead] = useState<{ conversationId: string; read: typeof read } | null>(null);
  useEffect(() => {
    if (!conversationId || openingLocal) return;
    let active = true;
    void read(conversationId).then(() => { if (active) follow(conversationId); })
      .catch((reason) => { if (active) setError(message(reason)); })
      .finally(() => { if (active) setCompletedRead({ conversationId, read }); });
    return () => { active = false; };
  }, [conversationId, openingLocal, read, follow]);
  useEffect(() => () => { styleRun.current = null; }, []);

  const send = async (turn: Omit<SavedArtifactTurnInput, "clientRequestId">) => {
    if (!artifact || !conversationId || !changeable || busy || editing || sending.current) return null;
    sending.current = true;
    setUpdating(true);
    setError("");
    try {
      if (editFlush.current && !await editFlush.current()) return null;
      if ((turn.kind === "answer" || turn.kind === "start") && (!pendingId || pendingId !== currentPendingId.current)) {
        setError("The pending question changed. Review it before sending an answer.");
        return null;
      }
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
      return result;
    } catch (reason) {
      setError(message(reason));
      return null;
    } finally {
      sending.current = false;
      setUpdating(false);
    }
  };
  const refineOutput = (text: string, visiblePart = partInView.current) => {
    if (!text.trim() || !artifact || !changeable || busy || editing) return;
    const part = latest ? focus ?? (visiblePart !== null ? { section: visiblePart + 1, selected: false } : undefined) : undefined;
    void send({ kind: pending ? "answer" : "message", text, ...(part ? { focus: part } : {}),
      ...(webAvailable && web ? { web: true } : {}) }).then((result) => {
      if (result) {
        setDraft((current) => current === text ? "" : current);
        setFocus(undefined);
      }
    });
  };
  const refineFromForm = (text: string) => refineOutput(text, artifact?.kind === "slides" ? undefined : refineOrigin.current?.part);
  const retry = (answer: ArtifactConversationMessage) => {
    const earlier = conversation?.messages.find((item) => item.id === answer.payload.to) ??
      conversation?.messages.slice(0, conversation.messages.indexOf(answer)).reverse().find((item) => item.role === "user");
    if (!earlier || earlier.kind === "plan" || earlier.kind === "approve" || earlier.kind === "build") return;
    const { focus: requested, rebuild, model, effort, web, targetLineageId } = earlier.payload as Partial<SavedArtifactTurnInput>;
    if (targetLineageId && targetLineageId !== artifact?.lineageId) return;
    if (earlier.kind === "answer" && (!pending || (!targetLineageId && conversation?.works.length !== 1))) return;
    void send({ kind: earlier.kind as NonNullable<SavedArtifactTurnInput["kind"]>, text: earlier.text, ...(requested ? { focus: requested } : {}),
      ...(rebuild ? { rebuild } : {}), ...(model ? { model } : {}), ...(effort ? { effort } : {}), ...(web ? { web } : {}) });
  };
  const updateScope = async () => {
    if (!artifact || !conversationId || sourceState(scope) !== "ready" || busy) return;
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
    const reviewed = await studio.direct(conversationId, artifact, REVIEW_SLIDES, (hear, signal) =>
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
  const print = async (version: Artifact) => {
    const done = await prepareForPrint(version.id);
    try { if ("__TAURI_INTERNALS__" in window && /Mac/i.test(navigator.userAgent)) await invoke("print_output"); else window.print(); }
    finally { done(); }
  };
  const download = (version: Artifact) => {
    const url = URL.createObjectURL(new Blob([version.content], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url; link.download = `${baseId}-${version.kind}-v${version.versionNumber}.md`; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const openRefine = (trigger?: HTMLButtonElement) => {
    refineOrigin.current = { part: partInView.current, trigger };
    if (refine.current?.form?.hidden) {
      focusRefineOnOpen.current = true;
      setRefineOpen(true);
    } else refine.current?.focus();
  };
  const closeRefine = (trigger?: HTMLButtonElement) => {
    setRefineOpen(false);
    refineOrigin.current = undefined;
    (trigger?.isConnected ? trigger : refineTrigger.current)?.focus();
  };
  const point = (index: number, quote?: string) => {
    const active = document.activeElement;
    const trigger = !quote && active instanceof HTMLButtonElement && active.matches(".outputs-ask-section") ? active : undefined;
    setFocus({ section: index + 1, selected: true, ...(quote ? { quote } : {}) });
    openRefine(trigger);
  };
  const problem = error || failure?.message || (unavailable ? "This output is unavailable." : "");
  const targetType = studio.types.find((item) => item.id === (target.typeId ?? planWork?.plan?.options.type ??
    planWork?.options?.type ?? local?.setup.type));
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
    if (!discardOutputEdit(readerEdits.content)) return false;
    setEditing(false); pickVersion(id); setJump(null);
    return true;
  };
  const choosePanel = (next: typeof panel) => setPanel((current) => current === next ? null : next);
  const kind = artifact?.kind ?? targetType?.shape ?? conversation?.shape ?? "report";
  const type = artifact ? resolveOutputType(studio.types, artifact) : undefined;
  const controls = type ?? studio.types.find((item) => item.shape === kind);
  const refineInHeader = kind !== "slides";
  const Icon = outputIcon(artifact ? controls : targetType);
  const { Reader, Review, headingTrack, wide = false, printLabel, savedStage, file } = outputViews[kind];
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
      if (!alive) return;
      if (version.id !== shownId || (target.lineageId && target.lineageId !== version.lineageId)) {
        setError("This version could not be verified.");
        return;
      }
      setOpened(version);
    }).catch((reason) => { if (alive) setError(message(reason)); });
    return () => { alive = false; };
  }, [baseId, shownId, target.lineageId, workspaceId, working]);
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
  const capabilities = controls?.capabilities;
  const unitLabel = (kind: OutputKind | null) => studio.types.find((item) => item.shape === kind)?.capabilities?.unitLabel ?? "part";
  const unit = capabilities?.unitLabel ?? "part";
  const outline = controls?.capabilities.outline && Boolean(artifact?.outline.length);
  const formats = capabilities?.exports ?? [];
  const printAction = formats.includes("pdf") ? "Export PDF" : printLabel;
  const styling = capabilities?.design && panel === "style" && changeable;
  const showPlanning = Boolean(address && targetType && !artifact && !shownId);
  useLayoutEffect(() => onWideReader(showPlanning ? false : wide), [showPlanning, wide, onWideReader]);
  if (showPlanning && address && targetType) {
    const planJob = job && (!job.planId || job.planId === address.planId)
      && (!job.live.plan || job.live.plan.plan.planId === address.planId);
    const planActivity = activity?.open?.view === "library" && activity.open.tab === "outputs"
      && activity.open.planId === address.planId ? activity : undefined;
    return <PlanDetailReader.Provider value={read}>
      {problem && <p className="output-error reader-track" data-track="prose" role="alert">{problem}</p>}
      <PlanningWorkspace key={address.planId} address={address} type={targetType} controller={studio.planning}
        detail={conversation ?? null} work={planWork ?? null}
        detailReadComplete={completedRead?.conversationId === address.conversationId && completedRead.read === read}
        sourceState={sourceState} currentScope={scope}
        modelMenu={modelMenu} webAvailable={webAvailable} busy={Boolean(planJob || planActivity)}
        stage={planJob ? outputActivity(job.live) : planActivity?.detail || planActivity?.title || null} onStop={stop}
        dirty={dirty} editFlush={editFlush} />
    </PlanDetailReader.Provider>;
  }
  const canRefine = changeable && !editing;
  const savedPreferences = canRefine && conversation && savedWork && <PlanDetailReader.Provider value={read}>
    <SavedPlanPreferences key={savedWork.key} detail={conversation} work={savedWork} controller={studio.planning}
      disabled={busy} dirty={readerEdits.preferences} editFlush={editFlush} />
  </PlanDetailReader.Provider>;
  const keyResultsAboveDeck = savedSlides && keyResults.needsAttention;
  const keyResultsInDisclosures = keyResults.visibleStatus && !keyResultsAboveDeck;
  const approvedPlan = artifact?.provenance.creationPlan && controls && <ApprovedPlanDisclosure value={artifact.provenance.creationPlan}
    type={controls} currentTitle={artifact.title} />;
  const disclosures = (savedPreferences || approvedPlan || keyResultsInDisclosures) &&
    <div className="reader-track output-reader-disclosures" data-track={kind === "slides" ? "deck" : "prose"}>
      {refineInHeader && savedPreferences}
      {approvedPlan}
      {keyResultsInDisclosures && <div><KeyResultsStatusView status={keyResults} /></div>}
      {!refineInHeader && savedPreferences}
    </div>;
  const visualReview = artifact?.provenance.skill?.visualReview;
  const reviewNeedsAttention = visualReview && (visualReview.findings.length > 0
    || (visualReview.status !== "clean" && visualReview.status !== "repaired"));
  const reviewing = (!runVersion || workingOnVersion)
    && (job?.title === REVIEW_SLIDES || job?.live.running.some((step) => step.kind === "stage" && step.id === "visual_review"));
  const reviewAboveDeck = !savedSlides || reviewNeedsAttention || reviewing;
  const review = artifact && Review && capabilities?.design && <Review key={artifact.id} artifact={artifact} baseId={baseId}
    workspaceId={workspaceId} disabled={busy || Boolean(activity) || !latest || !changeable}
    onReview={() => void reviewSlides()} onFollowUp={refineOutput} />;
  const openOutline = () => {
    if (panel === "outline" && !discardOutputEdit(readerEdits.content)) return;
    if (panel !== "outline" && artifact) pickVersion(artifact.id);
    choosePanel("outline");
  };
  const leaveOutline = (next: () => void) => {
    if (panel !== "outline" || discardOutputEdit(readerEdits.content)) next();
  };
  const allowPoint = canRefine && latest;
  const actions = artifact && <ArtifactMenu label="Output actions" items={[
        { label: "Versions", onSelect: () => leaveOutline(() => choosePanel("versions")) },
        ...(outline ? [{ label: "Outline", disabled: editing, onSelect: openOutline }] : []),
        { label: "Edit", disabled: busy || !latest || !changeable, onSelect: async () => {
          if (editFlush.current && !await editFlush.current()) return;
          leaveOutline(() => { pickVersion(artifact.id); setEditing(true); setPanel(null); });
        } },
        { label: "Check again", disabled: busy || !latest || !changeable, onSelect: () => void check() },
        ...(capabilities?.design ? [{ label: "Style", disabled: editing, onSelect: () => leaveOutline(() => choosePanel("style")) }] : []),
        ...(printAction ? [{ label: printAction, divider: true, disabled: editing, onSelect: () => { setError(""); void withSavedVersion(print); } }] : []),
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
      ]} />;

  return <CitationDetails.Provider value={artifact?.citations ?? []}><article ref={reader}
    className="output-reader" data-kind={kind} data-mode={editing ? "edit" : "read"} aria-label="Open output">
    <header className="output-reader-heading reader-track" data-track={editing ? "full" : headingTrack}>
      {refineInHeader && (artifact || targetType) && <div className="output-reader-meta">
        <Icon size={16} strokeWidth={1.75} aria-hidden="true" />
        <span>{artifact?.outputType || targetType?.label}</span>
        {artifact && <span>Version {artifact.versionNumber}</span>}
      </div>}
      <h1>{title}</h1>
      <div className="output-reader-actions">
        {refineInHeader && canRefine && <button ref={refineTrigger} type="button" className="gx-btn gx-btn-quiet gx-btn-sm" aria-label="Refine output"
          aria-controls={refineId} aria-expanded={refineOpen} onClick={(event) => {
            if (refineOpen) closeRefine(event.currentTarget);
            else openRefine(event.currentTarget);
          }}>
          <MessageSquarePlus size={16} strokeWidth={1.75} aria-hidden="true" />Refine
        </button>}
        {actions}
      </div>
    </header>
    {problem && <p role="alert" className="output-error reader-track" data-track="prose">{problem}</p>}
    {!savedSlides && disclosures}
    {(job || activity) && <div className="output-run reader-track" data-track="prose" role="status">
      <span>{job ? outputActivity(job.live) : activity?.detail || activity?.title}</span>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={job?.stopping} onClick={stop}>Stop</button>
    </div>}
    {keyResultsAboveDeck && <div className="reader-track" data-track="deck"><KeyResultsStatusView status={keyResults} /></div>}
    {job?.live.reply && <div className="reader-track" data-track="prose"><AnswerBody content={job.live.reply} numbers={[]} /></div>}
    {!artifact && !job && !activity && !problem && <p className="reader-track" data-track="prose">
      {conversation?.lastTurn?.error || (shownId ? "Loading version…" : "Waiting for the output…")}
    </p>}
    {artifact && <AudioDurationScope artifact={artifact}>
      {panel === "versions" && <ul className="output-versions reader-track" data-track="prose" aria-label="Versions">{versions.map((version) => <li key={version.id}>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" aria-current={version.id === artifact.id ? "page" : undefined} onClick={() => switchVersion(version.id)}>Version {version.versionNumber}</button>
      </li>)}</ul>}
      {panel === "outline" && outline && controls && <SavedOutline key={artifact.id} artifact={artifact} type={controls}
        parts={savedWork && savedWork.versionId === artifact.id ? savedWork.parts : undefined}
        busy={busy} readOnly={!changeable || !latest || editing} dirty={readerEdits.content} onRebuild={async (outline) => {
          const result = await send({ kind: "rebuild", rebuild: { artifactId: artifact.id, outline } });
          return Boolean(result && "outcome" in result && result.artifact);
        }} />}
      {editing ? <EditPane key={artifact.id} artifact={artifact} type={type} saving={saving} onSave={(content) => void save(content)}
        onCancel={() => { if (discardOutputEdit(readerEdits.content)) { setEditing(false); pickVersion(null); } }} onCite={cite} onDirtyChange={onDirtyChange} />
        : <>
          {reviewAboveDeck && review}
          <Reader artifact={artifact} onCite={cite} onPart={onPart} onAskChat={onAskChat}
            onAsk={allowPoint ? point : undefined} goTo={jump} changed={changed} onNotice={onNotify}
            type={type} presentation="read" work={{ working, disabled: busy || Boolean(activity), run: runReaderAction, voices: controls?.voices }}
            design={styling ? {
              onAccent: (accent) => void save(withAccent(artifact.content, accent)),
              onTheme: (theme) => void save(withTheme(artifact.content, theme)),
              onDeckTheme: (theme: DeckTheme) => void save(withDeckMarker(artifact.content, { theme })),
              onDeckStyle: (style) => void restyle(style),
              accentDisabled: busy || !latest, deckStyleDisabled: (!restyling && busy) || !latest, pendingDeckStyle: pendingStyle,
            } : undefined} />
          {savedSlides && disclosures}
          {!reviewAboveDeck && review}
        </>}
      {printAction && <PrintRoot artifact={artifact} type={type} />}
    </AudioDurationScope>}
    {history.length > 0 && <details className="output-history reader-track" data-track="prose">
      <summary>Change history</summary>
      <ArtifactMessageList messages={history} pending={pending} history={studio.artifacts}
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
    {!job && last && (last.kind === "reply" || last.kind === "answer") && <div className="reader-track" data-track="prose">
      <AnswerBody content={last.text} {...(artifact ? outputCitations({ artifact, onCite: cite }) : { numbers: [] })} />
    </div>}
    {!job && last?.kind === "paused" && !last.payload.plan && <div className="reader-track" data-track="prose">
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => void send({ kind: "continue" })}>Continue</button>
    </div>}
    {canRefine && <form className="output-refine reader-track" data-track="prose" id={refineId}
      aria-label={refineInHeader ? "Refine output" : undefined} hidden={refineInHeader && !refineOpen}
      onKeyDown={refineInHeader ? (event) => {
        if (event.key === "Escape") { event.stopPropagation(); closeRefine(refineOrigin.current?.trigger); }
      } : undefined} onSubmit={(event) => { event.preventDefault(); refineFromForm(draft); }}>
      {latest && !sameScope(useScope, scope) && <div className="output-scope">
        <span>Using {useScope.mode === "selection" ? "this output’s " : ""}{scopeLabel(useScope)}</span>
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={busy || sourceState(scope) !== "ready"} onClick={() => void updateScope()}>
          {scope.mode === "library" ? "Update to all sources" : `Update to current ${scopeLabel(scope)}`}
        </button>
      </div>}
      {pending && <div className="output-question"><p>{pending.question}</p>{pending.options.map((option) => <button key={option} type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={busy} onClick={() => refineFromForm(option)}>{option}</button>)}</div>}
      {focus && <small>About {focus.quote ? `“${focus.quote}”` : `${unit} ${focus.section}`}
        <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => setFocus(undefined)}>Clear</button></small>}
      <label>Refine<textarea ref={refine} value={draft} maxLength={20_000} rows={2} onChange={(event) => setDraft(event.target.value)} /></label>
      <StudioWebSwitch available={webAvailable} checked={web} onChange={setWeb} />
      <button type="submit" className="gx-btn gx-btn-primary gx-btn-sm" disabled={busy || !draft.trim()}>Refine</button>
    </form>}
  </article></CitationDetails.Provider>;
}
