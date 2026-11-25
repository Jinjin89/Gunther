import type { Artifact, ContentsSource, ConversationBriefInput, ConversationCitation, ConversationContext, KnowledgeSession, SessionMessage } from "@gunther/contracts";
import { useMemo, useRef, useState } from "react";
import { EvidenceDetail } from "../components/evidence/EvidenceDetail";
import { isWebCitation } from "../components/evidence/citationKind";
import { WebFindActions } from "../components/evidence/WebFindActions";
import { kindCounts } from "../contents/contentsTree";
import { AnswerTrace } from "../developer/AnswerTrace";
import { ResearchPlan, noSourcesNeeded, savedResearchPlan, verificationNotice } from "./AnswerBody";
import { BriefPanel, type BriefCitation } from "./ConversationBrief";
import { claimVerdict, citationMeta, CoverageMeter, EvidenceDisclosure, SourceKindMark, StepTimeline } from "./EvidencePrimitives";
import { CitedHeader, SearchEvidence } from "./EvidenceSearch";
import { OutputEvidence } from "./OutputEvidence";
import { SourceEvidence } from "./SourceEvidence";
import { useImportWebFind, webFindOf } from "./useImportWebFind";
import "./evidencePane.css";

export interface EvidenceTarget {
  contextKey: string;
  citations: ConversationCitation[];
  index: number;
  passageSelector?: boolean;
}
export interface TraceTarget { kind: "trace"; contextKey: string; sessionId: string; messageId: string }

const identity = (citation: ConversationCitation) => citation.ref != null ? `ref:${citation.ref}` : `id:${citation.id}`;

function answerCoverage({ coverage, checked }: ConversationContext) {
  const claims = coverage?.claims ?? [];
  const counts = { verified: 0, warning: 0, danger: 0, neutral: 0 };
  const flags = new Map<string, number>();
  const marks = new Map<number, { label: string; tone: "warning" | "danger" }>();
  for (const claim of claims) {
    const verdict = claimVerdict(claim.verdict);
    counts[verdict.tone] += 1;
    if ("label" in verdict) {
      flags.set(verdict.tally, (flags.get(verdict.tally) ?? 0) + 1);
      if (verdict.markCitation) for (const ref of claim.refs) {
        if (verdict.tone === "danger" || !marks.has(ref)) marks.set(ref, verdict);
      }
    }
  }
  const segments = (["verified", "warning", "danger", "neutral"] as const).map((tone) => ({ tone, count: counts[tone] }));
  const total = claims.length - counts.neutral;
  const unchecked = coverage?.status === "skipped" ? "Source check skipped" : "Source check not recorded";
  let summary = verificationNotice(coverage) ?? (checked || coverage?.status === "complete" ? "Sources checked" : unchecked);
  if (total > 0) {
    const verified = counts.verified === total ? (total === 1 ? "1" : `All ${total}`) : `${counts.verified} of ${total}`;
    summary = [`${verified} ${total === 1 ? "claim" : "claims"} verified`,
      ...[...flags].map(([label, count]) => `${count} ${label}`)].join(" · ");
  } else if (claims.length > 0) {
    summary = "No factual claims to check";
  }
  return { summary, segments, claims, marks };
}

