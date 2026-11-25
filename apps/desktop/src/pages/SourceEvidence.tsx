import type { ContentsSource, ConversationCitation, KeyResult } from "@gunther/contracts";
import { ArrowRight } from "lucide-react";
import { useEffect, useState } from "react";
import { knowledgeApi } from "../api";
import { keyResultCitation } from "../keyResults/KeyResultsGroup";
import { KEY_RESULTS_CHANGED_EVENT, type KeyResultsChange } from "../keyResults/events";
import { SourceKindMark, sourceKindMeta } from "./EvidencePrimitives";

const ORIGIN: Record<KeyResult["origin"]["kind"], string> = {
  answer: "From chat", output: "From output", saved_answer: "From saved answer",
};

export function SourceEvidence({ baseId, source, onInspect, onCount }: {
  baseId: string; source: ContentsSource; onInspect: (passages: ConversationCitation[]) => void; onCount: (id: string, count: number) => void;
}) {
  const sourceId = source.id;
  const [results, setResults] = useState<KeyResult[] | null>(null);
  const [error, setError] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const changed = (event: Event) => {
      if ((event as CustomEvent<KeyResultsChange>).detail?.baseId === baseId) setRevision((value) => value + 1);
    };
    const sourcesChanged = () => setRevision((value) => value + 1);
    window.addEventListener(KEY_RESULTS_CHANGED_EVENT, changed);
    window.addEventListener("gunther:sources-updated", sourcesChanged);
    return () => { window.removeEventListener(KEY_RESULTS_CHANGED_EVENT, changed); window.removeEventListener("gunther:sources-updated", sourcesChanged); };
  }, [baseId]);
  useEffect(() => {
    const controller = new AbortController();
    setResults(null); setError(false); onCount(sourceId, 0);
    void knowledgeApi.keyResults(baseId, controller.signal, [sourceId]).then((items) => {
      if (!controller.signal.aborted) { setResults(items); onCount(sourceId, items.length); }
    }).catch(() => { if (!controller.signal.aborted) { setError(true); onCount(sourceId, 0); } });
    return () => controller.abort();
  }, [baseId, sourceId, revision, onCount]);
  const meta = sourceKindMeta(source.kind);
  const description = [meta.kindLabel, source.year, source.pages && `${source.pages} ${source.pages === 1 ? "page" : "pages"}`].filter(Boolean).join(" · ");
  return <div className="evidence-overview evidence-bands" aria-label="Evidence overview">
    <section className="evidence-context" aria-label="Source details">
      <header className="evidence-source-heading">
        <SourceKindMark {...meta} />
        <div><h3>{source.title}</h3><span className="evidence-meta">{description}</span></div>
      </header>
      {results && <p>{results.length} {results.length === 1 ? "key result" : "key results"} backed by this source</p>}
    </section>
    <section className="evidence-cited" aria-label="Source key results">
      <h3>Key results{results && <> <span className="evidence-count">({results.length})</span></>}</h3>
      {error ? <p role="status">Could not load key results. <button type="button" className="gx-link" onClick={() => setRevision((value) => value + 1)}>Retry</button></p>
        : !results ? <p>Loading key results…</p>
          : results.length === 0 ? <div className="evidence-card evidence-results-empty">Checked findings appear here when this source supports them.</div>
            : results.map((result) => <button type="button" className="evidence-card evidence-result" key={result.id}
              disabled={result.evidence.length === 0} onClick={() => onInspect(result.evidence.map(keyResultCitation))}>
              <strong>{result.text}</strong>
              <span className="evidence-meta">{[ORIGIN[result.origin.kind], result.origin.title].filter(Boolean).join(" · ")}</span>
              <span className="evidence-pill">{result.evidence.length} {result.evidence.length === 1 ? "passage" : "passages"}<ArrowRight size={12} aria-hidden="true" /></span>
            </button>)}
    </section>
  </div>;
}
