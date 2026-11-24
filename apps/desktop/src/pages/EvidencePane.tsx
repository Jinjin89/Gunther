import type { ContentsSource, ConversationCitation, KnowledgeSession, SessionMessage } from "@gunther/contracts";
import { useMemo } from "react";
import { EvidenceDetail, hostOf } from "../components/evidence/EvidenceDetail";
import { AgentSteps, ResearchPlan, noSourcesNeeded } from "./AnswerBody";
import "./evidencePane.css";

export interface EvidenceTarget {
  contextKey: string;
  citations: ConversationCitation[];
  index: number;
  passageSelector?: boolean;
  displayIndex?: number;
}

const identity = (citation: ConversationCitation) => citation.ref != null ? `ref:${citation.ref}` : `id:${citation.id}`;

export function EvidencePane({ session, selected, chatVisible, sources, selectedCount, allSelected, webEnabled, detail,
  onOpen, onStep, onBack, onOpenSource, onOriginalPdfChange }: {
  session: KnowledgeSession | null;
  selected: SessionMessage | null;
  chatVisible: boolean;
  sources: readonly ContentsSource[];
  selectedCount: number;
  allSelected: boolean;
  webEnabled: boolean;
  detail: EvidenceTarget | null;
  onOpen: (citations: ConversationCitation[], index: number) => void;
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
  const citation = detail?.citations[detail.index];
  if (detail && citation) return <EvidenceDetail citation={citation} index={detail.displayIndex ?? detail.index}
    passages={detail.passageSelector ? detail.citations : undefined} onPassage={detail.passageSelector ? onStep : undefined}
    onBack={onBack} position={detail.index} total={detail.citations.length}
    onPrevious={detail.index > 0 ? () => onStep(detail.index - 1) : undefined}
    onNext={detail.index + 1 < detail.citations.length ? () => onStep(detail.index + 1) : undefined}
    onOpenSource={onOpenSource} onOriginalPdfChange={onOriginalPdfChange} />;

  const card = (item: ConversationCitation, index: number, list: ConversationCitation[], numbered: boolean) => {
    const kind = item.kind === "web" ? "Web" : sources.find((source) => source.id === item.sourceId)?.kind === "paper" ? "Paper" : "Library";
    const locator = item.locator || (item.kind === "web" ? hostOf(item.url) : "");
    return <button type="button" className="evidence-card" key={`${identity(item)}:${index}`} onClick={() => onOpen(list, index)}>
      <span className="evidence-card-meta">{numbered && <strong>[{index + 1}]</strong>}<small>{kind}</small></span>
      <strong>{item.sourceTitle}</strong>
      {locator && <small>{locator}</small>}
      {(item.supportText || item.quote) && <span className="evidence-card-quote">{item.supportText || item.quote}</span>}
    </button>;
  };
  if (!chatVisible) return <div className="evidence-overview" aria-label="Evidence overview"><p>Open a chat to inspect its sources and searches.</p></div>;
  if (!selected) return <div className="evidence-overview" aria-label="Evidence overview">
    <h3>Current scope</h3>
    <p>{allSelected ? `All ${sources.length} ${sources.length === 1 ? "source" : "sources"}` : `${selectedCount} of ${sources.length} selected`}</p>
    <p>Web {webEnabled ? "on" : "off"}</p>
    <p>Ask a question to inspect its sources and searches here.</p>
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