export function EvidencePane({ session, selected, chatVisible, sources, selectedSourceIds, allSelected, webEnabled, onFocusSources, detail,
  output, partInView, baseId, workspaceId, sourceId, onSourceCount, brief, traceEnabled, onOpenTrace, onNotify,
  onOpen, onStep, onBack, onOpenSource, onOpenLibrarySource, onOriginalPdfChange }: {
  session: KnowledgeSession | null;
  selected: SessionMessage | null;
  chatVisible: boolean;
  sources: readonly ContentsSource[];
  selectedSourceIds: readonly string[];
  allSelected: boolean;
  webEnabled: boolean;
  onFocusSources: () => void;
  detail: EvidenceTarget | TraceTarget | null;
  output?: Artifact | null;
  partInView?: number | null;
  baseId?: string;
  workspaceId?: string | null;
  sourceId?: string | null;
  onSourceCount: (id: string, count: number) => void;
  brief?: {
    updating: boolean;
    readOnly: boolean;
    save: (input: ConversationBriefInput) => Promise<void>;
    retry: () => void;
    ask?: ((text: string) => void) | undefined;
  } | undefined;
  traceEnabled?: boolean;
  onOpenTrace?: (() => void) | undefined;
  onNotify?: ((message: string) => void) | undefined;
  onOpen: (citations: ConversationCitation[], index: number, passageSelector?: boolean) => void;
  onStep: (index: number) => void;
  onBack: () => void;
  onOpenSource: (id: string) => void;
  onOpenLibrarySource?: ((id: string) => void) | undefined;
  onOriginalPdfChange: (original: boolean) => void;
}) {
  const citations = selected?.citations ?? [];
  const briefCitations = useMemo(() => {
    const targets = new Map<number, BriefCitation>();
    const remember = (message: SessionMessage) => message.citations.forEach((citation, index) => {
      if (citation.ref != null && !targets.has(citation.ref)) targets.set(citation.ref, { citations: message.citations, index });
    });
    if (selected?.role === "assistant") remember(selected);
    for (const message of session?.messages ?? []) {
      if (message.role === "assistant") remember(message);
    }
    return targets;
  }, [session?.messages, selected]);
  const [pendingBriefs, setPendingBriefs] = useState(() => new Map<string, ConversationBriefInput>());
  const briefWrites = useRef(new Map<string, Promise<void>>());
  const saveBrief = (sessionId: string, write: (input: ConversationBriefInput) => Promise<void>, input: ConversationBriefInput) => {
    // Each save replaces the whole brief. Show the pending edit immediately and send edits in order.
    setPendingBriefs((current) => new Map(current).set(sessionId, input));
    const save = () => write(input);
    const request = (briefWrites.current.get(sessionId) ?? Promise.resolve()).then(save, save);
    briefWrites.current.set(sessionId, request);
    const finish = () => {
      if (briefWrites.current.get(sessionId) !== request) return;
      briefWrites.current.delete(sessionId);
      setPendingBriefs((current) => {
        const next = new Map(current);
        next.delete(sessionId);
        return next;
      });
    };
    void request.then(finish, finish);
  };
  const savedCitations = useMemo(() => output?.citations ?? session?.messages.flatMap((message) => message.citations) ?? [], [output, session]);
  const finds = useMemo(() => [
    ...savedCitations.filter(isWebCitation).map(webFindOf),
    ...(output ? output.provenance.searched ?? [] : session?.messages.flatMap((message) => message.context.searched ?? []) ?? [])
      .filter((hit) => hit.kind === "web"),
  ], [output, savedCitations, session]);
  const imports = useImportWebFind({ baseId, workspaceId, finds, onNotify, onOpenSource: onOpenLibrarySource });
  const earlier = useMemo(() => {
    if (!session || !selected) return [];
    const stop = session.messages.findIndex((message) => message.id === selected.id);
    if (stop < 0) return [];
    const seen = new Set(selected.citations.map(identity));
    const previous: ConversationCitation[] = [];
    for (const message of session.messages.slice(0, stop)) {
      if (message.role !== "assistant") continue;
      for (const citation of message.citations) {
        const key = identity(citation);
        if (!seen.has(key)) { seen.add(key); previous.push(citation); }
      }
    }
    return previous;
  }, [session, selected]);
  if (detail && "kind" in detail) return <div className="evidence-trace" aria-label="Trace">
    <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={onBack}>← Back to evidence</button>
    <AnswerTrace sessionId={detail.sessionId} messageId={detail.messageId} onCopy={(label, value) => void navigator.clipboard.writeText(value)
      .then(() => onNotify?.(`${label} copied.`), () => onNotify?.(`${label} could not be copied.`))} />
  </div>;
  const saved = detail?.citations[detail.index];
  const citation = saved && isWebCitation(saved) ? savedCitations.find((item) => item.id === saved.id) ?? saved : saved;
  if (detail && citation) return <EvidenceDetail citation={citation} index={sourceId && citation.ref != null ? citation.ref - 1 : detail.index}
    passages={detail.passageSelector ? detail.citations : undefined} onPassage={detail.passageSelector ? onStep : undefined}
    onBack={onBack} position={detail.index} total={detail.citations.length}
    onPrevious={detail.index > 0 ? () => onStep(detail.index - 1) : undefined}
    onNext={detail.index + 1 < detail.citations.length ? () => onStep(detail.index + 1) : undefined}
    onOpenSource={onOpenSource} onOriginalPdfChange={onOriginalPdfChange} webImports={imports} />;

  if (output) return <OutputEvidence key={output.id} artifact={output} sources={sources} partInView={partInView ?? null} imports={imports} onOpen={onOpen} />;
  const source = sourceId ? sources.find((item) => item.id === sourceId) : undefined;
  if (source && baseId) return <SourceEvidence key={`${baseId}:${source.id}`} baseId={baseId} source={source}
    onCount={onSourceCount} onInspect={(passages) => onOpen(passages, 0, true)} />;
  if (!chatVisible) return <div className="evidence-overview" aria-label="Evidence overview"><p>Loading evidence…</p></div>;
  const pendingBrief = session && pendingBriefs.get(session.id);
  const briefPanel = session && brief && <BriefPanel key={session.id}
    brief={pendingBrief ? { edited: [], error: null, ...session.brief, ...pendingBrief } : session.brief}
    selectedMessageId={selected?.id}
    updating={brief.updating} readOnly={brief.readOnly} hasMessages={session.messages.length > 0}
    citations={briefCitations} onSave={(input) => saveBrief(session.id, brief.save, input)} onRetry={brief.retry}
    onOpenCitation={onOpen} onAsk={brief.ask} />;
  if (!selected) {
    const chosen = new Set(selectedSourceIds);
    const scopedSources = sources.filter((item) => allSelected || chosen.has(item.id));
    const kinds = kindCounts(scopedSources).map((kind) => kind.label).join(" · ");
    return <div className="evidence-overview evidence-bands" aria-label="Evidence overview">
      <section className="evidence-context" aria-label="Scope">
        <header><h3>Scope</h3><button type="button" className="gx-link" onClick={onFocusSources}>Change</button></header>
        <div className="evidence-scope-summary">
          <span className="evidence-scope-total">{allSelected ? `All ${sources.length} ${sources.length === 1 ? "source" : "sources"}` : `${scopedSources.length} of ${sources.length} selected`}</span>
          <span className="evidence-pill">Web {webEnabled ? "on" : "off"}</span>
        </div>
        {kinds && <p>{kinds}</p>}
        <p>Ask a question to inspect its sources and searches here.</p>
      </section>
      {briefPanel}
    </div>;
  }
  const context = selected.context;
  const steps = context.steps ?? [];
  const searches = steps.filter((step) => step.tool === "search_library" || step.tool === "search_web").length;
  const process = steps.length > 0 || savedResearchPlan(context) !== null;
  const processSummary = [searches > 0 && `${searches} ${searches === 1 ? "search" : "searches"}`,
    steps.length > 0 && `${steps.length} ${steps.length === 1 ? "step" : "steps"}`].filter(Boolean).join(" · ");
  const noSources = !citations.length && noSourcesNeeded(context);
  const coverage = answerCoverage(context);
  const card = (item: ConversationCitation, index: number, list: ConversationCitation[], numbered: boolean) => {
    const meta = citationMeta(item, sources);
    // Cards show the answer's numbering. The Checker records references to the conversation pool.
    const mark = numbered ? coverage.marks.get(item.ref ?? index + 1) : undefined;
    const key = `${identity(item)}:${index}`;
    const quote = item.supportText || item.quote;
    const content = <>
      {numbered && <span className="evidence-citation-number">[{index + 1}]</span>}
      <SourceKindMark {...meta} />
      <span className="evidence-citation-copy">
        <strong>{item.sourceTitle}</strong>
        <span className="evidence-citation-meta">
          <span className="evidence-meta">{[meta.kindLabel, meta.locator].filter(Boolean).join(" · ")}</span>
          {mark && <span className={`evidence-pill is-${mark.tone}`}>{mark.label}</span>}
        </span>
        {quote && <span className="evidence-card-quote">{quote}</span>}
      </span>
    </>;
    return isWebCitation(item) ? <article className="evidence-card evidence-source-group" key={key}>
      <button type="button" className="evidence-card-open evidence-citation" onClick={() => onOpen(list, index)}>{content}</button>
      <WebFindActions find={webFindOf(item)} imports={imports} />
    </article> : <button type="button" className="evidence-card evidence-citation" key={key} onClick={() => onOpen(list, index)}>{content}</button>;
  };
  return <div className="evidence-overview evidence-bands" aria-label="Evidence overview">
    {briefPanel}
    {(!noSources || process || context.coverage || context.checked) && <section className="evidence-context" aria-label="Answer check">
      <header>
        <h3>Answer check</h3>
        {traceEnabled && onOpenTrace && <button type="button" className="gx-link" aria-label="View trace" onClick={onOpenTrace}>Trace ↗</button>}
      </header>
      <CoverageMeter summary={coverage.summary} segments={coverage.segments} claims={coverage.claims} />
      {process && <EvidenceDisclosure title="Process" subtitle={processSummary}>
        <StepTimeline steps={steps} />
        <ResearchPlan context={context} />
      </EvidenceDisclosure>}
    </section>}
    <section className="evidence-cited" aria-label="Cited sources">
      <CitedHeader citations={citations} imports={imports} />
      {citations.length ? citations.map((item, index) => card(item, index, citations, true))
        : <p>{noSources ? "No sources needed" : "Nothing cited"}</p>}
    </section>
    <SearchEvidence searched={context.searched} keyResultsUsed={context.keyResultsUsed}
      citations={citations} imports={imports} onOpen={onOpen} />
    {earlier.length > 0 && <EvidenceDisclosure title="Earlier in this conversation" count={earlier.length}>
      {earlier.map((item, index) => card(item, index, earlier, false))}
    </EvidenceDisclosure>}
  </div>;
}
