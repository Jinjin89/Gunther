import type { ConversationCitation, KeyResult } from "@gunther/contracts";
import { useEffect, useState } from "react";
import { knowledgeApi } from "../api";
import { keyResultCitation } from "../keyResults/KeyResultsGroup";
import { KEY_RESULTS_CHANGED_EVENT, type KeyResultsChange } from "../keyResults/events";

export function SourceEvidence({ baseId, sourceId, onInspect, onCount }: {
  baseId: string; sourceId: string; onInspect: (passages: ConversationCitation[]) => void; onCount: (id: string, count: number) => void;
}) {
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
  return <div className="evidence-overview" aria-label="Evidence overview">
    <section className="evidence-cited" aria-label="Source key results">
      <h3>Key results{results ? ` (${results.length})` : ""}</h3>
      {error ? <p role="status">Could not load key results. <button type="button" onClick={() => setRevision((value) => value + 1)}>Retry</button></p>
        : !results ? <p>Loading key results…</p>
          : results.length === 0 ? <p>No key results backed by this source.</p>
            : results.map((result) => <button type="button" className="evidence-card" key={result.id}
              disabled={result.evidence.length === 0} onClick={() => onInspect(result.evidence.map(keyResultCitation))}>{result.text}</button>)}
    </section>
  </div>;
}
