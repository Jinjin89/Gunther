import type { Artifact, ContentsSource, ConversationBriefInput, ConversationCitation, KnowledgeSession, SessionMessage } from "@gunther/contracts";
import { useMemo } from "react";
import { EvidenceDetail, hostOf } from "../components/evidence/EvidenceDetail";
import { isWebCitation } from "../components/evidence/citationKind";
import { AnswerTrace } from "../developer/AnswerTrace";
import { AgentSteps, ResearchPlan, noSourcesNeeded } from "./AnswerBody";
import { BriefPanel, hasBriefToShow } from "./ConversationBrief";
import { OutputEvidence } from "./OutputEvidence";
import { SourceEvidence } from "./SourceEvidence";
import "./evidencePane.css";

export interface EvidenceTarget {
  contextKey: string;
  citations: ConversationCitation[];
  index: number;
  passageSelector?: boolean;
}
export interface TraceTarget { kind: "trace"; contextKey: string; sessionId: string; messageId: string }

const identity = (citation: ConversationCitation) => citation.ref != null ? `ref:${citation.ref}` : `id:${citation.id}`;

export function EvidencePane({ session, selected, chatVisible, sources, selectedCount, allSelected, webEnabled, detail,
  output, partInView, baseId, sourceId, onSourceCount, brief, traceEnabled, onOpenTrace, onNotify,
  onOpen, onStep, onBack, onOpenSource, onOriginalPdfChange }: {
  session: KnowledgeSession | null;
  selected: SessionMessage | null;
  chatVisible: boolean;
  sources: readonly ContentsSource[];
  selectedCount: number;
  allSelected: boolean;
  webEnabled: boolean;
  detail: EvidenceTarget | TraceTarget | null;
  output?: Artifact | null;
  partInView?: number | null;
  baseId?: string;
  sourceId?: string | null;
  onSourceCount: (id: string, count: number) => void;
  brief?: { updating: boolean; readOnly: boolean; save: (input: ConversationBriefInput) => void; retry: () => void } | undefined;
  traceEnabled?: boolean;
  onOpenTrace?: (() => void) | undefined;
  onNotify?: ((message: string) => void) | undefined;
  onOpen: (citations: ConversationCitation[], index: number, passageSelector?: boolean) => void;
  onStep: (index: number) => void;
  onBack: () => void;
  onOpenSource: (id: string) => void;
  onOriginalPdfChange: (original: boolean) => void;
}) {
  const citations = selected?.citations ?? [];
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
  const citation = detail?.citations[detail.index];
  if (detail && citation) return <EvidenceDetail citation={citation} index={sourceId && citation.ref != null ? citation.ref - 1 : detail.index}
    passages={detail.passageSelector ? detail.citations : undefined} onPassage={detail.passageSelector ? onStep : undefined}
    onBack={onBack} position={detail.index} total={detail.citations.length}
    onPrevious={detail.index > 0 ? () => onStep(detail.index - 1) : undefined}
    onNext={detail.index + 1 < detail.citations.length ? () => onStep(detail.index + 1) : undefined}
    onOpenSource={onOpenSource} onOriginalPdfChange={onOriginalPdfChange} />;

  const card = (item: ConversationCitation, index: number, list: ConversationCitation[], numbered: boolean) => {
    const kind = isWebCitation(item) ? "Web" : sources.find((source) => source.id === item.sourceId)?.kind === "paper" ? "Paper" : "Library";
    const locator = item.locator || (isWebCitation(item) ? hostOf(item.url) : "");
    return <button type="button" className="evidence-card" key={`${identity(item)}:${index}`} onClick={() => onOpen(list, index)}>
      <span className="evidence-card-meta">{numbered && <strong>[{index + 1}]</strong>}<small>{kind}</small></span>
      <strong>{item.sourceTitle}</strong>
      {locator && <small>{locator}</small>}
      {(item.supportText || item.quote) && <span className="evidence-card-quote">{item.supportText || item.quote}</span>}
    </button>;
  };
  if (output) return <OutputEvidence key={output.id} artifact={output} partInView={partInView ?? null} onOpen={onOpen} />;
  if (sourceId && baseId) return <SourceEvidence key={`${baseId}:${sourceId}`} baseId={baseId} sourceId={sourceId}
    onCount={onSourceCount} onInspect={(passages) => onOpen(passages, 0, true)} />;
  if (!chatVisible) return <div className="evidence-overview" aria-label="Evidence overview"><p>Loading evidence…</p></div>;
  const briefPanel = session && brief && hasBriefToShow(session.brief, brief.updating, session.messages.length > 0) && <details className="evidence-brief" onKeyDown={(event) => {
    if (event.key === "Escape" && event.currentTarget.open) { event.stopPropagation(); event.currentTarget.open = false; }
  }}>
    <summary>Brief</summary>
    <BriefPanel brief={session.brief} updating={brief.updating} readOnly={brief.readOnly}
      hasMessages={session.messages.length > 0} citations={citations} onSave={brief.save} onRetry={brief.retry}
      onOpenCitation={(item) => { const index = citations.indexOf(item); if (index >= 0) onOpen(citations, index); }} />
  </details>;
  if (!selected) return <div className="evidence-overview" aria-label="Evidence overview">
    <h3>Current scope</h3>
    <p>{allSelected ? `All ${sources.length} ${sources.length === 1 ? "source" : "sources"}` : `${selectedCount} of ${sources.length} selected`}</p>
    <p>Web {webEnabled ? "on" : "off"}</p>
    <p>Ask a question to inspect its sources and searches here.</p>
    {briefPanel}
  </div>;
  const steps = selected.context.steps ?? [];
  const searches = steps.filter((step) => step.tool === "search_library" || step.tool === "search_web").length;
  const coverage = selected.context.coverage;
  return <div className="evidence-overview" aria-label="Evidence overview">
    <section className="evidence-method" aria-label="How it was reached">
      <h3>How it was reached</h3>
      {steps.length > 0 && <details><summary>{searches > 0 && `${searches} ${searches === 1 ? "search" : "searches"} · `}{steps.length} {steps.length === 1 ? "step" : "steps"}</summary><AgentSteps steps={steps} /></details>}
      <ResearchPlan context={selected.context} />
      {coverage && <p>Source check: {coverage.status}{coverage.unchecked > 0 ? ` · ${coverage.unchecked} unchecked` : ""}</p>}
      {!coverage && selected.context.checked && <p>Sources checked</p>}
      {briefPanel}
      {traceEnabled && onOpenTrace && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" aria-label="View trace" onClick={onOpenTrace}>Trace ↗</button>}
    </section>
    <section className="evidence-cited" aria-label="Cited sources">
      <h3>Cited ({citations.length})</h3>
      {citations.length ? citations.map((item, index) => card(item, index, citations, true))
        : <p>{noSourcesNeeded(selected.context) ? "No sources needed" : "Nothing cited"}</p>}
    </section>
    {earlier.length > 0 && <details className="evidence-earlier">
      <summary>Earlier in this conversation ({earlier.length})</summary>
      {earlier.map((item, index) => card(item, index, earlier, false))}
    </details>}
  </div>;
}
